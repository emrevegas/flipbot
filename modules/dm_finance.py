"""Send full deposit / withdraw hubs to user DMs."""

from __future__ import annotations

import discord
from discord.ext import commands

from modules.deposit_hub import (
    PrefixDepositView,
    build_deposit_hub_embed,
    collect_deposit_methods,
    resolve_user_lang,
)
from modules.withdraw_hub import PrefixWithdrawView, build_withdraw_hub_embed, collect_withdraw_methods
from modules import flip_utils as utils


async def _channel_ack(ctx: commands.Context, text: str) -> None:
    if ctx.guild is not None:
        await ctx.send(text, delete_after=15)


async def open_deposit_dm(user: discord.User | discord.Member) -> bool:
    methods = collect_deposit_methods()
    if not methods:
        return False
    lang = resolve_user_lang(user.id)
    embed = build_deposit_hub_embed(lang)
    view = PrefixDepositView(user.id, methods, lang)
    dm = await user.create_dm()
    await dm.send(embed=embed, view=view)
    return True


async def open_withdraw_dm(user: discord.User | discord.Member) -> bool:
    methods = collect_withdraw_methods()
    if not methods:
        return False
    lang = resolve_user_lang(user.id)
    embed = build_withdraw_hub_embed(user.id, lang)
    view = PrefixWithdrawView(user.id, methods, lang)
    dm = await user.create_dm()
    await dm.send(embed=embed, view=view)
    return True


async def handle_deposit_command(ctx: commands.Context) -> None:
    try:
        ok = await open_deposit_dm(ctx.author)
    except discord.Forbidden:
        return await ctx.send(
            embed=utils.error_embed("Enable **Direct Messages** from server members to use `.deposit`.")
        )
    if not ok:
        return await ctx.send(
            embed=utils.error_embed(
                "No deposit methods available. Staff must enable crypto or in-game in `/panel`."
            )
        )
    await _channel_ack(ctx, "📬 **Check your DMs** — deposit panel sent.")


async def handle_withdraw_command(ctx: commands.Context) -> None:
    try:
        ok = await open_withdraw_dm(ctx.author)
    except discord.Forbidden:
        return await ctx.send(
            embed=utils.error_embed("Enable **Direct Messages** from server members to use `.withdraw`.")
        )
    if not ok:
        return await ctx.send(
            embed=utils.error_embed(
                "No withdrawal methods available. Staff must enable crypto, in-game, or panel methods."
            )
        )
    await _channel_ack(ctx, "📬 **Check your DMs** — withdrawal panel sent.")
