"""Scanner: parses markdown files for unfinished tasks."""
from __future__ import annotations

import glob
import hashlib
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional


CHECKBOX_RE = re.compile(r"^(\s*)-\s*\[\s\]\s*(.+?)\s*$")
TODO_RE = re.compile(r"\bTODO\s*:\s*(.+?)\s*$", re.IGNORECASE)
FIXME_RE = re.compile(r"\bFIXME\s*:\s*(.+?)\s*$", re.IGNORECASE)
TAG_RE = re.compile(r"#([A-Za-z0-9_\-/]+)")
EXPLICIT_ID_RE = re.compile(r"\b(TASK-\d{3,})\b")
# indented `- Acceptance: ...` / `- Done when: ...` bullets under a checkbox task
ACCEPTANCE_RE = re.compile(r"^(\s+)[-*]\s*(?:acceptance|done when)\s*:\s*(.+?)\s*$", re.IGNORECASE)


@dataclass
class ParsedTask:
    id: str
    source_file: str
    line: int
    text: str
    raw_text: str
    pattern: str  # checkbox | todo | fixme
    tags: List[str]
    explicit_id: Optional[str]
    acceptance: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _stable_id(source_file: str, line: int, text: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    h = hashlib.sha1(f"{source_file}:{line}:{text}".encode("utf-8")).hexdigest()[:8]
    return f"TASK-{h}"


def _parse_line(line_no: int, raw: str, source_file: str) -> Optional[ParsedTask]:
    line = raw.rstrip("\n")
    pattern: Optional[str] = None
    text: Optional[str] = None

    m = CHECKBOX_RE.match(line)
    if m:
        pattern = "checkbox"
        text = m.group(2).strip()
    if not text:
        m = TODO_RE.search(line)
        if m:
            pattern = "todo"
            text = m.group(1).strip()
    if not text:
        m = FIXME_RE.search(line)
        if m:
            pattern = "fixme"
            text = m.group(1).strip()

    if not text or not pattern:
        return None

    # Skip already done checkboxes (shouldn't match anyway, but defensive)
    if pattern == "checkbox" and re.match(r"^\s*-\s*\[x\]", line, re.IGNORECASE):
        return None

    tags = TAG_RE.findall(text)
    explicit_id_match = EXPLICIT_ID_RE.search(text)
    explicit_id = explicit_id_match.group(1) if explicit_id_match else None

    tid = _stable_id(source_file, line_no, text, explicit_id)
    return ParsedTask(
        id=tid,
        source_file=source_file,
        line=line_no,
        text=text,
        raw_text=line,
        pattern=pattern,
        tags=tags,
        explicit_id=explicit_id,
    )


def scan_text(content: str, source_file: str = "<memory>") -> List[ParsedTask]:
    tasks: List[ParsedTask] = []
    parent: Optional[ParsedTask] = None
    parent_indent = 0
    for i, raw in enumerate(content.splitlines(), start=1):
        m = ACCEPTANCE_RE.match(raw)
        if parent and m and len(m.group(1)) > parent_indent:
            parent.acceptance.append(m.group(2))
            continue
        t = _parse_line(i, raw, source_file)
        if t:
            tasks.append(t)
        if t and t.pattern == "checkbox":
            parent, parent_indent = t, len(raw) - len(raw.lstrip())
        elif raw.strip() and not raw[:1].isspace():
            parent = None
    return tasks


def _expand_globs(repo_root: Path, globs: List[str]) -> List[Path]:
    files: List[Path] = []
    seen = set()
    for g in globs:
        for hit in glob.glob(str(repo_root / g), recursive=True):
            p = Path(hit)
            if p.is_file() and p.suffix.lower() == ".md" and p not in seen:
                seen.add(p)
                files.append(p)
    return files


def scan_repo(repo_root: Path, globs: List[str]) -> List[ParsedTask]:
    all_tasks: List[ParsedTask] = []
    for path in _expand_globs(repo_root, globs):
        rel = str(path.relative_to(repo_root))
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        all_tasks.extend(scan_text(content, source_file=rel))
    return all_tasks
