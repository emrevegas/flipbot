"""VegasBet — prefix + slash command Discord bot."""
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
from modules.command_context import ReplyContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("flipbot")

COGS = [
    "cogs.registration",
    "cogs.economy",
    "cogs.admin",
    "cogs.promo",
    "cogs.affiliate",
    "cogs.rakeback",
    "cogs.admin_panel",      # VegasBot /panel (full hub)
    "cogs.user_management",  # VegasBot /user_panel (full admin user panel)
    "cogs.maintenance",
    "cogs.games",
    "cogs.cases",
    "cogs.deposit",
    "cogs.bonus",
    "cogs.giveaway",
    "cogs.races",
    "cogs.stats",
    "cogs.wallet",
    "cogs.threads",
    "cogs.help_cmd",
    "cogs.crypto_deposit",
    "cogs.crypto_withdraw",
    "cogs.ingame_deposit",
    "cogs.private_rooms",
    "cogs.live_blackjack",
    "cogs.live_stats",
]


class FlipBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=intents,
            help_command=None,  # replaced by cogs.help_cmd
        )

    async def get_context(
        self,
        origin: discord.Message,
        /,
        *,
        cls: type[commands.Context] = ReplyContext,
    ) -> ReplyContext:
        return await super().get_context(origin, cls=cls)

    async def setup_hook(self):
        # Init DB + caches
        await db.get_db()
        try:
            from modules.database import get_data
            from cogs.admin_panel import _persist_games_panel
            await _persist_games_panel(get_data("server/games") or {})
            log.info("Synced server/games panel config to SQLite games table")
        except Exception:
            log.error(f"Failed to sync panel games to SQLite:\n{traceback.format_exc()}")
        from modules import flip_utils as _utils
        await _utils.refresh_tier_cache()
        # Pre-generate card + tower assets if missing
        from modules.image_gen import _ensure_card_assets, _ensure_tower_assets, _ensure_crystal_assets
        _ensure_card_assets()
        _ensure_tower_assets()
        _ensure_crystal_assets()
        # Load cogs
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded {cog}")
            except Exception:
                log.error(f"Failed to load {cog}:\n{traceback.format_exc()}")
        # Re-register pending crypto withdrawal approval buttons
        try:
            from cogs.crypto_withdraw import WithdrawApprovalView
            from modules.database import get_data

            withdrawals = get_data("server/crypto_withdrawals") or {}
            n = sum(1 for w in withdrawals.values() if w.get("status") == "pending")
            for wid, w in withdrawals.items():
                if w.get("status") == "pending":
                    self.add_view(WithdrawApprovalView(wid))
            if n:
                log.info(f"Registered {n} pending crypto withdrawal views")
        except Exception:
            log.error(f"Failed to register withdrawal views:\n{traceback.format_exc()}")
        try:
            from modules.ticket_system import register_ticket_views
            register_ticket_views(self)
        except Exception:
            log.error(f"Failed to register ticket views:\n{traceback.format_exc()}")
        # Sync slash commands
        synced = await self.tree.sync()
        log.info(f"Synced {len(synced)} slash commands")

    async def on_ready(self):
        log.info(f"Ready as {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{config.PREFIX}help | {config.BOT_DISPLAY_NAME}",
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
            try:
                await ctx.send(embed=_err("Something went wrong running that command. Try again."))
            except Exception:
                pass


def _err(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xE74C3C)


async def main():
    bot = FlipBot()
    async with bot:
        await bot.start(config.TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
