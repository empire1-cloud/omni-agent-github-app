#!/usr/bin/env python3
"""Omni-Agent CLI entrypoint (checkout form of the installed `omni-agent` command)."""
from __future__ import annotations

import sys
from pathlib import Path

# Make the checkout importable regardless of cwd
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from omni_agent.cli import build_parser, main  # noqa: E402,F401


if __name__ == "__main__":
    sys.exit(main(default_root=ROOT))
