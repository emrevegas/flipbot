"""Jackpot room settings and per-channel round state."""

from __future__ import annotations

import time
from typing import Any

from modules.database import get_data, set_data

SETTINGS_KEY = "server/jackpot"
ROUNDS_KEY = "server/jackpot_rounds"

COUNTDOWN_BASE_SEC = 30
COUNTDOWN_EXTEND_SEC = 15
MIN_PLAYERS = 2
MAX_PLAYERS = 24


def get_settings() -> dict:
    data = get_data(SETTINGS_KEY) or {}
    if not isinstance(data, dict):
        data = {}
    return data


def save_settings(data: dict) -> None:
    set_data(SETTINGS_KEY, data)


def get_channel_id() -> int | None:
    cid = get_settings().get("channel_id")
    return int(cid) if cid else None


def is_jackpot_channel(channel_id: int) -> bool:
    jc = get_channel_id()
    return jc is not None and int(channel_id) == jc


def _rounds() -> dict:
    data = get_data(ROUNDS_KEY) or {}
    return data if isinstance(data, dict) else {}


def _save_rounds(data: dict) -> None:
    set_data(ROUNDS_KEY, data)


def get_round(channel_id: int | str) -> dict | None:
    r = _rounds().get(str(channel_id))
    return r if isinstance(r, dict) else None


def set_round(channel_id: int | str, round_data: dict | None) -> None:
    all_r = _rounds()
    key = str(channel_id)
    if round_data is None:
        all_r.pop(key, None)
    else:
        all_r[key] = round_data
    _save_rounds(all_r)


def pool_total(players: list[dict]) -> float:
    return sum(max(0.0, float(p.get("bet") or 0)) for p in players)


def new_round(channel_id: int) -> dict:
    now = int(time.time())
    return {
        "channel_id": str(channel_id),
        "status": "waiting",
        "message_id": None,
        "menu_message_id": None,
        "players": [],
        "countdown_ends": 0,
        "countdown_secs": COUNTDOWN_BASE_SEC,
        "winner_id": None,
        "winner_message_id": None,
        "preserve_message_ids": [],
        "created_at": now,
    }


def can_cancel(round_data: dict) -> bool:
    return round_data.get("status") in ("waiting", "countdown")


def player_in_round(round_data: dict, user_id: int) -> bool:
    return any(int(p.get("user_id", 0)) == int(user_id) for p in round_data.get("players", []))
