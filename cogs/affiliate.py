"""Affiliate system: .affiliate"""
from __future__ import annotations

import re

import discord
from discord.ext import commands

from database import db
from modules import image_gen, utils
import config


class Affiliate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="affiliate", aliases=["aff", "ref"], invoke_without_command=True)
    async def affiliate(self, ctx: commands.Context):
        """Affiliate program. Subcommands: create, stats, use, claim, referred"""
        aff = await db.get_affiliate(ctx.author.id)
        if not aff:
            embed = discord.Embed(
                title="🤝 Affiliate Program",
                description=(
                    "You don't have an affiliate code yet.\n\n"
                    f"**Earn:**\n"
                    f"• **{int(config.AFFILIATE_FTD_RATE*100)}%** of your referrals' first deposit\n"
                    f"• **{int(config.AFFILIATE_EDGE_RATE*100)}%** of house edge from all referred plays — **forever**\n\n"
                    "Create your code with `.affiliate create <CODE>`"
                ),
                color=0xF59E0B,
            )
            return await ctx.send(embed=embed)

        await self._send_affiliate_card(ctx, aff)

    @affiliate.command(name="create")
    async def affiliate_create(self, ctx: commands.Context, code: str):
        """Create your affiliate code. .affiliate create MYCODE"""
        code = code.upper().strip()
        if not re.match(r"^[A-Z0-9_]{3,20}$", code):
            return await ctx.send(embed=utils.error_embed(
                "Code must be 3–20 alphanumeric/underscore characters."
            ))
        existing = await db.get_affiliate(ctx.author.id)
        if existing:
            return await ctx.send(embed=utils.error_embed(
                f"You already have code `{existing['code']}`."
            ))
        taken = await db.get_affiliate_by_code(code)
        if taken:
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` is already taken."))

        aff = await db.create_affiliate(ctx.author.id, code)
        embed = discord.Embed(
            title="✅ Affiliate Code Created",
            description=f"Your code: **`{code}`**\n\nShare it with others using `.affiliate use {code}`",
            color=0x2ECC71,
        )
        embed.add_field(name="FTD Commission", value=f"{int(config.AFFILIATE_FTD_RATE*100)}%", inline=True)
        embed.add_field(name="Lifetime Edge", value=f"{int(config.AFFILIATE_EDGE_RATE*100)}%", inline=True)
        await ctx.send(embed=embed)

    @affiliate.command(name="stats")
    async def affiliate_stats(self, ctx: commands.Context):
        """View your affiliate stats as an image card."""
        aff = await db.get_affiliate(ctx.author.id)
        if not aff:
            return await ctx.send(embed=utils.error_embed(
                "No affiliate code yet. Use `.affiliate create <CODE>`"
            ))
        await self._send_affiliate_card(ctx, aff)

    @affiliate.command(name="use")
    async def affiliate_use(self, ctx: commands.Context, code: str):
        """Use someone's affiliate code. .affiliate use CODE"""
        code = code.upper().strip()
        aff = await db.get_affiliate_by_code(code)
        if not aff:
            return await ctx.send(embed=utils.error_embed(f"Code `{code}` not found."))
        if int(aff["user_id"]) == ctx.author.id:
            return await ctx.send(embed=utils.error_embed("You can't use your own code."))

        # check if already referred by anyone
        dbc = await db.get_db()
        existing = await (await dbc.execute(
            "SELECT * FROM affiliate_refs WHERE referred_id=?", (str(ctx.author.id),)
        )).fetchone()
        if existing:
            return await ctx.send(embed=utils.error_embed("You already used an affiliate code."))

        await dbc.execute(
            "INSERT OR IGNORE INTO affiliate_refs (affiliate_id, referred_id) VALUES (?, ?)",
            (str(aff["user_id"]), str(ctx.author.id)),
        )
        await dbc.commit()
        embed = discord.Embed(
            title="✅ Affiliate Code Applied",
            description=(
                f"You are now referred by **`{code}`**.\n"
                "Your activity will earn your referrer commissions."
            ),
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)

        # notify affiliate owner if in same server
        try:
            owner = ctx.guild.get_member(int(aff["user_id"])) if ctx.guild else None
            if owner:
                await owner.send(
                    embed=discord.Embed(
                        description=f"🎉 **{ctx.author.display_name}** just used your affiliate code `{code}`!",
                        color=0xF59E0B,
                    )
                )
        except Exception:
            pass

    @affiliate.command(name="claim")
    async def affiliate_claim(self, ctx: commands.Context):
        """Claim your affiliate earnings."""
        aff = await db.get_affiliate(ctx.author.id)
        if not aff:
            return await ctx.send(embed=utils.error_embed("No affiliate code. Use `.affiliate create <CODE>`"))

        claimable = float(aff["claimable"])
        if claimable < config.AFFILIATE_MIN_CLAIM:
            return await ctx.send(embed=utils.error_embed(
                f"Minimum claim is **{utils.fmt_pts(config.AFFILIATE_MIN_CLAIM)} pts**. "
                f"You have {utils.fmt_pts(claimable)} pts."
            ))

        amount = await db.claim_affiliate(ctx.author.id)
        embed = discord.Embed(
            title="💰 Affiliate Earnings Claimed!",
            description=f"**+{utils.fmt_pts(amount)} pts** added to your balance.",
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)

    @affiliate.command(name="referred")
    async def affiliate_referred(self, ctx: commands.Context):
        """List users referred by you."""
        aff = await db.get_affiliate(ctx.author.id)
        if not aff:
            return await ctx.send(embed=utils.error_embed("No affiliate code yet."))

        refs = await db.get_affiliate_refs(ctx.author.id)
        if not refs:
            return await ctx.send(embed=utils.info_embed(
                "Referred Users", "Nobody has used your code yet."
            ))

        lines = []
        for ref in refs[:20]:
            member = ctx.guild.get_member(int(ref["referred_id"])) if ctx.guild else None
            name = member.display_name if member else f"<@{ref['referred_id']}>"
            ftd_str = f"✅ FTD {utils.fmt_pts(ref['first_deposit'])} pts" if ref["ftd_paid"] else "⏳ No deposit yet"
            lines.append(f"• **{name}** — {ftd_str}")

        embed = discord.Embed(
            title=f"👥 Referred Users ({len(refs)})",
            description="\n".join(lines),
            color=0x5865F2,
        )
        await ctx.send(embed=embed)

    async def _send_affiliate_card(self, ctx: commands.Context, aff: dict):
        refs = await db.get_affiliate_refs(aff["user_id"])
        ftd_count = sum(1 for r in refs if r["ftd_paid"])
        buf = await image_gen.render_affiliate_card(
            username=ctx.author.display_name,
            code=aff["code"],
            referrals=len(refs),
            ftd=ftd_count,
            ftd_earnings=float(aff["ftd_earnings"]),
            edge_earnings=float(aff["edge_earnings"]),
            claimable=float(aff["claimable"]),
            total_claimed=float(aff["total_claimed"]),
        )
        await ctx.send(
            content=f"Your Affiliate Code: `{aff['code']}`",
            file=discord.File(buf, "affiliate.png"),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Affiliate(bot))
