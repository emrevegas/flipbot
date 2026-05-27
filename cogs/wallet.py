"""Wallet commands."""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
import modules.bonus as bonus_engine
from modules.database import get_user_stats
from modules import flip_utils as utils
from modules.economy import get_coins_per_usd, get_coin_usd_rate


class Wallet(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="wallet", aliases=["w"])
    async def wallet(self, ctx: commands.Context):
        """Show your wallet card. .wallet"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        panel = get_user_stats(ctx.author.id) or {}

        balance = float(user["balance"])
        wagered = float(panel.get("total_wagered", 0) or user.get("total_wagered", 0))
        deposited = float(panel.get("total_deposit", 0) or user.get("total_deposited", 0))
        withdrawn = float(panel.get("total_withdraw", 0) or user.get("total_withdrawn", 0))

        active_bonus = bonus_engine.get_active_bonus(ctx.author.id)

        embed = discord.Embed(
            title=f"💼 Wallet — {ctx.author.display_name}",
            color=0x5865F2,
        )
        embed.add_field(
            name="Balance",
            value=f"`{utils.fmt_pts(balance)} coins` (${utils.pts_to_usd(balance):.2f})",
            inline=False,
        )
        embed.add_field(name="Total Deposited", value=f"`{utils.fmt_pts(deposited)} pts`", inline=True)
        embed.add_field(name="Total Withdrawn", value=f"`{utils.fmt_pts(withdrawn)} pts`", inline=True)
        embed.add_field(name="Total Wagered",   value=f"`{utils.fmt_pts(wagered)} pts`", inline=True)

        if active_bonus:
            wagered_b = float(active_bonus.get("wagered_so_far", 0))
            req_b     = float(active_bonus.get("wager_requirement", 0))
            pct       = min(wagered_b / req_b * 100, 100) if req_b > 0 else 100
            embed.add_field(
                name="Active Bonus",
                value=f"{active_bonus.get('bonus_name', 'Bonus')} — **{pct:.1f}%** complete",
                inline=False,
            )

        # pending withdrawals
        dbc = await db.get_db()
        pending = await (await dbc.execute(
            "SELECT SUM(amount) as total FROM withdrawal_requests WHERE user_id=? AND status='pending'",
            (str(ctx.author.id),),
        )).fetchone()
        pending_amt = float(pending["total"] or 0)
        if pending_amt > 0:
            embed.add_field(
                name="Pending Withdrawal",
                value=f"`{utils.fmt_pts(pending_amt)} pts`",
                inline=True,
            )
        coins_per_usd = int(get_coins_per_usd())
        embed.set_footer(text=f"Rate: {coins_per_usd} coins = $1.00 USD (panel)")
        await ctx.send(embed=embed)

    @commands.command(name="convert")
    async def convert(self, ctx: commands.Context, amount: float):
        """Convert pts <-> USD. .convert 1000"""
        usd = utils.pts_to_usd(amount)
        rate = get_coin_usd_rate()
        coins_per_usd = int(get_coins_per_usd())
        embed = discord.Embed(
            title="💱 Conversion",
            color=0x5865F2,
        )
        embed.add_field(name="Coins", value=f"`{utils.fmt_pts(amount)}`", inline=True)
        embed.add_field(name="USD",    value=f"`${usd:.2f}`", inline=True)
        embed.add_field(name="Rate",   value=f"`{coins_per_usd} coins = $1.00`", inline=True)
        embed.set_footer(text=f"1 coin = ${rate:.4g} USD")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Wallet(bot))
