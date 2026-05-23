"""Promo code redemption: .redeem — uses VegasBot promo engine (full panel settings)."""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules import flip_utils as utils
from modules.player import Player
import modules.promo as promo_engine


class Promo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="redeem")
    async def redeem(self, ctx: commands.Context, code: str):
        """Redeem a promo code. Usage: .redeem CODE"""
        code = code.upper().strip()
        await db.ensure_user(ctx.author.id, ctx.author.name)

        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        ok, err, template = promo_engine.redeem_promo_code(
            ctx.author.id, code, member=member,
        )
        if not ok:
            return await ctx.send(embed=utils.error_embed(err), delete_after=10)

        ptype = template.get("type", "balance")
        if ptype == "balance":
            reward = int(template.get("reward_amount", 0))
            wager_req = int(reward * float(template.get("wager_multiplier", 1.0)))
            player = Player(ctx.author.id)
            player.add_balance("real", reward, by="system", reason=f"Promo: {code}")

            embed = discord.Embed(title="🎉 Promo Code Redeemed!", color=0x2ECC71)
            embed.add_field(name="Code", value=f"`{code}`", inline=True)
            embed.add_field(name="Reward", value=f"**+{utils.fmt_pts(reward)} pts**", inline=True)
            if wager_req > 0:
                embed.add_field(
                    name="Wager Requirement",
                    value=f"`{utils.fmt_pts(wager_req)} pts` before withdrawal",
                    inline=False,
                )
            embed.add_field(
                name="New Balance",
                value=f"`{utils.fmt_pts(player.get_balance('real'))} pts`",
                inline=True,
            )
            await ctx.send(embed=embed)
        else:
            game = template.get("game", "?")
            rounds = int(template.get("rounds", 0))
            bet = int(template.get("bet_amount", 0))
            embed = discord.Embed(title="🎉 Free Game Promo Activated!", color=0x5865F2)
            embed.description = (
                f"**Code:** `{code}`\n"
                f"**Game:** {game}\n"
                f"**Rounds:** {rounds} × `{utils.fmt_pts(bet)} pts`"
            )
            await ctx.send(embed=embed)

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Promo(bot))
