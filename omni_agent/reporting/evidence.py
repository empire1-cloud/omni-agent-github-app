"""Per-task receipts: what changed, why, what was tested, what was blocked, what was protected.

Built only from the state DB, so a receipt shows what was recorded rather than
what anyone claims happened.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from omni_agent.state_machine import StateMachine


STATUS_LABELS = {
    "not_started": "Queued",
    "analyzing": "In progress",
    "building": "In progress",
    "evaluating": "In progress",
    "done": "Done — verified",
    "blocked_context": "Blocked — needs context",
    "blocked_dependency": "Blocked — waiting on another task",
    "evaluating_failed": "Needs another pass — checks failed",
}


def task_receipt(state: StateMachine, task_id: str) -> Optional[Dict[str, Any]]:
    task = state.get_task(task_id)
    if not task:
        return None
    runs = state.list_runs(task_id)
    last = runs[-1] if runs else None
    # the last run that actually wrote files (previews record planned changes too)
    applied_run = next((r for r in reversed(runs) if r.get("final_status") not in ("dry_run", "declined", None)), None)

    changed: List[Dict[str, Any]] = []
    planned: List[Dict[str, Any]] = []
    for a in state.list_artifacts(task_id=task_id):
        if a["kind"] != "file_change":
            continue
        entry = {"path": a["path"], "action": a["metadata"].get("action"),
                 "summary": a["metadata"].get("summary"), "run_id": a["run_id"]}
        if a["metadata"].get("dry_run"):
            if last and a["run_id"] == last["run_id"]:
                planned.append(entry)
        elif applied_run and a["run_id"] == applied_run["run_id"]:
            changed.append(entry)

    protected = sorted({a["path"] for a in state.list_artifacts(task_id=task_id)
                        if a["kind"] == "guardrail_block" and a["path"]})

    spec = state.persona_output(last["run_id"], "analyst") if last else None
    evaluation = state.persona_output(applied_run["run_id"], "evaluator") if applied_run else None
    tests = (evaluation or {}).get("tests") or {}
    lint = (evaluation or {}).get("lint") or {}
    criteria = (evaluation or {}).get("criteria_results") or []

    pr = next((a["metadata"] for a in reversed(state.list_artifacts(task_id=task_id))
               if a["kind"] == "github_pull_request"), None)

    return {
        "task_id": task_id,
        "title": task["normalized_text"],
        "source": f"{task['source_file']}:{task['source_line']}",
        "status": task["status"],
        "status_label": ("Blocked — protected path" if protected and task["status"].startswith("blocked")
                         else STATUS_LABELS.get(task["status"], task["status"])),
        "why": {
            "task": task["normalized_text"],
            "scope": (spec or {}).get("scope"),
            "acceptance_criteria": (spec or {}).get("acceptance_criteria") or [],
        },
        "what_changed": changed,
        "planned_changes": planned,
        "tests": {
            "status": tests.get("status") or ("not run" if not evaluation else "skipped"),
            "command": tests.get("command"),
            "lint": lint.get("status"),
            "criteria_met": sum(1 for c in criteria if c.get("met")),
            "criteria_total": sum(1 for c in criteria if not c.get("out_of_scope")),
            "criteria": criteria,
        },
        "cohesion_score": (applied_run or {}).get("cohesion_score"),
        "blocked": task.get("blocked_reason") if task["status"].startswith("blocked") else None,
        "protected_paths": protected,
        "approvals": state.list_approvals(task_id),
        "pull_request": (pr or {}).get("pr_url"),
        "runs": [{"run_id": r["run_id"], "started_at": r["started_at"],
                  "final_status": r["final_status"], "cohesion_score": r["cohesion_score"]} for r in runs],
    }


def format_receipt(r: Dict[str, Any]) -> str:
    lines = [f"{r['task_id']} — {r['status_label']}", f"  {r['title']}", f"  source: {r['source']}", ""]

    lines.append("WHY")
    lines.append(f"  {r['why']['scope'] or r['why']['task']}")
    for c in r["why"]["acceptance_criteria"]:
        lines.append(f"  · must: {c}")

    lines.append("")
    lines.append("WHAT CHANGED")
    if r["what_changed"]:
        for c in r["what_changed"]:
            lines.append(f"  {c['action'] or 'edit':<8} {c['path']}")
    elif r["planned_changes"]:
        lines.append("  nothing written yet — the last run was a preview. It would:")
        for c in r["planned_changes"]:
            lines.append(f"  {c['action'] or 'edit':<8} {c['path']}")
    else:
        lines.append("  nothing written")

    t = r["tests"]
    lines.append("")
    lines.append("WHAT WAS CHECKED")
    lines.append(f"  tests: {t['status']}" + (f"  ({t['command']})" if t.get("command") else ""))
    if t.get("lint"):
        lines.append(f"  lint:  {t['lint']}")
    if t["criteria_total"]:
        lines.append(f"  acceptance: {t['criteria_met']}/{t['criteria_total']} criteria met")
    if r["cohesion_score"] is not None:
        lines.append(f"  score: {r['cohesion_score']}")

    lines.append("")
    lines.append("WHAT WAS BLOCKED")
    lines.append(f"  {r['blocked']}" if r["blocked"] else "  nothing")

    lines.append("")
    lines.append("WHAT WAS PROTECTED")
    if r["protected_paths"]:
        for p in r["protected_paths"]:
            lines.append(f"  refused write to {p}")
    else:
        lines.append("  no protected path was requested")

    if r["approvals"]:
        lines.append("")
        lines.append("WHO APPROVED")
        for a in r["approvals"]:
            lines.append(f"  {a['decision']} by {a['approver']} at {a['ts'][:19]}"
                         + (f" — {a['scope']}" if a.get("scope") else "")
                         + (f" ({a['note']})" if a.get("note") else ""))
    if r["pull_request"]:
        lines.append("")
        lines.append(f"PULL REQUEST  {r['pull_request']}")
    return "\n".join(lines)
