"""
Bonus Engine — Deposit bonus system for Vegas Bot.

Bonus types:
  fixed      → grow balance to (deposit × wager_target_multiplier); then withdraw up to (deposit × max_withdrawal_multiplier)
  percentage → get N% bonus on deposit; must wager (deposit+bonus) × wager_multiplier total before withdrawing

Data layout:
  server/bonuses              → bonus templates dict  {bonus_id: {...}}
  users/{id}/active_bonus     → single active bonus state for a user (or {})
"""
import time
from modules.database import get_data, set_data, replace_data, get_user_data, set_user_data


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_bonus_templates() -> dict:
    """Return all configured bonus templates from server/bonuses."""
    data = get_data("server/bonuses") or {}
    return data if isinstance(data, dict) else {}


def get_enabled_bonus_templates() -> dict:
    """Return only enabled, open, and not-full bonus templates."""
    now = time.time()
    result = {}
    for k, v in get_bonus_templates().items():
        if not v.get("enabled", True):
            continue
        closes_at = v.get("closes_at")
        if closes_at and now > closes_at:
            continue
        max_users = int(v.get("max_users", 0))
        if max_users > 0 and len(v.get("activated_users", [])) >= max_users:
            continue
        result[k] = v
    return result


def save_bonus_templates(templates: dict):
    replace_data("server/bonuses", templates)


def get_active_bonus(user_id) -> dict:
    """Return the user's current active bonus dict, or {} if none."""
    data = get_user_data(int(user_id), "active_bonus") or {}
    if not isinstance(data, dict):
        return {}
    if data.get("status") != "active":
        return {}
    return data


def _save_active_bonus(user_id, data: dict):
    set_user_data(int(user_id), "active_bonus", data)


def has_active_bonus(user_id) -> bool:
    return bool(get_active_bonus(user_id))


# ── Pending bonus selection (crypto / in-game flows) ───────────────────────────

def set_pending_deposit_bonus(user_id, bonus_id: str | None) -> None:
    """Store the bonus the user chose before sending crypto / in-game funds."""
    prefs = get_user_data(int(user_id), "deposit_prefs") or {}
    if not isinstance(prefs, dict):
        prefs = {}
    if not bonus_id or bonus_id == "__none__":
        prefs.pop("pending_bonus_id", None)
    else:
        prefs["pending_bonus_id"] = bonus_id
    set_user_data(int(user_id), "deposit_prefs", prefs)


def get_pending_deposit_bonus(user_id) -> str | None:
    """Return the pending deposit bonus id without clearing it."""
    prefs = get_user_data(int(user_id), "deposit_prefs") or {}
    if not isinstance(prefs, dict):
        return None
    return prefs.get("pending_bonus_id")


def pop_pending_deposit_bonus(user_id) -> str | None:
    """Return and clear the pending deposit bonus id, if any."""
    prefs = get_user_data(int(user_id), "deposit_prefs") or {}
    if not isinstance(prefs, dict):
        return None
    bonus_id = prefs.pop("pending_bonus_id", None)
    set_user_data(int(user_id), "deposit_prefs", prefs)
    return bonus_id


def _validate_bonus_template(user_id, bonus_id: str, template: dict) -> tuple[bool, str]:
    """Shared eligibility checks for a bonus template."""
    if not template:
        return False, "Bonus template not found."
    if not template.get("enabled", True):
        return False, "This bonus is no longer available."

    now = int(time.time())
    closes_at = template.get("closes_at")
    if closes_at and now > closes_at:
        return False, "This bonus has expired and is no longer available."

    max_users = int(template.get("max_users", 0))
    activated_users = template.get("activated_users", [])
    if max_users > 0 and len(activated_users) >= max_users:
        return False, "This bonus is full and no longer accepting new participants."

    req_min_level = int(template.get("req_min_level", 0))
    req_min_wagered = int(template.get("req_min_wagered", 0))
    if req_min_level > 0 or req_min_wagered > 0:
        user_level_data = get_user_data(int(user_id), "level") or {}
        user_level = int(user_level_data.get("level", 1))
        if req_min_level > 0 and user_level < req_min_level:
            return False, f"You need to be at least **Level {req_min_level}** to activate this bonus."
        if req_min_wagered > 0:
            user_stats = get_user_data(int(user_id), "stats") or {}
            total_wagered = int(user_stats.get("total_wagered", 0))
            if total_wagered < req_min_wagered:
                return False, f"You need to wager at least **{req_min_wagered:,}** total to activate this bonus."
    return True, ""


def compute_deposit_bonus(template: dict, deposit_amount: int) -> tuple[int, int, int | None]:
    """Compute (bonus_amount, wager_requirement, max_withdrawal) for one deposit slice."""
    deposit_amount = int(deposit_amount)
    btype = template.get("type", "fixed")

    if btype == "fixed":
        wager_target_mult = float(template.get("wager_target_multiplier", 2.0))
        max_withdrawal_mult = float(template.get("max_withdrawal_multiplier", 4.0))
        bonus_amount = 0
        wager_requirement = int(deposit_amount * wager_target_mult)
        max_withdrawal = int(deposit_amount * max_withdrawal_mult)
        return bonus_amount, wager_requirement, max_withdrawal

    if btype == "percentage":
        pct = float(template.get("percentage", 0))
        bonus_amount = int(deposit_amount * pct / 100)
        wager_mult = float(template.get("wager_multiplier", 1))
        wager_requirement = int((deposit_amount + bonus_amount) * wager_mult)
        cap = template.get("max_withdrawal")
        return bonus_amount, wager_requirement, int(cap) if cap else None

    raise ValueError("Unknown bonus type.")


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def activate_bonus(user_id, bonus_id: str, deposit_amount: int) -> tuple[bool, str, int]:
    """
    Apply a deposit bonus for a user. Stacks wager onto an existing active bonus
    instead of resetting progress. Returns (success, error_message, bonus_amount_added).
    Caller is responsible for adding bonus_amount to player balance.
    """
    if not bonus_id or bonus_id == "__none__":
        return True, "", 0

    templates = get_bonus_templates()
    template = templates.get(bonus_id)
    ok, err = _validate_bonus_template(user_id, bonus_id, template)
    if not ok:
        return False, err, 0

    try:
        bonus_amount, wager_requirement, max_withdrawal = compute_deposit_bonus(
            template, deposit_amount
        )
    except ValueError:
        return False, "Unknown bonus type.", 0

    now = int(time.time())
    btype = template.get("type", "fixed")
    existing = get_active_bonus(user_id)

    if existing:
        # Stack: preserve wagered_so_far and add new requirements on top.
        existing["deposit_amount"] = int(existing.get("deposit_amount", 0)) + int(deposit_amount)
        existing["bonus_amount"] = int(existing.get("bonus_amount", 0)) + bonus_amount
        existing["wager_requirement"] = int(existing.get("wager_requirement", 0)) + wager_requirement
        if max_withdrawal is not None:
            prev_cap = existing.get("max_withdrawal")
            existing["max_withdrawal"] = (
                int(prev_cap) + int(max_withdrawal) if prev_cap else int(max_withdrawal)
            )
        _save_active_bonus(user_id, existing)
    else:
        active = {
            "bonus_id": bonus_id,
            "bonus_name": template.get("name", bonus_id),
            "type": btype,
            "deposit_amount": int(deposit_amount),
            "bonus_amount": bonus_amount,
            "wager_requirement": wager_requirement,
            "wagered_so_far": 0,
            "max_withdrawal": max_withdrawal,
            "min_balance_forfeit": int(template.get("min_balance_forfeit", 0)),
            "activated_at": now,
            "status": "active",
        }
        _save_active_bonus(user_id, active)

    if bonus_amount > 0:
        try:
            from modules.player import Player as _Player
            _p = _Player(user_id)
            _p._write_transaction(
                ttype="bonus",
                amount=bonus_amount,
                reason=f"Bonus applied: {template.get('name', bonus_id)}",
                by="system",
            )
        except Exception:
            pass

    templates = get_bonus_templates()
    if bonus_id in templates:
        users_list = templates[bonus_id].setdefault("activated_users", [])
        if int(user_id) not in users_list:
            users_list.append(int(user_id))
        save_bonus_templates(templates)

    return True, "", bonus_amount


def add_wager(user_id, bet_amount: int) -> bool:
    """
    Call after every real-money bet (percentage bonuses only).
    Accumulates total wagered amount; returns True when wager requirement met.
    Fixed bonuses use check_balance_milestone() instead — this is a no-op for them.
    """
    data = get_active_bonus(user_id)
    if not data:
        return False

    if data.get("type") == "fixed":
        return False  # Fixed bonuses track balance milestone, not total bets

    required = int(data.get("wager_requirement", 0))
    if required == 0:
        return False

    data["wagered_so_far"] = int(data.get("wagered_so_far", 0)) + int(bet_amount)
    if data["wagered_so_far"] >= required:
        data["status"] = "completed"
        _save_active_bonus(user_id, data)
        return True

    _save_active_bonus(user_id, data)
    return False


def check_balance_milestone(user_id, current_balance: int) -> bool:
    """
    For fixed bonuses: check if player's current balance has reached the wager target.
    If reached, marks the bonus as completed and returns True.
    """
    data = get_active_bonus(user_id)
    if not data or data.get("type") != "fixed":
        return False

    target = int(data.get("wager_requirement", 0))
    if target > 0 and current_balance >= target:
        data["status"] = "completed"
        data["completed_at"] = int(time.time())
        _save_active_bonus(user_id, data)
        return True
    return False


def check_forfeit(user_id, current_balance: int) -> bool:
    """
    Check if balance dropped below min_balance_forfeit.
    If so, forfeit the bonus (mark as forfeited).
    Returns True if bonus was just forfeited.
    """
    data = get_active_bonus(user_id)
    if not data:
        return False

    threshold = int(data.get("min_balance_forfeit", 0))
    if threshold > 0 and current_balance <= threshold:
        data["status"] = "forfeited"
        data["forfeited_at"] = int(time.time())
        _save_active_bonus(user_id, data)
        return True
    return False


def can_deposit(user_id) -> tuple[bool, str]:
    """Deposits are allowed while a bonus is active; wager requirements stack."""
    return True, ""


def get_max_withdrawal(user_id) -> int | None:
    """Return the max withdrawal cap if bonus is active and has one, else None."""
    data = get_active_bonus(user_id)
    if not data:
        return None
    cap = data.get("max_withdrawal")
    return int(cap) if cap else None


def is_wager_complete(user_id) -> bool:
    """Return True if the active bonus wager requirement has been met."""
    raw = get_user_data(int(user_id), "active_bonus") or {}
    return raw.get("status") == "completed"


def complete_bonus_on_withdraw(user_id):
    """
    Called when a user with an active fixed bonus successfully withdraws.
    Marks the bonus as completed (they cashed out).
    """
    raw = get_user_data(int(user_id), "active_bonus") or {}
    if raw.get("status") == "active":
        raw["status"] = "completed"
        raw["completed_at"] = int(time.time())
        set_user_data(int(user_id), "active_bonus", raw)


def forfeit_bonus(user_id):
    """Manually forfeit a user's active bonus (e.g. admin action)."""
    raw = get_user_data(int(user_id), "active_bonus") or {}
    if raw.get("status") == "active":
        raw["status"] = "forfeited"
        raw["forfeited_at"] = int(time.time())
        set_user_data(int(user_id), "active_bonus", raw)


# ── Admin helpers ──────────────────────────────────────────────────────────────

def create_bonus_template(
    name: str,
    btype: str,
    *,
    # fixed-type params
    wager_target_multiplier: float = 2.0,
    max_withdrawal_multiplier: float = 4.0,
    # percentage-type params
    percentage: float = 0,
    wager_multiplier: float = 1,
    max_withdrawal: int | None = None,
    # shared params
    expire_hours: int = 0,       # hours from NOW until template closes to new signups (0 = never)
    max_users: int = 0,          # max activations allowed (0 = unlimited)
    min_balance_forfeit: int = 0,
    description: str = "",
    # requirements
    req_min_level: int = 0,      # 0 = no level requirement
    req_min_wagered: int = 0,    # 0 = no wagered requirement
) -> str:
    """Create a new bonus template. Returns the generated bonus_id."""
    bonus_id = str(int(time.time() * 1000))
    now = int(time.time())
    templates = get_bonus_templates()
    templates[bonus_id] = {
        "id": bonus_id,
        "name": name,
        "type": btype,
        "enabled": True,
        # fixed-type fields
        "wager_target_multiplier": wager_target_multiplier,
        "max_withdrawal_multiplier": max_withdrawal_multiplier,
        # percentage-type fields
        "percentage": percentage,
        "wager_multiplier": wager_multiplier,
        "max_withdrawal": max_withdrawal,
        # shared fields
        "closes_at": (now + expire_hours * 3600) if expire_hours else None,
        "expire_hours": int(expire_hours),   # stored for reactivation
        "max_users": max_users,
        "activated_users": [],
        "min_balance_forfeit": min_balance_forfeit,
        "description": description,
        "created_at": now,
        # requirements
        "req_min_level": int(req_min_level),
        "req_min_wagered": int(req_min_wagered),
    }
    save_bonus_templates(templates)
    return bonus_id


def delete_bonus_template(bonus_id: str) -> bool:
    templates = get_bonus_templates()
    if bonus_id not in templates:
        return False
    del templates[bonus_id]
    save_bonus_templates(templates)
    return True


def toggle_bonus_template(bonus_id: str) -> bool | None:
    """Toggle enabled state. Returns new state or None if not found."""
    templates = get_bonus_templates()
    if bonus_id not in templates:
        return None
    templates[bonus_id]["enabled"] = not templates[bonus_id].get("enabled", True)
    save_bonus_templates(templates)
    return templates[bonus_id]["enabled"]


def reactivate_bonus_template(bonus_id: str) -> tuple[bool, str]:
    """
    Re-enable a disabled/expired bonus template.
    If expire_hours > 0, sets a fresh closes_at from now.
    Returns (success, error_message).
    """
    templates = get_bonus_templates()
    if bonus_id not in templates:
        return False, "Bonus template not found."
    tmpl = templates[bonus_id]
    expire_hours = int(tmpl.get("expire_hours", 0))
    if expire_hours > 0:
        templates[bonus_id]["closes_at"] = int(time.time()) + expire_hours * 3600
    else:
        templates[bonus_id]["closes_at"] = None
    templates[bonus_id]["enabled"] = True
    save_bonus_templates(templates)
    return True, ""


def auto_close_expired_bonuses() -> list[str]:
    """
    Scan all bonus templates; disable any that have passed their closes_at.
    Returns list of bonus_ids that were just closed.
    """
    templates = get_bonus_templates()
    now = int(time.time())
    closed = []
    changed = False
    for bid, tmpl in templates.items():
        if not tmpl.get("enabled", True):
            continue
        closes_at = tmpl.get("closes_at")
        if closes_at and now > closes_at:
            templates[bid]["enabled"] = False
            closed.append(bid)
            changed = True
    if changed:
        save_bonus_templates(templates)
    return closed
