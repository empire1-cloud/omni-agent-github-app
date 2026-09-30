"""Onboarding, safe-mode, intake, evidence and dashboard flows, end to end on a temp repo."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from omni_agent import cli, intake
from omni_agent.guardrails import Guardrails
from omni_agent.orchestrator import Orchestrator, load_config
from omni_agent.reporting import dashboard, roi
from omni_agent.reporting.evidence import format_receipt, task_receipt
from omni_agent.scanner import scan_text
from omni_agent.setup_wizard import detect_repo_root, init_workspace


def _repo(tmp_path: Path, tasks: str) -> Orchestrator:
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "memory" / "tasks").mkdir(parents=True)
    (tmp_path / "memory" / "tasks" / "inbox.md").write_text(tasks, encoding="utf-8")
    init_workspace(tmp_path, persona_mode="rule")
    orc = Orchestrator(tmp_path, load_config(tmp_path))
    orc.scan()
    return orc


DOCS_TASK = "- [ ] documentation p1: Add a usage section to `docs/USAGE.md`.\n"
SECRET_TASK = "- [ ] backend p1: Remove the token from `secrets/tokens.py`.\n"


# ---------- guardrails ----------

def test_leading_double_star_also_protects_repo_root():
    g = Guardrails(Path("."), ["src/**"], ["**/.env", "**/secrets/**"])
    assert not g.check_write(".env")[0]
    assert not g.check_write("secrets/a.py")[0]
    assert not g.check_write("src/.env")[0]
    assert g.check_write("src/secrets.py")[0]


def test_traversal_cannot_borrow_an_allowed_prefix():
    g = Guardrails(Path("."), ["src/**"], ["**/.env"])
    assert not g.check_write("src/../setup.py")[0]
    assert not g.check_write("src/../.env")[0]


# ---------- setup ----------

def test_detect_repo_root_walks_up_to_git(tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert detect_repo_root(nested) == tmp_path.resolve()


def test_init_detects_roots_and_never_overwrites(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    res = init_workspace(tmp_path)
    assert res.allowed_paths == ["src/**", "tests/**", "docs/**", "memory/tasks/**"]
    cfg = load_config(tmp_path)
    assert cfg["safety"]["dry_run_by_default"] is True
    assert cfg["github"]["auto_post_pr"] is False
    assert cfg["evaluation"]["test_path"] == "tests"
    assert "**/.env" in cfg["guardrails"]["forbidden_paths"]

    (tmp_path / "memory/tasks/inbox.md").write_text("mine\n")
    again = init_workspace(tmp_path)
    assert again.created == []
    assert (tmp_path / "memory/tasks/inbox.md").read_text() == "mine\n"


# ---------- safe mode ----------

def test_preview_writes_nothing(tmp_path):
    orc = _repo(tmp_path, DOCS_TASK)
    out = orc.run_next(dry_run=True)
    assert out["final_status"] == "dry_run"
    assert out["files_changed"] == ["docs/USAGE.md"]
    assert not (tmp_path / "docs/USAGE.md").exists()
    r = task_receipt(orc.state, out["task_id"])
    assert r["what_changed"] == []
    assert [c["path"] for c in r["planned_changes"]] == ["docs/USAGE.md"]


def test_declined_confirmation_writes_nothing_and_is_recorded(tmp_path):
    orc = _repo(tmp_path, DOCS_TASK)
    seen = []
    out = orc.run_next(confirm=lambda changes: seen.append(changes) or False, approver="ana")
    assert out["final_status"] == "declined"
    assert seen and seen[0][0]["path"] == "docs/USAGE.md"
    assert not (tmp_path / "docs/USAGE.md").exists()
    assert orc.state.list_approvals(out["task_id"])[0]["decision"] == "declined"


def test_confirmed_apply_writes_and_records_approver(tmp_path):
    orc = _repo(tmp_path, DOCS_TASK)
    out = orc.run_next(confirm=lambda changes: True, approver="ana")
    assert out["final_status"] == "done"
    assert (tmp_path / "docs/USAGE.md").exists()
    r = task_receipt(orc.state, out["task_id"])
    assert [c["path"] for c in r["what_changed"]] == ["docs/USAGE.md"]
    assert r["approvals"][0]["approver"] == "ana"
    assert r["tests"]["criteria_met"] == r["tests"]["criteria_total"] > 0
    text = format_receipt(r)
    for heading in ("WHY", "WHAT CHANGED", "WHAT WAS CHECKED", "WHAT WAS BLOCKED", "WHAT WAS PROTECTED", "WHO APPROVED"):
        assert heading in text


def test_require_approval_gates_real_writes(tmp_path):
    orc = _repo(tmp_path, DOCS_TASK)
    orc.config["safety"]["require_approval"] = True
    tid = orc.state.next_runnable()["id"]
    assert orc.run_task(tid)["final_status"] == "needs_approval"
    assert orc.run_task(tid, dry_run=True)["final_status"] == "dry_run"
    orc.state.add_approval(tid, approver="lead")
    assert orc.run_task(tid)["final_status"] == "done"


def test_protected_path_blocks_instead_of_redirecting(tmp_path):
    orc = _repo(tmp_path, SECRET_TASK)
    out = orc.run_next()
    assert out["final_status"] == "blocked_context"
    assert out["files_changed"] == []
    r = task_receipt(orc.state, out["task_id"])
    assert r["protected_paths"] == ["secrets/tokens.py"]
    assert "protected" in r["blocked"]


def test_rule_mode_never_overwrites_existing_code(tmp_path):
    orc = _repo(tmp_path, "- [ ] backend p1: Create `src/app.py` with a helper.\n")
    (tmp_path / "src/app.py").write_text("KEEP = 1\n")
    out = orc.run_next()
    assert out["final_status"] == "blocked_context"
    assert (tmp_path / "src/app.py").read_text() == "KEEP = 1\n"


# ---------- intake ----------

def test_scanner_reads_acceptance_bullets():
    tasks = scan_text("- [ ] do x\n  - Acceptance: a\n  - Done when: b\n- [ ] do y\n")
    assert tasks[0].acceptance == ["a", "b"]
    assert tasks[1].acceptance == []


def test_acceptance_reaches_the_spec(tmp_path):
    orc = _repo(tmp_path, DOCS_TASK + "  - Acceptance: Mentions slugify.\n")
    out = orc.run_next(dry_run=True)
    assert out["mini_spec"]["acceptance_criteria"][0] == "Mentions slugify."


def test_github_issue_import_is_idempotent(tmp_path):
    items = [
        {"number": 7, "title": "Fix crash in `src/app.py`", "body": "- [ ] no crash\nTODO: later",
         "labels": [{"name": "bug"}, {"name": "priority: high"}], "url": "https://x/7"},
        {"number": 8, "title": "a PR", "pull_request": {}},
    ]
    tasks = intake.from_github_issues(items)
    assert len(tasks) == 1 and tasks[0].priority == 1 and tasks[0].path == "src/app.py"
    assert tasks[0].acceptance == ["no crash"]
    first = intake.write_tasks(tmp_path, tasks, "memory/tasks/imported/github.md")
    second = intake.write_tasks(tmp_path, tasks, "memory/tasks/imported/github.md")
    assert first["added"] == ["gh#7"] and second["skipped"] == ["gh#7"]
    parsed = scan_text((tmp_path / "memory/tasks/imported/github.md").read_text())
    assert len(parsed) == 1 and parsed[0].acceptance == ["no crash"]


def test_jira_csv_reads_repeated_label_columns(tmp_path):
    f = tmp_path / "jira.csv"
    f.write_text("Summary,Issue key,Priority,Labels,Labels\n"
                 "Update readme,ENG-1,Low,misc,documentation\n,ENG-2,High,,\n", encoding="utf-8")
    tasks = intake.from_csv(f, source="jira")
    assert len(tasks) == 1
    assert (tasks[0].ref, tasks[0].priority, tasks[0].task_type) == ("ENG-1", 3, "docs")
    assert intake.render_task(tasks[0]).startswith("- [ ] documentation p3: Update readme [ENG-1]")


def test_csv_without_title_column_is_rejected(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        intake.from_csv(f)


def test_template_fills_acceptance():
    t = intake.from_template("Fix login", template="bugfix", path="src/login.py", priority="p0")
    line = intake.render_task(t)
    assert line.startswith("- [ ] backend p0: Fix login in `src/login.py`")
    assert "Acceptance:" in line


# ---------- dashboard & outcomes ----------

def test_dashboard_and_outcomes(tmp_path):
    orc = _repo(tmp_path, DOCS_TASK + SECRET_TASK)
    orc.run_next(confirm=lambda c: True, approver="ana")
    orc.run_next()
    o = roi.outcomes(orc.state, orc.config)
    assert o["tasks_completed_7d"] == 1 and o["protected_writes_7d"] == 1
    assert any("Refused 1 write" in s for s in o["statements"])
    paths = dashboard.write(orc.state, orc.config, tmp_path)
    page = paths["html"].read_text()
    assert "secrets/tokens.py" in page and "ana" in page
    data = json.loads(paths["json"].read_text())
    assert data["counts"] == {"queued": 0, "blocked": 1, "done": 1, "total": 2}


# ---------- CLI ----------

def test_cli_defaults_to_preview(tmp_path, capsys):
    _repo(tmp_path, DOCS_TASK)
    assert cli.main(["--repo-root", str(tmp_path), "run-next"]) == 0
    assert "Preview only" in capsys.readouterr().out
    assert not (tmp_path / "docs/USAGE.md").exists()


def test_cli_refuses_unconfirmed_writes_without_a_tty(tmp_path):
    _repo(tmp_path, DOCS_TASK)
    script = Path(__file__).resolve().parents[2] / "scripts" / "omni_agent.py"
    res = subprocess.run([sys.executable, str(script), "--repo-root", str(tmp_path), "run-next", "--apply"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert res.returncode == 4
    assert not (tmp_path / "docs/USAGE.md").exists()


def test_cli_requires_init(tmp_path, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--repo-root", str(tmp_path), "status"])
    assert "omni-agent init" in capsys.readouterr().err
