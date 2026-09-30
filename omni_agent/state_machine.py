"""SQLite-backed state machine and persistence layer for omni-agent."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


VALID_STATES = {
    "not_started",
    "analyzing",
    "building",
    "evaluating",
    "done",
    "blocked_context",
    "blocked_dependency",
    "evaluating_failed",
}

# allowed transitions
TRANSITIONS: Dict[str, set] = {
    "not_started": {"analyzing", "blocked_context", "blocked_dependency"},
    "analyzing": {"building", "blocked_context", "blocked_dependency"},
    "building": {"evaluating", "blocked_context", "blocked_dependency"},
    "evaluating": {"done", "evaluating_failed", "blocked_context", "blocked_dependency"},
    "evaluating_failed": {"analyzing", "building", "blocked_context", "blocked_dependency"},
    "blocked_context": {"analyzing", "building", "evaluating"},
    "blocked_dependency": {"analyzing", "building", "evaluating"},
    "done": set(),
}


class StateError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  source_file TEXT NOT NULL,
  source_line INTEGER,
  raw_text TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  universe TEXT,
  layer TEXT,
  task_type TEXT,
  priority INTEGER DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'not_started',
  acceptance_criteria TEXT,
  assumptions TEXT,
  blocked_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_universe_layer ON tasks(universe, layer);

CREATE TABLE IF NOT EXISTS task_runs (
  run_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  mode TEXT NOT NULL,
  final_status TEXT,
  cohesion_score REAL,
  summary TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_task_id ON task_runs(task_id);

CREATE TABLE IF NOT EXISTS state_transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  task_id TEXT NOT NULL,
  from_state TEXT NOT NULL,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT,
  ts TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_transitions_task_id ON state_transitions(task_id);

CREATE TABLE IF NOT EXISTS persona_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  persona TEXT NOT NULL,
  mode_used TEXT NOT NULL,
  output_json TEXT NOT NULL,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT,
  metadata_json TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS test_executions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  command TEXT NOT NULL,
  exit_code INTEGER,
  status TEXT NOT NULL,
  output_excerpt TEXT,
  ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  run_id TEXT,
  approver TEXT NOT NULL,
  decision TEXT NOT NULL,
  scope TEXT,
  note TEXT,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_task_id ON approvals(task_id);

CREATE TABLE IF NOT EXISTS run_config_snapshots (
  run_id TEXT PRIMARY KEY,
  config_json TEXT NOT NULL,
  ts TEXT NOT NULL
);
"""


@dataclass
class TaskRecord:
    id: str
    source_file: str
    source_line: int
    raw_text: str
    normalized_text: str
    task_type: Optional[str]
    priority: int
    status: str
    acceptance_criteria: Optional[str]
    assumptions: Optional[str]
    blocked_reason: Optional[str]
    created_at: str
    updated_at: str
    closed_at: Optional[str]
    universe: Optional[str] = None
    layer: Optional[str] = None


class StateMachine:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def conn(self):
        c = sqlite3.connect(str(self.db_path))
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def _init_db(self) -> None:
        with self.conn() as c:
            c.executescript(SCHEMA)

    # ---------- task upsert ----------
    def upsert_task(
        self,
        *,
        id: str,
        source_file: str,
        source_line: int,
        raw_text: str,
        normalized_text: str,
        task_type: Optional[str] = None,
        priority: int = 3,
        acceptance_criteria: Optional[List[str]] = None,
    ) -> bool:
        ts = now_iso()
        acceptance_json = json.dumps(acceptance_criteria) if acceptance_criteria else None
        with self.conn() as c:
            row = c.execute("SELECT id, status FROM tasks WHERE id = ?", (id,)).fetchone()
            if row:
                # Don't reset status on rescan; only refresh metadata
                c.execute(
                    """UPDATE tasks
                       SET source_file=?, source_line=?, raw_text=?, normalized_text=?,
                           task_type=COALESCE(?, task_type), priority=?, acceptance_criteria=?,
                           updated_at=?
                       WHERE id=?""",
                    (source_file, source_line, raw_text, normalized_text, task_type, priority,
                     acceptance_json, ts, id),
                )
                return False
            c.execute(
                """INSERT INTO tasks
                   (id, source_file, source_line, raw_text, normalized_text, task_type,
                    priority, acceptance_criteria, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'not_started', ?, ?)""",
                (id, source_file, source_line, raw_text, normalized_text, task_type, priority,
                 acceptance_json, ts, ts),
            )
            return True

    def update_triage(self, id: str, *, task_type: str, priority: int) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE tasks SET task_type=?, priority=?, updated_at=? WHERE id=?",
                (task_type, priority, now_iso(), id),
            )

    # ---------- query ----------
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def list_tasks(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM tasks"
        args: tuple = ()
        if status:
            q += " WHERE status = ?"
            args = (status,)
        q += " ORDER BY priority ASC, updated_at ASC"
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def next_runnable(self) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                """SELECT * FROM tasks
                   WHERE status IN ('not_started','analyzing','building','evaluating',
                                    'evaluating_failed','blocked_context','blocked_dependency')
                   ORDER BY
                     CASE status
                       WHEN 'not_started' THEN 0
                       WHEN 'evaluating_failed' THEN 1
                       WHEN 'analyzing' THEN 2
                       WHEN 'building' THEN 3
                       WHEN 'evaluating' THEN 4
                       ELSE 9
                     END,
                     priority ASC,
                     updated_at ASC
                   LIMIT 1"""
            ).fetchone()
            return dict(row) if row else None

    # ---------- state transition ----------
    def transition(
        self,
        task_id: str,
        to_state: str,
        *,
        actor: str,
        reason: Optional[str] = None,
        run_id: Optional[str] = None,
        force: bool = False,
    ) -> None:
        if to_state not in VALID_STATES:
            raise StateError(f"invalid target state '{to_state}'")
        with self.conn() as c:
            row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise StateError(f"task {task_id} not found")
            from_state = row["status"]
            if not force and to_state not in TRANSITIONS.get(from_state, set()) and from_state != to_state:
                raise StateError(f"illegal transition {from_state} -> {to_state}")
            ts = now_iso()
            closed_at = ts if to_state == "done" else None
            c.execute(
                """UPDATE tasks SET status=?, updated_at=?, closed_at=COALESCE(?, closed_at),
                                    blocked_reason=CASE WHEN ? LIKE 'blocked_%' THEN ? ELSE NULL END
                   WHERE id=?""",
                (to_state, ts, closed_at, to_state, reason, task_id),
            )
            c.execute(
                """INSERT INTO state_transitions
                   (run_id, task_id, from_state, to_state, actor, reason, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, task_id, from_state, to_state, actor, reason, ts),
            )

    # ---------- runs ----------
    def start_run(self, task_id: str, mode: str, config_snapshot: dict) -> str:
        run_id = uuid.uuid4().hex
        ts = now_iso()
        with self.conn() as c:
            c.execute(
                "INSERT INTO task_runs (run_id, task_id, started_at, mode) VALUES (?,?,?,?)",
                (run_id, task_id, ts, mode),
            )
            c.execute(
                "INSERT INTO run_config_snapshots (run_id, config_json, ts) VALUES (?,?,?)",
                (run_id, json.dumps(config_snapshot, default=str), ts),
            )
        return run_id

    def end_run(self, run_id: str, *, final_status: str, cohesion_score: Optional[float], summary: str) -> None:
        with self.conn() as c:
            c.execute(
                "UPDATE task_runs SET ended_at=?, final_status=?, cohesion_score=?, summary=? WHERE run_id=?",
                (now_iso(), final_status, cohesion_score, summary, run_id),
            )

    def add_persona_output(self, run_id: str, task_id: str, persona: str, mode_used: str, output: dict) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO persona_outputs (run_id, task_id, persona, mode_used, output_json, ts)
                   VALUES (?,?,?,?,?,?)""",
                (run_id, task_id, persona, mode_used, json.dumps(output, default=str), now_iso()),
            )

    def add_artifact(self, run_id: str, task_id: str, kind: str, path: Optional[str], metadata: dict) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO artifacts (run_id, task_id, kind, path, metadata_json, ts)
                   VALUES (?,?,?,?,?,?)""",
                (run_id, task_id, kind, path, json.dumps(metadata, default=str), now_iso()),
            )

    def add_test_execution(
        self, run_id: str, task_id: str, *, command: str, exit_code: int, status: str, output_excerpt: str
    ) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO test_executions
                   (run_id, task_id, command, exit_code, status, output_excerpt, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (run_id, task_id, command, exit_code, status, output_excerpt, now_iso()),
            )

    def latest_run_for_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                "SELECT * FROM task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None

    # ---------- approvals ----------
    def add_approval(
        self,
        task_id: str,
        *,
        approver: str,
        decision: str = "approved",
        scope: Optional[str] = None,
        note: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT INTO approvals (task_id, run_id, approver, decision, scope, note, ts)
                   VALUES (?,?,?,?,?,?,?)""",
                (task_id, run_id, approver, decision, scope, note, now_iso()),
            )

    def list_approvals(self, task_id: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM approvals"
        args: tuple = ()
        if task_id:
            q += " WHERE task_id = ?"
            args = (task_id,)
        q += " ORDER BY ts ASC"
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def is_approved(self, task_id: str) -> bool:
        """True when the most recent decision for the task is an approval."""
        with self.conn() as c:
            row = c.execute(
                "SELECT decision FROM approvals WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return bool(row and row["decision"] == "approved")

    # ---------- evidence queries ----------
    def list_runs(self, task_id: str) -> List[Dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM task_runs WHERE task_id=? ORDER BY started_at ASC", (task_id,)
            ).fetchall()]

    def list_transitions(self, task_id: str) -> List[Dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM state_transitions WHERE task_id=? ORDER BY id ASC", (task_id,)
            ).fetchall()]

    def list_artifacts(self, *, task_id: Optional[str] = None, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses, args = [], []
        if task_id:
            clauses.append("task_id=?")
            args.append(task_id)
        if run_id:
            clauses.append("run_id=?")
            args.append(run_id)
        q = "SELECT * FROM artifacts"
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY id ASC"
        with self.conn() as c:
            rows = [dict(r) for r in c.execute(q, tuple(args)).fetchall()]
        for r in rows:
            r["metadata"] = json.loads(r.pop("metadata_json") or "{}")
        return rows

    def persona_output(self, run_id: str, persona: str) -> Optional[Dict[str, Any]]:
        with self.conn() as c:
            row = c.execute(
                "SELECT output_json FROM persona_outputs WHERE run_id=? AND persona=? ORDER BY id DESC LIMIT 1",
                (run_id, persona),
            ).fetchone()
        return json.loads(row["output_json"]) if row else None

    def list_test_executions(self, run_id: str) -> List[Dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM test_executions WHERE run_id=? ORDER BY id ASC", (run_id,)
            ).fetchall()]

    # ---------- export ----------
    def export_json(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            tasks = [dict(r) for r in c.execute("SELECT * FROM tasks").fetchall()]
            runs = [dict(r) for r in c.execute(
                "SELECT * FROM task_runs ORDER BY started_at DESC LIMIT 200"
            ).fetchall()]
        payload = {"exported_at": now_iso(), "tasks": tasks, "recent_runs": runs}
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
