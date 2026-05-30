"""Withdraw gate — minimum deposit volume within N days (`.set depotowd`)."""

from __future__ import annotations

import time

from modules.database import get_server_data, get_user_data, set_server_data
from modules.utils import format_balance


def get_requirement(server_data: dict | None) -> tuple[int, int]:
    """Return (min_coins, within_days). Zero = disabled."""
    if not isinstance(server_data, dict):
        return 0, 0
    try:
        amount = int(server_data.get("depotowd_min", 0) or 0)
    except (TypeError, ValueError):
        amount = 0
    try:
        days = int(server_data.get("depotowd_days", 0) or 0)
    except (TypeError, ValueError):
        days = 0
    return max(0, amount), max(0, days)


def set_requirement(guild_id: str, amount: int, days: int) -> None:
    sd = get_server_data(guild_id) or {}
    if amount <= 0 or days <= 0:
        sd.pop("depotowd_min", None)
        sd.pop("depotowd_days", None)
    else:
        sd["depotowd_min"] = int(amount)
        sd["depotowd_days"] = int(days)
    set_server_data(guild_id, sd)


def deposit_total_within_days(user_id: int | str, within_days: int) -> int:
    """Sum approved/completed deposits in the last N days."""
    days = int(within_days or 0)
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    history = get_user_data(int(user_id), "deposit_history") or {}
    if not isinstance(history, dict):
        return 0
    total = 0
    for dep in history.values():
        if not isinstance(dep, dict):
            continue
        if dep.get("status") not in ("approved", "completed"):
            continue
        try:
            ts = int(dep.get("timestamp") or dep.get("approved_at") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts < cutoff:
            continue
        try:
            amt = int(dep.get("confirmed_amount") or dep.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0
        total += max(0, amt)
    return total


def check_withdraw_deposit_requirement(
    user_id: int | str,
    server_data: dict | None,
) -> tuple[bool, str]:
    """True if user may submit a withdraw request."""
    min_amt, days = get_requirement(server_data)
    if min_amt <= 0 or days <= 0:
        return True, ""
    total = deposit_total_within_days(user_id, days)
    if total >= min_amt:
        return True, ""
    return False, (
        f"Son **{days}** gün içinde en az **{format_balance(min_amt, 'real')}** "
        f"yatırım yapman gerekiyor (mevcut: **{format_balance(total, 'real')}**)."
    )


def format_requirement_summary(server_data: dict | None) -> str:
    min_amt, days = get_requirement(server_data)
    if min_amt <= 0 or days <= 0:
        return "Withdraw için deposit şartı **kapalı**."
    return (
        f"Withdraw için son **{days}** günde minimum **{format_balance(min_amt, 'real')}** "
        f"yatırım gerekli."
    )
