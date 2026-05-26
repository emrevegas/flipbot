"""Jackpot — multiplayer pool game in a dedicated channel."""

from __future__ import annotations

import discord
from discord.ext import commands

import config
from modules.jackpot_flow import (
    bootstrap_jackpot_room,
    cancel_jackpot,
    join_jackpot,
    on_jackpot_channel_message,
    send_jp_feedback,
)


class Jackpot(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._bootstrapped = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._bootstrapped:
            return
        self._bootstrapped = True
        await bootstrap_jackpot_room(self.bot)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        from modules.channel_guard import handle_wrong_channel_message

        if await handle_wrong_channel_message(message, self.bot):
            return
        content = (message.content or "").strip()
        if not content:
            await on_jackpot_channel_message(message)
            return

        prefix = config.PREFIX
        lower = content.lower()
        invoked = False

        for cmd in ("jackpot", "jp"):
            p = f"{prefix}{cmd}"
            if lower == p or lower.startswith(p + " "):
                invoked = True
                parts = content.split()
                if len(parts) < 2:
                    await send_jp_feedback(
                        message.channel,
                        f"Usage: `{prefix}{cmd} <bet>` (e.g. `{prefix}jp 100`, `{prefix}jp all`)",
                    )
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return
                from modules.bet_parse import resolve_bet_amount

                bet, err = await resolve_bet_amount(message.author.id, parts[1])
                if err or bet is None:
                    await send_jp_feedback(message.channel, err or "Invalid bet.")
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return
                ctx = await self.bot.get_context(message)
                await join_jackpot(ctx, bet, join_message=message)
                return

        if lower in (f"{prefix}canceljp", f"{prefix}cancel jackpot"):
            invoked = True
            ctx = await self.bot.get_context(message)
            await cancel_jackpot(ctx)
            return

        if not invoked:
            await on_jackpot_channel_message(message)


async def setup(bot: commands.Bot):
    await bot.add_cog(Jackpot(bot))
