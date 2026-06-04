"""Gates of Olympus style slot — 6x5 scatter pays, separate from classic slots."""

from __future__ import annotations

import io

import discord
from discord.ext import commands

from database import db
from modules import flip_balance_cap as bc
from modules.game_media_v2 import gif_result_layout
from modules.gates_slot_gif import GATES_GIF, render_gates_gif


def _spin_until_fair(
    bet: float,
    *,
    rigged: bool,
    force_win: bool = False,
    pf_fl: list | None = None,
) -> tuple[list, list, int, float]:
    from Games.gates_slot import spin_round

    grid, wins, gross, orb = spin_round(int(bet), pf_fl=pf_fl)
    if force_win:
        for _ in range(48):
            if gross > bet:
                return grid, wins, gross, orb
            grid, wins, gross, orb = spin_round(int(bet), pf_fl=None)
        if gross <= bet:
            gross = int(bet * 2)
        return grid, wins, gross, orb
    if not rigged:
        return grid, wins, gross, orb
    for _ in range(48):
        if not wins or gross <= bet:
            return grid, wins, gross, orb
        grid, wins, gross, orb = spin_round(int(bet), pf_fl=None)
    return grid, wins, 0 if gross > bet else gross, orb


async def _run_gates_round(
    user_id: int,
    bet: float,
    username: str,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
) -> io.BytesIO:
    from cogs.games import _payout, _record

    pf_fl = None
    try:
        from modules.provably_fair import consume_pf_round

        _ss, _cs, _n, pf_fl = consume_pf_round(user_id)
    except Exception:
        pf_fl = None

    force_win = await bc.should_force_win_outcome(user_id, "gates", bet, gross=bet * 8)
    rigged = await bc.should_rig_outcome(user_id, "gates", bet, gross=bet * 8)
    grid, wins, gross, orb_mult = _spin_until_fair(
        bet,
        rigged=rigged and not force_win,
        force_win=force_win,
        pf_fl=pf_fl,
    )

    won = gross > bet
    payout = await _payout(user_id, "gates", bet, float(gross))
    user_row = await db.get_user(user_id)
    balance = float(user_row["balance"]) if user_row else 0.0

    await _record(
        user_id,
        won,
        bet,
        payout,
        game_id="gates",
        user=user,
        client=client,
        guild_id=guild_id,
    )

    return await render_gates_gif(
        username=username,
        bet=bet,
        balance=balance,
        grid=grid,
        wins=wins,
        payout=payout,
        won=won,
        orb_mult=orb_mult,
    )


async def _send_gates_v2(
    target: commands.Context | discord.abc.Messageable | discord.Message,
    user_id: int,
    bet: float,
    gif: io.BytesIO,
    *,
    message: discord.Message | None = None,
) -> discord.Message | None:
    layout = gif_result_layout(
        GATES_GIF,
        user_id=user_id,
        bet=bet,
        rebet_cb=_gates_rebet_from_interaction,
    )
    file = discord.File(gif, GATES_GIF)
    if message is not None:
        await message.edit(content=None, embed=None, attachments=[file], view=layout)
        return message
    if isinstance(target, discord.Message):
        await target.edit(content=None, embed=None, attachments=[file], view=layout)
        return target
    if isinstance(target, commands.Context):
        return await target.send(file=file, view=layout)
    return await target.send(file=file, view=layout)


async def _gates_rebet_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    bet: float,
) -> None:
    from cogs.games import _check_game_interaction

    if not await _check_game_interaction(interaction, user_id, "gates", bet):
        return
    await db.ensure_user(user_id, interaction.user.name)
    await interaction.response.defer()

    gif = await _run_gates_round(
        user_id,
        bet,
        interaction.user.display_name,
        user=interaction.user,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
    )
    await _send_gates_v2(
        interaction.message,
        user_id,
        bet,
        gif,
        message=interaction.message,
    )


async def start_gates(ctx: commands.Context, bet: float) -> None:
    await db.ensure_user(ctx.author.id, ctx.author.name)
    gif = await _run_gates_round(
        ctx.author.id,
        bet,
        ctx.author.display_name,
        user=ctx.author,
        client=ctx.bot,
        guild_id=ctx.guild.id if ctx.guild else None,
    )
    await _send_gates_v2(ctx, ctx.author.id, bet, gif)
