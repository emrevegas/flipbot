"""Async balance-cap helpers for legacy games (db balance). Uses modules.balance_cap."""

from __future__ import annotations

from database import db
import modules.balance_cap as cap
from modules.game_rig import NO_RIG_GAMES


async def _balance(user_id: int | str) -> int:
    user = await db.get_user(user_id)
    if not user:
        return 0
    return int(float(user.get("balance", 0)))


async def _house_edge(game_id: str) -> float:
    cfg = await db.get_game_config(game_id)
    if cfg and cfg.get("house_edge") is not None:
        return float(cfg["house_edge"])
    return 0.02


def _net_from_gross(gross: float, house_edge: float) -> int:
    g = float(gross)
    if g <= 0:
        return 0
    return int(g * (1.0 - house_edge))


async def get_balance_ceiling(user_id: int | str) -> float | None:
    """Effective cap from panel (global / per-user / welcome / promo / bonus)."""
    ceiling = cap.get_balance_ceiling(user_id, "real")
    return float(ceiling) if ceiling is not None else None


async def should_rig_outcome(
    user_id: int | str,
    game_id: str,
    bet: float,
    *,
    pvp: bool = False,
    payout: float | None = None,
    gross: float | None = None,
) -> bool:
    """True if the round should be a natural loss before showing the outcome."""
    if pvp or game_id in NO_RIG_GAMES:
        return False

    bal = await _balance(user_id)
    b = int(bet)

    if payout is not None:
        p = int(payout)
    elif gross is not None:
        he = await _house_edge(game_id)
        p = _net_from_gross(gross, he)
    else:
        p = 0

    return cap.should_rig_outcome(user_id, "real", bal, b, p, game_id=game_id)


async def apply_balance_cap(
    user_id: int | str,
    new_balance: float,
    *,
    game_id: str = "",
) -> float:
    """Target balance after a win; returns allowed balance (0 net win if capped)."""
    current = await _balance(user_id)
    target = float(new_balance)
    add = int(max(0, round(target - current)))
    if add <= 0:
        return target

    allowed_add = cap.cap_game_payout(
        user_id, "real", current, 0, add, game_id=game_id or "unknown",
    )
    return float(current + allowed_add)
