"""Wallet commands."""
from __future__ import annotations

import discord
from discord.ext import commands

import config
from database import db
from modules import utils


class Wallet(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="wallet", aliases=["w"])
    async def wallet(self, ctx: commands.Context):
        """Show your wallet card. .wallet"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        active_bonus = await db.get_active_bonus(ctx.author.id)

        balance = float(user["balance"])
        wagered = float(user["total_wagered"])
        deposited = float(user["total_deposited"])
        withdrawn = float(user["total_withdrawn"])

        embed = discord.Embed(
            title=f"💼 Wallet — {ctx.author.display_name}",
            color=0x5865F2,
        )
        embed.add_field(
            name="Balance",
            value=f"`{utils.fmt_pts(balance)} pts` (${balance / config.POINTS_PER_USD:.2f})",
            inline=False,
        )
        embed.add_field(name="Total Deposited", value=f"`{utils.fmt_pts(deposited)} pts`", inline=True)
        embed.add_field(name="Total Withdrawn", value=f"`{utils.fmt_pts(withdrawn)} pts`", inline=True)
        embed.add_field(name="Total Wagered",   value=f"`{utils.fmt_pts(wagered)} pts`", inline=True)

        if active_bonus:
            wagered_b = float(active_bonus["wagered"])
            req_b     = float(active_bonus["wager_req"])
            pct       = min(wagered_b / req_b * 100, 100) if req_b > 0 else 100
            embed.add_field(
                name="Active Bonus",
                value=f"{active_bonus['bonus_name']} — **{pct:.1f}%** complete",
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
        embed.set_footer(text=f"Rate: {int(config.POINTS_PER_USD)} pts = $1.00 USD")
        await ctx.send(embed=embed)

    @commands.command(name="convert")
    async def convert(self, ctx: commands.Context, amount: float):
        """Convert pts <-> USD. .convert 1000"""
        usd = amount / config.POINTS_PER_USD
        embed = discord.Embed(
            title="💱 Conversion",
            color=0x5865F2,
        )
        embed.add_field(name="Points", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        embed.add_field(name="USD",    value=f"`${usd:.2f}`", inline=True)
        embed.add_field(name="Rate",   value=f"`{int(config.POINTS_PER_USD)} pts = $1.00`", inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Wallet(bot))
