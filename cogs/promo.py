"""Promo code redemption: .redeem"""
from __future__ import annotations

import time

import discord
from discord.ext import commands

from database import db
from modules import utils


class Promo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="redeem")
    async def redeem(self, ctx: commands.Context, code: str):
        """Redeem a promo code. Usage: .redeem CODE"""
        code = code.upper().strip()
        await db.ensure_user(ctx.author.id, ctx.author.name)

        promo = await db.get_promo(code)
        if not promo:
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` not found."), delete_after=8)
        if not promo["enabled"]:
            return await ctx.send(embed=utils.error_embed("This promo is currently disabled."), delete_after=8)
        if promo["expires_at"] and int(promo["expires_at"]) < int(time.time()):
            return await ctx.send(embed=utils.error_embed("This promo code has expired."), delete_after=8)
        if promo["max_uses"] > 0 and promo["uses"] >= promo["max_uses"]:
            return await ctx.send(embed=utils.error_embed("This promo code is fully used."), delete_after=8)
        if await db.has_used_promo(ctx.author.id, code):
            return await ctx.send(embed=utils.error_embed("You already used this code."), delete_after=8)

        reward = float(promo["reward"])
        await db.use_promo(ctx.author.id, code)
        new_bal = await db.add_balance(ctx.author.id, reward, note=f"Promo: {code}", by="system")

        embed = discord.Embed(
            title="🎟️ Promo Redeemed!",
            color=0x2ECC71,
        )
        embed.add_field(name="Code", value=f"`{code}`", inline=True)
        embed.add_field(name="Reward", value=f"**+{utils.fmt_pts(reward)} pts**", inline=True)
        embed.add_field(name="New Balance", value=f"`{utils.fmt_pts(new_bal)} pts`", inline=True)
        embed.set_footer(text=f"${utils.pts_to_usd(new_bal):.2f} USD total")
        await ctx.send(embed=embed)

        # try to delete the trigger message to keep chat clean
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Promo(bot))
