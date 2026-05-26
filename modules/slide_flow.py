"""Slide — Components V2 MediaGallery result + Re-bet / 2× Bet."""

from __future__ import annotations

import io

import discord
from discord.ext import commands

from database import db
from modules import flip_balance_cap as bc, image_gen
from modules.game_media_v2 import gif_result_layout

SLIDE_GIF = "slide.gif"


async def _run_slide_round(
    user_id: int,
    bet: float,
    username: str,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
) -> io.BytesIO:
    from Games.slide import gross_payout, pick_rigged_multiplier, roll_multiplier
    from cogs.games import _payout, _record

    rigged = await bc.should_rig_outcome(user_id, "slide", bet)
    if rigged:
        result_mult = pick_rigged_multiplier()
    else:
        result_mult = roll_multiplier()

    gross = gross_payout(bet, result_mult)
    won = gross > bet
    net = await _payout(user_id, "slide", bet, gross)
    net_change = net - bet

    await _record(
        user_id,
        won,
        bet,
        net,
        game_id="slide",
        user=user,
        client=client,
        guild_id=guild_id,
    )

    return await image_gen.render_slide_gif(
        username=username,
        bet=bet,
        result_mult=result_mult,
        won=net_change > 0,
        net_change=net_change,
    )


async def _send_slide_v2(
    target: commands.Context | discord.abc.Messageable | discord.Message,
    user_id: int,
    bet: float,
    gif: io.BytesIO,
    *,
    message: discord.Message | None = None,
) -> discord.Message | None:
    layout = gif_result_layout(
        SLIDE_GIF,
        user_id=user_id,
        bet=bet,
        rebet_cb=_slide_rebet_from_interaction,
    )
    file = discord.File(gif, SLIDE_GIF)
    if message is not None:
        await message.edit(content=None, embed=None, attachments=[file], view=layout)
        return message
    if isinstance(target, discord.Message):
        await target.edit(content=None, embed=None, attachments=[file], view=layout)
        return target
    if isinstance(target, commands.Context):
        return await target.send(file=file, view=layout)
    return await target.send(file=file, view=layout)


async def _slide_rebet_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    bet: float,
) -> None:
    from cogs.games import _check_game_interaction

    if not await _check_game_interaction(interaction, user_id, "slide", bet):
        return
    await db.ensure_user(user_id, interaction.user.name)
    await interaction.response.defer()

    gif = await _run_slide_round(
        user_id,
        bet,
        interaction.user.display_name,
        user=interaction.user,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
    )
    await _send_slide_v2(
        interaction.message,
        user_id,
        bet,
        gif,
        message=interaction.message,
    )


async def start_slide(ctx: commands.Context, bet: float) -> None:
    await db.ensure_user(ctx.author.id, ctx.author.name)
    gif = await _run_slide_round(
        ctx.author.id,
        bet,
        ctx.author.display_name,
        user=ctx.author,
        client=ctx.bot,
        guild_id=ctx.guild.id if ctx.guild else None,
    )
    await _send_slide_v2(ctx, ctx.author.id, bet, gif)
