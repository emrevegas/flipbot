"""FlipBot — prefix + slash command Discord bot."""
from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from pathlib import Path

import discord
from discord.ext import commands

import config
from database import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("flipbot")

COGS = [
    "cogs.registration",   # auto-register + .register — load first
    "cogs.economy",
    "cogs.admin",
    "cogs.promo",
    "cogs.affiliate",
    "cogs.rakeback",
    "cogs.panel",
    "cogs.games",
    "cogs.cases",
    "cogs.deposit",
    "cogs.bonus",
    "cogs.giveaway",
    "cogs.races",
    "cogs.stats",
    "cogs.wallet",
    "cogs.threads",
]


class FlipBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=intents,
            help_command=_HelpCommand(),
        )

    async def setup_hook(self):
        # Init DB
        await db.get_db()
        # Load cogs
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded {cog}")
            except Exception:
                log.error(f"Failed to load {cog}:\n{traceback.format_exc()}")
        # Sync slash commands
        synced = await self.tree.sync()
        log.info(f"Synced {len(synced)} slash commands")

    async def on_ready(self):
        log.info(f"Ready as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{config.PREFIX}help | flipbot",
            )
        )

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=_err(f"Missing argument: `{error.param.name}`"))
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=_err(str(error)))
        elif isinstance(error, commands.CheckFailure):
            await ctx.send(embed=_err("You don't have permission to use this command."), delete_after=6)
        elif isinstance(error, commands.CommandNotFound):
            pass
        else:
            log.error(f"Command error in {ctx.command}: {error}", exc_info=error)


def _err(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xE74C3C)


class _HelpCommand(commands.DefaultHelpCommand):
    def get_ending_note(self):
        return f"Use {self.context.prefix}help <command> for more info."


async def main():
    bot = FlipBot()
    async with bot:
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
