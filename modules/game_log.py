"""Unified short game result logs (one channel, description-only embeds)."""

from __future__ import annotations

from typing import Optional, Union

import discord

from modules.database import get_server_data
from modules.utils import format_balance

_RESULT_COLORS = {
    "win": 0x57F287,
    "lose": 0xED4245,
    "tie": 0xFEE75C,
}


def _get_log_channel_id(guild_id: Union[int, str]) -> Optional[int]:
    server_data = get_server_data(str(guild_id))
    ch_id = server_data.get("game_log_channel") or server_data.get("pf_log_channel")
    return int(ch_id) if ch_id else None


def _resolve_client(
    user: discord.abc.User,
    log_message: Optional[discord.Message] = None,
    client: Optional[discord.Client] = None,
) -> Optional[discord.Client]:
    if client is not None:
        return client
    if log_message is not None and log_message.channel:
        try:
            return log_message.channel._state._parent
        except Exception:
            pass
    if isinstance(user, discord.Member) and user.guild:
        try:
            return user.guild._state._parent
        except Exception:
            pass
    try:
        return user._state._parent
    except Exception:
        pass
    return None


def _resolve_guild_id(
    user: discord.abc.User,
    log_message: Optional[discord.Message],
    guild_id: Optional[Union[int, str]],
    channel_id: Optional[int] = None,
    client: Optional[discord.Client] = None,
) -> Optional[int]:
    if guild_id is not None:
        return int(guild_id)
    if isinstance(user, discord.Member) and user.guild:
        return user.guild.id
    if log_message and log_message.guild:
        return log_message.guild.id
    if channel_id and client:
        ch = client.get_channel(int(channel_id))
        if ch and getattr(ch, "guild", None):
            return ch.guild.id
    return None


def _format_profit_text(result: str, profit: int, mode: str) -> str:
    if result == "tie":
        return format_balance(0, mode)
    if result == "win":
        return f"+{format_balance(abs(profit), mode)}"
    return f"-{format_balance(abs(profit), mode)}"


async def post_short_game_log(
    user: discord.abc.User,
    game_name: str,
    result: str,
    profit: int,
    mode: str,
    *,
    log_message: Optional[discord.Message] = None,
    guild_id: Optional[Union[int, str]] = None,
    client: Optional[discord.Client] = None,
    channel_id: Optional[int] = None,
    skip: bool = False,
) -> None:
    """
    Post: @user **win** +$X at **Game** (description-only embed, English).
    Only win / tie / lose. Set skip=True to suppress (e.g. bot case battles).
    """
    if skip:
        return

    if (mode or "").lower() != "real":
        return

    result = (result or "").lower()
    if result == "push":
        result = "tie"
    if result not in _RESULT_COLORS:
        return

    client = _resolve_client(user, log_message, client)
    if client is None:
        return

    resolved_guild = _resolve_guild_id(user, log_message, guild_id, channel_id, client)
    if resolved_guild is None:
        return

    ch_id = _get_log_channel_id(resolved_guild)
    if not ch_id:
        return

    channel = client.get_channel(ch_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(ch_id)
        except Exception:
            return

    amount = _format_profit_text(result, profit, mode)
    desc = f"{user.mention} **{result}** {amount} at **{game_name}**"
    embed = discord.Embed(description=desc, color=_RESULT_COLORS[result])

    try:
        await channel.send(embed=embed)
    except Exception:
        pass
