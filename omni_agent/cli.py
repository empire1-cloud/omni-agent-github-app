"""Omni-Agent CLI.

Installed as `omni-agent`; `python scripts/omni_agent.py` runs the same code.
Every run is a read-only preview unless `--apply` is passed.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from omni_agent.orchestrator import Orchestrator, find_config, load_config


DEMO_REPO = Path(__file__).resolve().parent / "demo" / "repo"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _repo_root(args) -> Path:
    from omni_agent.setup_wizard import detect_repo_root
    return Path(args.repo_root).resolve() if args.repo_root else detect_repo_root()


def _build_orchestrator(args) -> Orchestrator:
    repo_root = _repo_root(args)
    if not args.config and find_config(repo_root) is None:
        print(f"No Omni-Agent config in {repo_root}.\nRun `omni-agent init` there first "
              "(it only writes .omni-agent/config.yaml and memory/tasks/inbox.md).", file=sys.stderr)
        raise SystemExit(1)
    config = load_config(repo_root, Path(args.config) if args.config else None)
    return Orchestrator(repo_root, config)


def _ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


# ---------- setup ----------

def cmd_init(args) -> int:
    from omni_agent.setup_wizard import detect_allowed_roots, init_workspace

    repo_root = _repo_root(args)
    allowed = args.allow or detect_allowed_roots(repo_root)
    interactive = sys.stdin.isatty() and not args.yes
    print(f"Repository: {repo_root}")
    print("Omni-Agent may only write under:")
    for a in allowed:
        print(f"  + {a}")
    print("Always protected: .env files, keys, secrets/, .git/, CI workflows.")
    if interactive and not args.allow:
        answer = _ask("Press Enter to accept, or type comma-separated globs to use instead: ").strip()
        if answer:
            allowed = [a.strip() for a in answer.split(",") if a.strip()]

    res = init_workspace(repo_root, allowed=allowed, persona_mode=args.mode,
                         with_examples=not args.no_examples, force=args.force)
    for f in res.created:
        print(f"created  {f}")
    for f in res.kept:
        print(f"kept     {f} (already existed{'; use --force to regenerate' if f.endswith('.yaml') else ''})")

    orc = Orchestrator(repo_root, load_config(repo_root))
    summary = orc.scan()
    print(f"\nFound {summary['parsed']} open task(s).")
    if args.json:
        _print_json({"created": res.created, "kept": res.kept, "allowed_paths": res.allowed_paths,
                     "forbidden_paths": res.forbidden_paths, "scan": summary})
    print("\nNext:\n  omni-agent preview        # see what the first task would change (writes nothing)\n"
          "  omni-agent run-next --apply\n  omni-agent dashboard")
    return 0


def cmd_quickstart(args) -> int:
    args.yes = True
    args.allow = None
    args.no_examples = False
    args.force = False
    args.mode = getattr(args, "mode", "hybrid")
    json_flag, args.json = args.json, False
    cmd_init(args)
    args.json = json_flag
    print("\nRunning your first task as a preview (nothing is written)…\n")
    args.task_id = None
    return cmd_preview(args)


# ---------- run ----------

def _confirm_writes(orc: Orchestrator, assume_yes: bool):
    def confirm(changes: List[Dict[str, Any]]) -> bool:
        print("\nAbout to write:")
        for ch in changes:
            print(f"  {ch.get('action', 'edit'):<8} {ch.get('path')}")
        print("Every path above passed the guardrails "
              f"({len(orc.guardrails.allowed)} allowed roots, {len(orc.guardrails.forbidden)} protected patterns).")
        if assume_yes:
            return True
        return _ask("Write these files? [y/N] ").strip().lower() in ("y", "yes")
    return confirm


def _run(args, task_id: Optional[str]) -> int:
    orc = _build_orchestrator(args)
    safety = orc.config.get("safety", {}) or {}
    apply = bool(getattr(args, "apply", False)) and not getattr(args, "dry_run", False)
    if not apply and not safety.get("dry_run_by_default", True) and not getattr(args, "dry_run", False) \
            and not getattr(args, "preview_only", False):
        apply = True  # config opted out of preview-by-default
    confirm = None
    if apply and safety.get("confirm_before_write", True):
        if not args.yes and not sys.stdin.isatty():
            print("Refusing to write without confirmation: pass --yes to confirm non-interactively.",
                  file=sys.stderr)
            return 4
        confirm = _confirm_writes(orc, args.yes)
    approver = getattr(args, "by", None) or getpass.getuser()

    kwargs = {"dry_run": not apply, "confirm": confirm, "approver": approver}
    out = orc.run_task(task_id, **kwargs) if task_id else orc.run_next(**kwargs)
    if not out:
        print("No runnable tasks. Add one with `omni-agent new \"...\"` or `omni-agent import`.")
        return 0
    if out.get("skipped"):
        print(f"{out['task_id']}: {out['skipped']}")
        return 0
    _print_human_output(out)
    if out.get("final_status") in ("dry_run", "declined"):
        print(f"\nPreview only — nothing was written. To apply: omni-agent run-task {out['task_id']} --apply")
    if args.json:
        _print_json(out)
    status = out.get("final_status")
    if status == "needs_approval":
        return 4
    return 0 if status in ("done", "dry_run", "declined") else 2


def cmd_run_next(args) -> int:
    return _run(args, None)


def cmd_run_task(args) -> int:
    return _run(args, args.task_id)


def cmd_preview(args) -> int:
    args.apply = False
    args.dry_run = True
    args.preview_only = True
    return _run(args, getattr(args, "task_id", None))


# ---------- intake ----------

def cmd_scan(args) -> int:
    orc = _build_orchestrator(args)
    summary = orc.scan()
    print(f"Scanned tasks. parsed={summary['parsed']} new={summary['new']} existing={summary['existing']}")
    if args.json:
        _print_json(summary)
    return 0


def _write_and_scan(args, tasks, target: str) -> int:
    from omni_agent.intake import write_tasks

    orc = _build_orchestrator(args)
    if not orc.guardrails.check_write(target)[0]:
        print(f"Refusing to write tasks to '{target}': {orc.guardrails.check_write(target)[1]}", file=sys.stderr)
        return 3
    res = write_tasks(orc.repo_root, tasks, target)
    summary = orc.scan()
    print(f"Added {len(res['added'])} task(s) to {target}"
          + (f"; skipped {len(res['skipped'])} already imported" if res["skipped"] else "") + ".")
    print(f"Queue now has {summary['parsed']} open task(s). Next: omni-agent preview")
    if args.json:
        _print_json({**res, "scan": summary})
    return 0


def cmd_import(args) -> int:
    from omni_agent import intake

    try:
        if args.source == "github":
            if args.file:
                items = json.loads(Path(args.file).read_text(encoding="utf-8"))
            elif args.repo:
                items = intake.fetch_github_issues(args.repo, label=args.label, limit=args.limit)
            else:
                print("import github needs --repo OWNER/NAME or --file issues.json", file=sys.stderr)
                return 3
            tasks = intake.from_github_issues(items)
        else:
            if not args.file:
                print(f"import {args.source} needs --file export.csv", file=sys.stderr)
                return 3
            tasks = intake.from_csv(Path(args.file), source=args.source)
    except (OSError, ValueError) as e:
        print(f"Import failed: {e}", file=sys.stderr)
        return 3
    return _write_and_scan(args, tasks, args.to or f"memory/tasks/imported/{args.source}.md")


def cmd_new(args) -> int:
    from omni_agent.intake import from_template

    try:
        task = from_template(args.title, template=args.template, path=args.path,
                             priority=args.priority, task_type=args.type, acceptance=args.accept)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 3
    return _write_and_scan(args, [task], args.to)


# ---------- evidence ----------

def cmd_explain(args) -> int:
    from omni_agent.reporting.evidence import format_receipt, task_receipt

    orc = _build_orchestrator(args)
    r = task_receipt(orc.state, args.task_id)
    if not r:
        print(f"Unknown task {args.task_id}", file=sys.stderr)
        return 3
    print(format_receipt(r))
    if args.json:
        _print_json(r)
    return 0


def cmd_approve(args) -> int:
    orc = _build_orchestrator(args)
    if not orc.state.get_task(args.task_id):
        print(f"Unknown task {args.task_id}", file=sys.stderr)
        return 3
    decision = "rejected" if args.reject else "approved"
    who = args.by or getpass.getuser()
    orc.state.add_approval(args.task_id, approver=who, decision=decision, scope="task", note=args.note)
    print(f"{args.task_id} {decision} by {who}.")
    return 0


def cmd_status(args) -> int:
    from omni_agent.reporting.roi import format_summary_line, outcomes

    orc = _build_orchestrator(args)
    st = orc.status()
    print(f"Total tasks: {st['total']}")
    for s, n in sorted(st["by_status"].items()):
        print(f"  {s}: {n}")
    print("")
    for line in outcomes(orc.state, orc.config)["statements"]:
        print(f"• {line}")
    print("")
    print("ROI (all time): " + format_summary_line(st["roi"]))
    print("ROI (last 7d):  " + format_summary_line(st["roi_weekly"]))
    if args.verbose or args.json:
        for t in st["tasks"]:
            print(f"  - {t['id']} [{t['status']}] {t['task_type']} p{t['priority']} :: {t['normalized_text']}")
    if args.json:
        _print_json(st)
    return 0


def cmd_report(args) -> int:
    orc = _build_orchestrator(args)
    path = orc.report()
    print(f"Report written: {path}")
    return 0


def cmd_dashboard(args) -> int:
    from omni_agent.reporting import dashboard

    orc = _build_orchestrator(args)
    paths = dashboard.write(orc.state, orc.config, orc.repo_root)
    print(f"Dashboard written: {paths['html']}")
    if args.open:
        webbrowser.open(paths["html"].as_uri())
    if args.json:
        _print_json({k: str(v) for k, v in paths.items()})
    return 0


def cmd_pr_preview(args) -> int:
    orc = _build_orchestrator(args)
    from omni_agent.reporting.pr_preview import PRPreviewError
    try:
        result = orc.generate_pr_preview(args.task_id)
    except PRPreviewError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 3
    print(f"PR preview written: {result['path']}")
    if args.print:
        print("")
        print(result["markdown"])
    if args.json:
        _print_json({"path": str(result["path"]), "meta": result["meta"]})
    return 0


# ---------- demo ----------

def cmd_demo(args) -> int:
    """Copy the bundled demo repo somewhere disposable and run every task end to end, offline."""
    from omni_agent.reporting import dashboard
    from omni_agent.reporting.evidence import format_receipt, task_receipt
    from omni_agent.setup_wizard import init_workspace

    target = Path(args.dir).resolve() if args.dir else Path(tempfile.mkdtemp(prefix="omni-agent-demo-")) / "repo"
    if target.exists() and any(target.iterdir()):
        print(f"{target} is not empty; pick another --dir.", file=sys.stderr)
        return 3
    shutil.copytree(DEMO_REPO, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    init_workspace(target, persona_mode="rule")
    orc = Orchestrator(target, load_config(target))
    orc.config["workspace"] = {"name": "textkit demo"}
    found = orc.scan()["parsed"]
    print(f"Demo repo: {target}\nFound {found} tasks in memory/tasks/inbox.md. Running each one (rule mode, offline).\n")

    seen: List[str] = []
    while True:
        nxt = orc.state.next_runnable()
        if not nxt or nxt["id"] in seen:
            break
        seen.append(nxt["id"])
        out = orc.run_task(nxt["id"], confirm=lambda changes: True, approver="demo")
        print(f"{out['task_id']:<14} {out['final_status']:<16} {nxt['normalized_text'][:70]}")

    print("")
    for tid in seen:
        print(format_receipt(task_receipt(orc.state, tid)))
        print("-" * 72)
    paths = dashboard.write(orc.state, orc.config, target)
    print(f"Dashboard: {paths['html']}")
    if args.open:
        webbrowser.open(paths["html"].as_uri())
    return 0


# ---------- output ----------

def _print_human_output(out: dict) -> None:
    print("=" * 72)
    print(f"Task ID:               {out.get('task_id')}")
    print(f"Run ID:                {out.get('run_id')}")
    print(f"Final status:          {out.get('final_status')}")
    print(f"Dry run:               {out.get('dry_run')}")
    if out.get("blocked_stage"):
        print(f"Blocked stage:         {out.get('blocked_stage')}")
        print(f"Blocked reason:        {out.get('blocked_reason')}")
    print(f"Cohesion score:        {out.get('cohesion_score')}")
    print(f"Files changed:         {out.get('files_changed')}")
    if out.get("filtered_by_guardrails"):
        print(f"Filtered by guardrails: {out.get('filtered_by_guardrails')}")
    print(f"Risks/blockers:        {out.get('risks_blockers')}")
    print(f"Next task recommended: {out.get('next_task_recommendation')}")
    spec = out.get("mini_spec") or {}
    if spec:
        print("Mini-spec:")
        print(f"  scope: {spec.get('scope')}")
        print(f"  acceptance_criteria: {spec.get('acceptance_criteria')}")
        print(f"  impacted_files: {spec.get('impacted_files')}")
    tests = out.get("tests") or {}
    if tests:
        print(f"Tests: status={tests.get('status')} ran={tests.get('ran')} cmd={tests.get('command')}")
    lint = out.get("lint") or {}
    if lint:
        print(f"Lint:  status={lint.get('status')}")
    gc = (out.get("evidence") or {}).get("guardrail_compliance") or {}
    if gc:
        print(f"Guardrail: score={gc.get('score')} proposed={gc.get('proposed_total')} "
              f"applied={gc.get('applied_total')} filtered={len(gc.get('filtered_paths') or [])}")
    github_pr = out.get("github_pr") or {}
    if github_pr.get("pr_url"):
        print(f"Pull request:          {github_pr.get('pr_url')}")
        if github_pr.get("check_run_id"):
            print(f"Check run ID:          {github_pr.get('check_run_id')}")
    cr = out.get("client_report")
    if cr:
        print(f"Client report: {cr.get('markdown')}")
        print(f"               {cr.get('json')}")
    print("=" * 72)


def build_parser(default_root: Optional[Path] = None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="omni-agent",
        description="Turns backlog tasks into verified code changes with guardrails. "
                    "Runs are previews unless you pass --apply.",
    )
    p.add_argument("--repo-root", default=str(default_root) if default_root else None,
                   help="repository to work in (default: nearest git root above the current directory)")
    p.add_argument("--config", default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    sub = p.add_subparsers(dest="cmd", required=True)

    it = sub.add_parser("init", help="set up this repo: detect write roots, seed example tasks")
    it.add_argument("--allow", action="append", help="allowed write glob (repeatable); skips detection")
    it.add_argument("--mode", choices=["hybrid", "rule", "llm"], default="hybrid")
    it.add_argument("--no-examples", action="store_true", help="don't create an example inbox")
    it.add_argument("--force", action="store_true", help="regenerate an existing .omni-agent/config.yaml")
    it.add_argument("-y", "--yes", action="store_true", help="accept detected settings without prompting")

    sub.add_parser("quickstart", help="init with detected settings, then preview the first task")

    dm = sub.add_parser("demo", help="run the bundled demo repo end to end (offline, disposable)")
    dm.add_argument("--dir", help="where to put the demo copy (default: a temp dir)")
    dm.add_argument("--open", action="store_true", help="open the dashboard when done")

    sub.add_parser("scan", help="scan markdown for tasks")

    pv = sub.add_parser("preview", help="show what a task would change; never writes")
    pv.add_argument("task_id", nargs="?")

    def run_flags(sp):
        sp.add_argument("--apply", action="store_true", help="write changes (default is a preview)")
        sp.add_argument("--dry-run", action="store_true", help="force a preview even if config allows writes")
        sp.add_argument("-y", "--yes", action="store_true", help="confirm writes without prompting")
        sp.add_argument("--by", help="name recorded as the approver of the writes (default: OS user)")

    rn = sub.add_parser("run-next", help="run the next highest-priority task (preview unless --apply)")
    run_flags(rn)
    rt = sub.add_parser("run-task", help="run a specific task by id (preview unless --apply)")
    rt.add_argument("task_id")
    run_flags(rt)

    ex = sub.add_parser("explain", help="receipt for a task: what changed, why, checks, blocks, protections")
    ex.add_argument("task_id")

    ap = sub.add_parser("approve", help="record who approved (or rejected) a task")
    ap.add_argument("task_id")
    ap.add_argument("--by")
    ap.add_argument("--note")
    ap.add_argument("--reject", action="store_true")

    im = sub.add_parser("import", help="import tasks from GitHub issues, Jira CSV, or CSV")
    im.add_argument("source", choices=["github", "jira", "csv"])
    im.add_argument("--repo", help="github: OWNER/NAME (uses GITHUB_TOKEN if set; read-only)")
    im.add_argument("--file", help="github: `gh issue list --json ...` output; jira/csv: the export file")
    im.add_argument("--label", help="github: only issues with this label")
    im.add_argument("--limit", type=int, default=50)
    im.add_argument("--to", help="markdown file to append to (default memory/tasks/imported/<source>.md)")

    nw = sub.add_parser("new", help="add a task from a template")
    nw.add_argument("title")
    nw.add_argument("--template", choices=["bugfix", "refactor", "test", "docs"])
    nw.add_argument("--path", help="file the task should touch")
    nw.add_argument("--priority", help="p0..p4, or high/medium/low")
    nw.add_argument("--type", help="backend, frontend, tests, docs, infra")
    nw.add_argument("--accept", action="append", help="acceptance criterion (repeatable)")
    nw.add_argument("--to", default="memory/tasks/inbox.md")

    sub.add_parser("status", help="show task counts, outcomes, ROI, and task list")
    sub.add_parser("report", help="write reports/latest.md")
    db = sub.add_parser("dashboard", help="write the team dashboard (single HTML file)")
    db.add_argument("--open", action="store_true")

    pp = sub.add_parser("pr-preview", help="generate a PR-ready markdown body for a done task")
    pp.add_argument("task_id")
    pp.add_argument("--print", action="store_true", help="print preview body to stdout")

    return p


HANDLERS = {
    "init": cmd_init,
    "quickstart": cmd_quickstart,
    "demo": cmd_demo,
    "scan": cmd_scan,
    "preview": cmd_preview,
    "run-next": cmd_run_next,
    "run-task": cmd_run_task,
    "explain": cmd_explain,
    "approve": cmd_approve,
    "import": cmd_import,
    "new": cmd_new,
    "status": cmd_status,
    "report": cmd_report,
    "dashboard": cmd_dashboard,
    "pr-preview": cmd_pr_preview,
}


def main(argv=None, *, default_root: Optional[Path] = None) -> int:
    parser = build_parser(default_root)
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return HANDLERS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
