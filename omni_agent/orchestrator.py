"""Orchestrator: scan, triage, run persona pipeline, write back to notes."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from omni_agent.guardrails import Guardrails
from omni_agent.github_integration import GitHubIntegrationConfig, create_pr_from_task_sync
from omni_agent.llm_client import LLMClient
from omni_agent.personas import analyst, developer, evaluator
from omni_agent.reporting import client_report, pr_preview, roi as roi_mod
from omni_agent.scanner import scan_repo
from omni_agent.state_machine import StateMachine
from omni_agent.triage import triage_task


logger = logging.getLogger("omni_agent.orchestrator")


# Where a repo keeps its own Omni-Agent config (written by `omni-agent init`).
WORKSPACE_CONFIG = Path(".omni-agent") / "config.yaml"
PACKAGED_CONFIG = Path(__file__).resolve().parent / "config.yaml"


def find_config(repo_root: Path) -> Optional[Path]:
    """Return the repo's own config, or None when the repo was never initialised."""
    for candidate in (repo_root / WORKSPACE_CONFIG, repo_root / "omni_agent" / "config.yaml"):
        if candidate.is_file():
            return candidate
    return None


def load_config(repo_root: Path, config_path: Optional[Path] = None) -> Dict[str, Any]:
    cfg_path = config_path or find_config(repo_root) or PACKAGED_CONFIG
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg["_config_path"] = str(cfg_path)
    cfg.setdefault("repo_root", str(repo_root))
    return cfg


class Orchestrator:
    def __init__(self, repo_root: Path, config: Dict[str, Any]):
        self.repo_root = Path(repo_root).resolve()
        self.config = config
        gr = config.get("guardrails", {})
        self.guardrails = Guardrails(self.repo_root, gr.get("allowed_paths", []), gr.get("forbidden_paths", []))
        self.state = StateMachine(self.repo_root / config["state"]["db_path"])
        self.llm = LLMClient(config, self.repo_root)
        self.persona_mode = config.get("persona_mode", "hybrid")

    # ---------- scanner ----------
    def scan(self) -> Dict[str, Any]:
        scan_cfg = self.config.get("scan", {})
        globs = scan_cfg.get("globs", []) or []
        parsed = scan_repo(self.repo_root, globs)
        new_count = 0
        existing = 0
        for t in parsed:
            tri = triage_task(t, self.config)
            inserted = self.state.upsert_task(
                id=t.id,
                source_file=t.source_file,
                source_line=t.line,
                raw_text=t.raw_text,
                normalized_text=t.text,
                task_type=tri.task_type,
                priority=tri.priority,
                acceptance_criteria=t.acceptance,
            )
            if inserted:
                new_count += 1
            else:
                existing += 1
            self.state.update_triage(t.id, task_type=tri.task_type, priority=tri.priority)
        return {"parsed": len(parsed), "new": new_count, "existing": existing}

    # ---------- task pipeline ----------
    def run_task(
        self,
        task_id: str,
        *,
        dry_run: bool = False,
        confirm: Optional[Callable[[List[Dict[str, Any]]], bool]] = None,
        approver: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run one task. `confirm` is shown the guarded change list before real
        writes; `approver` is recorded against that decision."""
        task = self.state.get_task(task_id)
        if not task:
            raise ValueError(f"task {task_id} not found")
        if task["status"] == "done":
            return {"task_id": task_id, "skipped": "already done"}

        safety = self.config.get("safety", {}) or {}
        if not dry_run and safety.get("require_approval") and not self.state.is_approved(task_id):
            return {
                "task_id": task_id,
                "run_id": None,
                "final_status": "needs_approval",
                "dry_run": False,
                "risks_blockers": [
                    f"safety.require_approval is on: record one with `omni-agent approve {task_id}` "
                    "after reviewing the preview"
                ],
            }

        # triage (re-run)
        from omni_agent.scanner import ParsedTask  # local to avoid cycle
        ptask = ParsedTask(
            id=task["id"],
            source_file=task["source_file"],
            line=task["source_line"] or 0,
            text=task["normalized_text"],
            raw_text=task["raw_text"],
            pattern="checkbox",
            tags=[],
            explicit_id=None,
        )
        tri = triage_task(ptask, self.config)

        run_id = self.state.start_run(task_id, mode=self.persona_mode, config_snapshot=self.config)

        # If task is in an in-progress state from a prior interrupted run, force-reset to not_started
        if task["status"] not in ("not_started", "blocked_context", "blocked_dependency", "evaluating_failed"):
            self.state.transition(task_id, "not_started", actor="orchestrator",
                                  reason="resume: previous run did not complete", run_id=run_id, force=True)
            task = self.state.get_task(task_id)

        # blocked_context up-front?
        block_decision = self._should_block_upfront(tri)
        if block_decision:
            self.state.transition(
                task_id, "blocked_context", actor="triage", reason="; ".join(tri.missing_context), run_id=run_id
            )
            self.state.end_run(run_id, final_status="blocked_context", cohesion_score=None,
                               summary=f"missing context: {tri.missing_context}")
            self._writeback_blocked(task, tri.missing_context)
            return self._build_output(task, run_id, tri, None, None, None,
                                      final_status="blocked_context",
                                      blockers=tri.missing_context, dry_run=dry_run,
                                      blocked_stage="pre_analyst",
                                      blocked_reason=block_decision)

        # ANALYST
        self.state.transition(task_id, "analyzing", actor="orchestrator", run_id=run_id)
        analyst_out = analyst.run(task, tri.__dict__, self.llm, self.persona_mode)
        self.state.add_persona_output(run_id, task_id, "analyst", analyst_out["mode_used"], analyst_out["spec"])
        spec = analyst_out["spec"]

        # If analyst surfaced missing_context with no acceptance criteria, block
        missing_ctx = spec.get("missing_context") or tri.missing_context
        if missing_ctx and not spec.get("acceptance_criteria"):
            self.state.transition(task_id, "blocked_context", actor="analyst",
                                  reason="; ".join(missing_ctx), run_id=run_id)
            self.state.end_run(run_id, final_status="blocked_context", cohesion_score=None,
                               summary="analyst flagged missing context")
            self._writeback_blocked(task, missing_ctx)
            return self._build_output(task, run_id, tri, spec, None, None,
                                      final_status="blocked_context",
                                      blockers=missing_ctx, dry_run=dry_run,
                                      blocked_stage="analyst",
                                      blocked_reason="analyst_flagged_missing_context")

        # DEVELOPER
        self.state.transition(task_id, "building", actor="orchestrator", run_id=run_id)
        dev_out = developer.run(
            task, tri.__dict__, spec, self.llm, self.guardrails, self.repo_root,
            self.persona_mode, dry_run=dry_run, confirm=confirm,
        )
        self.state.add_persona_output(run_id, task_id, "developer", dev_out["mode_used"], dev_out["patch"])
        for a in dev_out.get("applied", []):
            self.state.add_artifact(run_id, task_id, "file_change", a.get("path"), a)
        for p in dev_out.get("filtered_paths", []) or []:
            self.state.add_artifact(run_id, task_id, "guardrail_block", p,
                                    {"path": p, "reason": self.guardrails.check_write(p)[1]})
        if confirm is not None and not dry_run and not dev_out.get("blocked_context"):
            planned = ", ".join(a.get("path", "") for a in dev_out.get("applied", []))
            self.state.add_approval(
                task_id, run_id=run_id, approver=approver or "operator",
                decision="declined" if dev_out.get("declined") else "approved",
                scope=f"writes: {planned}" if planned else "writes",
            )
        if dev_out.get("declined"):
            dry_run = True

        if dev_out.get("blocked_context"):
            self.state.transition(task_id, "blocked_context", actor="developer",
                                  reason=dev_out["blocked_context"], run_id=run_id)
            self.state.end_run(run_id, final_status="blocked_context", cohesion_score=None,
                               summary=dev_out["blocked_context"])
            self._writeback_blocked(task, [dev_out["blocked_context"]])
            return self._build_output(task, run_id, tri, spec, dev_out, None,
                                      final_status="blocked_context",
                                      blockers=[dev_out["blocked_context"]], dry_run=dry_run,
                                      blocked_stage="developer",
                                      blocked_reason="developer_no_writable_target")

        if dry_run:
            # don't transition further; revert to not_started but keep artifacts as evidence
            final = "declined" if dev_out.get("declined") else "dry_run"
            reason = "writes declined at confirmation" if final == "declined" else "dry-run complete"
            self.state.transition(task_id, "not_started", actor="orchestrator",
                                  reason=reason, run_id=run_id, force=True)
            self.state.end_run(run_id, final_status=final, cohesion_score=None,
                               summary=f"{reason}: no evaluator, no writeback")
            return self._build_output(task, run_id, tri, spec, dev_out, None,
                                      final_status=final, blockers=[], dry_run=True)

        # EVALUATOR
        self.state.transition(task_id, "evaluating", actor="orchestrator", run_id=run_id)
        eval_out = evaluator.run(
            task, spec, dev_out, self.llm, self.repo_root, self.persona_mode,
            self.config.get("cohesion", {}).get("weights", {}),
            test_path=(self.config.get("evaluation", {}) or {}).get("test_path", "backend/tests"),
        )
        self.state.add_persona_output(run_id, task_id, "evaluator", eval_out["mode_used"], eval_out)
        for t in [eval_out.get("tests", {})]:
            if t.get("ran"):
                self.state.add_test_execution(
                    run_id, task_id,
                    command=t.get("command", "pytest"),
                    exit_code=t.get("exit_code") or 0,
                    status=t.get("status", "skipped"),
                    output_excerpt=t.get("output", ""),
                )

        threshold = self.config.get("cohesion", {}).get("done_threshold", 85)
        score = eval_out["cohesion_score"]

        if score >= threshold and eval_out.get("regression_ok"):
            self.state.transition(task_id, "done", actor="evaluator",
                                  reason=f"score {score} >= {threshold}", run_id=run_id)
            final_status = "done"
            summary = f"done score={score}"
            self._writeback_done(task)
        else:
            self.state.transition(task_id, "evaluating_failed", actor="evaluator",
                                  reason=f"score {score} below threshold {threshold}", run_id=run_id)
            final_status = "evaluating_failed"
            summary = f"failed score={score}"

        self.state.end_run(run_id, final_status=final_status, cohesion_score=score, summary=summary)
        out = self._build_output(task, run_id, tri, spec, dev_out, eval_out,
                                 final_status=final_status, blockers=[], dry_run=False)
        if final_status == "done":
            self._attach_github_pr(
                task_id=task_id,
                run_id=run_id,
                cohesion_score=score,
                out=out,
            )
        # auto-generate client report (non-fatal if it errors)
        try:
            paths = client_report.generate(run_output=out, state=self.state, config=self.config)
            out["client_report"] = {"markdown": str(paths["markdown"]), "json": str(paths["json"])}
        except Exception as e:  # pragma: no cover
            logger.warning("client report generation failed: %s", e)
            out["client_report"] = None
        return out

    def run_next(self, *, dry_run: bool = False, **kwargs: Any) -> Optional[Dict[str, Any]]:
        nxt = self.state.next_runnable()
        if not nxt:
            return None
        return self.run_task(nxt["id"], dry_run=dry_run, **kwargs)

    def _attach_github_pr(
        self,
        *,
        task_id: str,
        run_id: str,
        cohesion_score: float,
        out: Dict[str, Any],
    ) -> None:
        """Post and persist a PR for a completed task without failing the task."""
        out["github_pr"] = None
        github_config = GitHubIntegrationConfig(self.config)
        if not github_config.is_ready():
            return

        title = None
        body = None
        try:
            preview = self.generate_pr_preview(task_id)
            title = (preview.get("meta") or {}).get("title")
            body = preview.get("markdown")
        except Exception as exc:  # pragma: no cover - fallback body remains valid
            logger.warning("PR preview generation failed for %s: %s", task_id, exc)

        result = create_pr_from_task_sync(
            task_id=task_id,
            config=github_config,
            cohesion_score=cohesion_score,
            title=title,
            body=body,
        )
        if not result:
            return

        evidence = {"status": "created", **result}
        out["github_pr"] = evidence
        try:
            self.state.add_artifact(
                run_id,
                task_id,
                "github_pull_request",
                result.get("pr_url"),
                evidence,
            )
        except Exception as exc:  # pragma: no cover - output still carries the URL
            logger.warning("PR artifact persistence failed for %s: %s", task_id, exc)

    # ---------- monetized features ----------
    def compute_roi(self, *, window: Optional[str] = None) -> Dict[str, Any]:
        """window: None=all-time, 'weekly', 'monthly'."""
        if window == "weekly":
            return roi_mod.weekly(self.state, self.config)
        if window == "monthly":
            return roi_mod.monthly(self.state, self.config)
        return roi_mod.compute_roi(self.state, self.config)

    def generate_client_report(self, run_output: Dict[str, Any]) -> Dict[str, Path]:
        return client_report.generate(
            run_output=run_output, state=self.state, config=self.config,
        )

    def generate_pr_preview(self, task_id: str) -> Dict[str, Any]:
        return pr_preview.generate(task_id=task_id, state=self.state, config=self.config)

    # ---------- block-up-front decision ----------
    def _should_block_upfront(self, tri):
        """Decide whether to block before Analyst. Returns reason code or None."""
        if not tri.missing_context:
            return None
        if self.persona_mode == "rule":
            return "rule_mode_missing_context"
        tri_cfg = self.config.get("triage", {}) or {}
        if self.persona_mode == "hybrid" and tri_cfg.get("hybrid_block_on_missing_context", True):
            threshold = tri_cfg.get("missing_context_threshold", "high")
            sev_map = {"high": 1, "medium": 2, "low": 3}
            need = sev_map.get(threshold, 1)
            if len(tri.missing_context) >= need:
                conf_thresh = float(tri_cfg.get("confidence_threshold", 0.5))
                if tri.confidence < conf_thresh:
                    return "missing_context_low_confidence"
        return None

    # ---------- writeback ----------
    def _writeback_done(self, task: Dict[str, Any]) -> None:
        path = self.repo_root / task["source_file"]
        if not path.exists():
            return
        ok, _ = self.guardrails.check_write(task["source_file"])
        if not ok:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            idx = (task["source_line"] or 1) - 1
            if 0 <= idx < len(lines):
                line = lines[idx]
                new_line = re.sub(r"^(\s*-\s*)\[\s\]", r"\1[x]", line, count=1)
                if new_line != line:
                    new_line = re.sub(r"\s*\(done .*?\)\s*$", "", new_line)
                    new_line = f"{new_line.rstrip()} (done {ts} {task['id']})"
                    lines[idx] = new_line
                    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _writeback_blocked(self, task: Dict[str, Any], reasons: list) -> None:
        path = self.repo_root / task["source_file"]
        if not path.exists():
            return
        ok, _ = self.guardrails.check_write(task["source_file"])
        if not ok:
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            idx = (task["source_line"] or 1) - 1
            if 0 <= idx < len(lines):
                tag = f"BLOCKED_CONTEXT: {' | '.join(reasons)} (task {task['id']})"
                marker_re = re.compile(r"\s*<!--\s*BLOCKED_CONTEXT:.*?-->\s*$")
                base = marker_re.sub("", lines[idx]).rstrip()
                lines[idx] = f"{base} <!-- {tag} -->"
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    # ---------- output ----------
    def _build_output(
        self,
        task: Dict[str, Any],
        run_id: str,
        triage_obj,
        spec: Optional[Dict[str, Any]],
        dev_out: Optional[Dict[str, Any]],
        eval_out: Optional[Dict[str, Any]],
        *,
        final_status: str,
        blockers: list,
        dry_run: bool,
        blocked_stage: Optional[str] = None,
        blocked_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        files_changed = []
        if dev_out:
            files_changed = [a["path"] for a in dev_out.get("applied", []) if a.get("path")]
        next_recommendation = None
        nxt = self.state.next_runnable()
        if nxt and nxt["id"] != task["id"]:
            next_recommendation = nxt["id"]

        return {
            "task_id": task["id"],
            "run_id": run_id,
            "final_status": final_status,
            "dry_run": dry_run,
            "blocked_stage": blocked_stage,
            "blocked_reason": blocked_reason,
            "mini_spec": spec,
            "files_changed": files_changed,
            "filtered_by_guardrails": (dev_out or {}).get("filtered_paths", []),
            "tests": (eval_out or {}).get("tests"),
            "lint": (eval_out or {}).get("lint"),
            "evidence": {
                "criteria_results": (eval_out or {}).get("criteria_results", []),
                "regression_ok": (eval_out or {}).get("regression_ok"),
                "guardrail_compliance": (eval_out or {}).get("guardrail_compliance"),
                "developer_notes": (dev_out or {}).get("patch", {}).get("notes") if dev_out else None,
                "developer_errors": (dev_out or {}).get("errors") if dev_out else None,
            },
            "cohesion_score": (eval_out or {}).get("cohesion_score"),
            "risks_blockers": blockers,
            "next_task_recommendation": next_recommendation,
            "modes_used": {
                "analyst": (spec or {}).get("__mode__")
                or (dev_out or {}).get("__mode__")
                or self.persona_mode,
                "developer": (dev_out or {}).get("mode_used"),
                "evaluator": (eval_out or {}).get("mode_used"),
            },
        }

    # ---------- reports ----------
    def status(self) -> Dict[str, Any]:
        from collections import Counter
        tasks = self.state.list_tasks()
        counts = Counter(t["status"] for t in tasks)
        return {
            "total": len(tasks),
            "by_status": dict(counts),
            "tasks": tasks,
            "roi": self.compute_roi(),
            "roi_weekly": self.compute_roi(window="weekly"),
        }

    def report(self) -> Path:
        cfg = self.config.get("reports", {})
        latest_path = self.repo_root / cfg.get("latest_path", "omni_agent/reports/latest.md")
        snapshot_path = self.repo_root / cfg.get("state_snapshot_path", "omni_agent/reports/state_snapshot.json")
        latest_path.parent.mkdir(parents=True, exist_ok=True)

        st = self.status()
        lines = ["# Omni-Agent Latest Report", "",
                 f"_Generated: {datetime.now(timezone.utc).isoformat()}_", ""]
        lines.append("## Summary")
        lines.append(f"- Total tasks: **{st['total']}**")
        for s, n in sorted(st["by_status"].items()):
            lines.append(f"- `{s}`: {n}")
        lines.append("")
        lines.append("## Tasks")
        lines.append("| ID | Status | Type | Priority | Source | Last run | Score |")
        lines.append("|---|---|---|---|---|---|---|")
        for t in st["tasks"]:
            run = self.state.latest_run_for_task(t["id"])
            score = "" if not run else (run.get("cohesion_score") or "")
            mode = "" if not run else (run.get("mode") or "")
            run_label = f"{run['final_status'] or '...'} ({mode})" if run else "—"
            lines.append(
                f"| `{t['id']}` | `{t['status']}` | {t['task_type'] or ''} | {t['priority']} "
                f"| {t['source_file']}:{t['source_line']} | {run_label} | {score} |"
            )

        # Weekly ROI section (additive, non-breaking)
        wroi = st["roi_weekly"]
        lines += ["", "## Weekly ROI (last 7 days)",
                  f"- Tasks completed: **{wroi['tasks_completed']}**",
                  f"- Avg cohesion: **{wroi['avg_cohesion_score']}**",
                  f"- Blocked rate: **{int(wroi['blocked_rate']*100)}%**",
                  f"- Test pass rate: **{wroi['test_pass_rate']}**",
                  f"- Hours saved: **{wroi['estimated_hours_saved']}**",
                  f"- Cost saved: **{wroi['currency']} {wroi['estimated_cost_saved']}**",
                  f"- Assumptions: {int(wroi['assumptions']['minutes_per_task_manual'])} min/task @ "
                  f"{wroi['currency']} {wroi['assumptions']['dev_hourly_rate']}/hr"]
        latest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # JSON snapshot (adds roi_weekly; preserves existing keys)
        snapshot = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {"total": st["total"], "by_status": st["by_status"]},
            "tasks": st["tasks"],
            "roi_weekly": wroi,
            "roi_all_time": st["roi"],
        }
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")

        # canonical tasks.json export (per spec)
        json_export = self.repo_root / self.config["state"]["json_export_path"]
        self.state.export_json(json_export)
        return latest_path
