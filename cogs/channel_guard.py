"""Global play-channel routing for prefix and slash commands."""

from __future__ import annotations

import discord
from discord.ext import commands

from modules.channel_guard import (
    ChannelGuardError,
    assert_command_channel,
    handle_wrong_channel_message,
    interaction_channel_check,
)

__all__ = ("ChannelGuardError",)


class ChannelGuardCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await handle_wrong_channel_message(message, self.bot)


async def global_command_channel_check(ctx: commands.Context) -> bool:
    assert_command_channel(ctx)
    return True


async def setup(bot: commands.Bot):
    bot.add_check(global_command_channel_check)
    bot.tree.interaction_check(interaction_channel_check)
    await bot.add_cog(ChannelGuardCog(bot))
