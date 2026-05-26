"""Unified game result logs — game log channel (Panel → Game Log)."""

from __future__ import annotations

from typing import Optional, Union

import discord

from modules.database import get_data, get_server_data
from modules.flip_utils import fmt_pts

_RESULT_COLORS = {
    "win": 0x57F287,
    "lose": 0xED4245,
    "tie": 0xFEE75C,
}

_GAME_DEFAULT_NAMES = {
    "coinflip": "Coinflip",
    "dice": "Dice",
    "roulette": "Roulette",
    "htw": "HTW",
    "mines": "Mines",
    "hilo": "Hi-Lo",
    "blackjack": "Blackjack",
    "limbo": "Limbo",
    "slide": "Slide",
    "jackpot": "Jackpot",
    "slot": "Slots",
    "slots": "Slots",
    "towers": "Towers",
    "crystals": "Crystals",
    "chicken_road": "Chicken Road",
    "case_opening": "Case Opening",
    "case_battle": "Case Battle",
    "live_blackjack": "Live Blackjack",
}


def get_game_display_name(game_id: str) -> str:
    gid = (game_id or "").strip().lower()
    games = get_data("server/games") or {}
    entry = games.get(gid) if isinstance(games, dict) else None
    if isinstance(entry, dict):
        name = entry.get("name")
        if name:
            return str(name)
    return _GAME_DEFAULT_NAMES.get(gid, gid.replace("_", " ").title() or "Game")


def _get_log_channel_id(guild_id: Union[int, str]) -> Optional[int]:
    server_data = get_server_data(str(guild_id))
    ch_id = server_data.get("game_log_channel") or server_data.get("pf_log_channel")
    return int(ch_id) if ch_id else None


def _resolve_client(
    client: Optional[discord.Client],
    user: Optional[discord.abc.User] = None,
) -> Optional[discord.Client]:
    if client is not None:
        return client
    if isinstance(user, discord.Member) and user.guild:
        try:
            return user.guild._state._parent
        except Exception:
            pass
    return None


def _resolve_guild_id(
    guild_id: Optional[Union[int, str]],
    user: Optional[discord.abc.User] = None,
    client: Optional[discord.Client] = None,
) -> Optional[int]:
    if guild_id is not None:
        return int(guild_id)
    if isinstance(user, discord.Member) and user.guild:
        return user.guild.id
    if client and client.guilds:
        return client.guilds[0].id
    return None


async def _resolve_user(
    client: Optional[discord.Client],
    user_id: Union[int, str],
    user: Optional[discord.abc.User] = None,
) -> discord.abc.User:
    if user is not None:
        return user
    uid = int(user_id)
    if client:
        u = client.get_user(uid)
        if u:
            return u
        try:
            return await client.fetch_user(uid)
        except Exception:
            pass
    return discord.Object(id=uid)


async def _send_log_embed(
    client: discord.Client,
    guild_id: int,
    description: str,
    result: str,
) -> None:
    ch_id = _get_log_channel_id(guild_id)
    if not ch_id:
        return
    channel = client.get_channel(ch_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(ch_id)
        except Exception:
            return
    color = _RESULT_COLORS.get(result, 0x5865F2)
    embed = discord.Embed(description=description, color=color)
    try:
        await channel.send(embed=embed)
    except Exception:
        pass


async def post_solo_game_log(
    *,
    user_id: Union[int, str],
    game_id: str,
    bet: float,
    won: bool,
    payout: float = 0.0,
    user: Optional[discord.abc.User] = None,
    client: Optional[discord.Client] = None,
    guild_id: Optional[Union[int, str]] = None,
    mode: str = "real",
    tie: bool = False,
    skip: bool = False,
) -> None:
    """
    @user bet X pts to Game and wins Y pts | loses | ties
    """
    if skip or (mode or "").lower() != "real":
        return

    client = _resolve_client(client, user)
    if client is None:
        return

    resolved_guild = _resolve_guild_id(guild_id, user, client)
    if resolved_guild is None:
        return

    u = await _resolve_user(client, user_id, user)
    game = get_game_display_name(game_id)
    bet_s = fmt_pts(bet)

    if tie:
        desc = f"{u.mention} bet **{bet_s} pts** to **{game}** and ties"
        result = "tie"
    elif won:
        pay_s = fmt_pts(payout)
        desc = f"{u.mention} bet **{bet_s} pts** to **{game}** and wins **{pay_s} pts**"
        result = "win"
    else:
        desc = f"{u.mention} bet **{bet_s} pts** to **{game}** and loses"
        result = "lose"

    await _send_log_embed(client, resolved_guild, desc, result)


async def post_pvp_game_log(
    *,
    player_a: discord.abc.User,
    player_b: discord.abc.User,
    game_id: str,
    winner: Optional[discord.abc.User],
    payout: float,
    bet: float,
    client: Optional[discord.Client] = None,
    guild_id: Optional[Union[int, str]] = None,
    mode: str = "real",
    skip: bool = False,
) -> None:
    """
    @user versus @user2 plays Game and @winner wins (payout) | ties
    """
    if skip or (mode or "").lower() != "real":
        return

    client = _resolve_client(client, player_a)
    if client is None:
        return

    resolved_guild = _resolve_guild_id(guild_id, player_a, client)
    if resolved_guild is None:
        return

    game = get_game_display_name(game_id)
    if winner is None:
        desc = (
            f"{player_a.mention} versus {player_b.mention} "
            f"plays **{game}** and ties"
        )
        result = "tie"
    else:
        pay_s = fmt_pts(payout)
        desc = (
            f"{player_a.mention} versus {player_b.mention} "
            f"plays **{game}** and {winner.mention} wins **{pay_s} pts**"
        )
        result = "win"

    await _send_log_embed(client, resolved_guild, desc, result)


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
    bet: int = 0,
) -> None:
    """Backward-compatible wrapper (Case Battle, provably_fair)."""
    if skip:
        return
    res = (result or "").lower()
    if res == "push":
        res = "tie"
    won = res == "win"
    tie = res == "tie"
    gid = game_name.lower().replace(" ", "_").replace("-", "_")
    await post_solo_game_log(
        user_id=user.id,
        game_id=gid if gid in _GAME_DEFAULT_NAMES else "case_battle",
        bet=float(bet) if bet else abs(profit),
        won=won,
        payout=float(profit) if won and profit > 0 else 0.0,
        user=user,
        client=client or _resolve_client(None, user),
        guild_id=guild_id,
        mode=mode,
        tie=tie,
    )
