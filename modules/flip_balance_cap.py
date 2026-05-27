"""Balance-cap rig checks for legacy games (db balance). No payout trimming."""

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
    ceiling = cap.get_balance_ceiling(user_id, "real")
    return float(ceiling) if ceiling is not None else None


async def max_win_exceeds_cap(
    user_id: int | str,
    balance_after_bet: int,
    max_net_win: int,
) -> bool:
    """True if crediting max_net_win would pass the user's balance cap."""
    return cap.should_force_cap_loss(
        user_id, "real", int(balance_after_bet), int(max_net_win),
    )


async def should_rig_outcome(
    user_id: int | str,
    game_id: str,
    bet: float,
    *,
    pvp: bool = False,
    payout: float | None = None,
    gross: float | None = None,
    force_cap_rig: bool = False,
) -> bool:
    """
    True => force a natural loss before showing the outcome.
    Cap overflow => 100% rig; else server rigged_chance roll.
    """
    if force_cap_rig:
        return True
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
