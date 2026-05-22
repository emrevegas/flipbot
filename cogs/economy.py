"""Economy commands: .balance, .leaderboard"""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules import image_gen, utils


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="balance", aliases=["bal", "b"])
    async def balance(self, ctx: commands.Context, member: discord.Member = None):
        """Show your (or another user's) balance as an image card."""
        target = member or ctx.author
        await db.ensure_user(target.id, target.name)
        user = await db.get_user(target.id)
        if not user:
            return await ctx.send(embed=utils.error_embed("User not found."))

        buf = await image_gen.render_balance_card(
            username=target.display_name,
            balance=float(user["balance"]),
            avatar_url=str(target.display_avatar.url),
            total_wagered=float(user["total_wagered"]),
            total_deposited=float(user["total_deposited"]),
        )
        await ctx.send(file=discord.File(buf, "balance.png"))

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        """Top 10 balances rendered as an image card."""
        rows = await db.leaderboard(10)
        if not rows:
            return await ctx.send(embed=utils.info_embed("Leaderboard", "No players yet."))
        buf = await image_gen.render_leaderboard_card(rows, self.bot)
        await ctx.send(file=discord.File(buf, "leaderboard.png"))

    @commands.command(name="pay")
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: float):
        """Transfer points to another user."""
        if amount <= 0:
            return await ctx.send(embed=utils.error_embed("Amount must be positive."))
        if member.id == ctx.author.id:
            return await ctx.send(embed=utils.error_embed("You can't pay yourself."))

        await db.ensure_user(ctx.author.id, ctx.author.name)
        sender = await db.get_user(ctx.author.id)
        if not sender or float(sender["balance"]) < amount:
            return await ctx.send(embed=utils.error_embed("Insufficient balance."))

        await db.add_balance(ctx.author.id, -amount, note=f"Pay to {member.id}", by=str(ctx.author.id))
        await db.add_balance(member.id, amount, note=f"Pay from {ctx.author.id}", by=str(ctx.author.id))

        embed = discord.Embed(
            description=f"✅ Sent **{utils.fmt_pts(amount)} pts** to {member.mention}",
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
