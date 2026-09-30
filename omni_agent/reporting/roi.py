"""ROI tracker: compute sellable metrics from state DB.

Metrics produced:
    tasks_completed, tasks_total, avg_cohesion_score, blocked_rate, test_pass_rate,
    estimated_hours_saved, estimated_cost_saved

Assumptions are configurable under config['roi']:
    minutes_per_task_manual  (default 60)
    dev_hourly_rate          (default 100)
    currency                 (default "USD")
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from omni_agent.state_machine import StateMachine


def _cfg(config: Dict[str, Any]) -> Tuple[float, float, str]:
    roi = (config or {}).get("roi", {}) or {}
    return (
        float(roi.get("minutes_per_task_manual", 60)),
        float(roi.get("dev_hourly_rate", 100)),
        str(roi.get("currency", "USD")),
    )


def _window_clause(since: Optional[datetime]) -> Tuple[str, tuple]:
    if not since:
        return "", ()
    return " WHERE started_at >= ?", (since.isoformat(),)


def compute_roi(state: StateMachine, config: Dict[str, Any], *, since: Optional[datetime] = None) -> Dict[str, Any]:
    minutes_per_task, hourly_rate, currency = _cfg(config)

    with state.conn() as c:
        # All runs (optionally windowed)
        where_runs, args_runs = _window_clause(since)
        runs = [dict(r) for r in c.execute(
            f"SELECT * FROM task_runs{where_runs} ORDER BY started_at DESC", args_runs
        ).fetchall()]
        done_task_ids = {
            row["task_id"] for row in c.execute(
                "SELECT DISTINCT task_id FROM task_runs WHERE final_status='done'"
                + (" AND started_at >= ?" if since else ""),
                (since.isoformat(),) if since else (),
            ).fetchall()
        }
        # Tasks present
        tasks_total = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        # Blocked count (current status)
        blocked_count = c.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('blocked_context','blocked_dependency')"
        ).fetchone()[0]
        # Test pass rate: out of all test_executions (windowed)
        if since:
            tp = c.execute(
                "SELECT status, COUNT(*) as n FROM test_executions WHERE ts >= ? GROUP BY status",
                (since.isoformat(),),
            ).fetchall()
        else:
            tp = c.execute(
                "SELECT status, COUNT(*) as n FROM test_executions GROUP BY status"
            ).fetchall()
        test_counts = {row["status"]: row["n"] for row in tp}

    tasks_completed = len(done_task_ids)
    cohesion_scores = [r["cohesion_score"] for r in runs if r.get("cohesion_score") is not None]
    avg_cohesion = round(sum(cohesion_scores) / len(cohesion_scores), 2) if cohesion_scores else 0.0

    blocked_rate = round(blocked_count / tasks_total, 3) if tasks_total else 0.0

    passed = test_counts.get("pass", 0)
    failed = test_counts.get("fail", 0)
    test_pass_rate = round(passed / (passed + failed), 3) if (passed + failed) else None

    hours_saved = round(tasks_completed * (minutes_per_task / 60.0), 2)
    cost_saved = round(hours_saved * hourly_rate, 2)

    return {
        "tasks_completed": tasks_completed,
        "tasks_total": tasks_total,
        "avg_cohesion_score": avg_cohesion,
        "blocked_rate": blocked_rate,
        "blocked_count": blocked_count,
        "test_pass_rate": test_pass_rate,
        "test_executions": {"pass": passed, "fail": failed, "skipped": test_counts.get("skipped", 0)},
        "estimated_hours_saved": hours_saved,
        "estimated_cost_saved": cost_saved,
        "currency": currency,
        "assumptions": {
            "minutes_per_task_manual": minutes_per_task,
            "dev_hourly_rate": hourly_rate,
        },
        "window": {
            "since": since.isoformat() if since else None,
            "runs_in_window": len(runs),
        },
    }


def weekly(state: StateMachine, config: Dict[str, Any]) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    return compute_roi(state, config, since=since)


def monthly(state: StateMachine, config: Dict[str, Any]) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=30)
    return compute_roi(state, config, since=since)


def format_summary_line(roi: Dict[str, Any]) -> str:
    return (
        f"ROI — done:{roi['tasks_completed']}/{roi['tasks_total']} "
        f"avg_cohesion:{roi['avg_cohesion_score']} "
        f"blocked_rate:{int(roi['blocked_rate']*100)}% "
        f"hours_saved:{roi['estimated_hours_saved']} "
        f"cost_saved:{roi['currency']} {roi['estimated_cost_saved']}"
    )


def _blocked_created_between(state: StateMachine, start: datetime, end: datetime) -> int:
    with state.conn() as c:
        return c.execute(
            "SELECT COUNT(DISTINCT task_id) FROM state_transitions "
            "WHERE to_state IN ('blocked_context','blocked_dependency') AND ts >= ? AND ts < ?",
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]


def outcomes(state: StateMachine, config: Dict[str, Any], *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Plain-English outcome statements for the last 7 days, with the numbers behind them."""
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    wk = compute_roi(state, config, since=week_ago)

    with state.conn() as c:
        protected = c.execute(
            "SELECT COUNT(*) FROM artifacts WHERE kind='guardrail_block' AND ts >= ?",
            (week_ago.isoformat(),),
        ).fetchone()[0]
    blocked_this = _blocked_created_between(state, week_ago, now)
    blocked_prev = _blocked_created_between(state, week_ago - timedelta(days=7), week_ago)
    blocked_change = None
    if blocked_prev:
        blocked_change = round((blocked_this - blocked_prev) / blocked_prev * 100)

    statements = []
    if wk["tasks_completed"]:
        statements.append(
            f"Saved about {wk['estimated_hours_saved']:g} engineering hours last week "
            f"({wk['tasks_completed']} verified task{'s' if wk['tasks_completed'] != 1 else ''})."
        )
    if wk["test_pass_rate"] is not None:
        statements.append(f"{int(wk['test_pass_rate'] * 100)}% of test runs passed.")
    if blocked_change is not None and blocked_change < 0:
        statements.append(f"Blocked work down {abs(blocked_change)}% versus the week before.")
    elif blocked_this:
        statements.append(f"{blocked_this} task{'s' if blocked_this != 1 else ''} stopped and asked for input "
                          "instead of guessing.")
    if protected:
        statements.append(f"Refused {protected} write{'s' if protected != 1 else ''} to protected paths.")
    if not statements:
        statements.append("No completed runs in the last 7 days yet — run a preview to get started.")

    return {
        "statements": statements,
        "hours_saved_7d": wk["estimated_hours_saved"],
        "cost_saved_7d": wk["estimated_cost_saved"],
        "tasks_completed_7d": wk["tasks_completed"],
        "test_pass_rate_7d": wk["test_pass_rate"],
        "blocked_new_7d": blocked_this,
        "blocked_new_prev_7d": blocked_prev,
        "blocked_change_pct": blocked_change,
        "protected_writes_7d": protected,
        "currency": wk["currency"],
        "assumptions": wk["assumptions"],
    }
