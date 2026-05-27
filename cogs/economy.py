"""Economy commands: .balance, .leaderboard"""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules.database import get_user_stats
from modules import image_gen, flip_utils as utils


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

        panel = get_user_stats(target.id) or {}
        total_deposited = float(panel.get("total_deposit", 0) or user.get("total_deposited", 0))
        total_wagered = float(panel.get("total_wagered", 0) or user.get("total_wagered", 0))

        buf = await image_gen.render_balance_card(
            username=target.display_name,
            balance=float(user["balance"]),
            avatar_url=str(target.display_avatar.url),
            total_wagered=total_wagered,
            total_deposited=total_deposited,
        )
        await ctx.send(file=discord.File(buf, "balance.png"))

    @commands.command(name="setprivacy")
    async def setprivacy(self, ctx: commands.Context, mode: str = ""):
        """Show your name on public deposit/withdraw logs. Usage: .setprivacy public | anonymous"""
        from modules.log_privacy import get_privacy_mode, set_privacy_mode

        m = (mode or "").strip().lower()
        if m not in ("public", "anonymous"):
            current = get_privacy_mode(ctx.author.id)
            return await ctx.send(
                embed=utils.info_embed(
                    "Log privacy",
                    f"Current: **{current}**\n\n"
                    f"Use `{ctx.prefix}setprivacy public` to show your display name on feed logs.\n"
                    f"Use `{ctx.prefix}setprivacy anonymous` to hide it (default).",
                )
            )
        set_privacy_mode(ctx.author.id, m)
        label = "public" if m == "public" else "anonymous"
        await ctx.send(
            embed=utils.success_embed(
                f"Log privacy set to **{label}**. "
                + (
                    "Your display name will appear on deposit and payout feed messages."
                    if m == "public"
                    else "Feed messages will show **Anonymous** for you."
                )
            )
        )

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        """Top 10 balances rendered as an image card."""
        rows = await db.leaderboard(10)
        if not rows:
            return await ctx.send(embed=utils.info_embed("Leaderboard", "No players yet."))
        buf = await image_gen.render_leaderboard_card(rows, self.bot)
        await ctx.send(file=discord.File(buf, "leaderboard.png"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
