"""VegasBet — prefix + slash command Discord bot (core — compiled in licensed releases)."""

from __future__ import annotations

import asyncio
import logging
import traceback

import discord
from discord.ext import commands

import config
from database import db
from modules.command_context import ReplyContext

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("flipbot")

COGS = [
    "cogs.registration",
    "cogs.economy",
    "cogs.admin",
    "cogs.promo",
    "cogs.daily",
    "cogs.affiliate",
    "cogs.rakeback",
    "cogs.admin_panel",
    "cogs.user_management",
    "cogs.maintenance",
    "cogs.channel_guard",
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
    "cogs.jackpot",
    "cogs.self_roles",
]


class FlipBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.presences = True
        super().__init__(
            command_prefix=commands.when_mentioned_or(config.PREFIX),
            intents=intents,
            help_command=None,
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
        from modules.image_gen import _ensure_card_assets, _ensure_tower_assets, _ensure_crystal_assets

        _ensure_card_assets()
        _ensure_tower_assets()
        _ensure_crystal_assets()
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded {cog}")
            except Exception:
                log.error(f"Failed to load {cog}:\n{traceback.format_exc()}")
        from modules.control_bot import should_load_vds_panel

        if should_load_vds_panel():
            try:
                await self.load_extension("cogs.vds_panel")
                log.info("Loaded cogs.vds_panel (Ada control bot)")
            except Exception:
                log.error(f"Failed to load cogs.vds_panel:\n{traceback.format_exc()}")
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
        try:
            from cogs.self_roles import build_menu_view
            from modules.self_roles_store import get_config

            n = 0
            for guild in self.guilds:
                cfg = get_config(guild.id)
                if cfg.get("roles"):
                    self.add_view(build_menu_view(guild.id, cfg))
                    n += 1
            if n:
                log.info(f"Registered self-role menus for {n} guild(s)")
        except Exception:
            log.error(f"Failed to register self-role views:\n{traceback.format_exc()}")
        synced = await self.tree.sync()
        log.info(f"Synced {len(synced)} slash commands")

    async def on_ready(self):
        log.info(f"Ready as {self.user} (ID: {self.user.id})")
        if not self.intents.presences:
            log.warning(
                "Presences intent is disabled — promo/giveaway custom-status checks may fail."
            )
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
            from modules.channel_guard import ChannelGuardError

            if isinstance(error, ChannelGuardError):
                from modules.channel_guard import message_was_channel_guard_handled

                if error.text and not message_was_channel_guard_handled(ctx.message):
                    try:
                        await ctx.send(error.text, delete_after=12)
                    except Exception:
                        pass
                    try:
                        if ctx.message:
                            await ctx.message.delete()
                    except Exception:
                        pass
            else:
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


async def main() -> None:
    bot = FlipBot()
    async with bot:
        await bot.start(config.TOKEN)
