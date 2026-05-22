"""Affiliate system: .affiliate
Commission formula: referrer earns AFFILIATE_NET_RATE (10%) of each referred user's
(daily approved deposits − daily approved withdrawals), settled every midnight UTC.
"""
from __future__ import annotations

import datetime
import logging
import re

import discord
from discord.ext import commands, tasks

from database import db
from modules import image_gen, utils
import config

log = logging.getLogger("flipbot.affiliate")


class Affiliate(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._daily_settle.start()

    def cog_unload(self):
        self._daily_settle.cancel()

    # ── Daily settlement loop ──────────────────────────────────────────────────

    @tasks.loop(time=datetime.time(hour=0, minute=0, tzinfo=datetime.timezone.utc))
    async def _daily_settle(self):
        """Run at 00:00 UTC: settle yesterday's affiliate commissions."""
        yesterday = (
            datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
        ).strftime("%Y-%m-%d")
        try:
            rows = await db.settle_affiliate_daily(yesterday)
            if rows:
                log.info(
                    f"[affiliate] Settled {len(rows)} commissions for {yesterday} "
                    f"(total earned: {sum(r['earned'] for r in rows):.2f} pts)"
                )
        except Exception as exc:
            log.error(f"[affiliate] Daily settle failed for {yesterday}: {exc}", exc_info=exc)

    @_daily_settle.before_loop
    async def _before_daily(self):
        await self.bot.wait_until_ready()

    # ── Commands ───────────────────────────────────────────────────────────────

    @commands.group(name="affiliate", aliases=["aff", "ref"], invoke_without_command=True)
    async def affiliate(self, ctx: commands.Context):
        """Affiliate program. Subcommands: create, stats, use, claim, referred, today"""
        aff = await db.get_affiliate(ctx.author.id)
        if not aff:
            rate_pct = int(config.AFFILIATE_NET_RATE * 100)
            embed = discord.Embed(
                title="🤝 Affiliate Program",
                description=(
                    "You don't have an affiliate code yet.\n\n"
                    f"**How it works:**\n"
                    f"• Someone uses your code with `.affiliate use <CODE>`\n"
                    f"• Each day at midnight, for every person you referred:\n"
                    f"  `(deposits − withdrawals) × {rate_pct}%` is added to your claimable balance\n\n"
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

        await db.create_affiliate(ctx.author.id, code)
        rate_pct = int(config.AFFILIATE_NET_RATE * 100)
        embed = discord.Embed(
            title="✅ Affiliate Code Created",
            description=(
                f"Your code: **`{code}`**\n\n"
                f"You earn **{rate_pct}%** of each referred user's daily net deposits.\n"
                f"Share with: `.affiliate use {code}`"
            ),
            color=0x2ECC71,
        )
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

        rate_pct = int(config.AFFILIATE_NET_RATE * 100)
        embed = discord.Embed(
            title="✅ Affiliate Code Applied",
            description=(
                f"You are now referred by **`{code}`**.\n"
                f"Your referrer will earn {rate_pct}% of your daily net deposits."
            ),
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)

        try:
            owner = ctx.guild.get_member(int(aff["user_id"])) if ctx.guild else None
            if owner:
                await owner.send(embed=discord.Embed(
                    description=f"🎉 **{ctx.author.display_name}** just used your affiliate code `{code}`!",
                    color=0xF59E0B,
                ))
        except Exception:
            pass

    @affiliate.command(name="claim")
    async def affiliate_claim(self, ctx: commands.Context):
        """Claim your affiliate earnings. .affiliate claim"""
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
        await ctx.send(embed=discord.Embed(
            title="💰 Affiliate Earnings Claimed!",
            description=f"**+{utils.fmt_pts(amount)} pts** added to your balance.",
            color=0x2ECC71,
        ))

    @affiliate.command(name="referred")
    async def affiliate_referred(self, ctx: commands.Context):
        """List users referred by you. .affiliate referred"""
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
            today = await db.get_affiliate_today_net(ref["referred_id"])
            net_str = (
                f"📈 Today net: `{utils.fmt_pts(today['net'])} pts`"
                if today["net"] != 0
                else "No activity today"
            )
            lines.append(f"• **{name}** — {net_str}")

        await ctx.send(embed=discord.Embed(
            title=f"👥 Referred Users ({len(refs)})",
            description="\n".join(lines),
            color=0x5865F2,
        ))

    @affiliate.command(name="today")
    async def affiliate_today(self, ctx: commands.Context):
        """Show today's live (unsettled) commissions breakdown. .affiliate today"""
        aff = await db.get_affiliate(ctx.author.id)
        if not aff:
            return await ctx.send(embed=utils.error_embed("No affiliate code yet."))

        refs = await db.get_affiliate_refs(ctx.author.id)
        if not refs:
            return await ctx.send(embed=utils.info_embed("Today", "No referred users yet."))

        rate_pct = int(config.AFFILIATE_NET_RATE * 100)
        total_earned_today = 0.0
        lines = []
        for ref in refs:
            t = await db.get_affiliate_today_net(ref["referred_id"])
            if t["net"] <= 0:
                continue
            total_earned_today += t["earned_today"]
            member = ctx.guild.get_member(int(ref["referred_id"])) if ctx.guild else None
            name = member.display_name if member else f"User {ref['referred_id']}"
            lines.append(
                f"• **{name}** — dep: `{utils.fmt_pts(t['deposits'])}` "
                f"wd: `{utils.fmt_pts(t['withdrawals'])}` "
                f"→ earn: **`+{utils.fmt_pts(t['earned_today'])} pts`**"
            )

        desc = (
            f"**{t['date_str']}** — Rate: {rate_pct}%\n"
            f"Estimated today's earnings: **`{utils.fmt_pts(total_earned_today)} pts`**\n"
            f"*(Settled daily at 00:00 UTC)*\n\n"
        ) if lines else "No positive net activity today."

        if lines:
            desc += "\n".join(lines)

        await ctx.send(embed=discord.Embed(
            title="📊 Today's Affiliate Activity",
            description=desc,
            color=0xF59E0B,
        ))

    # ── Card helper ────────────────────────────────────────────────────────────

    async def _send_affiliate_card(self, ctx: commands.Context, aff: dict):
        refs = await db.get_affiliate_refs(aff["user_id"])

        # Tally today's unsettled earnings across all refs
        today_earning = 0.0
        for ref in refs:
            t = await db.get_affiliate_today_net(ref["referred_id"])
            today_earning += t["earned_today"]

        buf = await image_gen.render_affiliate_card(
            username=ctx.author.display_name,
            code=aff["code"],
            referrals=len(refs),
            net_earnings=float(aff.get("net_earnings", 0)),
            claimable=float(aff["claimable"]),
            total_claimed=float(aff["total_claimed"]),
            today_earning=today_earning,
        )
        await ctx.send(
            content=f"Your Affiliate Code: `{aff['code']}`",
            file=discord.File(buf, "affiliate.png"),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Affiliate(bot))
