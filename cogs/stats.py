"""Player stats commands."""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from database import db
from modules.database import get_user_stats
from modules import image_gen, utils


class Stats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="stats")
    async def stats(self, ctx: commands.Context, member: discord.Member = None):
        """Show player stats card. .stats [@user]"""
        target = member or ctx.author
        await db.ensure_user(target.id, target.name)

        user = await db.get_user(target.id)
        user_stats = await db.get_user_stats(target.id)
        panel = get_user_stats(target.id) or {}

        loop = asyncio.get_event_loop()
        img_buf = await loop.run_in_executor(
            None,
            image_gen.render_stats_card,
            target.display_name,
            user_stats,
            float(panel.get("total_wagered", 0) or (user.get("total_wagered", 0) if user else 0)),
            float(panel.get("total_deposit", 0) or 0),
        )
        await ctx.send(
            content=f"📊 Stats for **{target.display_name}**:",
            file=discord.File(img_buf, "stats.png"),
        )

    @commands.command(name="wl")
    async def wl(self, ctx: commands.Context, member: discord.Member = None):
        """Show win/loss ratio. .wl [@user]"""
        target = member or ctx.author
        await db.ensure_user(target.id, target.name)
        user_stats = await db.get_user_stats(target.id)

        played = int(user_stats.get("games_played", 0))
        wins   = int(user_stats.get("wins", 0))
        losses = int(user_stats.get("losses", 0))
        wr     = (wins / played * 100) if played else 0

        embed = discord.Embed(
            title=f"📊 W/L — {target.display_name}",
            color=0x5865F2,
        )
        embed.add_field(name="Games Played", value=str(played), inline=True)
        embed.add_field(name="Wins",         value=f"✅ {wins}", inline=True)
        embed.add_field(name="Losses",       value=f"❌ {losses}", inline=True)
        embed.add_field(name="Win Rate",     value=f"`{wr:.1f}%`", inline=True)
        profit = float(user_stats.get("total_profit", 0))
        embed.add_field(
            name="Total P/L",
            value=f"`{'+'if profit>=0 else ''}{utils.fmt_pts(profit)} pts`",
            inline=True,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Stats(bot))
