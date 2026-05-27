"""Deposit multiplier wager gate (withdraw / case battle)."""

from __future__ import annotations

from modules.database import get_user_data


def get_effective_total_wagered(user_id: int | str, stats: dict | None = None) -> int:
    """Combine panel.db stats and flipbot.db users.total_wagered."""
    stats = stats or get_user_data(int(user_id), "stats") or {}
    panel_wagered = int(stats.get("total_wagered", 0) or 0)
    try:
        from modules.rakeback_roles import get_flip_total_wagered

        flip_wagered = int(get_flip_total_wagered(user_id))
    except Exception:
        flip_wagered = 0
    return max(panel_wagered, flip_wagered)


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


def clear_deposit_wager_cycle(user_id: int | str, stats: dict | None = None) -> dict:
    """Clear deposit-multiplier withdraw gate (does not remove bonus)."""
    from modules.database import set_user_data

    stats = dict(stats or get_user_data(int(user_id), "stats") or {})
    stats["last_deposit_amount"] = 0
    stats["wagered_at_last_deposit"] = 0
    set_user_data(int(user_id), "stats", stats)
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
    total_wagered = get_effective_total_wagered(user_id, stats)
    wagered_at = int(stats.get("wagered_at_last_deposit", 0) or 0)
    wagered_since = max(0, total_wagered - wagered_at)
    remaining = max(0, required - wagered_since)
    return required, wagered_since, remaining
