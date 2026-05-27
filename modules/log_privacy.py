"""Public vs anonymous name on deposit / withdraw feed logs."""

from __future__ import annotations

import discord

from modules.database import get_user_data, set_user_data


def get_privacy_mode(user_id: int | str) -> str:
    data = get_user_data(int(user_id), "privacy") or {}
    if not isinstance(data, dict):
        return "anonymous"
    mode = str(data.get("mode", "anonymous")).strip().lower()
    return "public" if mode == "public" else "anonymous"


def set_privacy_mode(user_id: int | str, mode: str) -> str:
    mode = "public" if str(mode).strip().lower() == "public" else "anonymous"
    set_user_data(int(user_id), "privacy", {"mode": mode})
    return mode


def log_display_name(
    user_id: int | str,
    member: discord.Member | discord.User | None = None,
) -> str:
    if get_privacy_mode(user_id) == "public":
        if member is not None:
            return member.display_name or member.name or "User"
        return "User"
    return "Anonymous"
