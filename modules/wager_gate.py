"""
Withdraw wager requirement: last_deposit × withdraw_min_multiplier.

Each confirmed deposit starts a new cycle:
  - last_deposit_amount = that deposit (coins)
  - wager_since_deposit = 0

Every real bet calls record_wager() once → wager_since_deposit increases.
Required = last_deposit_amount × multiplier (from server settings).
"""

from __future__ import annotations

from modules.database import check_permission, get_user_data, set_user_data


def _stats(user_id: int | str) -> dict:
    return dict(get_user_data(int(user_id), "stats") or {})


def _save_stats(user_id: int | str, stats: dict) -> None:
    set_user_data(int(user_id), "stats", stats)


def start_deposit_cycle(user_id: int | str, deposit_coins: int) -> None:
    """New deposit credited — reset cycle to this deposit only."""
    deposit_coins = int(deposit_coins)
    if deposit_coins <= 0:
        return
    stats = _stats(user_id)
    stats["last_deposit_amount"] = deposit_coins
    stats["wager_since_deposit"] = 0
    stats["wagered_at_last_deposit"] = 0
    _save_stats(user_id, stats)


def record_wager(user_id: int | str, amount: int | float) -> None:
    """Count bet toward the current deposit wager cycle."""
    amount = int(amount)
    if amount <= 0:
        return
    stats = _stats(user_id)
    if int(stats.get("last_deposit_amount", 0) or 0) <= 0:
        return
    stats["wager_since_deposit"] = int(stats.get("wager_since_deposit", 0) or 0) + amount
    _save_stats(user_id, stats)


def clear_deposit_wager_cycle(user_id: int | str) -> None:
    """After withdraw — no active deposit cycle until next deposit."""
    stats = _stats(user_id)
    stats["last_deposit_amount"] = 0
    stats["wager_since_deposit"] = 0
    stats["wagered_at_last_deposit"] = 0
    _save_stats(user_id, stats)


def get_multiplier(server_data: dict | None) -> float:
    if not server_data:
        return 0.0
    try:
        return float(server_data.get("withdraw_min_multiplier", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def get_withdraw_wager_status(
    user_id: int | str,
    server_data: dict | None,
) -> tuple[int, int, int]:
    """
    Returns (required, wagered, remaining) in coins.
    Staff/admin bypass (no requirement).
    """
    if not check_permission(int(user_id), "admin"):
        return 0, 0, 0

    mult = get_multiplier(server_data)
    if mult <= 0:
        return 0, 0, 0

    stats = _stats(user_id)
    last_deposit = int(stats.get("last_deposit_amount", 0) or 0)
    if last_deposit <= 0:
        return 0, 0, 0

    required = int(last_deposit * mult)
    wagered = int(stats.get("wager_since_deposit", 0) or 0)
    remaining = max(0, required - wagered)
    return required, wagered, remaining


def is_withdraw_wager_met(user_id: int | str, server_data: dict | None) -> bool:
    _, _, remaining = get_withdraw_wager_status(user_id, server_data)
    return remaining <= 0


# Backwards-compatible names
get_deposit_wager_gate = get_withdraw_wager_status
