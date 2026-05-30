#!/usr/bin/env python3
"""Thin launcher — real license check lives in compiled modules/license_guard.so."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from modules.flipbot_launcher import run  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run() or 0)
