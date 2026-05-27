"""Moderation action feed — all moderator / staff panel actions."""

from __future__ import annotations

import discord

from modules.database import get_server_data, set_server_data


def get_moderation_log_channel_id(guild_id: int | str | None) -> int | None:
    if not guild_id:
        return None
    data = get_server_data(str(guild_id)) or {}
    ch = data.get("moderation_log_channel_id")
    return int(ch) if ch else None


def set_moderation_log_channel(guild_id: int | str, channel_id: int | None) -> None:
    data = get_server_data(str(guild_id)) or {}
    if channel_id:
        data["moderation_log_channel_id"] = int(channel_id)
    else:
        data.pop("moderation_log_channel_id", None)
    set_server_data(str(guild_id), data)


async def _resolve_channel(
    bot: discord.Client,
    guild_id: int,
    channel_id: int,
) -> discord.TextChannel | None:
    guild = bot.get_guild(guild_id)
    if guild is None:
        return None
    ch = guild.get_channel(channel_id)
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        ch = await bot.fetch_channel(channel_id)
        return ch if isinstance(ch, discord.TextChannel) else None
    except Exception:
        return None


async def log_moderation(
    bot: discord.Client,
    guild: discord.Guild | None,
    *,
    actor_id: int,
    action: str,
    target_user_id: int | None = None,
    details: str = "",
    color: int = 0xE67E22,
) -> None:
    """Post a moderation log embed (no-op if channel not configured)."""
    if guild is None:
        return
    ch_id = get_moderation_log_channel_id(guild.id)
    if not ch_id:
        return
    channel = await _resolve_channel(bot, guild.id, ch_id)
    if channel is None:
        return

    actor_mention = f"<@{actor_id}>"
    embed = discord.Embed(
        title="🛡️ Moderation Log",
        description=action[:4000],
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Staff", value=f"{actor_mention}\n`{actor_id}`", inline=True)
    if target_user_id is not None:
        embed.add_field(
            name="Target user",
            value=f"<@{target_user_id}>\n`{target_user_id}`",
            inline=True,
        )
    if details:
        embed.add_field(name="Details", value=details[:1024], inline=False)
    embed.set_footer(text="Moderation audit")

    try:
        await channel.send(embed=embed)
    except Exception as exc:
        print(f"[ModerationLog] send failed: {exc}")
