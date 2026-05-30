"""Play-channel hub helpers for private rooms (deposit gate, routing, setup)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from modules.database import get_data, get_server_data, set_server_data
from modules.translator import t

if TYPE_CHECKING:
    from discord.ext import commands

DEPOSIT_WINDOW_DAYS = 2
PLAY_CHANNEL_NAMES = [f"play-{i}" for i in range(1, 6)]
DEFAULT_PLAY_CATEGORY_NAME = "🎮 Play Rooms"


def get_play_channel_ids(guild_id: str) -> list[int]:
    raw = get_server_data(guild_id).get("play_channel_ids") or []
    out: list[int] = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    return out


def is_play_hub_channel(guild_id: str, channel_id: int | str) -> bool:
    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return False
    return cid in get_play_channel_ids(guild_id)


def deposit_gate_message(user_id: int, *, lang: str = "en") -> str | None:
    """None if allowed; otherwise localized error string."""
    import modules.promo as promo_engine

    if promo_engine.user_has_deposit_within_days(user_id, DEPOSIT_WINDOW_DAYS):
        return None
    return t(
        "private_rooms.deposit_required",
        lang=lang,
        user_id=str(user_id),
        days=DEPOSIT_WINDOW_DAYS,
    )


def find_owner_room(guild_id: str, owner_id: int) -> tuple[str | None, dict | None]:
    rooms_data = get_data("server/private_rooms") or {}
    guild_rooms = rooms_data.get(guild_id) or {}
    for ch_id, room_info in guild_rooms.items():
        if int(room_info.get("owner") or 0) == int(owner_id):
            return str(ch_id), room_info
    return None, None


@dataclass
class OwnerRoomContext:
    guild_id: str
    channel_id: str
    channel: discord.TextChannel | None
    is_play_hub: bool
    room_info: dict


def owner_room_for_interaction(
    interaction: discord.Interaction,
    *,
    require_room: bool = False,
    require_private_room: bool = False,
    lang: str = "en",
) -> tuple[OwnerRoomContext | None, str | None]:
    """
    Resolve where hub/private-room actions run.

    Play hub (play-1..5): open to everyone; games/finance run in the play channel.
    require_private_room: room management only — needs an owned private room (depositors).
    Private room channels: owner-only; no play-hub deposit gate on menu use.
    """
    if not interaction.guild or not interaction.channel:
        return None, "❌ This can only be used in a server."

    user_id = interaction.user.id
    guild_id = str(interaction.guild.id)
    channel = interaction.channel
    if isinstance(channel, discord.Thread):
        guard_channel = interaction.guild.get_channel(channel.parent_id)
        ch_id = str(channel.parent_id)
    else:
        guard_channel = channel
        ch_id = str(channel.id)
    is_hub = is_play_hub_channel(guild_id, ch_id)

    if is_hub:
        room_ch_id, room_info = find_owner_room(guild_id, user_id)
        if require_private_room or require_room:
            if not room_ch_id:
                return None, t(
                    "private_rooms.no_room_for_management",
                    lang=lang,
                    user_id=str(user_id),
                )
            channel = interaction.guild.get_channel(int(room_ch_id))
            if not channel or not isinstance(channel, discord.TextChannel):
                return None, t(
                    "private_rooms.no_room_for_management",
                    lang=lang,
                    user_id=str(user_id),
                )
            return (
                OwnerRoomContext(
                    guild_id=guild_id,
                    channel_id=room_ch_id,
                    channel=channel,
                    is_play_hub=True,
                    room_info=room_info or {},
                ),
                None,
            )
        play_ch = guard_channel
        if not isinstance(play_ch, discord.TextChannel):
            return None, "❌ Invalid channel."
        return (
            OwnerRoomContext(
                guild_id=guild_id,
                channel_id=ch_id,
                channel=play_ch,
                is_play_hub=True,
                room_info=room_info or {},
            ),
            None,
        )

    rooms_data = get_data("server/private_rooms") or {}
    guild_rooms = rooms_data.get(guild_id) or {}
    room_info = guild_rooms.get(ch_id)
    if not room_info or int(room_info.get("owner") or 0) != user_id:
        return None, "❌ Only the room owner can use this menu!"

    if not isinstance(guard_channel, discord.TextChannel):
        return None, t("private_rooms.no_room_for_hub", lang=lang, user_id=str(user_id))

    return (
        OwnerRoomContext(
            guild_id=guild_id,
            channel_id=ch_id,
            channel=guard_channel,
            is_play_hub=False,
            room_info=room_info,
        ),
        None,
    )


async def reset_menu_message(
    interaction: discord.Interaction,
    guild_id: str,
    room_channel_id: str,
    *,
    hub_mode: bool,
) -> None:
    from cogs.private_rooms import build_play_hub_menu_layout, build_welcome_menu_layout

    try:
        ch = interaction.channel
        hub_ch_id = ch.parent_id if isinstance(ch, discord.Thread) else ch.id
        if hub_mode or is_play_hub_channel(guild_id, hub_ch_id):
            await interaction.message.edit(
                view=build_play_hub_menu_layout(guild_id, str(hub_ch_id))
            )
        else:
            await interaction.message.edit(
                view=build_welcome_menu_layout(guild_id, room_channel_id)
            )
    except discord.HTTPException:
        pass


async def setup_play_channels(
    guild: discord.Guild,
    bot: commands.Bot,
    *,
    category_name: str = DEFAULT_PLAY_CATEGORY_NAME,
) -> tuple[bool, str, list[str]]:
    """Create play category + play-1..play-5 and post persistent hub menus."""
    from cogs.private_rooms import build_play_hub_menu_layout

    guild_id = str(guild.id)
    server_data = get_server_data(guild_id)

    if not server_data.get("private_category_id"):
        return False, t("private_rooms.setup_need_private_category", lang="en"), []

    play_cat_id = server_data.get("play_category_id")
    category = (
        guild.get_channel(int(play_cat_id)) if play_cat_id else None
    )
    if not category or not isinstance(category, discord.CategoryChannel):
        category = await guild.create_category(
            name=category_name,
            reason="Vegas play hub setup",
        )
        server_data["play_category_id"] = category.id

    mentions: list[str] = []
    play_ids: list[int] = []
    menu_map: dict[str, int] = dict(server_data.get("play_menu_messages") or {})

    for name in PLAY_CHANNEL_NAMES:
        existing = discord.utils.get(category.text_channels, name=name)
        if existing:
            ch = existing
        else:
            ch = await category.create_text_channel(
                name=name,
                reason="Vegas play hub channel",
            )
        play_ids.append(ch.id)
        mentions.append(ch.mention)

        layout = build_play_hub_menu_layout(guild_id, str(ch.id))
        msg_id = menu_map.get(str(ch.id))
        posted = False
        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
                await msg.edit(view=layout)
                posted = True
            except (discord.NotFound, discord.HTTPException):
                pass
            if not posted:
                await asyncio.sleep(0.6)
        if not posted:
            msg = await ch.send(view=layout)
            menu_map[str(ch.id)] = msg.id
            await asyncio.sleep(0.6)

    server_data["play_channel_ids"] = play_ids
    server_data["play_menu_messages"] = menu_map
    set_server_data(guild_id, server_data)
    return True, "", mentions


async def refresh_play_hub_menus(guild: discord.Guild, bot: commands.Bot) -> int:
    from cogs.private_rooms import build_play_hub_menu_layout

    guild_id = str(guild.id)
    server_data = get_server_data(guild_id)
    menu_map: dict[str, int] = dict(server_data.get("play_menu_messages") or {})
    n = 0
    for ch_id in get_play_channel_ids(guild_id):
        ch = guild.get_channel(ch_id)
        if not ch or not isinstance(ch, discord.TextChannel):
            continue
        layout = build_play_hub_menu_layout(guild_id, str(ch.id))
        msg_id = menu_map.get(str(ch.id))
        ok = False
        if msg_id:
            try:
                msg = await ch.fetch_message(int(msg_id))
                await msg.edit(view=layout)
                ok = True
                n += 1
            except (discord.NotFound, discord.HTTPException):
                pass
        if not ok:
            msg = await ch.send(view=layout)
            menu_map[str(ch.id)] = msg.id
            n += 1
        await asyncio.sleep(0.6)
    server_data["play_menu_messages"] = menu_map
    set_server_data(guild_id, server_data)
    return n
