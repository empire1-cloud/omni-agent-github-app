"""Where the append-only JSONL audit files live, and whether they survive.

Serverless hosts (Vercel) give a read-only filesystem except `/tmp`, and
`/tmp` is wiped between invocations. Writing next to the code there raises,
and writing to `/tmp` silently loses records. So:

- `OMNI_AGENT_DATA_DIR` wins when set (point it at a mounted volume).
- On Vercel, fall back to `/tmp/omni-agent-data` so writes don't crash the
  request — but report the store as non-durable.
- Elsewhere (Render, Docker, local), keep the original `backend/data/`.

`event_storage_durable()` is surfaced on /api/health so "installs aren't
being recorded" is visible instead of discovered later. Mongo (MONGO_URL)
is the durable store on serverless hosts.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def on_serverless() -> bool:
    return bool(os.environ.get("VERCEL"))


def data_dir() -> Path:
    explicit = os.environ.get("OMNI_AGENT_DATA_DIR")
    if explicit:
        return Path(explicit)
    if on_serverless():
        return Path("/tmp/omni-agent-data")
    return DEFAULT_DATA_DIR


def event_storage_durable() -> bool:
    """True when recorded events outlive the current process."""
    if os.environ.get("MONGO_URL"):
        return True
    return bool(os.environ.get("OMNI_AGENT_DATA_DIR")) or not on_serverless()


def append_jsonl(path: Path, record: Dict[str, Any]) -> bool:
    """Append one record; never raise into a webhook handler. Returns False
    (and logs loudly) when the write failed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return True
    except OSError:
        logger.exception("Could not write %s; event %s was not recorded to disk",
                         path, record.get("event"))
        return False
