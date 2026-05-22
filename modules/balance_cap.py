"""Balance cap / ceiling logic.

A user's effective balance ceiling is the minimum of:
  1. A per-user admin-set cap (balance_caps table)
  2. The global cap (global_settings key 'global_cap')
  3. Infinity (no cap)

The rigging logic uses game-level rigged_chance from the games table.
"""
from __future__ import annotations

import random
from database import db


async def get_balance_ceiling(user_id: int | str) -> float | None:
    """Return effective balance cap for user, or None if uncapped."""
    user_cap = await db.get_balance_cap(user_id)
    global_cap_str = await db.get_global_setting("global_cap", "")
    global_cap: float | None = float(global_cap_str) if global_cap_str else None

    caps = [c for c in [user_cap, global_cap] if c is not None]
    return min(caps) if caps else None


async def should_rig_outcome(user_id: int | str, game_id: str, bet: float) -> bool:
    """Return True if this round should be forced to a loss (rigged).

    Rigging activates when:
    - The user is near their balance ceiling, OR
    - A random roll hits the game's configured rigged_chance
    """
    user = await db.get_user(user_id)
    if not user:
        return False

    balance = float(user["balance"])
    ceiling = await get_balance_ceiling(user_id)

    # near-ceiling rigging: force loss if payout would exceed cap
    if ceiling is not None and balance >= ceiling * 0.95:
        return True

    game = await db.get_game_config(game_id)
    if not game:
        return False

    rigged_chance = float(game.get("rigged_chance", 0.05))
    return random.random() < rigged_chance


async def apply_balance_cap(user_id: int | str, new_balance: float) -> float:
    """Clamp balance to ceiling if applicable. Returns clamped value."""
    ceiling = await get_balance_ceiling(user_id)
    if ceiling is not None and new_balance > ceiling:
        return ceiling
    return new_balance
