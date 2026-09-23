#!/usr/bin/env python3
"""Run edmft_workflow directly from a git clone, without pip installation.

Usage:
    /path/to/python /path/to/edmft_workflow/workflow.py -c /path/to/config.toml <command>

The repository root is inserted into sys.path explicitly, so the command works
from any calculation directory and does not depend on the user's current cwd.
"""
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edmft_workflow.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
