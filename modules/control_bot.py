"""Control-bot vs licensed customer instance detection."""

from __future__ import annotations

import os
from pathlib import Path

INSTALL_STATE = Path("install_state.json")


def is_licensed_customer_instance() -> bool:
    """True on customer VDS after install.py (must not load VDS admin cog)."""
    if os.getenv("LICENSE_KEY", "").strip():
        return True
    if INSTALL_STATE.exists():
        return True
    return False


def should_load_vds_panel() -> bool:
    """
    Load /build and /vds_manage only on the owner's control bot.
    Set ADA_CONTROL_BOT=1 in .env on your Ada bot machine.
    """
    if is_licensed_customer_instance():
        return False
    flag = os.getenv("ADA_CONTROL_BOT", "").strip().lower()
    return flag in ("1", "true", "yes", "on")
