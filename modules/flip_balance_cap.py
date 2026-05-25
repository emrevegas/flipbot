"""Balance cap + per-game rigged_chance (server/games JSON)."""
from __future__ import annotations

import random

from database import db
from modules.game_rig import get_rigged_chance, roll_rigged, NO_RIG_GAMES


async def get_balance_ceiling(user_id: int | str) -> float | None:
    """Return effective balance cap for user, or None if uncapped."""
    user_cap = await db.get_balance_cap(user_id)
    global_cap_str = await db.get_global_setting("global_cap", "")
    global_cap: float | None = float(global_cap_str) if global_cap_str else None

    caps = [c for c in [user_cap, global_cap] if c is not None]
    return min(caps) if caps else None


async def should_rig_outcome(
    user_id: int | str,
    game_id: str,
    bet: float,
    *,
    pvp: bool = False,
) -> bool:
    """True if this round should be forced to a natural loss before UI."""
    if pvp or game_id in NO_RIG_GAMES:
        return False

    user = await db.get_user(user_id)
    if not user:
        return False

    balance = float(user["balance"])
    ceiling = await get_balance_ceiling(user_id)

    if ceiling is not None and balance >= ceiling * 0.95:
        return True

    return roll_rigged(game_id)


async def apply_balance_cap(user_id: int | str, new_balance: float) -> float:
    """Clamp balance to ceiling if applicable."""
    ceiling = await get_balance_ceiling(user_id)
    if ceiling is not None and new_balance > ceiling:
        return ceiling
    return new_balance
