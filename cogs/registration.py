"""Registration cog — .register and auto-register on first command."""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules import utils


class Registration(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="register")
    async def register(self, ctx: commands.Context):
        """Register your account. .register"""
        user = await db.get_user(ctx.author.id)
        if user:
            return await ctx.send(embed=utils.info_embed(
                "Already Registered",
                f"You are already registered as **{user['username'] or ctx.author.name}**.",
            ))
        await db.ensure_user(ctx.author.id, ctx.author.name)
        embed = discord.Embed(
            title="✅ Welcome to FlipBot!",
            description=(
                f"Your account has been created, **{ctx.author.display_name}**!\n\n"
                "**Getting Started:**\n"
                "• `.balance` — check your balance\n"
                "• `.deposit` — deposit points\n"
                "• `.coinflip 100` — flip a coin\n"
                "• `.help` — see all commands"
            ),
            color=0x2ECC71,
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_command(self, ctx: commands.Context):
        """Auto-register any user that uses a command."""
        if ctx.author.bot:
            return
        await db.ensure_user(ctx.author.id, ctx.author.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Registration(bot))
