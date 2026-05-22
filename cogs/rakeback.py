"""Rakeback system: .rakeback"""
from __future__ import annotations

import discord
from discord.ext import commands

from database import db
from modules import image_gen, utils
import config


class Rakeback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="rakeback", aliases=["rb", "rake"], invoke_without_command=True)
    async def rakeback(self, ctx: commands.Context):
        """View your rakeback status as a card. .rakeback claim to withdraw."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        if not user:
            return await ctx.send(embed=utils.error_embed("Register first."))

        total_wagered = float(user["total_wagered"])
        accumulated = float(user["rakeback_accumulated"])
        total_claimed = float(user["rakeback_total_claimed"])

        tier = utils.get_rakeback_tier(total_wagered)
        next_tier = utils.get_next_rakeback_tier(total_wagered)

        buf = await image_gen.render_rakeback_card(
            username=ctx.author.display_name,
            accumulated=accumulated,
            total_claimed=total_claimed,
            total_wagered=total_wagered,
            tier_name=tier["name"],
            tier_rate=tier["rate"],
            next_tier_name=next_tier["name"] if next_tier else None,
            next_tier_min=next_tier["min_wagered"] if next_tier else None,
        )
        await ctx.send(file=discord.File(buf, "rakeback.png"))

    @rakeback.command(name="claim")
    async def rakeback_claim(self, ctx: commands.Context):
        """Claim accumulated rakeback."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        accumulated = float(user["rakeback_accumulated"])

        if accumulated < config.RAKEBACK_MIN_CLAIM:
            return await ctx.send(embed=utils.error_embed(
                f"Minimum claim is **{utils.fmt_pts(config.RAKEBACK_MIN_CLAIM)} pts**. "
                f"You have **{utils.fmt_pts(accumulated)} pts** accumulated."
            ))

        amount = await db.claim_rakeback(ctx.author.id)
        embed = discord.Embed(
            title="💸 Rakeback Claimed!",
            description=f"**+{utils.fmt_pts(amount)} pts** added to your balance.",
            color=0x2ECC71,
        )
        embed.set_footer(text=f"${utils.pts_to_usd(amount):.4f} USD")
        await ctx.send(embed=embed)

    @rakeback.command(name="tiers")
    async def rakeback_tiers(self, ctx: commands.Context):
        """View all rakeback tiers."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        total_wagered = float(user["total_wagered"]) if user else 0
        current_tier = utils.get_rakeback_tier(total_wagered)

        lines = []
        for tier in config.RAKEBACK_TIERS:
            active = tier["name"] == current_tier["name"]
            marker = "▶ " if active else "  "
            lines.append(
                f"{marker}**{tier['name']}** — "
                f"`{int(tier['rate']*100)}% rakeback` — "
                f"min wager: `{utils.fmt_pts(tier['min_wagered'])} pts`"
                + (" ← you" if active else "")
            )

        embed = discord.Embed(
            title="🏆 Rakeback Tiers",
            description="\n".join(lines),
            color=0xA855F7,
        )
        embed.add_field(
            name="Your Wager",
            value=f"`{utils.fmt_pts(total_wagered)} pts`",
            inline=True,
        )
        embed.add_field(
            name="Your Tier",
            value=f"**{current_tier['name']}** ({int(current_tier['rate']*100)}%)",
            inline=True,
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rakeback(bot))
