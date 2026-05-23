"""Event Manager — per-user challenge/event progress tracking and reward distribution.

Event storage  : server/events             (dict: event_id → event_dict)
User progress  : users/{uid}/event_progress (dict: event_id → progress_dict)
"""

import time
import random
import string

from modules.database import get_data, set_data, replace_data, get_user_data, set_user_data

# ── Game labels ─────────────────────────────────────────────────────────────────

GAME_LABELS: dict[str, str] = {
    "mines":        "💣 Mines",
    "coinflip":     "🔥 Coinflip",
    "towers":       "🗼 Towers",
    "crystals":     "💎 Crystals",
    "blackjack":    "🃏 Blackjack",
    "dice":         "🎲 Dice",
    "limbo":        "🚀 Limbo",
    "slots":        "🎰 Slots",
    "roulette":     "🎡 Roulette",
    "case_opening": "📦 Case Opening",
}

# ── Event type definitions ──────────────────────────────────────────────────────
# params: list of (param_key, default_value, placeholder_str)
# Labels/descriptions live in translations: events.types.{type_id}.label / .desc
# Param labels live in translations: events.params.{param_key}

EVENT_TYPE_DEFS: dict[str, dict] = {
    # ── Mines ──────────────────────────────────────────────────────────────────
    "mines_fearless": {
        "game":   "mines",
        "params": [("mines", 10, "10")],
    },
    "mines_marathon": {
        "game":   "mines",
        "params": [("gems", 25, "25")],
    },
    "mines_greed": {
        "game":   "mines",
        "params": [("multiplier", 10, "10")],
    },
    # ── Towers ─────────────────────────────────────────────────────────────────
    "towers_summit": {
        "game":   "towers",
        "params": [("level", 8, "8")],
    },
    "towers_streak": {
        "game":   "towers",
        "params": [("streak", 3, "3")],
    },
    # ── Crystals ───────────────────────────────────────────────────────────────
    "crystals_royale": {
        "game":   "crystals",
        "params": [],
    },
    "crystals_combo_streak": {
        "game":   "crystals",
        "params": [("streak", 3, "3")],
    },
    "crystals_collector": {
        "game":   "crystals",
        "params": [("wins", 10, "10")],
    },
    # ── Blackjack ──────────────────────────────────────────────────────────────
    "bj_natural_hunter": {
        "game":   "blackjack",
        "params": [("count", 3, "3")],
    },
    "bj_win_streak": {
        "game":   "blackjack",
        "params": [("streak", 5, "5")],
    },
    "bj_big_winner": {
        "game":   "blackjack",
        "params": [("bet", 1000, "1000")],
    },
    # ── Coinflip ───────────────────────────────────────────────────────────────
    "coinflip_streak": {
        "game":   "coinflip",
        "params": [("streak", 5, "5")],
    },
    "coinflip_grinder": {
        "game":   "coinflip",
        "params": [("wins", 15, "15")],
    },
    # ── Dice ───────────────────────────────────────────────────────────────────
    "dice_streak": {
        "game":   "dice",
        "params": [("streak", 5, "5")],
    },
    "dice_legend": {
        "game":   "dice",
        "params": [("wins", 20, "20")],
    },
    # ── Limbo ──────────────────────────────────────────────────────────────────
    "limbo_moon": {
        "game":   "limbo",
        "params": [("multiplier", 50, "50")],
    },
    "limbo_sniper": {
        "game":   "limbo",
        "params": [("count", 5, "5"), ("multiplier", 10, "10")],
    },
    # ── Slots ──────────────────────────────────────────────────────────────────
    "slots_mega": {
        "game":   "slots",
        "params": [("multiplier", 50, "50")],
    },
    "slots_addict": {
        "game":   "slots",
        "params": [("count", 30, "30")],
    },
    # ── Roulette ───────────────────────────────────────────────────────────────
    "roulette_hot": {
        "game":   "roulette",
        "params": [("streak", 5, "5")],
    },
    "roulette_grinder": {
        "game":   "roulette",
        "params": [("wins", 20, "20")],
    },
    # ── Case Opening ───────────────────────────────────────────────────────────
    "case_hunter": {
        "game":   "case_opening",
        "params": [("count", 10, "10")],
    },
    "case_big_spender": {
        "game":   "case_opening",
        "params": [("total_bet", 10000, "10000")],
    },
}

# game → list of event type IDs
GAME_EVENT_TYPES: dict[str, list[str]] = {}
for _eid, _edef in EVENT_TYPE_DEFS.items():
    GAME_EVENT_TYPES.setdefault(_edef["game"], []).append(_eid)


# ── Translation helpers ─────────────────────────────────────────────────────────

def get_type_label(type_id: str, user_id: str | None = None, lang: str | None = None) -> str:
    from modules.translator import t
    val = t(f"events.types.{type_id}.label", user_id=user_id, lang=lang)
    return val if not val.startswith("[Missing:") else type_id


def get_type_desc(type_id: str, user_id: str | None = None, lang: str | None = None,
                  event: dict | None = None) -> str:
    from modules.translator import t
    from modules.database import get_data as _gd
    kwargs: dict = {}
    if event:
        params = event.get("params") or {}
        # Pass each param value individually + a generic {value} for the first param
        kwargs.update(params)
        if params:
            first_val = next(iter(params.values()))
            kwargs.setdefault("value", first_val)
        coin_emoji = (_gd("server/server") or {}).get("coin_emoji", "💵")
        kwargs["coin_emoji"] = coin_emoji
    val = t(f"events.types.{type_id}.desc", user_id=user_id, lang=lang, **kwargs)
    return val if not val.startswith("[Missing:") else ""


def get_param_label(param_key: str, user_id: str | None = None, lang: str | None = None) -> str:
    from modules.translator import t
    val = t(f"events.params.{param_key}", user_id=user_id, lang=lang)
    return val if not val.startswith("[Missing:") else param_key


def get_reward_display(event: dict, user_id: str | None = None, lang: str | None = None) -> str:
    from modules.translator import t
    from modules.database import get_data as _gd
    _srv       = _gd("server/server") or {}
    coin_emoji = _srv.get("coin_emoji", "💵")
    reward = event.get("reward", {})
    rtype  = reward.get("type", "fixed")
    rvalue = reward.get("value", 0)
    if rtype == "multiplier":
        return t("events.reward_multiplier", user_id=user_id, lang=lang, value=rvalue)
    return t("events.reward_fixed", user_id=user_id, lang=lang, value=f"{int(rvalue):,}", coin_emoji=coin_emoji)


# ── Storage helpers ─────────────────────────────────────────────────────────────

def _new_event_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"evt_{int(time.time())}_{suffix}"


def get_all_events() -> dict:
    return get_data("server/events") or {}


def save_all_events(events: dict) -> None:
    replace_data("server/events", events)


def get_active_events() -> dict:
    return {k: v for k, v in get_all_events().items() if v.get("active", True)}


def get_user_event_progress(user_id, event_id: str) -> dict:
    progress = get_user_data(str(user_id), "event_progress") or {}
    return progress.get(event_id, {})


def _set_user_event_progress(user_id, event_id: str, data: dict) -> None:
    progress = get_user_data(str(user_id), "event_progress") or {}
    progress[event_id] = data
    set_user_data(str(user_id), "event_progress", progress)


def _clear_user_event_progress(user_id, event_id: str) -> None:
    progress = get_user_data(str(user_id), "event_progress") or {}
    progress.pop(event_id, None)
    set_user_data(str(user_id), "event_progress", progress)


# ── Event CRUD ──────────────────────────────────────────────────────────────────

def create_event(data: dict) -> str:
    """Create a new event. Returns the new event ID."""
    events = get_all_events()
    event_id = _new_event_id()
    data["id"] = event_id
    data["created_at"] = int(time.time())
    data.setdefault("winners", [])
    events[event_id] = data
    save_all_events(events)
    return event_id


def delete_event(event_id: str) -> bool:
    events = get_all_events()
    if event_id not in events:
        return False
    del events[event_id]
    save_all_events(events)
    return True


def toggle_event_active(event_id: str) -> bool | None:
    """Toggle active state. Returns new state or None if not found."""
    events = get_all_events()
    if event_id not in events:
        return None
    events[event_id]["active"] = not events[event_id].get("active", True)
    save_all_events(events)
    return events[event_id]["active"]


# ── Condition checking ──────────────────────────────────────────────────────────

_CRYSTALS_WIN        = {"triple", "full_house", "quadruple", "quintuple"}
_CRYSTALS_COMBO_GOOD = {"triple", "full_house", "quadruple", "quintuple"}


def _check_condition(event: dict, ctx: dict, progress: dict) -> tuple[bool, dict]:
    """
    Evaluate whether the event condition is met given game context.
    Returns (completed, updated_progress).
    """
    etype  = event["type"]
    params = event.get("params", {})
    p      = dict(progress)

    # ── Mines ──────────────────────────────────────────────────────────────────
    if etype == "mines_fearless":
        met = bool(ctx.get("won")) and ctx.get("mine_count", 0) >= int(params.get("mines", 10))
        return met, p

    if etype == "mines_marathon":
        if ctx.get("won"):
            p["gem_count"] = p.get("gem_count", 0) + int(ctx.get("gems_found", 0))
        return p.get("gem_count", 0) >= int(params.get("gems", 25)), p

    if etype == "mines_greed":
        met = bool(ctx.get("won")) and ctx.get("multiplier", 0.0) >= float(params.get("multiplier", 10))
        return met, p

    # ── Towers ─────────────────────────────────────────────────────────────────
    if etype == "towers_summit":
        return ctx.get("level_reached", 0) >= int(params.get("level", 8)), p

    if etype == "towers_streak":
        streak = p.get("streak", 0)
        streak = streak + 1 if ctx.get("won") else 0
        p["streak"] = streak
        return streak >= int(params.get("streak", 3)), p

    # ── Crystals ───────────────────────────────────────────────────────────────
    if etype == "crystals_royale":
        return ctx.get("combo") == "quintuple", p

    if etype == "crystals_combo_streak":
        streak = p.get("streak", 0)
        streak = streak + 1 if ctx.get("combo") in _CRYSTALS_COMBO_GOOD else 0
        p["streak"] = streak
        return streak >= int(params.get("streak", 3)), p

    if etype == "crystals_collector":
        if ctx.get("combo") in _CRYSTALS_WIN:
            p["wins_count"] = p.get("wins_count", 0) + 1
        return p.get("wins_count", 0) >= int(params.get("wins", 10)), p

    # ── Blackjack ──────────────────────────────────────────────────────────────
    if etype == "bj_natural_hunter":
        if ctx.get("is_natural_bj") and ctx.get("won"):
            p["naturals"] = p.get("naturals", 0) + 1
        return p.get("naturals", 0) >= int(params.get("count", 3)), p

    if etype == "bj_win_streak":
        streak = p.get("streak", 0)
        streak = streak + 1 if ctx.get("won") else 0
        p["streak"] = streak
        return streak >= int(params.get("streak", 5)), p

    if etype == "bj_big_winner":
        met = bool(ctx.get("won")) and int(ctx.get("bet", 0)) >= int(params.get("bet", 1000))
        return met, p

    # ── Coinflip ───────────────────────────────────────────────────────────────
    if etype == "coinflip_streak":
        streak = p.get("streak", 0)
        streak = streak + 1 if ctx.get("won") else 0
        p["streak"] = streak
        return streak >= int(params.get("streak", 5)), p

    if etype == "coinflip_grinder":
        if ctx.get("won"):
            p["wins_count"] = p.get("wins_count", 0) + 1
        return p.get("wins_count", 0) >= int(params.get("wins", 15)), p

    # ── Dice ───────────────────────────────────────────────────────────────────
    if etype == "dice_streak":
        streak = p.get("streak", 0)
        streak = streak + 1 if ctx.get("won") else 0
        p["streak"] = streak
        return streak >= int(params.get("streak", 5)), p

    if etype == "dice_legend":
        if ctx.get("won"):
            p["wins_count"] = p.get("wins_count", 0) + 1
        return p.get("wins_count", 0) >= int(params.get("wins", 20)), p

    # ── Limbo ──────────────────────────────────────────────────────────────────
    if etype == "limbo_moon":
        return ctx.get("multiplier_hit", 0.0) >= float(params.get("multiplier", 50)), p

    if etype == "limbo_sniper":
        if ctx.get("multiplier_hit", 0.0) >= float(params.get("multiplier", 10)):
            p["big_wins"] = p.get("big_wins", 0) + 1
        return p.get("big_wins", 0) >= int(params.get("count", 5)), p

    # ── Slots ──────────────────────────────────────────────────────────────────
    if etype == "slots_mega":
        met = bool(ctx.get("won")) and ctx.get("multiplier", 0.0) >= float(params.get("multiplier", 50))
        return bool(met), p

    if etype == "slots_addict":
        p["plays"] = p.get("plays", 0) + 1
        return p["plays"] >= int(params.get("count", 30)), p

    # ── Roulette ───────────────────────────────────────────────────────────────
    if etype == "roulette_hot":
        streak = p.get("streak", 0)
        streak = streak + 1 if ctx.get("won") else 0
        p["streak"] = streak
        return streak >= int(params.get("streak", 5)), p

    if etype == "roulette_grinder":
        if ctx.get("won"):
            p["wins_count"] = p.get("wins_count", 0) + 1
        return p.get("wins_count", 0) >= int(params.get("wins", 20)), p

    # ── Case Opening ───────────────────────────────────────────────────────────
    if etype == "case_hunter":
        p["cases"] = p.get("cases", 0) + 1
        return p["cases"] >= int(params.get("count", 10)), p

    if etype == "case_big_spender":
        p["total_spent"] = p.get("total_spent", 0) + int(ctx.get("bet", 0))
        return p["total_spent"] >= int(params.get("total_bet", 10000)), p

    return False, p


# ── Progress summary ────────────────────────────────────────────────────────────

def get_user_progress_for_event(user_id, event: dict) -> dict:
    """
    Return progress info for display.
    Keys: current (int), target (int), completed (bool), unit_key (str)
    unit_key maps to t("events.unit_{unit_key}", ...) in the cog.
    """
    etype     = event["type"]
    params    = event.get("params", {})
    prog      = get_user_event_progress(user_id, event["id"])
    completed = str(user_id) in event.get("winners", [])

    if etype in ("towers_streak", "crystals_combo_streak", "bj_win_streak",
                 "coinflip_streak", "dice_streak", "roulette_hot"):
        target = int(params.get("streak", 3))
        return {"current": prog.get("streak", 0), "target": target,
                "completed": completed, "unit_key": "streak"}

    if etype in ("coinflip_grinder", "dice_legend", "roulette_grinder", "crystals_collector"):
        target = int(params.get("wins", 10))
        return {"current": prog.get("wins_count", 0), "target": target,
                "completed": completed, "unit_key": "wins"}

    if etype == "mines_marathon":
        target = int(params.get("gems", 25))
        return {"current": prog.get("gem_count", 0), "target": target,
                "completed": completed, "unit_key": "gems"}

    if etype == "case_hunter":
        target = int(params.get("count", 10))
        return {"current": prog.get("cases", 0), "target": target,
                "completed": completed, "unit_key": "count"}

    if etype == "slots_addict":
        target = int(params.get("count", 30))
        return {"current": prog.get("plays", 0), "target": target,
                "completed": completed, "unit_key": "count"}

    if etype == "limbo_sniper":
        target = int(params.get("count", 5))
        return {"current": prog.get("big_wins", 0), "target": target,
                "completed": completed, "unit_key": "count"}

    if etype == "bj_natural_hunter":
        target = int(params.get("count", 3))
        return {"current": prog.get("naturals", 0), "target": target,
                "completed": completed, "unit_key": "count"}

    if etype == "case_big_spender":
        target = int(params.get("total_bet", 10000))
        return {"current": prog.get("total_spent", 0), "target": target,
                "completed": completed, "unit_key": "coins"}

    # Single-game events
    return {"current": 0, "target": 1, "completed": completed, "unit_key": "complete"}


# ── Reward calculation ──────────────────────────────────────────────────────────

def _calc_reward(event: dict, bet: int) -> int:
    reward = event.get("reward", {})
    rtype  = reward.get("type", "fixed")
    rvalue = float(reward.get("value", 0))
    return int(bet * rvalue) if rtype == "multiplier" else int(rvalue)


# ── Main processor ──────────────────────────────────────────────────────────────

def process_game_event(user_id, ctx: dict, player) -> list[dict]:
    """
    Process a completed game against all active events.

    ctx keys (all optional except 'game'):
      game, won, bet, mode,
      gems_found, mine_count, multiplier,  # mines
      combo,                               # crystals
      level_reached,                       # towers
      is_natural_bj,                       # blackjack
      multiplier_hit,                      # limbo
      multiplier,                          # slots / mines_greed

    Returns list of completed-event dicts:
      {"event": event_dict, "reward": int, "mode": str}
    """
    game = ctx.get("game")
    bet  = int(ctx.get("bet", 0))
    mode = ctx.get("mode", "real")
    uid  = str(user_id)

    if not game:
        return []

    active = get_active_events()
    completed_list: list[dict] = []

    for event in active.values():
        if event.get("game") != game:
            continue
        if int(event.get("min_bet", 0)) > bet:
            continue

        winners = event.get("winners", [])
        if uid in winners:
            continue

        max_w = event.get("max_winners")
        if max_w and len(winners) >= int(max_w):
            continue

        progress = get_user_event_progress(uid, event["id"])
        met, new_progress = _check_condition(event, ctx, progress)

        if met:
            reward      = _calc_reward(event, bet)
            reward_mode = event.get("reward_mode", "real")
            if reward > 0:
                player.add_balance(reward_mode, reward)

            all_events = get_all_events()
            if event["id"] in all_events:
                all_events[event["id"]].setdefault("winners", []).append(uid)
                save_all_events(all_events)

            _clear_user_event_progress(uid, event["id"])
            completed_list.append({"event": event, "reward": reward, "mode": reward_mode})
        else:
            _set_user_event_progress(uid, event["id"], new_progress)

    return completed_list
