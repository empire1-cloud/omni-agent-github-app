"""Black-box guardrails: enforce allowed/forbidden paths."""
from __future__ import annotations

import fnmatch
import posixpath
from pathlib import Path
from typing import Iterable, List, Tuple


class GuardrailViolation(Exception):
    """Raised when an operation is attempted on a forbidden path."""


class Guardrails:
    def __init__(self, repo_root: Path, allowed: Iterable[str], forbidden: Iterable[str]):
        self.repo_root = Path(repo_root).resolve()
        self.allowed: List[str] = list(allowed)
        self.forbidden: List[str] = list(forbidden)

    def _normalize(self, path: str | Path) -> str:
        p = Path(path)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(self.repo_root)
            except ValueError:
                # outside repo root -> treat as raw absolute, will fail any allow pattern
                return str(p)
        # collapse `a/../b` so a traversal can't borrow an allowed prefix
        return posixpath.normpath(str(p).replace("\\", "/"))

    @staticmethod
    def _match_any(rel_path: str, patterns: List[str]) -> bool:
        for pat in patterns:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            # a leading `**/` also matches zero directories: `**/.env` covers a root `.env`
            if pat.startswith("**/") and Guardrails._match_any(rel_path, [pat[3:]]):
                return True
            # support `**` style: convert to fnmatch by also checking parents
            if "**" in pat:
                # `a/**` should also match `a/b/c`
                base = pat.split("**")[0].rstrip("/")
                if base and (rel_path == base or rel_path.startswith(base + "/")):
                    # but only if the rest matches the suffix
                    suffix = pat.split("**", 1)[1].lstrip("/")
                    if not suffix:
                        return True
                    tail = rel_path[len(base) + 1 :] if base else rel_path
                    if fnmatch.fnmatch(tail, suffix) or fnmatch.fnmatch(rel_path, pat.replace("**", "*")):
                        return True
        return False

    def is_forbidden(self, path: str | Path) -> bool:
        rel = self._normalize(path)
        return self._match_any(rel, self.forbidden)

    def is_allowed(self, path: str | Path) -> bool:
        rel = self._normalize(path)
        if self._match_any(rel, self.forbidden):
            return False
        return self._match_any(rel, self.allowed)

    def check_write(self, path: str | Path) -> Tuple[bool, str]:
        rel = self._normalize(path)
        if self._match_any(rel, self.forbidden):
            return False, f"path '{rel}' is forbidden"
        if not self._match_any(rel, self.allowed):
            return False, f"path '{rel}' is not in allowed paths"
        return True, "ok"

    def assert_write(self, path: str | Path) -> None:
        ok, msg = self.check_write(path)
        if not ok:
            raise GuardrailViolation(msg)

    def filter_allowed(self, paths: Iterable[str | Path]) -> List[str]:
        return [self._normalize(p) for p in paths if self.is_allowed(p)]
