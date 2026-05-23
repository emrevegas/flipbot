"""
Multi-table live blackjack — table registry, seating, phases, timers.

Rules (summary):
- Category holds table channels; 2 permanent main tables; overflow when full.
- Overflow tables deleted after 180s with no seated players.
- Max 3 seats/table; user at one table only; max 2 seats per user per table.
- Full = no empty seat for a new sitter.
- Kick if user holds 2 seats but did not bet on both before round start.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import discord

from modules.database import get_data, get_user_data, set_data, set_user_data


def get_user_saved_bet(user_id: int) -> int:
    """Last bet from games hub preferences (`{uid}/selected_bet`)."""
    games = get_data("server/games") or {}
    lb = games.get("live_blackjack", {}) if isinstance(games, dict) else {}
    mn = int(lb.get("min_bet", 10) or 10)
    mx = int(lb.get("max_bet", 10000) or 10000)
    prefs = get_data(f"{int(user_id)}/selected_bet") or {}
    try:
        bet = int(prefs.get("bet", mn))
    except (TypeError, ValueError):
        bet = mn
    return max(mn, min(mx, bet))


def save_user_saved_bet(user_id: int, bet: int) -> int:
    games = get_data("server/games") or {}
    lb = games.get("live_blackjack", {}) if isinstance(games, dict) else {}
    mn = int(lb.get("min_bet", 10) or 10)
    mx = int(lb.get("max_bet", 10000) or 10000)
    bet = max(mn, min(mx, int(bet)))
    prefs = get_data(f"{int(user_id)}/selected_bet") or {"bet": bet, "mode": "demo"}
    prefs["bet"] = bet
    set_data(f"{int(user_id)}/selected_bet", prefs)
    return bet


def apply_saved_bet_for_user(table: dict, user_id: int) -> None:
    """Apply persisted hub bet to all of the user's seats at this table."""
    uid = int(user_id)
    if count_user_seats(table, uid) == 0:
        return
    bet = get_user_saved_bet(uid)
    for seat in table.get("seats", []):
        if int(seat.get("user_id") or 0) == uid and not seat.get("bet_confirmed"):
            seat["pending_bet"] = bet
    touch_activity(table)

SEAT_COUNT = 3
MAX_SEATS_PER_USER = 2
EMPTY_DELETE_SECONDS = 180
COUNTDOWN_SECONDS = [15, 10, 5, "START"]
COUNTDOWN_GAP_DEFAULT = 5
COUNTDOWN_GAP_START = 2
TURN_TIMEOUT_SECONDS = 25
MAX_NO_CONFIRM_ROUNDS = 2

PHASE_WAITING = "waiting"
PHASE_COUNTDOWN = "countdown"
PHASE_PLAYING = "playing"
PHASE_SETTLING = "settling"


def get_settings() -> dict:
    data = get_data("server/live_blackjack") or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("category_id", None)
    data.setdefault("main_table_channels", [])  # [ch_id, ch_id] length 2
    data.setdefault("bet_options", [10, 25, 50, 100, 250, 500, 1000])
    return data


def save_settings(data: dict) -> None:
    set_data("server/live_blackjack", data)


def _tables_store() -> dict:
    data = get_data("server/live_blackjack_tables") or {}
    return data if isinstance(data, dict) else {}


def _save_tables(data: dict) -> None:
    set_data("server/live_blackjack_tables", data)


def _empty_seat() -> dict:
    return {
        "user_id": None,
        "bet": 0,
        "side_pp": 0,
        "side_21_3": 0,
        "bet_confirmed": False,
        "pending_bet": 0,
        "pending_side_pp": 0,
        "pending_side_21_3": 0,
    }


def new_table(
    *,
    table_id: str,
    channel_id: int,
    guild_id: int,
    is_main: bool,
    main_index: int | None = None,
) -> dict:
    now = int(time.time())
    return {
        "id": table_id,
        "channel_id": int(channel_id),
        "guild_id": int(guild_id),
        "message_id": None,
        "is_main": bool(is_main),
        "main_index": main_index,
        "phase": PHASE_WAITING,
        "countdown_announce": None,
        "countdown_next_at": 0,
        "last_empty_since": now,
        "last_activity": now,
        "seats": [_empty_seat() for _ in range(SEAT_COUNT)],
        "dealer": [],
        "deck": [],
        "round": None,
        "_no_confirm_streak": {},
        "status_flash": None,
        "created_at": now,
    }


def get_table(table_id: str) -> dict | None:
    t = _tables_store().get(table_id)
    return t if isinstance(t, dict) else None


def get_table_by_channel(channel_id: int) -> dict | None:
    for t in _tables_store().values():
        if isinstance(t, dict) and int(t.get("channel_id", 0)) == int(channel_id):
            return t
    return None


def save_table(table: dict) -> None:
    data = _tables_store()
    data[table["id"]] = table
    _save_tables(data)


def delete_table(table_id: str) -> None:
    data = _tables_store()
    data.pop(table_id, None)
    _save_tables(data)


def list_tables(guild_id: int | None = None) -> list[dict]:
    out = [t for t in _tables_store().values() if isinstance(t, dict)]
    if guild_id is not None:
        out = [t for t in out if int(t.get("guild_id", 0)) == int(guild_id)]
    return out


def user_table_id(user_id: int) -> str | None:
    raw = get_user_data(int(user_id), "live_bj_table")
    return str(raw) if raw else None


def set_user_table(user_id: int, table_id: str | None) -> None:
    set_user_data(int(user_id), "live_bj_table", table_id or "")


def count_user_seats(table: dict, user_id: int) -> int:
    uid = int(user_id)
    return sum(1 for s in table.get("seats", []) if int(s.get("user_id") or 0) == uid)


def has_empty_seat(table: dict) -> bool:
    return any(s.get("user_id") is None for s in table.get("seats", []))


def seated_user_ids(table: dict) -> set[int]:
    return {int(s["user_id"]) for s in table.get("seats", []) if s.get("user_id")}


def seated_count(table: dict) -> int:
    return len(seated_user_ids(table))


def _no_confirm_streak_map(table: dict) -> dict:
    raw = table.get("_no_confirm_streak")
    if not isinstance(raw, dict):
        table["_no_confirm_streak"] = {}
        return table["_no_confirm_streak"]
    return raw


def user_has_confirmed_bet(table: dict, user_id: int) -> bool:
    uid = int(user_id)
    for seat in table.get("seats", []):
        if int(seat.get("user_id") or 0) != uid:
            continue
        if seat.get("bet_confirmed") and int(seat.get("bet") or 0) > 0:
            return True
    return False


def any_confirmed_bets(table: dict) -> bool:
    return any(
        s.get("user_id")
        and s.get("bet_confirmed")
        and int(s.get("bet") or 0) > 0
        for s in table.get("seats", [])
    )


def _clear_seat_bets_keep_player(seat: dict) -> None:
    uid = seat.get("user_id")
    seat.clear()
    seat.update(_empty_seat())
    if uid is not None:
        seat["user_id"] = int(uid)


def _reset_seated_for_new_betting_round(table: dict) -> None:
    """Waiting phase: clear unconfirmed stakes, re-apply saved hub bet."""
    for seat in table.get("seats", []):
        if not seat.get("user_id"):
            continue
        _clear_seat_bets_keep_player(seat)
    for uid in dict.fromkeys(seated_user_ids(table)):
        apply_saved_bet_for_user(table, uid)


def _kick_users_no_confirm_streak(table: dict) -> list[int]:
    """Kick seated users who failed to confirm for MAX_NO_CONFIRM_ROUNDS rounds."""
    streak = _no_confirm_streak_map(table)
    kicked: list[int] = []
    for uid in list(seated_user_ids(table)):
        if user_has_confirmed_bet(table, uid):
            streak.pop(str(uid), None)
            continue
        n = int(streak.get(str(uid), 0) or 0) + 1
        streak[str(uid)] = n
        if n < MAX_NO_CONFIRM_ROUNDS:
            continue
        for i, seat in enumerate(table.get("seats", [])):
            if int(seat.get("user_id") or 0) == uid:
                table["seats"][i] = _empty_seat()
        set_user_table(uid, None)
        streak.pop(str(uid), None)
        kicked.append(uid)
    return kicked


def abort_round_no_confirms(table: dict) -> list[int]:
    """
    Countdown finished but nobody confirmed — reset to waiting, track streaks, kick AFK.
    Returns list of kicked user ids.
    """
    kicked_two = kick_two_seats_no_bet(table)
    kicked_afk = _kick_users_no_confirm_streak(table)
    kicked = list(dict.fromkeys(kicked_two + kicked_afk))
    table["round"] = None
    table["dealer"] = []
    table["deck"] = []
    table.pop("round_results", None)
    table.pop("result_display_until", None)
    table.pop("dealer_show_count", None)
    table.pop("dealer_hole_hidden", None)
    table.pop("_dealer_animating", None)
    table.pop("_deal_animating", None)
    _reset_seated_for_new_betting_round(table)
    table["phase"] = PHASE_WAITING
    table["countdown_announce"] = None
    table["countdown_next_at"] = 0
    touch_activity(table)
    if seated_count(table) >= 2:
        _maybe_start_countdown(table)
    if kicked:
        mentions = ", ".join(f"<@{u}>" for u in kicked)
        table["status_flash"] = (
            f"⛔ Removed {mentions} — no confirmed bet for **{MAX_NO_CONFIRM_ROUNDS}** rounds."
        )
    else:
        table["status_flash"] = "⚠️ Round cancelled — confirm your bet before the timer ends."
    return kicked


def find_table_with_empty_seat(guild_id: int) -> dict | None:
    """Prefer main tables, then oldest overflow."""
    tables = list_tables(guild_id)
    mains = sorted(
        [t for t in tables if t.get("is_main")],
        key=lambda x: int(x.get("main_index") or 0),
    )
    overflow = [t for t in tables if not t.get("is_main")]
    for t in mains + overflow:
        if has_empty_seat(t) and t.get("phase") in (PHASE_WAITING, PHASE_COUNTDOWN):
            return t
    return None


def all_tables_full(guild_id: int) -> bool:
    tables = list_tables(guild_id)
    if not tables:
        return True
    playable = [t for t in tables if t.get("phase") in (PHASE_WAITING, PHASE_COUNTDOWN)]
    if not playable:
        return True
    return all(not has_empty_seat(t) for t in playable)


def touch_activity(table: dict) -> None:
    table["last_activity"] = int(time.time())
    if seated_count(table) > 0:
        table["last_empty_since"] = 0
    elif not table.get("last_empty_since"):
        table["last_empty_since"] = int(time.time())


def sit_seat(table: dict, user_id: int, seat_idx: int) -> tuple[bool, str]:
    uid = int(user_id)
    if seat_idx < 0 or seat_idx >= SEAT_COUNT:
        return False, "Invalid seat."
    if table.get("phase") not in (PHASE_WAITING, PHASE_COUNTDOWN):
        return False, "Round in progress — wait for the next round."

    other = user_table_id(uid)
    if other and other != table["id"]:
        return False, "You are already at another table. Leave it first."

    if count_user_seats(table, uid) >= MAX_SEATS_PER_USER:
        return False, "You can occupy at most 2 seats at this table."

    seat = table["seats"][seat_idx]
    if seat.get("user_id") is not None:
        return False, "This seat is taken."

    if not has_empty_seat(table):
        return False, "Table is full."

    seat["user_id"] = uid
    set_user_table(uid, table["id"])
    apply_saved_bet_for_user(table, uid)
    touch_activity(table)
    _maybe_start_countdown(table)
    return True, ""


def leave_seat(table: dict, user_id: int, seat_idx: int) -> tuple[bool, str]:
    uid = int(user_id)
    if table.get("phase") == PHASE_PLAYING:
        return False, "Cannot leave during an active round."
    if seat_idx < 0 or seat_idx >= SEAT_COUNT:
        return False, "Invalid seat."
    seat = table["seats"][seat_idx]
    if int(seat.get("user_id") or 0) != uid:
        return False, "That is not your seat."
    table["seats"][seat_idx] = _empty_seat()
    if count_user_seats(table, uid) == 0:
        set_user_table(uid, None)
    touch_activity(table)
    if seated_count(table) < 2 and table.get("phase") == PHASE_COUNTDOWN:
        table["phase"] = PHASE_WAITING
        table["countdown_announce"] = None
        table["countdown_next_at"] = 0
    return True, ""


def set_pending_bets(
    table: dict,
    user_id: int,
    bet: int,
    side_pp: int = 0,
    side_21_3: int = 0,
) -> tuple[bool, str]:
    uid = int(user_id)
    if count_user_seats(table, uid) == 0:
        return False, "Sit at a seat before betting."
    for seat in table["seats"]:
        if int(seat.get("user_id") or 0) == uid:
            seat["pending_bet"] = int(bet)
            if side_pp:
                seat["pending_side_pp"] = int(side_pp)
            if side_21_3:
                seat["pending_side_21_3"] = int(side_21_3)
    touch_activity(table)
    return True, ""


def confirm_bets(table: dict, user_id: int) -> tuple[bool, str]:
    uid = int(user_id)
    seats = [s for s in table["seats"] if int(s.get("user_id") or 0) == uid]
    if not seats:
        return False, "Sit at a seat first."
    if table.get("phase") not in (PHASE_WAITING, PHASE_COUNTDOWN):
        return False, "Betting is closed."

    for seat in seats:
        bet = int(seat.get("pending_bet") or 0)
        if bet <= 0:
            return False, "Select a bet amount first."
        seat["bet"] = bet
        seat["side_pp"] = int(seat.get("pending_side_pp") or 0)
        seat["side_21_3"] = int(seat.get("pending_side_21_3") or 0)
        seat["bet_confirmed"] = True

    _no_confirm_streak_map(table).pop(str(uid), None)
    table["status_flash"] = None
    touch_activity(table)
    _maybe_start_single_player(table)
    if seated_count(table) >= 2 and table.get("phase") == PHASE_WAITING:
        _maybe_start_countdown(table)
    return True, ""


def _maybe_start_countdown(table: dict) -> None:
    if seated_count(table) >= 2 and table.get("phase") == PHASE_WAITING:
        table["phase"] = PHASE_COUNTDOWN
        table["countdown_announce"] = COUNTDOWN_SECONDS[0]
        table["countdown_next_at"] = int(time.time()) + COUNTDOWN_GAP_DEFAULT


def _maybe_start_single_player(table: dict) -> None:
    if seated_count(table) != 1:
        return
    if table.get("phase") != PHASE_WAITING:
        return
    uid = next(iter(seated_user_ids(table)))
    seats = [s for s in table["seats"] if int(s.get("user_id") or 0) == uid]
    if all(s.get("bet_confirmed") for s in seats):
        from Games import live_blackjack as lbj

        lbj.begin_round(table)


def kick_two_seats_no_bet(table: dict) -> list[int]:
    """Remove users who took 2 seats but did not confirm bet on both."""
    kicked: list[int] = []
    by_user: dict[int, list[int]] = {}
    for i, s in enumerate(table.get("seats", [])):
        uid = s.get("user_id")
        if uid:
            by_user.setdefault(int(uid), []).append(i)
    for uid, idxs in by_user.items():
        if len(idxs) < 2:
            continue
        confirmed = sum(1 for i in idxs if table["seats"][i].get("bet_confirmed"))
        if confirmed < len(idxs):
            for i in idxs:
                table["seats"][i] = _empty_seat()
            set_user_table(uid, None)
            kicked.append(uid)
    return kicked


def reset_seats_after_round(table: dict) -> None:
    seated_uids: list[int] = []
    for seat in table.get("seats", []):
        uid = seat.get("user_id")
        seat.clear()
        seat.update(_empty_seat())
        if uid is not None:
            seat["user_id"] = int(uid)
            seated_uids.append(int(uid))
    for uid in dict.fromkeys(seated_uids):
        apply_saved_bet_for_user(table, uid)
    table["dealer"] = []
    table["deck"] = []
    table["round"] = None
    table["phase"] = PHASE_WAITING
    table["countdown_announce"] = None
    table["countdown_next_at"] = 0
    table.pop("round_results", None)
    table.pop("result_display_until", None)
    table.pop("dealer_show_count", None)
    table.pop("dealer_hole_hidden", None)
    table.pop("_dealer_animating", None)
    table.pop("_deal_animating", None)
    touch_activity(table)
    if seated_count(table) >= 2:
        _maybe_start_countdown(table)


def tick_countdown(table: dict) -> str | None:
    """Advance countdown; returns announcement text if changed."""
    if table.get("phase") != PHASE_COUNTDOWN:
        return None
    now = int(time.time())
    if now < int(table.get("countdown_next_at") or 0):
        return None
    cur = table.get("countdown_announce")
    try:
        idx = COUNTDOWN_SECONDS.index(cur) if cur is not None else -1
    except ValueError:
        if str(cur).strip() in ("4", "3", "2", "1"):
            idx = COUNTDOWN_SECONDS.index(5)
        else:
            idx = -1
    nxt = idx + 1
    if nxt >= len(COUNTDOWN_SECONDS):
        from Games import live_blackjack as lbj

        if lbj.begin_round(table):
            return "GO"
        return "RESET"
    table["countdown_announce"] = COUNTDOWN_SECONDS[nxt]
    step = COUNTDOWN_SECONDS[nxt]
    gap = COUNTDOWN_GAP_START if step == "START" else COUNTDOWN_GAP_DEFAULT
    table["countdown_next_at"] = now + gap
    return str(step)


def should_delete_overflow(table: dict) -> bool:
    if table.get("is_main"):
        return False
    if seated_count(table) > 0:
        return False
    since = int(table.get("last_empty_since") or 0)
    if not since:
        return False
    return int(time.time()) - since >= EMPTY_DELETE_SECONDS


async def create_main_tables(
    guild: discord.Guild,
    bot: discord.Client,
) -> tuple[bool, str, list[str]]:
    """
    Create the two permanent main table channels and table messages.
    Returns (success, error_message, channel_mentions).
    """
    from modules.database import set_data, get_data
    from modules.live_blackjack_v2 import build_table_layout

    settings = get_settings()
    cat_id = settings.get("category_id")
    if not cat_id:
        return False, "Set the Live Blackjack category first.", []
    category = guild.get_channel(int(cat_id))
    if not category:
        return False, "Category not found — set it again.", []

    games = get_data("server/games") or {}
    if not isinstance(games, dict):
        games = {}
    lb = games.get("live_blackjack")
    if not isinstance(lb, dict):
        lb = {
            "name": "Live Blackjack",
            "emoji": "🃏",
            "enabled": True,
            "min_bet": 10,
            "max_bet": 10000,
            "rigged_chance": 0.0,
        }
        games["live_blackjack"] = lb
        set_data("server/games", games)

    created: list[str] = []
    for idx in range(2):
        name = f"live-blackjack-{idx + 1}"
        ch = await guild.create_text_channel(
            name=name,
            category=category,
            reason="Live BJ main table",
        )
        tid = f"main_{idx + 1}"
        table = new_table(
            table_id=tid,
            channel_id=ch.id,
            guild_id=guild.id,
            is_main=True,
            main_index=idx,
        )
        msg = await ch.send(view=build_table_layout(table, bot=bot))
        table["message_id"] = msg.id
        save_table(table)
        register_main_table(tid, ch.id, idx)
        created.append(ch.mention)
    return True, "", created


def register_main_table(table_id: str, channel_id: int, main_index: int) -> None:
    settings = get_settings()
    mains = list(settings.get("main_table_channels") or [])
    while len(mains) <= main_index:
        mains.append(None)
    mains[main_index] = int(channel_id)
    settings["main_table_channels"] = mains[:2]
    save_settings(settings)


def get_game_settings() -> dict:
    games = get_data("server/games") or {}
    lb = games.get("live_blackjack", {}) if isinstance(games, dict) else {}
    if not isinstance(lb, dict):
        lb = {}
    min_bet = int(lb.get("min_bet", 10) or 10)
    max_bet = int(lb.get("max_bet", 10000) or 10000)
    try:
        rigged = float(lb.get("rigged_chance", 0) or 0)
    except (TypeError, ValueError):
        rigged = 0.0
    rigged = max(0.0, min(100.0, rigged))
    return {
        "min_bet": min_bet,
        "max_bet": max_bet,
        "rigged_chance": rigged,
        "enabled": bool(lb.get("enabled", True)),
    }
