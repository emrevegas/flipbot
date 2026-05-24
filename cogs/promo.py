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
        new_balance = None
        if ptype == "balance":
            reward = int(template.get("reward_amount", 0))
            player = Player(ctx.author.id)
            player.add_balance("real", reward, by="system", reason=f"Promo: {code}")
            new_balance = float(player.get_balance("real"))

        await promo_engine.send_promo_redeemed_image(
            ctx,
            user=ctx.author,
            code=code,
            template=template,
            new_balance=new_balance,
        )

        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Promo(bot))
