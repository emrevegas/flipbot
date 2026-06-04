"""Gates of Olympus style slot — 6x5 scatter pays, separate from classic slots."""

from __future__ import annotations

import io

import discord
from discord.ext import commands

from database import db
from modules import flip_balance_cap as bc
from modules.game_media_v2 import gif_result_layout
from modules.gates_slot_gif import GATES_GIF, render_gates_gif


async def _net_from_gross(gross: float, game_id: str = "gates") -> int:
    cfg = await db.get_game_config(game_id)
    he = float(cfg["house_edge"]) if cfg else 0.13
    return int(float(gross) * (1.0 - he)) if gross > 0 else 0


async def _spin_for_outcome(
    user_id: int,
    bet: float,
    *,
    rigged: bool,
    force_win: bool = False,
    pf_fl: list | None = None,
) -> tuple[list, dict, list, int]:
    from Games.gates_slot import (
        apply_emoji_map,
        build_win_grid,
        evaluate_grid,
        get_gates_emojis,
        gross_payout,
        roll_grid_orbs,
        spin_round,
        _rng_from_pf,
    )

    emoji_map = get_gates_emojis()
    bal_row = await db.get_user(user_id)
    balance = int(float(bal_row["balance"])) if bal_row else 0
    b = int(bet)
    bal_after_bet = balance - b

    def _pack(grid, grid_orbs, wins, gross):
        return apply_emoji_map(grid, emoji_map), grid_orbs, wins, gross

    if force_win:
        for match in (9, 10, 8, 11):
            for sym_id in ("ruby", "emerald", "sapphire", "topaz"):
                grid, grid_orbs = build_win_grid(
                    match_count=match,
                    symbol_id=sym_id,
                    pf_fl=pf_fl,
                    grid_orbs={},
                )
                if match <= 9:
                    grid_orbs = roll_grid_orbs(_rng_from_pf(pf_fl, salt=3), cap_safe=True)
                wins = evaluate_grid(grid, grid_orbs)
                gross = gross_payout(b, wins, grid_orbs)
                net = await _net_from_gross(gross)
                if gross <= b:
                    continue
                if await bc.max_win_exceeds_cap(user_id, bal_after_bet, net):
                    continue
                return _pack(grid, grid_orbs, wins, gross)
        grid, grid_orbs, wins, gross = spin_round(b, pf_fl=pf_fl)
        if gross <= b:
            grid, grid_orbs = build_win_grid(match_count=8, pf_fl=pf_fl, grid_orbs={})
            wins = evaluate_grid(grid, grid_orbs)
            gross = gross_payout(b, wins, grid_orbs)
        return _pack(grid, grid_orbs, wins, max(gross, int(b * 1.5)))

    if rigged:
        for _ in range(64):
            grid, grid_orbs, wins, gross = spin_round(b, pf_fl=None)
            if not wins or gross <= b:
                return _pack(grid, grid_orbs, wins, gross)
        grid, grid_orbs, wins, gross = spin_round(b, pf_fl=None)
        return _pack(grid, {}, [], 0)

    grid, grid_orbs, wins, gross = spin_round(b, pf_fl=pf_fl)
    net = await _net_from_gross(gross)
    if gross > b and await bc.max_win_exceeds_cap(user_id, bal_after_bet, net):
        for _ in range(64):
            grid, grid_orbs, wins, gross = spin_round(b, pf_fl=None)
            net = await _net_from_gross(gross)
            if not wins or gross <= b:
                return _pack(grid, grid_orbs, wins, gross)
        return _pack(grid, {}, [], 0)

    return _pack(grid, grid_orbs, wins, gross)


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
    from Games.gates_slot import get_gates_emojis

    pf_fl = None
    try:
        from modules.provably_fair import consume_pf_round

        _ss, _cs, _n, pf_fl = consume_pf_round(user_id)
    except Exception:
        pf_fl = None

    gross_hint = bet * 4
    force_win = await bc.should_force_win_outcome(user_id, "gates", bet, gross=gross_hint)
    rigged = await bc.should_rig_outcome(user_id, "gates", bet, gross=gross_hint)

    grid, grid_orbs, wins, gross = await _spin_for_outcome(
        user_id,
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
        grid_orbs=grid_orbs,
        emoji_map=get_gates_emojis(),
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
