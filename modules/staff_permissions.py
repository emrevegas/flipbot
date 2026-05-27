"""Staff role helpers (admin, moderator, cashier, …)."""

from __future__ import annotations

from modules.database import get_data, is_super_admin


def normalize_permissions(perms) -> list[str]:
    if not perms:
        return []
    if isinstance(perms, str):
        perms = [perms]
    return [str(p).strip() for p in perms if p]


def get_staff_permissions(user_id: int | str) -> list[str]:
    if is_super_admin(user_id):
        return ["admin"]
    admins = get_data("server/admins") or {}
    return normalize_permissions(admins.get(str(user_id), []))


def staff_has_permission(user_id: int | str, permission: str) -> bool:
    """True when the user has a staff permission (admin implies all)."""
    if is_super_admin(user_id):
        return True
    perms = [p.lower() for p in get_staff_permissions(user_id)]
    if "admin" in perms:
        return True
    return permission.lower() in perms


def is_moderator_only(user_id: int | str) -> bool:
    """Moderator role without full admin."""
    perms = [p.lower() for p in get_staff_permissions(user_id)]
    return "moderator" in perms and "admin" not in perms


def can_open_user_panel(user_id: int | str) -> bool:
    if is_super_admin(user_id):
        return True
    if not staff_has_permission(user_id, "admin"):
        return True
    perms = [p.lower() for p in get_staff_permissions(user_id)]
    allowed = {"admin", "moderator", "cashier", "ticketadmin"}
    return bool(perms) and any(p in allowed for p in perms)


MODERATOR_PANEL_ACTIONS = frozenset({
    "add_balance",
    "remove_balance",
    "game_history",
    "statistics",
    "referral_info",
    "activity",
})
