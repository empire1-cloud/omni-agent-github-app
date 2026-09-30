"""First-run setup: find the repo, pick safe write roots, seed example tasks.

`omni-agent init` calls `init_workspace`, which writes `.omni-agent/config.yaml`
and `memory/tasks/inbox.md` into the target repository. Nothing outside those
two files is touched, and an existing config is never overwritten unless asked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from omni_agent.orchestrator import PACKAGED_CONFIG, WORKSPACE_CONFIG


# Directories that commonly hold code or docs a maintenance task may edit.
# Only the ones that exist in the target repo become write roots.
CANDIDATE_ROOTS = [
    "src", "app", "lib", "pkg", "packages", "internal", "cmd",
    "tests", "test", "spec", "__tests__",
    "docs",
    "frontend/src", "backend/app", "backend/tests",
]

TEST_DIR_CANDIDATES = ["tests", "test", "backend/tests", "spec"]

# Always protected, whatever the repo looks like. Leading `**/` also matches the repo root.
DEFAULT_FORBIDDEN = [
    "**/.env",
    "**/.env.*",
    "**/*.pem",
    "**/*.key",
    "**/secrets/**",
    "**/keys/**",
    "**/credentials*",
    ".git/**",
    ".github/workflows/**",
    ".omni-agent/**",
]

EXAMPLE_TASKS = """# Inbox

Omni-Agent picks up every unchecked `- [ ]` line below. Put a file path in the
task so it knows exactly where to work, and add p0-p4 to set priority.

## Examples (safe to delete)

- [ ] documentation p2: Add a "Getting started" section to `docs/GETTING_STARTED.md` explaining how to run the project locally.
  - Acceptance: Documentation file is updated and contains the requested section.
- [ ] documentation p3: Add a "Contributing" section to `docs/CONTRIBUTING.md` listing how to run the tests.
- [ ] TODO p4: Integrate with payment provider TBD (this one is meant to be blocked: it lacks context).
"""


@dataclass
class InitResult:
    repo_root: Path
    config_path: Path
    inbox_path: Path
    allowed_paths: List[str]
    forbidden_paths: List[str]
    test_path: Optional[str]
    created: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)


def detect_repo_root(start: Optional[Path] = None) -> Path:
    """Nearest ancestor holding `.git` or an Omni-Agent config; falls back to `start`."""
    start = Path(start or Path.cwd()).resolve()
    for d in (start, *start.parents):
        if (d / ".git").exists() or (d / WORKSPACE_CONFIG).is_file():
            return d
    return start


def detect_allowed_roots(repo_root: Path) -> List[str]:
    roots = [f"{c}/**" for c in CANDIDATE_ROOTS if (repo_root / c).is_dir()]
    # a docs task needs somewhere to land even in a repo with no docs/ yet
    if "docs/**" not in roots:
        roots.append("docs/**")
    if not any(t in roots for t in ("tests/**", "test/**", "spec/**", "backend/tests/**")):
        roots.append("tests/**")
    roots.append("memory/tasks/**")
    return roots


def detect_test_path(repo_root: Path) -> Optional[str]:
    for c in TEST_DIR_CANDIDATES:
        if (repo_root / c).is_dir():
            return c
    return None


def build_config(repo_root: Path, *, allowed: List[str], test_path: Optional[str],
                 persona_mode: str = "hybrid") -> Dict[str, Any]:
    base = yaml.safe_load(PACKAGED_CONFIG.read_text(encoding="utf-8")) or {}
    base.pop("repo_root", None)
    base["persona_mode"] = persona_mode
    base["workspace"] = {"name": repo_root.name}
    base.setdefault("llm", {})["env_files"] = [".env"]
    base["safety"] = {
        "dry_run_by_default": True,
        "confirm_before_write": True,
        "require_approval": False,
    }
    base["guardrails"] = {"allowed_paths": allowed, "forbidden_paths": list(DEFAULT_FORBIDDEN)}
    base["state"] = {
        "db_path": ".omni-agent/state/omni.db",
        "json_export_path": ".omni-agent/state/tasks.json",
    }
    base["reports"] = {
        "latest_path": ".omni-agent/reports/latest.md",
        "state_snapshot_path": ".omni-agent/reports/state_snapshot.json",
        "dashboard_path": ".omni-agent/reports/dashboard.html",
    }
    base["evaluation"] = {"test_path": test_path or "tests"}
    base["scan"]["globs"] = ["memory/tasks/**/*.md"]
    # PR posting stays off until the operator fills in owner/repo/installation
    gh = base.get("github", {}) or {}
    gh.update({"auto_post_pr": False, "auto_create_pr": False, "post_check_runs": False,
               "include_check_runs": False, "owner": None, "repo": None, "installation_id": None})
    base["github"] = gh
    return base


def init_workspace(
    repo_root: Path,
    *,
    allowed: Optional[List[str]] = None,
    persona_mode: str = "hybrid",
    with_examples: bool = True,
    force: bool = False,
) -> InitResult:
    repo_root = Path(repo_root).resolve()
    allowed = allowed or detect_allowed_roots(repo_root)
    test_path = detect_test_path(repo_root)
    config_path = repo_root / WORKSPACE_CONFIG
    inbox_path = repo_root / "memory" / "tasks" / "inbox.md"
    result = InitResult(repo_root, config_path, inbox_path, allowed,
                        list(DEFAULT_FORBIDDEN), test_path)

    rel_cfg = str(WORKSPACE_CONFIG)
    if config_path.exists() and not force:
        result.kept.append(rel_cfg)
    else:
        cfg = build_config(repo_root, allowed=allowed, test_path=test_path, persona_mode=persona_mode)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        header = (
            "# Omni-Agent workspace config (generated by `omni-agent init`).\n"
            "# Writes are only ever made under guardrails.allowed_paths, never under forbidden_paths.\n"
        )
        config_path.write_text(header + yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        # runtime state and reports stay out of version control
        (config_path.parent / ".gitignore").write_text("state/\nreports/\n", encoding="utf-8")
        result.created.append(rel_cfg)

    rel_inbox = "memory/tasks/inbox.md"
    if inbox_path.exists():
        result.kept.append(rel_inbox)
    elif with_examples:
        inbox_path.parent.mkdir(parents=True, exist_ok=True)
        inbox_path.write_text(EXAMPLE_TASKS, encoding="utf-8")
        result.created.append(rel_inbox)
    return result
