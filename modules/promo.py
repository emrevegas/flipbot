"""
Promo Code Engine — Free-bet & balance reward system for Vegas Bot.

Promo types:
  balance   → instantly credit a fixed coin amount to user balance; 1× wager req applied
  freegame  → N rounds of a specified game at a fixed bet; winnings credited after all rounds
              with 1× wager requirement

Data layout:
  server/promo_codes           → all promo templates  {CODE_STRING: {...}}
  users/{id}/active_promo      → single active promo state for a user (or {})
"""
import json
import os
import time
import discord
from modules.database import get_data, set_data, replace_data, get_user_data, set_user_data, _get_conn

# Forfeit active promo when this fraction of the promo value is lost from peak balance.
PROMO_LOSS_FORFEIT_RATIO = 0.90


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_promo_codes() -> dict:
    """Return all promo code templates."""
    data = get_data("server/promo_codes") or {}
    return data if isinstance(data, dict) else {}


def save_promo_codes(codes: dict):
    replace_data("server/promo_codes", codes)


def get_promo_code(code: str) -> dict | None:
    """Return a single promo code template, or None if not found."""
    return get_promo_codes().get(code.upper().strip())


def get_promo_template(code: str) -> dict | None:
    """Alias for get_promo_code (used by room panels and admin UI)."""
    return get_promo_code(code)


def _fmt_pts_plain(n: int | float) -> str:
    return f"{int(n):,}"


def build_promo_redeem_summary(code: str, template: dict) -> dict:
    """Plain-text promo redeem summary for image cards and UI."""
    code = code.upper().strip()
    ptype = template.get("type", "balance")
    wager_m = float(template.get("wager_multiplier", 1.0))
    terms: list[str] = []
    reward_sub = ""

    if ptype == "freegame":
        game = str(template.get("game", "?")).replace("_", " ").title()
        rounds = int(template.get("rounds", 0))
        bet = int(template.get("bet_amount", 0))
        title = "Free Bet Activated"
        reward_label = "FREE ROUNDS"
        reward_value = f"{rounds}× {game}"
        reward_sub = f"{_fmt_pts_plain(bet)} pts per round"
        if wager_m > 0:
            terms.append(f"After all rounds: wager winnings × {wager_m:g}")
        else:
            terms.append("Winnings credited after all free rounds")
    else:
        reward = int(template.get("reward_amount", 0))
        wager_req = int(reward * wager_m) if wager_m > 0 else 0
        title = "Promo Code Redeemed"
        reward_label = "REWARD"
        reward_value = f"+{_fmt_pts_plain(reward)} pts"
        if wager_req > 0:
            terms.append(f"Wager {_fmt_pts_plain(wager_req)} pts before withdrawal")
        elif wager_m > 0:
            terms.append(f"Wager requirement: {wager_m:g}× bonus amount")

    min_forfeit = int(template.get("min_balance_forfeit", 0) or 0)
    if min_forfeit > 0:
        terms.append(f"Forfeits if balance drops below {_fmt_pts_plain(min_forfeit)} pts")
    else:
        terms.append(
            f"Forfeits if you lose {int(PROMO_LOSS_FORFEIT_RATIO * 100)}% of the promo value "
            f"or balance reaches 0"
        )

    promo_min_wd = int(template.get("promo_min_withdrawal", 0) or 0)
    promo_max_wd = int(template.get("promo_max_withdrawal", 0) or 0)
    if promo_min_wd > 0 or promo_max_wd > 0:
        wd_parts: list[str] = []
        if promo_min_wd > 0:
            wd_parts.append(f"min {_fmt_pts_plain(promo_min_wd)} pts")
        if promo_max_wd > 0:
            wd_parts.append(f"max {_fmt_pts_plain(promo_max_wd)} pts")
        terms.append("Withdrawal limits after wager: " + " · ".join(wd_parts))

    desc = (template.get("description") or "").strip()
    if desc:
        terms.append(desc if len(desc) <= 100 else desc[:97] + "...")

    return {
        "title": title,
        "code": code,
        "reward_label": reward_label,
        "reward_value": reward_value,
        "reward_sub": reward_sub,
        "terms": terms,
    }


async def send_promo_redeemed_image(
    target,
    *,
    user: discord.User | discord.Member,
    code: str,
    template: dict,
    new_balance: float | None = None,
    ephemeral: bool = False,
) -> None:
    """Render and send promo redeem card (ctx, Interaction, or followup)."""
    from modules import image_gen

    summary = build_promo_redeem_summary(code, template)
    buf = await image_gen.render_promo_redeemed_card(
        user.display_name,
        avatar_url=str(user.display_avatar.url),
        new_balance=new_balance,
        **summary,
    )
    file = discord.File(buf, "promo.png")

    if hasattr(target, "send"):
        await target.send(file=file)
    elif hasattr(target, "response") and not target.response.is_done():
        await target.response.send_message(file=file, ephemeral=ephemeral)
    else:
        await target.followup.send(file=file, ephemeral=ephemeral)


def get_active_promo(user_id) -> dict:
    """Return the user's active promo state, or {} if none."""
    uid = int(user_id)
    sync_promo_wager_fields(uid)
    data = get_stored_promo(uid)
    if not data:
        return {}
    if data.get("status") not in ("active", "wagering"):
        return {}
    return data


def save_active_promo(user_id, data: dict):
    set_user_data(int(user_id), "active_promo", data)


def clear_active_promo(user_id):
    set_user_data(int(user_id), "active_promo", {"status": "none"})


def reset_all_user_promo_states() -> dict:
    """
    Clear active_promo for every user who has promo state stored (fixes legacy bugged records).
    Does not delete server/promo_codes templates.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key, value FROM kv_store WHERE key LIKE 'user:%:active_promo'"
    ).fetchall()
    cleared = 0
    by_status: dict[str, int] = {}
    for row in rows:
        key = row["key"]
        try:
            data = json.loads(row["value"]) if row["value"] else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        if not isinstance(data, dict):
            continue
        status = (data.get("status") or "none").lower()
        if status in ("", "none"):
            continue
        parts = key.split(":")
        if len(parts) < 3:
            continue
        try:
            uid = int(parts[1])
        except ValueError:
            continue
        clear_active_promo(uid)
        cleared += 1
        by_status[status] = by_status.get(status, 0) + 1
    return {"cleared": cleared, "by_status": by_status}


def user_has_deposit_within_days(user_id, within_days: int) -> bool:
    """True if user has an approved/completed deposit in the last N days."""
    days = int(within_days or 0)
    if days <= 0:
        return True
    cutoff = int(time.time()) - days * 86400
    history = get_user_data(int(user_id), "deposit_history") or {}
    if not isinstance(history, dict):
        return False
    for dep in history.values():
        if not isinstance(dep, dict):
            continue
        if dep.get("status") not in ("approved", "completed"):
            continue
        try:
            ts = int(dep.get("timestamp") or dep.get("approved_at") or 0)
        except (TypeError, ValueError):
            ts = 0
        if ts < cutoff:
            continue
        try:
            amt = int(dep.get("confirmed_amount") or dep.get("amount") or 0)
        except (TypeError, ValueError):
            amt = 0
        if amt > 0:
            return True
    return False


def _promo_value_for_forfeit(data: dict, template: dict | None = None) -> int:
    """Coin value used for promo-loss forfeit (balance reward or free-game exposure/winnings)."""
    template = template or _promo_template_for(data)
    try:
        explicit = int(data.get("promo_value", 0) or 0)
    except (TypeError, ValueError):
        explicit = 0
    if explicit > 0:
        return explicit

    ptype = (data.get("type") or template.get("type") or "balance").lower()
    if ptype == "balance":
        return int(data.get("reward_amount") or template.get("reward_amount") or 0)

    total_won = int(data.get("total_winnings", 0) or 0)
    if data.get("status") == "wagering" and total_won > 0:
        return total_won
    bet = int(data.get("bet_amount") or template.get("bet_amount") or 0)
    rounds = int(data.get("rounds_total") or template.get("rounds") or 0)
    return bet * rounds


def sync_promo_forfeit_tracking(user_id, current_balance: int) -> bool:
    """
    Ensure promo_value / balance_at_activation / peak_balance exist on active promos.
    Legacy records with wager progress get a best-effort activation baseline.
    Returns True if fields were saved.
    """
    data = get_stored_promo(user_id)
    if data.get("status") not in ("active", "wagering"):
        return False

    template = _promo_template_for(data)
    changed = False
    promo_val = _promo_value_for_forfeit(data, template)
    if promo_val > 0 and int(data.get("promo_value", 0) or 0) != promo_val:
        data["promo_value"] = promo_val
        changed = True

    bal = int(current_balance)
    if not data.get("balance_at_activation"):
        wagered = int(data.get("wagered_so_far", 0) or 0)
        if wagered > 0 and promo_val > 0:
            data["balance_at_activation"] = bal + promo_val
        else:
            data["balance_at_activation"] = bal
        changed = True

    peak = int(data.get("peak_balance", 0) or 0)
    activation = int(data.get("balance_at_activation", 0) or 0)
    new_peak = max(peak, bal, activation)
    if new_peak != peak:
        data["peak_balance"] = new_peak
        changed = True

    if changed:
        save_active_promo(user_id, data)
    return changed


def _set_wagering_forfeit_baseline(user_id, *, promo_value: int | None = None) -> None:
    """After free-game winnings are credited, reset loss baseline for wagering phase."""
    from modules.player import Player

    data = get_stored_promo(user_id)
    if data.get("status") != "wagering":
        return
    bal = int(Player(int(user_id)).get_balance("real"))
    data["balance_at_activation"] = bal
    data["peak_balance"] = bal
    if promo_value is not None and int(promo_value) > 0:
        data["promo_value"] = int(promo_value)
    elif not int(data.get("promo_value", 0) or 0):
        data["promo_value"] = _promo_value_for_forfeit(data)
    save_active_promo(user_id, data)


def _forfeit_promo_state(data: dict, user_id) -> bool:
    data["status"] = "forfeited"
    data["forfeited_at"] = int(time.time())
    save_active_promo(user_id, data)
    return True


def get_stored_promo(user_id) -> dict:
    """Raw active_promo record (any status)."""
    data = get_user_data(int(user_id), "active_promo") or {}
    return data if isinstance(data, dict) else {}


def _promo_template_for(data: dict) -> dict:
    code = (data.get("code") or "").strip()
    return get_promo_template(code) if code else {}


def _promo_wager_multiplier(data: dict, template: dict | None = None) -> float:
    template = template or _promo_template_for(data)
    raw = data.get("wager_multiplier")
    if raw is None:
        raw = template.get("wager_multiplier", 1.0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def compute_promo_wager_requirement(data: dict, template: dict | None = None) -> int:
    """
    Effective wager target for the current promo phase.
    Balance: reward × multiplier. Free-game (wagering): winnings × multiplier,
    or (bet × rounds) × multiplier when there were no winnings.
    """
    template = template or _promo_template_for(data)
    mult = _promo_wager_multiplier(data, template)
    if mult <= 0:
        return 0

    ptype = (data.get("type") or template.get("type") or "balance").lower()
    if ptype == "balance":
        reward = int(data.get("reward_amount") or template.get("reward_amount") or 0)
        return int(reward * mult) if reward > 0 else 0

    total_won = int(data.get("total_winnings", 0))
    if total_won > 0:
        return int(total_won * mult)

    bet = int(data.get("bet_amount") or template.get("bet_amount") or 0)
    rounds = int(data.get("rounds_total") or template.get("rounds") or 0)
    exposure = bet * rounds
    return int(exposure * mult) if exposure > 0 else 0


def sync_promo_limits_from_template(user_id) -> bool:
    """
    Copy min/max WD and forfeit limits from the promo template into active_promo.
    Keeps balance cap aligned when template is edited or legacy records lack fields.
    """
    data = get_stored_promo(user_id)
    if data.get("status") not in ("active", "wagering", "completed"):
        return False
    template = _promo_template_for(data)
    if not template:
        return False
    changed = False
    for key in ("promo_min_withdrawal", "promo_max_withdrawal", "min_balance_forfeit"):
        try:
            tval = int(template.get(key, 0) or 0)
        except (TypeError, ValueError):
            tval = 0
        if int(data.get(key, 0) or 0) != tval:
            data[key] = tval
            changed = True
    if changed:
        save_active_promo(user_id, data)
    return changed


def get_promo_balance_ceiling(user_id) -> int | None:
    """Max balance cap from active promo (promo_max_withdrawal), or None if unset."""
    uid = int(user_id)
    sync_promo_limits_from_template(uid)
    data = get_stored_promo(uid)
    if data.get("status") not in ("active", "wagering", "completed"):
        return None
    try:
        pmx = int(data.get("promo_max_withdrawal", 0) or 0)
    except (TypeError, ValueError):
        pmx = 0
    if pmx <= 0:
        template = _promo_template_for(data)
        try:
            pmx = int(template.get("promo_max_withdrawal", 0) or 0)
        except (TypeError, ValueError):
            pmx = 0
    return pmx if pmx > 0 else None


def propagate_promo_limits_to_active_users(code: str) -> int:
    """After editing a promo template, refresh limits on all users still on that code."""
    code = code.upper().strip()
    template = get_promo_code(code)
    if not template:
        return 0
    conn = _get_conn()
    rows = conn.execute(
        "SELECT key, value FROM kv_store WHERE key LIKE 'user:%:active_promo'"
    ).fetchall()
    updated = 0
    for row in rows:
        key = row["key"]
        try:
            data = json.loads(row["value"]) if row["value"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        if (data.get("code") or "").upper().strip() != code:
            continue
        if data.get("status") not in ("active", "wagering", "completed"):
            continue
        parts = key.split(":")
        if len(parts) < 3:
            continue
        try:
            uid = int(parts[1])
        except ValueError:
            continue
        for fld in ("promo_min_withdrawal", "promo_max_withdrawal", "min_balance_forfeit"):
            data[fld] = int(template.get(fld, 0) or 0)
        save_active_promo(uid, data)
        updated += 1
    return updated


def sync_promo_wager_fields(user_id) -> bool:
    """Repair wager_requirement / reward fields from the promo template. Returns True if saved."""
    sync_promo_limits_from_template(user_id)
    data = get_stored_promo(user_id)
    status = data.get("status")
    if status not in ("wagering",):
        return False

    template = _promo_template_for(data)
    ptype = (data.get("type") or template.get("type") or "balance").lower()
    changed = False

    if not data.get("type"):
        data["type"] = ptype
        changed = True

    mult = _promo_wager_multiplier(data, template)
    if data.get("wager_multiplier") != mult:
        data["wager_multiplier"] = mult
        changed = True

    if ptype == "balance":
        reward = int(data.get("reward_amount") or template.get("reward_amount") or 0)
        if reward > 0 and int(data.get("reward_amount", 0)) != reward:
            data["reward_amount"] = reward
            changed = True

    req = compute_promo_wager_requirement(data, template)
    if int(data.get("wager_requirement", -1)) != req:
        data["wager_requirement"] = req
        changed = True

    if req <= 0 and mult <= 0:
        data["status"] = "completed"
        data["completed_at"] = int(time.time())
        changed = True

    if changed:
        save_active_promo(user_id, data)
    return changed


def clear_unwithdrawable_completed_promo(user_id, current_balance: int) -> bool:
    """
    Drop a completed promo when the user can no longer withdraw under promo rules
    (e.g. lost entire balance after finishing wager).
    """
    data = get_stored_promo(user_id)
    if data.get("status") != "completed":
        return False
    if current_balance <= 0:
        clear_active_promo(user_id)
        return True
    promo_min = int(data.get("promo_min_withdrawal", 0) or 0)
    if promo_min > 0 and current_balance < promo_min:
        clear_active_promo(user_id)
        return True
    return False


def has_active_promo(user_id) -> bool:
    return bool(get_active_promo(user_id))


def get_promo_display_state(user_id) -> dict:
    """Active, wagering, or completed (awaiting withdraw limits) promo state."""
    from modules.player import Player

    uid = int(user_id)
    sync_promo_wager_fields(uid)
    data = get_stored_promo(uid)
    if not data:
        return {}
    status = data.get("status")
    if status == "completed":
        bal = Player(int(user_id)).get_balance("real")
        if clear_unwithdrawable_completed_promo(user_id, bal):
            return {}
    if status in ("active", "wagering", "completed"):
        return data
    return {}


# ── Code management (admin) ────────────────────────────────────────────────────

def create_promo_code(
    *,
    code: str,
    promo_type: str,           # "balance" | "freegame"
    reward_amount: int = 0,    # balance type
    game: str = "",            # freegame type
    bet_amount: int = 0,       # freegame type — coins per round
    rounds: int = 0,           # freegame type — number of rounds
    wager_multiplier: float = 1.0,
    max_uses: int = 0,         # 0 = unlimited
    expires_at: int | None = None,
    expire_hours: int = 0,     # stored for reactivation
    description: str = "",
    created_by: str = "",
    # ── Requirements ────────────────────────────────────
    req_min_level: int = 0,           # 0 = no level requirement
    req_min_wagered: int = 0,         # 0 = no wagered requirement (matches rakeback tier threshold)
    min_balance_forfeit: int = 0,     # 0 = forfeit when balance hits 0; >0 = forfeit below threshold
    promo_min_withdrawal: int = 0,    # 0 = server default after wager complete
    promo_max_withdrawal: int = 0,    # 0 = no cap after wager complete
    req_status_contains: str = "",    # comma-separated keywords; empty = disabled
    req_deposit_within_days: int = 0,  # 0 = no recent-deposit requirement
) -> tuple[bool, str]:
    """
    Create a new promo code. Returns (success, error_message).
    """
    code = code.upper().strip()
    if not code:
        return False, "Code cannot be empty."
    if not code.replace("-", "").replace("_", "").isalnum():
        return False, "Code must be alphanumeric (letters, digits, hyphens, underscores)."

    codes = get_promo_codes()
    if code in codes:
        return False, f"Code `{code}` already exists."

    if promo_type not in ("balance", "freegame"):
        return False, "Type must be 'balance' or 'freegame'."

    if promo_type == "balance" and reward_amount <= 0:
        return False, "Reward amount must be > 0 for balance type."
    if promo_type == "freegame" and (bet_amount <= 0 or rounds <= 0 or not game):
        return False, "Freegame type requires game, bet_amount > 0, and rounds > 0."

    promo_min_withdrawal = int(promo_min_withdrawal or 0)
    promo_max_withdrawal = int(promo_max_withdrawal or 0)
    if promo_min_withdrawal < 0 or promo_max_withdrawal < 0:
        return False, "Withdrawal limits cannot be negative."
    if promo_max_withdrawal > 0 and promo_min_withdrawal > promo_max_withdrawal:
        return False, "Min withdrawal cannot exceed max withdrawal."

    codes[code] = {
        "code": code,
        "type": promo_type,
        # balance-type fields
        "reward_amount": int(reward_amount),
        # freegame-type fields
        "game": game.lower(),
        "bet_amount": int(bet_amount),
        "rounds": int(rounds),
        # common
        "wager_multiplier": float(wager_multiplier),
        "max_uses": int(max_uses),
        "used_by": [],
        "enabled": True,
        "expires_at": expires_at,
        "expire_hours": int(expire_hours),   # stored for reactivation
        "description": description,
        "created_at": int(time.time()),
        "created_by": str(created_by),
        # requirements
        "req_min_level": int(req_min_level),
        "req_min_wagered": int(req_min_wagered),
        "min_balance_forfeit": int(min_balance_forfeit),
        "promo_min_withdrawal": promo_min_withdrawal,
        "promo_max_withdrawal": promo_max_withdrawal,
        "req_status_contains": normalize_status_keywords(req_status_contains),
        "req_deposit_within_days": max(0, int(req_deposit_within_days or 0)),
    }
    save_promo_codes(codes)
    return True, ""


def normalize_status_keywords(raw: str) -> str:
    """Comma-separated custom-status keywords; empty string = requirement off."""
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    return ", ".join(parts)


def status_keywords_list(raw: str) -> list[str]:
    return [k.strip().lower() for k in (raw or "").split(",") if k.strip()]


def _iter_member_activities(member) -> list:
    """Collect activities from member + presence (requires presences intent)."""
    if member is None:
        return []
    seen: set[int] = set()
    out: list = []
    presence = getattr(member, "presence", None)
    if presence is not None:
        for act in getattr(presence, "activities", None) or []:
            if id(act) not in seen:
                seen.add(id(act))
                out.append(act)
    for act in getattr(member, "activities", None) or []:
        if id(act) not in seen:
            seen.add(id(act))
            out.append(act)
    legacy = getattr(member, "activity", None)
    if legacy is not None and id(legacy) not in seen:
        out.append(legacy)
    return out


def _activity_custom_status_text(activity) -> str:
    if isinstance(activity, discord.CustomActivity):
        for val in (activity.state, activity.name):
            text = (val or "").strip()
            if text and not text.startswith(":"):
                return text
        return (activity.state or activity.name or "").strip()
    if getattr(activity, "type", None) == discord.ActivityType.custom:
        for attr in ("state", "name", "details"):
            text = (getattr(activity, attr, None) or "").strip()
            if text:
                return text
    return ""


def get_member_custom_status(member) -> str:
    """Discord custom status text (özel durum), or empty."""
    for activity in _iter_member_activities(member):
        text = _activity_custom_status_text(activity)
        if text:
            return text
    return ""


def resolve_member_for_status(
    guild: discord.Guild | None,
    user_id: int | str,
    member: discord.Member | None = None,
) -> discord.Member | None:
    """Best-effort member with presence data from cache."""
    if guild is None:
        return member if isinstance(member, discord.Member) else None
    uid = int(user_id)
    if isinstance(member, discord.Member) and member.guild.id == guild.id:
        if get_member_custom_status(member):
            return member
    cached = guild.get_member(uid)
    if cached is not None:
        return cached
    return member if isinstance(member, discord.Member) else None


def check_status_requirement(member, req_status_contains: str) -> tuple[bool, str]:
    keywords = status_keywords_list(req_status_contains)
    if not keywords:
        return True, ""
    if member is None:
        return False, "Could not verify your Discord status. Use this command in the server."
    text = get_member_custom_status(member).lower()
    display = ", ".join(keywords)
    if not text:
        return False, (
            f"Set your Discord **custom status** (profile → custom status) to include: **{display}**\n"
            "If it is already set, wait a few seconds and try `.redeem` again in a server channel."
        )
    if not any(kw in text for kw in keywords):
        return False, f"Your custom status must contain one of: **{display}**"
    return True, ""


def toggle_promo_code(code: str) -> bool | None:
    """Toggle enabled state. Returns new state or None if not found."""
    codes = get_promo_codes()
    code = code.upper()
    if code not in codes:
        return None
    codes[code]["enabled"] = not codes[code].get("enabled", True)
    save_promo_codes(codes)
    return codes[code]["enabled"]


def delete_promo_code(code: str) -> bool:
    codes = get_promo_codes()
    code = code.upper()
    if code not in codes:
        return False
    del codes[code]
    save_promo_codes(codes)
    return True


def update_promo_code(code: str, **fields) -> tuple[bool, str]:
    """
    Update fields of an existing promo code.
    Allowed fields: description, max_uses, wager_multiplier, reward_amount,
    rounds, bet_amount, expires_at, expire_hours, req_min_level, req_min_wagered.
    Returns (success, error_message).
    """
    codes = get_promo_codes()
    code = code.upper()
    if code not in codes:
        return False, "Code not found."

    allowed = {
        "description", "max_uses", "wager_multiplier", "reward_amount",
        "rounds", "bet_amount", "expires_at", "expire_hours",
        "req_min_level", "req_min_wagered", "min_balance_forfeit",
        "promo_min_withdrawal", "promo_max_withdrawal", "req_status_contains",
        "req_deposit_within_days",
    }
    limit_keys = {"promo_min_withdrawal", "promo_max_withdrawal", "min_balance_forfeit"}
    touched_limits = False
    for k, v in fields.items():
        if k in allowed:
            if k == "req_status_contains":
                v = normalize_status_keywords(str(v))
            elif k == "req_deposit_within_days":
                v = max(0, int(v or 0))
            elif k in limit_keys:
                v = max(0, int(v or 0))
                touched_limits = True
            codes[code][k] = v
    save_promo_codes(codes)
    if touched_limits:
        propagate_promo_limits_to_active_users(code)
    return True, ""


def reactivate_promo_code(code: str) -> tuple[bool, str]:
    """
    Re-enable a disabled/expired promo code.
    If expire_hours > 0, sets a fresh expires_at from now.
    Returns (success, error_message).
    """
    codes = get_promo_codes()
    code = code.upper()
    if code not in codes:
        return False, "Code not found."

    tmpl = codes[code]
    expire_hours = int(tmpl.get("expire_hours", 0))
    if expire_hours > 0:
        codes[code]["expires_at"] = int(time.time()) + expire_hours * 3600
    else:
        codes[code]["expires_at"] = None
    codes[code]["enabled"] = True
    save_promo_codes(codes)
    return True, ""


def auto_close_expired_promos() -> list[str]:
    """
    Scan all promo codes; disable any that have passed their expires_at.
    Returns list of codes that were just closed.
    """
    codes = get_promo_codes()
    now = int(time.time())
    closed = []
    changed = False
    for code, tmpl in codes.items():
        if not tmpl.get("enabled", True):
            continue
        exp = tmpl.get("expires_at")
        if exp and now > exp:
            codes[code]["enabled"] = False
            closed.append(code)
            changed = True
    if changed:
        save_promo_codes(codes)
    return closed


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def redeem_promo_code(
    user_id,
    code: str,
    *,
    member=None,
    guild: discord.Guild | None = None,
) -> tuple[bool, str, dict]:
    """
    Validate and activate a promo code for a user.
    Returns (success, error_message, promo_template).
    Caller must credit balance for 'balance' type promos.
    member: discord.Member for custom-status requirement checks.
    """
    code = code.upper().strip()
    codes = get_promo_codes()
    template = codes.get(code)

    if not template:
        return False, "Invalid promo code.", {}
    if not template.get("enabled", True):
        return False, "This promo code is no longer active.", {}

    now = int(time.time())
    expires_at = template.get("expires_at")
    if expires_at and now > expires_at:
        return False, "This promo code has expired.", {}

    max_uses = int(template.get("max_uses", 0))
    used_by = template.get("used_by", [])
    if max_uses > 0 and len(used_by) >= max_uses:
        return False, "This promo code has reached its usage limit.", {}

    user_id_str = str(user_id)
    if user_id_str in used_by:
        return False, "You have already used this promo code.", {}

    # ── Requirement checks ────────────────────────────────────────────────
    req_min_level = int(template.get("req_min_level", 0))
    req_min_wagered = int(template.get("req_min_wagered", 0))
    if req_min_level > 0 or req_min_wagered > 0:
        user_level_data = get_user_data(int(user_id), "level") or {}
        user_level = int(user_level_data.get("level", 1))
        if req_min_level > 0 and user_level < req_min_level:
            return False, f"You need to be at least **Level {req_min_level}** to use this promo code.", {}
        if req_min_wagered > 0:
            user_stats = get_user_data(int(user_id), "stats") or {}
            total_wagered = int(user_stats.get("total_wagered", 0))
            if total_wagered < req_min_wagered:
                from modules.utils import format_balance
                return False, f"You need to wager at least **{format_balance(req_min_wagered, 'real')}** total to use this promo code.", {}

    req_status = (template.get("req_status_contains") or "").strip()
    if req_status:
        member = resolve_member_for_status(guild, user_id, member)
        ok_status, status_err = check_status_requirement(member, req_status)
        if not ok_status:
            return False, status_err, {}

    req_dep_days = int(template.get("req_deposit_within_days", 0) or 0)
    if req_dep_days > 0 and not user_has_deposit_within_days(user_id, req_dep_days):
        return (
            False,
            f"You need an approved deposit within the last **{req_dep_days}** days to use this promo.",
            {},
        )

    from modules.player import Player

    balance = Player(int(user_id)).get_balance("real")
    clear_unwithdrawable_completed_promo(user_id, balance)
    sync_promo_forfeit_tracking(user_id, balance)
    check_forfeit_promo(user_id, balance)

    if has_active_promo(user_id):
        return False, "You already have an active promo. Complete it before redeeming another.", {}

    if get_stored_promo(user_id).get("status") == "completed":
        return (
            False,
            "Finish your promo withdrawal before redeeming another code.",
            {},
        )

    # Mark code as used
    codes[code]["used_by"] = used_by + [user_id_str]
    save_promo_codes(codes)

    # Build active promo state
    promo_type = template.get("type", "balance")
    wager_mult = float(template.get("wager_multiplier", 1.0))

    min_forfeit = int(template.get("min_balance_forfeit", 0))
    promo_min_wd = int(template.get("promo_min_withdrawal", 0))
    promo_max_wd = int(template.get("promo_max_withdrawal", 0))

    if promo_type == "balance":
        reward = int(template.get("reward_amount", 0))
        active_state = {
            "code": code,
            "type": "balance",
            "reward_amount": reward,
            "promo_value": reward,
            "balance_at_activation": int(balance) + reward,
            "peak_balance": int(balance) + reward,
            "wager_multiplier": wager_mult,
            "wager_requirement": int(reward * wager_mult) if wager_mult > 0 else 0,
            "wagered_so_far": 0,
            "min_balance_forfeit": min_forfeit,
            "promo_min_withdrawal": promo_min_wd,
            "promo_max_withdrawal": promo_max_wd,
            "status": "wagering",   # immediately in wagering state after balance credit
            "activated_at": now,
        }
    else:  # freegame
        bet_amt = int(template.get("bet_amount", 0))
        rounds_total = int(template.get("rounds", 0))
        active_state = {
            "code": code,
            "type": "freegame",
            "game": template.get("game", ""),
            "bet_amount": bet_amt,
            "rounds_total": rounds_total,
            "rounds_played": 0,
            "total_winnings": 0,
            "promo_value": bet_amt * rounds_total,
            "balance_at_activation": int(balance),
            "peak_balance": int(balance),
            "wager_requirement": 0,   # set after all rounds
            "wagered_so_far": 0,
            "wager_multiplier": wager_mult,
            "min_balance_forfeit": min_forfeit,
            "promo_min_withdrawal": promo_min_wd,
            "promo_max_withdrawal": promo_max_wd,
            "status": "active",       # rounds not yet exhausted
            "activated_at": now,
        }

    save_active_promo(user_id, active_state)

    # Log to per-user transaction history
    try:
        from modules.player import Player as _Player
        _p = _Player(user_id)
        if promo_type == "balance":
            _p._write_transaction(
                ttype="promo",
                amount=int(template.get("reward_amount", 0)),
                reason=f"Promo code: {code}",
                by="system",
            )
        else:
            _p._write_transaction(
                ttype="promo",
                amount=0,
                reason=f"Free-game promo: {code} ({template.get('rounds', 0)}x {template.get('game', '')})",
                by="system",
            )
    except Exception:
        pass

    return True, "", template


def on_freeround_result(user_id, winnings: int) -> tuple[int, bool]:
    """
    Called after each free-game round completes.
    `winnings` = payout for that round (0 if lost).
    Returns (rounds_remaining, all_rounds_complete).
    """
    data = get_active_promo(user_id)
    if not data or data.get("type") != "freegame" or data.get("status") != "active":
        return 0, False

    data["rounds_played"] = int(data.get("rounds_played", 0)) + 1
    data["total_winnings"] = int(data.get("total_winnings", 0)) + int(winnings)

    rounds_total = int(data.get("rounds_total", 0))
    rounds_played = data["rounds_played"]
    remaining = rounds_total - rounds_played

    if remaining <= 0:
        # All rounds done — set wagering requirement (credit happens in complete_freegame_promo)
        template = _promo_template_for(data)
        data["status"] = "wagering"
        data["wagered_so_far"] = 0
        data["wager_requirement"] = compute_promo_wager_requirement(data, template)

    save_active_promo(user_id, data)
    return max(remaining, 0), (remaining <= 0)


def complete_freegame_promo(user_id) -> int:
    """
    Credit total free-game winnings to user's real balance and return the amount.
    The caller is responsible for calling this AFTER on_freeround_result returns all_done=True.
    """
    from modules.player import Player
    data = get_user_data(int(user_id), "active_promo") or {}
    total_won = int(data.get("total_winnings", 0))
    if total_won > 0:
        player = Player(user_id)
        player.add_balance("real", total_won)
    promo_val = total_won if total_won > 0 else _promo_value_for_forfeit(data)
    _set_wagering_forfeit_baseline(user_id, promo_value=promo_val)
    return total_won


def on_real_bet_wagered(user_id, bet_amount: int) -> bool:
    """
    Called after every real-money bet (from base_game handle_result).
    Accumulates wagered amount toward promo wager requirement.
    Returns True when the promo wager requirement is fully met (promo completed).
    """
    data = get_active_promo(user_id)
    if not data or data.get("status") != "wagering":
        return False

    required = int(data.get("wager_requirement", 0))
    if required <= 0:
        if _promo_wager_multiplier(data) <= 0:
            data["status"] = "completed"
            data["completed_at"] = int(time.time())
            save_active_promo(user_id, data)
            return True
        return False

    data["wagered_so_far"] = int(data.get("wagered_so_far", 0)) + int(bet_amount)
    if data["wagered_so_far"] >= required:
        data["status"] = "completed"
        data["completed_at"] = int(time.time())
        save_active_promo(user_id, data)
        return True

    save_active_promo(user_id, data)
    return False


def check_forfeit_promo(user_id, current_balance: int) -> bool:
    """
    Check if the active promo should be forfeited.

    - min_balance_forfeit > 0: balance at or below that threshold
    - min_balance_forfeit == 0: balance at 0, OR promo value × 90% lost from peak balance
    - Also clears completed promos when balance is gone (no withdrawal possible).

    Returns True if promo state was cleared or forfeited.
    """
    data = get_stored_promo(user_id)
    if not data:
        return False

    if data.get("status") == "completed":
        return clear_unwithdrawable_completed_promo(user_id, current_balance)

    if data.get("status") not in ("active", "wagering"):
        return False

    sync_promo_forfeit_tracking(user_id, current_balance)
    data = get_stored_promo(user_id)

    bal = int(current_balance)
    threshold = int(data.get("min_balance_forfeit", 0))
    should_forfeit = False

    if threshold > 0:
        should_forfeit = bal <= threshold
    elif bal <= 0:
        should_forfeit = True
    else:
        promo_val = _promo_value_for_forfeit(data)
        if promo_val > 0:
            peak = int(data.get("peak_balance", 0) or data.get("balance_at_activation", 0) or 0)
            if peak < bal:
                data["peak_balance"] = bal
                save_active_promo(user_id, data)
                peak = bal
            loss = peak - bal
            forfeit_at = int(promo_val * PROMO_LOSS_FORFEIT_RATIO)
            if forfeit_at > 0 and loss >= forfeit_at:
                should_forfeit = True

    if should_forfeit:
        return _forfeit_promo_state(data, user_id)
    return False


def get_promo_withdraw_limits(user_id) -> tuple[int | None, int | None]:
    """Min/max withdrawal after promo wager is completed (None = use server / no cap)."""
    from modules.player import Player

    data = get_stored_promo(user_id)
    if data.get("status") != "completed":
        return None, None
    bal = Player(int(user_id)).get_balance("real")
    if clear_unwithdrawable_completed_promo(user_id, bal):
        return None, None
    min_w = int(data.get("promo_min_withdrawal", 0))
    max_w = int(data.get("promo_max_withdrawal", 0))
    return (min_w if min_w > 0 else None, max_w if max_w > 0 else None)


def clear_completed_promo_after_withdraw(user_id) -> None:
    """Remove completed promo state after a successful withdrawal."""
    data = get_user_data(int(user_id), "active_promo") or {}
    if isinstance(data, dict) and data.get("status") == "completed":
        clear_active_promo(user_id)


def get_promo_wager_info(user_id) -> dict:
    """Return wagering progress info for the active promo, or {}."""
    data = get_active_promo(user_id)
    if not data:
        return {}
    req = int(data.get("wager_requirement", 0))
    done = int(data.get("wagered_so_far", 0))
    return {
        "code": data.get("code", ""),
        "type": data.get("type", ""),
        "status": data.get("status", ""),
        "wager_requirement": req,
        "wagered_so_far": done,
        "remaining": max(req - done, 0),
        "pct": int(done / req * 100) if req > 0 else 100,
    }
