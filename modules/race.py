"""
Race Engine — Wager Race & Deposit Race system for Vegas Bot.

Race types:
  wager   → tracks total coins wagered during the race period
  deposit → tracks total coins deposited during the race period

Data layout:
  server/active_races   → dict of {race_id: race_dict} for all running races
  server/race_history   → list of past completed races
"""
import time
from modules.database import get_data, set_data, replace_data

_ACTIVE_KEY = "server/active_races"


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_active_races() -> dict:
    """Return dict of {race_id: race_dict} for all currently active races."""
    data = get_data(_ACTIVE_KEY) or {}
    if not isinstance(data, dict):
        return {}
    return {rid: r for rid, r in data.items() if isinstance(r, dict) and r.get("status") == "active"}


def _save_all_races(races: dict):
    replace_data(_ACTIVE_KEY, races)


def get_active_race() -> dict:
    """Legacy helper: return the first active race or {}."""
    races = get_active_races()
    if not races:
        return {}
    return next(iter(races.values()))


def has_active_race() -> bool:
    return bool(get_active_races())


def is_race_expired(race: dict) -> bool:
    return int(time.time()) > int(race.get("ends_at", 0))


# ── Lifecycle ──────────────────────────────────────────────────────────────────

def create_race(
    *,
    race_type: str,           # "wager" | "deposit"
    period: str,              # "daily" | "weekly" | "monthly"
    name: str,
    channel_id: int,
    prizes: dict,             # {"1": amount, "2": amount, ...}
    duration_hours: int = 0,  # 0 = auto-derive from period
) -> tuple[bool, str, str]:
    """Create a new race. Returns (success, error_message, race_id)."""
    if race_type not in ("wager", "deposit"):
        return False, "Type must be 'wager' or 'deposit'.", ""
    if period not in ("daily", "weekly", "monthly"):
        return False, "Period must be 'daily', 'weekly', or 'monthly'.", ""
    if not name.strip():
        return False, "Race name cannot be empty.", ""
    if not channel_id:
        return False, "Channel ID is required.", ""

    if duration_hours <= 0:
        duration_hours = {"daily": 24, "weekly": 168, "monthly": 720}[period]

    clean_prizes = {}
    for k, v in prizes.items():
        try:
            clean_prizes[str(int(k))] = int(v)
        except (ValueError, TypeError):
            pass

    now = int(time.time())
    race_id = str(int(now * 1000))
    race = {
        "race_id": race_id,
        "type": race_type,
        "period": period,
        "name": name.strip(),
        "channel_id": int(channel_id),
        "message_id": None,
        "prizes": clean_prizes,
        "starts_at": now,
        "ends_at": now + duration_hours * 3600,
        "status": "active",
        "entries": {},   # user_id_str → total amount
    }
    all_races = get_data(_ACTIVE_KEY) or {}
    if not isinstance(all_races, dict):
        all_races = {}
    all_races[race_id] = race
    _save_all_races(all_races)
    return True, "", race_id


def add_entry(user_id, amount: int, race_type: str | None = None) -> bool:
    """
    Add amount to all active races matching the given race_type.
    If race_type is None, adds to every active non-expired race.
    Returns True if at least one race was updated.
    """
    all_races = get_data(_ACTIVE_KEY) or {}
    if not isinstance(all_races, dict):
        return False
    now = int(time.time())
    updated = False
    for rid, race in all_races.items():
        if race.get("status") != "active":
            continue
        if now > int(race.get("ends_at", 0)):
            continue
        if race_type and race.get("type") != race_type:
            continue
        uid = str(user_id)
        entries = race.get("entries", {})
        entries[uid] = int(entries.get(uid, 0)) + max(0, int(amount))
        race["entries"] = entries
        updated = True
    if updated:
        _save_all_races(all_races)
    return updated


def set_message_id(race_id: str, message_id: int):
    """Store the leaderboard message ID in a specific active race."""
    all_races = get_data(_ACTIVE_KEY) or {}
    if not isinstance(all_races, dict):
        return
    if race_id in all_races:
        all_races[race_id]["message_id"] = int(message_id)
        _save_all_races(all_races)


def end_race(race_id: str) -> dict | None:
    """
    Mark the given race as ended, archive it, remove from active.
    Returns the final race dict, or None if not found.
    """
    all_races = get_data(_ACTIVE_KEY) or {}
    if not isinstance(all_races, dict):
        return None
    race = all_races.pop(race_id, None)
    if not race:
        return None

    race["status"] = "ended"
    race["ended_at"] = int(time.time())

    # Archive
    history = get_data("server/race_history") or []
    if not isinstance(history, list):
        history = []
    history.append(race)
    set_data("server/race_history", history)

    # Save remaining races
    _save_all_races(all_races)
    return race


def get_leaderboard(race: dict, top_n: int = 10) -> list[tuple[str, int]]:
    """Return sorted [(user_id_str, amount)] list."""
    entries = race.get("entries", {})
    sorted_entries = sorted(entries.items(), key=lambda x: x[1], reverse=True)
    return sorted_entries[:top_n]


def get_race_history() -> list:
    data = get_data("server/race_history") or []
    return data if isinstance(data, list) else []
