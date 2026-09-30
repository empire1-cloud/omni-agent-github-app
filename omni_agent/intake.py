"""Task intake: turn GitHub issues, Jira/CSV exports, or a template into markdown tasks.

Markdown stays the single source the scanner reads. Every importer produces
`IntakeTask`s, and `write_tasks` appends them as `- [ ]` lines (with indented
`Acceptance:` sub-bullets) to a file under `memory/tasks/`. A `[ref]` marker on
each line makes re-imports idempotent.
"""
from __future__ import annotations

import csv
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class IntakeTask:
    title: str
    ref: Optional[str] = None
    priority: Optional[int] = None  # 0 (p0, most urgent) .. 4 (p4)
    task_type: Optional[str] = None
    path: Optional[str] = None
    acceptance: List[str] = field(default_factory=list)
    url: Optional[str] = None


# Words that mean the same thing as the triage keywords in config.yaml.
_TYPE_WORD = {
    "backend": "backend", "frontend": "frontend", "tests": "test",
    "docs": "documentation", "infra": "infra", "compliance": "compliance",
}
_TYPE_ALIASES = {
    "documentation": "docs", "docs": "docs", "doc": "docs", "readme": "docs",
    "test": "tests", "tests": "tests", "testing": "tests", "qa": "tests",
    "frontend": "frontend", "ui": "frontend", "ux": "frontend",
    "backend": "backend", "api": "backend", "server": "backend",
    "ci": "infra", "infra": "infra", "infrastructure": "infra", "devops": "infra",
    "compliance": "compliance", "security": "compliance",
}
_PRIORITY_WORDS = {
    "p0": 0, "blocker": 0, "critical": 0, "urgent": 0, "highest": 0,
    "p1": 1, "high": 1, "major": 1,
    "p2": 2, "medium": 2, "normal": 2,
    "p3": 3, "low": 3, "minor": 3,
    "p4": 4, "lowest": 4, "trivial": 4,
}

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "bugfix": {"type": "backend", "acceptance": [
        "The bug no longer reproduces.",
        "Existing tests still pass and a regression test covers the fix.",
    ]},
    "refactor": {"type": "backend", "acceptance": [
        "Behaviour is unchanged; existing tests pass.",
        "No files outside the task path are modified.",
    ]},
    "test": {"type": "tests", "acceptance": ["New tests run and pass under pytest."]},
    "docs": {"type": "docs", "acceptance": [
        "Documentation file is updated and contains the requested section.",
    ]},
}

_CHECKBOX_IN_BODY = re.compile(r"^\s*[-*]\s*\[\s\]\s*(.+?)\s*$", re.MULTILINE)
_PATH_IN_TEXT = re.compile(r"`([A-Za-z0-9_./-]+\.[A-Za-z]{1,5})`")


def _clean(text: str) -> str:
    """One line, no markers the scanner would mistake for a new task."""
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"\b(TODO|FIXME)\s*:", r"\1 -", text, flags=re.IGNORECASE)
    return text.replace("[ ]", "[_]")


def parse_priority(*values: Optional[str]) -> Optional[int]:
    for v in values:
        for word in re.split(r"[^a-z0-9]+", (v or "").lower()):
            if word in _PRIORITY_WORDS:
                return _PRIORITY_WORDS[word]
    return None


def parse_type(*values: Optional[str]) -> Optional[str]:
    for v in values:
        for word in re.split(r"[^a-z0-9]+", (v or "").lower()):
            if word in _TYPE_ALIASES:
                return _TYPE_ALIASES[word]
    return None


def render_task(t: IntakeTask) -> str:
    head = []
    if t.task_type:
        head.append(_TYPE_WORD.get(t.task_type, t.task_type))
    if t.priority is not None:
        head.append(f"p{t.priority}")
    title = _clean(t.title)
    if t.path and t.path not in title:
        title = f"{title} in `{t.path}`"
    line = f"- [ ] {' '.join(head) + ': ' if head else ''}{title}"
    if t.ref:
        line += f" [{t.ref}]"
    lines = [line]
    for a in t.acceptance:
        lines.append(f"  - Acceptance: {_clean(a)}")
    if t.url:
        lines.append(f"  - Source: {t.url}")
    return "\n".join(lines)


def write_tasks(repo_root: Path, tasks: Iterable[IntakeTask], target: str) -> Dict[str, Any]:
    """Append tasks to `target` (relative to repo_root), skipping refs already present."""
    path = Path(repo_root) / target
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    added, skipped = [], []
    blocks = []
    for t in tasks:
        if t.ref and f"[{t.ref}]" in existing:
            skipped.append(t.ref)
            continue
        blocks.append(render_task(t))
        added.append(t.ref or _clean(t.title))
    if blocks:
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = existing
        if not existing:
            prefix = "# Imported tasks\n\n"
        elif not existing.endswith("\n"):
            prefix += "\n"
        path.write_text(prefix + "\n".join(blocks) + "\n", encoding="utf-8")
    return {"file": target, "added": added, "skipped": skipped}


# ---------- GitHub issues ----------

def from_github_issues(items: List[Dict[str, Any]]) -> List[IntakeTask]:
    """Accepts `gh issue list --json number,title,body,labels,url` output or REST API issues."""
    tasks = []
    for it in items:
        if "pull_request" in it:
            continue  # the REST issues endpoint also returns PRs
        labels = [lb.get("name", "") if isinstance(lb, dict) else str(lb) for lb in it.get("labels") or []]
        body = it.get("body") or ""
        path_hit = _PATH_IN_TEXT.search(it.get("title", "")) or _PATH_IN_TEXT.search(body)
        tasks.append(IntakeTask(
            title=it.get("title", ""),
            ref=f"gh#{it.get('number')}",
            priority=parse_priority(*labels),
            task_type=parse_type(*labels),
            path=path_hit.group(1) if path_hit else None,
            acceptance=_CHECKBOX_IN_BODY.findall(body),
            url=it.get("url") or it.get("html_url"),
        ))
    return tasks


def fetch_github_issues(repo: str, *, token: Optional[str] = None, label: Optional[str] = None,
                        limit: int = 50) -> List[Dict[str, Any]]:
    """Read open issues with the operator's own token (or anonymously for public repos).
    Read-only; the Omni-Agent GitHub App is not involved."""
    params = {"state": "open", "per_page": str(min(limit, 100))}
    if label:
        params["labels"] = label
    url = f"https://api.github.com/repos/{repo}/issues?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))[:limit]


# ---------- CSV / Jira ----------

_CSV_COLUMNS = {
    "title": ["title", "summary", "task", "name"],
    "ref": ["id", "key", "issue key", "ref"],
    "priority": ["priority", "p"],
    "type": ["type", "task_type", "issue type", "component", "components", "labels", "label"],
    "path": ["path", "file", "target"],
    "acceptance": ["acceptance", "acceptance criteria", "acceptance_criteria", "done when"],
    "url": ["url", "link"],
}


def from_csv(path: Path, *, source: str = "csv") -> List[IntakeTask]:
    """Generic CSV and Jira CSV export. Column names are matched case-insensitively;
    repeated columns (Jira exports one `Labels` column per label) are all read."""
    with Path(path).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]

    def cols(field_name: str) -> List[int]:
        return [i for i, h in enumerate(header) if h in _CSV_COLUMNS[field_name]]

    idx = {k: cols(k) for k in _CSV_COLUMNS}
    if not idx["title"]:
        raise ValueError(f"no title column found; expected one of {_CSV_COLUMNS['title']}")

    def get(row: List[str], field_name: str) -> List[str]:
        return [row[i].strip() for i in idx[field_name] if i < len(row) and row[i].strip()]

    tasks = []
    for n, row in enumerate(rows[1:], start=2):
        title = (get(row, "title") or [""])[0]
        if not title:
            continue
        ref = (get(row, "ref") or [None])[0] or f"{source}:{n}"
        acceptance = [a for cell in get(row, "acceptance") for a in re.split(r"\s*[;|]\s*", cell) if a]
        tasks.append(IntakeTask(
            title=title,
            ref=ref,
            priority=parse_priority(*get(row, "priority")),
            task_type=parse_type(*get(row, "type"), title),
            path=(get(row, "path") or [None])[0],
            acceptance=acceptance,
            url=(get(row, "url") or [None])[0],
        ))
    return tasks


# ---------- templates ----------

def from_template(title: str, *, template: Optional[str] = None, path: Optional[str] = None,
                  priority: Optional[str] = None, task_type: Optional[str] = None,
                  acceptance: Optional[List[str]] = None) -> IntakeTask:
    tpl = TEMPLATES.get(template or "", {})
    if template and not tpl:
        raise ValueError(f"unknown template '{template}'; choose from {', '.join(TEMPLATES)}")
    return IntakeTask(
        title=title,
        priority=parse_priority(priority) if priority else None,
        task_type=parse_type(task_type) if task_type else tpl.get("type"),
        path=path,
        acceptance=list(acceptance or []) or list(tpl.get("acceptance", [])),
    )
