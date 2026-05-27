"""Shared post-deposit helpers (bonus activation after credit)."""

from __future__ import annotations

import modules.bonus as bonus_engine


def _bonus_template_name(bonus_id: str | None) -> str | None:
    if not bonus_id:
        return None
    template = bonus_engine.get_bonus_templates().get(bonus_id) or {}
    return template.get("name", bonus_id)


def apply_pending_deposit_bonus(
    user_id: int,
    deposit_amount: int,
    *,
    consume: bool = False,
) -> tuple[bool, int, str | None, str | None]:
    """
    Apply the user's pending bonus selection to a credited deposit.
    consume=True clears the selection (crypto one-shot); False keeps it (in-game).
    Returns (bonus_applied_ok, bonus_amount_credited, bonus_id, bonus_name).
    """
    if consume:
        bonus_id = bonus_engine.pop_pending_deposit_bonus(user_id)
    else:
        bonus_id = bonus_engine.get_pending_deposit_bonus(user_id)
    if not bonus_id:
        return True, 0, None, None

    bonus_name = _bonus_template_name(bonus_id)
    ok, err, bonus_amt = bonus_engine.activate_bonus(user_id, bonus_id, int(deposit_amount))
    if not ok:
        print(f"[deposit_credit] Bonus apply failed for {user_id}: {err}")
        return False, 0, bonus_id, bonus_name
    return True, bonus_amt, bonus_id, bonus_name
