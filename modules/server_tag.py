"""Discord Server Tag (primary guild) requirement for rewards."""

from __future__ import annotations

import discord

from modules.database import get_data, set_data

SETTINGS_KEY = "server/reward_requirements"


def get_settings() -> dict:
    data = get_data(SETTINGS_KEY)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("require_server_tag", True)
    return data


def save_settings(data: dict) -> None:
    set_data(SETTINGS_KEY, data)


def require_server_tag_enabled() -> bool:
    return bool(get_settings().get("require_server_tag", True))


def set_require_server_tag(enabled: bool) -> None:
    cfg = get_settings()
    cfg["require_server_tag"] = bool(enabled)
    save_settings(cfg)


def member_has_server_tag(member: discord.Member | None, guild: discord.Guild | None) -> bool:
    """True if member displays this guild's Server Tag on their profile."""
    if member is None or guild is None:
        return False
    pg = getattr(member, "primary_guild", None)
    if pg is None:
        return False
    if not getattr(pg, "identity_enabled", False):
        return False
    guild_id = getattr(pg, "id", None)
    try:
        return int(guild_id or 0) == int(guild.id)
    except (TypeError, ValueError):
        return False


def server_tag_error(guild: discord.Guild | None) -> str:
    name = guild.name if guild else "this server"
    return (
        f"You must equip **{name}**'s **Server Tag** on your Discord profile.\n"
        "Profile → **Edit Profile** → **Server Tag** → select this server, then try again."
    )


async def check_server_tag(
    member: discord.Member | None,
    guild: discord.Guild | None,
    user_id: int,
) -> tuple[bool, str]:
    if not require_server_tag_enabled() or guild is None:
        return True, ""
    if isinstance(member, discord.Member) and member.guild.id == guild.id:
        if member_has_server_tag(member, guild):
            return True, ""
    try:
        fetched = await guild.fetch_member(int(user_id))
        if member_has_server_tag(fetched, guild):
            return True, ""
    except Exception:
        pass
    return False, server_tag_error(guild)
