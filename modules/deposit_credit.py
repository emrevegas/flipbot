"""Shared post-deposit helpers (bonus activation after credit)."""

from __future__ import annotations

import modules.bonus as bonus_engine


def apply_pending_deposit_bonus(
    user_id: int,
    deposit_amount: int,
    *,
    consume: bool = False,
) -> tuple[bool, int]:
    """
    Apply the user's pending bonus selection to a credited deposit.
    consume=True clears the selection (crypto one-shot); False keeps it (in-game).
    Returns (bonus_applied_ok, bonus_amount_credited).
    """
    if consume:
        bonus_id = bonus_engine.pop_pending_deposit_bonus(user_id)
    else:
        bonus_id = bonus_engine.get_pending_deposit_bonus(user_id)
    if not bonus_id:
        return True, 0

    ok, err, bonus_amt = bonus_engine.activate_bonus(user_id, bonus_id, int(deposit_amount))
    if not ok:
        print(f"[deposit_credit] Bonus apply failed for {user_id}: {err}")
        return False, 0
    return True, bonus_amt
