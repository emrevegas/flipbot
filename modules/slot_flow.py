"""Slots — 3×5 grid, 30 fixed paylines, V2 GIF + rebet."""

from __future__ import annotations

import io

import discord
from discord.ext import commands

from database import db
from modules import flip_balance_cap as bc, image_gen
from modules.game_media_v2 import gif_result_layout

SLOTS_GIF = "slots.gif"
NUM_LINES = 30


def _spin_until_fair(bet: float, *, rigged: bool, force_win: bool = False) -> tuple[list, list, int]:
    from Games.slot import NUM_LINES as LINES, spin_round

    grid, wins, gross = spin_round(int(bet), num_lines=LINES)
    if force_win:
        for _ in range(48):
            if gross > bet:
                return grid, wins, gross
            grid, wins, gross = spin_round(int(bet), num_lines=LINES)
        return grid, wins, gross if gross > bet else bet * 2
    if not rigged:
        return grid, wins, gross
    for _ in range(48):
        if not wins or gross <= bet:
            return grid, wins, gross
        grid, wins, gross = spin_round(int(bet), num_lines=LINES)
    return grid, wins, 0 if gross > bet else gross


async def _run_slots_round(
    user_id: int,
    bet: float,
    username: str,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
) -> io.BytesIO:
    from Games.slot import get_slot_emojis
    from cogs.games import _payout, _record

    force_win = await bc.should_force_win_outcome(user_id, "slots", bet, gross=bet * 5)
    rigged = await bc.should_rig_outcome(user_id, "slots", bet, gross=bet * 5)
    grid, wins, gross = _spin_until_fair(
        bet, rigged=rigged and not force_win, force_win=force_win,
    )

    won = gross > bet
    payout = await _payout(user_id, "slots", bet, float(gross))
    user_row = await db.get_user(user_id)
    balance = float(user_row["balance"]) if user_row else 0.0

    await _record(
        user_id,
        won,
        bet,
        payout,
        game_id="slots",
        user=user,
        client=client,
        guild_id=guild_id,
    )

    emoji_map, spin_emoji = get_slot_emojis()
    grid_ids = [[s["id"] for s in row] for row in grid]

    return await image_gen.render_slots_gif(
        username=username,
        bet=bet,
        balance=balance,
        grid_ids=grid_ids,
        wins=wins,
        emoji_map=emoji_map,
        spin_emoji=spin_emoji,
        payout=payout,
        won=won,
    )


async def _send_slots_v2(
    target: commands.Context | discord.abc.Messageable | discord.Message,
    user_id: int,
    bet: float,
    gif: io.BytesIO,
    *,
    message: discord.Message | None = None,
) -> discord.Message | None:
    layout = gif_result_layout(
        SLOTS_GIF,
        user_id=user_id,
        bet=bet,
        rebet_cb=_slots_rebet_from_interaction,
    )
    file = discord.File(gif, SLOTS_GIF)
    if message is not None:
        await message.edit(content=None, embed=None, attachments=[file], view=layout)
        return message
    if isinstance(target, discord.Message):
        await target.edit(content=None, embed=None, attachments=[file], view=layout)
        return target
    if isinstance(target, commands.Context):
        return await target.send(file=file, view=layout)
    return await target.send(file=file, view=layout)


async def _slots_rebet_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    bet: float,
) -> None:
    from cogs.games import _check_game_interaction

    if not await _check_game_interaction(interaction, user_id, "slots", bet):
        return
    await db.ensure_user(user_id, interaction.user.name)
    await interaction.response.defer()

    gif = await _run_slots_round(
        user_id,
        bet,
        interaction.user.display_name,
        user=interaction.user,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
    )
    await _send_slots_v2(
        interaction.message,
        user_id,
        bet,
        gif,
        message=interaction.message,
    )


async def start_slots(ctx: commands.Context, bet: float) -> None:
    await db.ensure_user(ctx.author.id, ctx.author.name)
    gif = await _run_slots_round(
        ctx.author.id,
        bet,
        ctx.author.display_name,
        user=ctx.author,
        client=ctx.bot,
        guild_id=ctx.guild.id if ctx.guild else None,
    )
    await _send_slots_v2(ctx, ctx.author.id, bet, gif)
