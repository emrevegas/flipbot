"""Live Stats Tracker — shared tracking helpers.

Called from Games/base_game.py, modules/player.py.
Stores data in:
  server/daily_stats        — resets at midnight each day
  server/live_stats_records — all-time records (biggest win ever)
  server/live_stats_totals  — running totals that don't fit in user_stats SQL schema
                              (e.g. total_withdraw)
"""

import time
from modules.database import get_data, replace_data

_DAILY_KEY   = "server/daily_stats"
_RECORDS_KEY = "server/live_stats_records"
_TOTALS_KEY  = "server/live_stats_totals"


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


# ── Daily stats ────────────────────────────────────────────────────────────────

def get_daily_stats() -> dict:
    """Return today's daily stats dict, resetting it if the stored date differs."""
    data = get_data(_DAILY_KEY) or {}
    if not isinstance(data, dict) or data.get("date") != _today_str():
        data = {"date": _today_str()}
        replace_data(_DAILY_KEY, data)
    return data


def _save_daily(data: dict) -> None:
    replace_data(_DAILY_KEY, data)


def update_daily_game(user_id: str, game_id: str, bet: int, result: str, profit: int) -> None:
    """Called from base_game.handle_result() for every tracked real-money play."""
    data = get_daily_stats()

    data["games_played"] = int(data.get("games_played", 0)) + 1
    data["wagered"]      = int(data.get("wagered", 0)) + int(bet)

    if result == "win":
        data["wins"]   = int(data.get("wins", 0)) + 1
    elif result == "lose":
        data["losses"] = int(data.get("losses", 0)) + 1

    # Per-game play counts
    gc = data.get("game_counts") or {}
    gc[game_id] = int(gc.get(game_id, 0)) + 1
    data["game_counts"] = gc

    # Most active user (plays today)
    ma = data.get("most_active") or {}
    ma[str(user_id)] = int(ma.get(str(user_id), 0)) + 1
    data["most_active"] = ma

    # Biggest win today
    if result == "win" and profit > 0:
        bw = data.get("biggest_win") or {}
        if profit > int(bw.get("amount", 0)):
            data["biggest_win"] = {
                "amount":  profit,
                "user_id": str(user_id),
                "game":    game_id,
            }

    _save_daily(data)

    # Update all-time record too
    if result == "win" and profit > 0:
        _update_record_win(str(user_id), game_id, profit)


def update_daily_deposit(user_id: str, amount: int) -> None:
    """Called from player.record_deposit() after a confirmed deposit."""
    data = get_daily_stats()
    data["deposit"] = int(data.get("deposit", 0)) + int(amount)
    _save_daily(data)


def update_daily_withdraw(user_id: str, amount: int) -> None:
    """Called from player.record_withdraw() after a withdrawal is submitted."""
    data = get_daily_stats()
    data["withdraw"] = int(data.get("withdraw", 0)) + int(amount)
    _save_daily(data)


# ── All-time records ───────────────────────────────────────────────────────────

def _update_record_win(user_id: str, game_id: str, profit: int) -> None:
    records = get_data(_RECORDS_KEY) or {}
    bw = records.get("biggest_win") or {}
    if int(profit) > int(bw.get("amount", 0)):
        records["biggest_win"] = {
            "amount":    int(profit),
            "user_id":   user_id,
            "game":      game_id,
            "timestamp": int(time.time()),
        }
        replace_data(_RECORDS_KEY, records)


def get_records() -> dict:
    return get_data(_RECORDS_KEY) or {}


def get_totals() -> dict:
    return get_data(_TOTALS_KEY) or {}
