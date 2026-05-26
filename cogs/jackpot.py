"""Jackpot — multiplayer pool game in a dedicated channel."""

from __future__ import annotations

import discord
from discord.ext import commands

import config
from modules.jackpot_flow import cancel_jackpot, join_jackpot, on_jackpot_channel_message


class Jackpot(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
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
                    await message.channel.send(
                        embed=discord.Embed(
                            description=f"❌ Usage: `{prefix}{cmd} <bet>` (e.g. `{prefix}jp 100`, `{prefix}jp all`)",
                            color=0xE74C3C,
                        ),
                        delete_after=12,
                    )
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return
                from modules.bet_parse import resolve_bet_amount

                bet, err = await resolve_bet_amount(message.author.id, parts[1])
                if err or bet is None:
                    await message.channel.send(
                        embed=discord.Embed(description=f"❌ {err or 'Invalid bet.'}", color=0xE74C3C),
                        delete_after=10,
                    )
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
