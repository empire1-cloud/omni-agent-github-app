"""Team dashboard: one self-contained HTML file built from the local state DB.

No server, no network, no external assets: open the file in a browser, attach
it to a status update, or publish it wherever the team already looks.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from omni_agent.reporting import roi as roi_mod
from omni_agent.reporting.evidence import task_receipt
from omni_agent.state_machine import StateMachine


QUEUED = {"not_started", "analyzing", "building", "evaluating", "evaluating_failed"}
BLOCKED = {"blocked_context", "blocked_dependency"}


def build_data(state: StateMachine, config: Dict[str, Any]) -> Dict[str, Any]:
    tasks = state.list_tasks()
    receipts = [task_receipt(state, t["id"]) for t in tasks]
    all_time = roi_mod.compute_roi(state, config)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace": (config.get("workspace") or {}).get("name", "default"),
        "outcomes": roi_mod.outcomes(state, config),
        "roi_all_time": all_time,
        "counts": {
            "queued": sum(1 for t in tasks if t["status"] in QUEUED),
            "blocked": sum(1 for t in tasks if t["status"] in BLOCKED),
            "done": sum(1 for t in tasks if t["status"] == "done"),
            "total": len(tasks),
        },
        "tasks": receipts,
        "approvals": state.list_approvals(),
    }


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _task_rows(receipts: List[Dict[str, Any]], statuses: set, cols: str) -> str:
    rows = []
    for r in receipts:
        if r["status"] not in statuses:
            continue
        if cols == "queue":
            planned = ", ".join(c["path"] for c in r["planned_changes"]) or "—"
            rows.append(f"<tr><td><code>{_e(r['task_id'])}</code></td><td>{_e(r['title'])}</td>"
                        f"<td>{_e(r['status_label'])}</td><td>{_e(planned)}</td></tr>")
        elif cols == "blocked":
            rows.append(f"<tr><td><code>{_e(r['task_id'])}</code></td><td>{_e(r['title'])}</td>"
                        f"<td>{_e(r['blocked'])}</td></tr>")
        else:
            files = ", ".join(c["path"] for c in r["what_changed"]) or "—"
            t = r["tests"]
            checks = t["status"]
            if t["criteria_total"]:
                checks += f" · {t['criteria_met']}/{t['criteria_total']} criteria"
            approved = ", ".join(sorted({a["approver"] for a in r["approvals"] if a["decision"] == "approved"})) or "—"
            rows.append(f"<tr><td><code>{_e(r['task_id'])}</code></td><td>{_e(r['title'])}</td>"
                        f"<td>{_e(files)}</td><td>{_e(checks)}</td>"
                        f"<td>{_e(r['cohesion_score'])}</td><td>{_e(approved)}</td></tr>")
    return "".join(rows)


def _receipt_block(r: Dict[str, Any]) -> str:
    def li(items: List[str]) -> str:
        return "".join(f"<li>{_e(i)}</li>" for i in items) or "<li class=muted>nothing</li>"

    changed = [f"{c['action'] or 'edit'} {c['path']}" for c in r["what_changed"]]
    if not changed and r["planned_changes"]:
        changed = [f"(preview) {c['action'] or 'edit'} {c['path']}" for c in r["planned_changes"]]
    t = r["tests"]
    checked = [f"tests: {t['status']}"]
    if t.get("lint"):
        checked.append(f"lint: {t['lint']}")
    if t["criteria_total"]:
        checked.append(f"acceptance: {t['criteria_met']}/{t['criteria_total']} criteria met")
    approvals = [f"{a['decision']} by {a['approver']} · {a['ts'][:16].replace('T', ' ')}" for a in r["approvals"]]
    return (
        f"<details><summary><code>{_e(r['task_id'])}</code> {_e(r['title'])}"
        f"<span class='pill s-{_e(r['status'])}'>{_e(r['status_label'])}</span></summary>"
        "<div class=receipt>"
        f"<div><h4>Why</h4><ul>{li([r['why']['scope'] or r['why']['task']] + ['must: ' + c for c in r['why']['acceptance_criteria']])}</ul></div>"
        f"<div><h4>What changed</h4><ul>{li(changed)}</ul></div>"
        f"<div><h4>What was checked</h4><ul>{li(checked)}</ul></div>"
        f"<div><h4>What was blocked</h4><ul>{li([r['blocked']] if r['blocked'] else [])}</ul></div>"
        f"<div><h4>What was protected</h4><ul>{li(r['protected_paths'])}</ul></div>"
        f"<div><h4>Who approved</h4><ul>{li(approvals)}</ul></div>"
        "</div></details>"
    )


CSS = """
:root{--bg:#f7f6f2;--panel:#fff;--ink:#16181b;--muted:#5f6670;--line:#e3e1da;--accent:#9a6a00;--ok:#1f7a4d;--warn:#a35200;--bad:#b3261e}
@media (prefers-color-scheme: dark){:root{--bg:#0d0f11;--panel:#16191c;--ink:#f1eee7;--muted:#9aa1a9;--line:#2a2e33;--accent:#f1bd52;--ok:#5df0b1;--warn:#ffb86b;--bad:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 64px}
header{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:baseline}
h1{margin:0;font-size:24px}h2{font-size:17px;margin:36px 0 10px}h4{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.muted{color:var(--muted)}code{font:13px ui-monospace,Menlo,monospace}
.outcomes{margin:20px 0 0;padding:18px 20px;background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:8px}
.outcomes li{margin:2px 0}.outcomes ul{margin:0;padding-left:18px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:20px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.tile b{display:block;font-size:26px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:13px}
.table{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:640px}th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
td code{white-space:nowrap}th{font-size:12px;color:var(--muted);font-weight:600}tr:last-child td{border-bottom:0}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:8px 0}
summary{cursor:pointer;padding:10px 14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.pill{margin-left:auto;font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.s-done{color:var(--ok)}.s-blocked_context,.s-blocked_dependency{color:var(--warn)}.s-evaluating_failed{color:var(--bad)}
.receipt{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;padding:4px 14px 14px}
.receipt ul{margin:0;padding-left:16px}
"""


def render_html(data: Dict[str, Any]) -> str:
    o = data["outcomes"]
    c = data["counts"]
    r = data["roi_all_time"]
    rate = "—" if r["test_pass_rate"] is None else f"{int(r['test_pass_rate'] * 100)}%"
    receipts = data["tasks"]
    approvals = "".join(
        f"<tr><td>{_e(a['ts'][:16].replace('T', ' '))}</td><td><code>{_e(a['task_id'])}</code></td>"
        f"<td>{_e(a['approver'])}</td><td>{_e(a['decision'])}</td><td>{_e(a.get('scope'))}</td></tr>"
        for a in reversed(data["approvals"])
    )
    empty = "<tr><td colspan=6 class=muted>none</td></tr>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Omni-Agent Dashboard</title><style>{CSS}</style></head>
<body><main>
<header><h1>Omni-Agent · {_e(data['workspace'])}</h1>
<span class=muted>Generated {_e(data['generated_at'][:16].replace('T', ' '))} UTC from local run history</span></header>
<section class=outcomes><h4>Last 7 days</h4><ul>{''.join(f'<li>{_e(s)}</li>' for s in o['statements'])}</ul></section>
<section class=tiles>
<div class=tile><b>{c['queued']}</b><span>in queue</span></div>
<div class=tile><b>{c['blocked']}</b><span>blocked</span></div>
<div class=tile><b>{c['done']}</b><span>done &amp; verified</span></div>
<div class=tile><b>{rate}</b><span>test pass rate</span></div>
<div class=tile><b>{r['estimated_hours_saved']:g}</b><span>hours saved (all time)</span></div>
</section>
<h2>Queue</h2><div class=table><table><tr><th>Task</th><th>Description</th><th>State</th><th>Preview would change</th></tr>
{_task_rows(receipts, QUEUED, 'queue') or empty}</table></div>
<h2>Blocked</h2><div class=table><table><tr><th>Task</th><th>Description</th><th>Why it stopped</th></tr>
{_task_rows(receipts, BLOCKED, 'blocked') or empty}</table></div>
<h2>Completed</h2><div class=table><table><tr><th>Task</th><th>Description</th><th>Files changed</th><th>Checks</th><th>Score</th><th>Approved by</th></tr>
{_task_rows(receipts, {'done'}, 'done') or empty}</table></div>
<h2>Approvals</h2><div class=table><table><tr><th>When</th><th>Task</th><th>Who</th><th>Decision</th><th>Scope</th></tr>
{approvals or empty}</table></div>
<h2>Receipts</h2>
{''.join(_receipt_block(x) for x in receipts) or '<p class=muted>No tasks yet. Run <code>omni-agent scan</code>.</p>'}
<p class=muted>Hours saved assume {o['assumptions']['minutes_per_task_manual']:g} minutes per task at {_e(o['currency'])} {o['assumptions']['dev_hourly_rate']:g}/hour; change them under <code>roi:</code> in the config.</p>
</main></body></html>
"""


def write(state: StateMachine, config: Dict[str, Any], repo_root: Path) -> Dict[str, Path]:
    reports = config.get("reports", {}) or {}
    html_path = Path(repo_root) / reports.get("dashboard_path", "omni_agent/reports/dashboard.html")
    data = build_data(state, config)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render_html(data), encoding="utf-8")
    json_path = html_path.with_suffix(".json")
    json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return {"html": html_path, "json": json_path}
