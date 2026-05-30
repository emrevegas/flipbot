"""Control-bot vs licensed customer instance detection."""

from __future__ import annotations

import os
from pathlib import Path

INSTALL_STATE = Path("install_state.json")


def is_licensed_customer_instance() -> bool:
    """True on customer VDS after install.py (must not load VDS admin cog)."""
    if is_ada_standalone_bot():
        return False
    if os.getenv("LICENSE_KEY", "").strip():
        return True
    if INSTALL_STATE.exists():
        return True
    return False


def is_ada_standalone_bot() -> bool:
    """True when running ada_bot.py (separate control bot process)."""
    return os.getenv("ADA_STANDALONE_BOT", "").strip().lower() in ("1", "true", "yes", "on")


def should_load_vds_panel() -> bool:
    """
    Deprecated for casino bot — use ada_bot.py instead.
    Kept for backwards compatibility if ADA_CONTROL_BOT=1 on dev machine without install_state.
    """
    if is_licensed_customer_instance():
        return False
    flag = os.getenv("ADA_CONTROL_BOT", "").strip().lower()
    return flag in ("1", "true", "yes", "on")
