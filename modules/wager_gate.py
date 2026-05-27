"""Deposit multiplier wager gate (withdraw / case battle)."""

from __future__ import annotations

from modules.database import get_user_data


def resolve_withdraw_cycle_stats(user_id: int | str, stats: dict | None = None) -> dict:
    """Return stats with withdraw-cycle fields populated (legacy backfill)."""
    stats = dict(stats or get_user_data(int(user_id), "stats") or {})
    if int(stats.get("last_deposit_amount", 0) or 0) > 0:
        return stats
    total_dep = int(stats.get("total_deposit", 0) or 0)
    if total_dep <= 0:
        return stats
    stats["last_deposit_amount"] = total_dep
    stats["wagered_at_last_deposit"] = int(stats.get("wagered_at_last_deposit", 0) or 0)
    return stats


def get_deposit_wager_gate(user_id: int | str, server_data: dict) -> tuple[int, int, int]:
    """
    Server deposit-multiplier gate only (does not include bonus wager).

    Returns (required, wagered_since, remaining).
    """
    multiplier = float(server_data.get("withdraw_min_multiplier", 0) or 0)
    if multiplier <= 0:
        return 0, 0, 0

    stats = resolve_withdraw_cycle_stats(user_id)
    last_deposit = int(stats.get("last_deposit_amount", 0) or 0)
    if last_deposit <= 0:
        return 0, 0, 0

    required = int(last_deposit * multiplier)
    total_wagered = int(stats.get("total_wagered", 0) or 0)
    wagered_at = int(stats.get("wagered_at_last_deposit", 0) or 0)
    wagered_since = max(0, total_wagered - wagered_at)
    remaining = max(0, required - wagered_since)
    return required, wagered_since, remaining
