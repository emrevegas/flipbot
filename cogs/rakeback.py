"""Rakeback: .rakeback — panel tiers, image card, Components V2 claim."""
from __future__ import annotations

import discord
from discord import ui
from discord.ext import commands

from database import db
from modules import flip_utils as utils, image_gen, rakeback_engine
from modules.translator import t
from modules.ui_v2 import ACCENT_BRAND, add_action_row, build_layout, new_container, panel_markdown
from modules.utils import get_user_lang


class _RakebackClaimButton(ui.Button):
    def __init__(self, user_id: int, min_claim: float, lang: str, *, disabled: bool):
        super().__init__(
            label=t("rakeback.withdraw_button", lang=lang)[:80],
            style=discord.ButtonStyle.success,
            disabled=disabled,
        )
        self.user_id = user_id
        self.min_claim = min_claim
        self.lang = lang

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self.lang), ephemeral=True,
            )
        user = await db.get_user(self.user_id)
        accumulated = float((user or {}).get("rakeback_accumulated", 0))
        if accumulated < self.min_claim:
            return await interaction.response.send_message(
                embed=utils.error_embed(
                    f"Minimum claim is **{utils.fmt_pts(self.min_claim)}**. "
                    f"You have **{utils.fmt_pts(accumulated)}**."
                ),
                ephemeral=True,
            )
        amount = await db.claim_rakeback(self.user_id)
        await interaction.response.send_message(
            embed=utils.success_embed(
                f"**+{utils.fmt_pts(amount)}** added to your balance "
                f"(${utils.pts_to_usd(amount):.4f} USD)."
            ),
            ephemeral=True,
        )


def _build_rakeback_layout(
    user_id: int,
    *,
    tier: dict,
    accumulated: float,
    total_wagered: float,
    min_claim: float,
    lang: str,
) -> ui.LayoutView:
    pct = int(float(tier.get("rate", 0)) * 100)
    tier_line = f"**{tier.get('name', 'None')}** — {pct}% rakeback"
    if tier.get("role_id"):
        tier_line = f"<@&{tier['role_id']}> — {pct}%"

    body = (
        f"**{t('rakeback.accumulated_field', lang=lang)}**\n"
        f"{utils.fmt_pts(accumulated)} coins (${utils.pts_to_usd(accumulated):.4f})\n\n"
        f"**{t('rakeback.total_wagered_field', lang=lang)}**\n"
        f"{utils.fmt_pts(total_wagered)} coins\n\n"
        f"**{t('rakeback.tier_field', lang=lang)}**\n"
        f"{tier_line}\n\n"
        f"**Min claim:** {utils.fmt_pts(min_claim)}"
    )

    c = new_container(accent=ACCENT_BRAND)
    c.add_item(ui.TextDisplay(panel_markdown(
        title=t("rakeback.title", lang=lang),
        body=body,
        footer=t("rakeback.footer", lang=lang),
        emoji="💸",
    )))
    add_action_row(
        c,
        _RakebackClaimButton(
            user_id, min_claim, lang,
            disabled=accumulated < min_claim,
        ),
    )
    return build_layout(c, timeout=180)


class Rakeback(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.group(name="rakeback", aliases=["rb", "rake"], invoke_without_command=True)
    async def rakeback(self, ctx: commands.Context):
        """View rakeback card + claim panel."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        if not user:
            return await ctx.send(embed=utils.error_embed("Register first."))

        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        lang = get_user_lang(ctx.author.id)
        total_wagered = float(user["total_wagered"])
        accumulated = float(user["rakeback_accumulated"])
        total_claimed = float(user["rakeback_total_claimed"])
        min_claim = float(rakeback_engine.get_min_withdrawal())

        if member is not None:
            from modules.rakeback_roles import sync_rakeback_tier_roles
            await sync_rakeback_tier_roles(member, total_wagered)

        tier = utils.get_rakeback_tier(total_wagered)
        nxt = utils.get_next_rakeback_tier(total_wagered)

        buf = await image_gen.render_rakeback_card(
            username=ctx.author.display_name,
            accumulated=accumulated,
            total_claimed=total_claimed,
            total_wagered=total_wagered,
            tier_name=tier["name"],
            tier_rate=float(tier["rate"]),
            next_tier_name=nxt["name"] if nxt else None,
            next_tier_min=nxt["min_wagered"] if nxt else None,
        )
        layout = _build_rakeback_layout(
            ctx.author.id,
            tier=tier,
            accumulated=accumulated,
            total_wagered=total_wagered,
            min_claim=min_claim,
            lang=lang,
        )
        await ctx.send(file=discord.File(buf, "rakeback.png"), view=layout)

    @rakeback.command(name="claim")
    async def rakeback_claim(self, ctx: commands.Context):
        """Claim accumulated rakeback."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        accumulated = float(user["rakeback_accumulated"])
        min_claim = float(rakeback_engine.get_min_withdrawal())

        if accumulated < min_claim:
            return await ctx.send(embed=utils.error_embed(
                f"Minimum claim is **{utils.fmt_pts(min_claim)}**. "
                f"You have **{utils.fmt_pts(accumulated)}** accumulated."
            ))

        amount = await db.claim_rakeback(ctx.author.id)
        embed = discord.Embed(
            title="💸 Rakeback Claimed!",
            description=(
                f"**+{utils.fmt_pts(amount)}** added to your balance.\n"
                f"(${utils.pts_to_usd(amount):.4f} USD)"
            ),
            color=0x2ECC71,
        )
        await ctx.send(embed=embed)

    @rakeback.command(name="tiers")
    async def rakeback_tiers(self, ctx: commands.Context):
        """View rakeback tiers from /panel settings."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        user = await db.get_user(ctx.author.id)
        total_wagered = float(user["total_wagered"]) if user else 0
        member = ctx.author if isinstance(ctx.author, discord.Member) else None
        if member is not None:
            from modules.rakeback_roles import sync_rakeback_tier_roles
            await sync_rakeback_tier_roles(member, total_wagered)

        current = utils.get_rakeback_tier(total_wagered)

        lines = []
        for tier in utils.get_all_tiers():
            active = (
                tier.get("name") == current.get("name")
                and float(tier.get("rate", 0)) == float(current.get("rate", 0))
            )
            marker = "▶ " if active else "  "
            pct = int(float(tier.get("rate", 0)) * 100)
            role_part = f"<@&{tier['role_id']}> " if tier.get("role_id") else ""
            lines.append(
                f"{marker}{role_part}**{tier['name']}** — `{pct}%` — "
                f"min wager: `{utils.fmt_pts(tier['min_wagered'])}`"
                + (" ← you" if active else "")
            )

        embed = discord.Embed(
            title="🏆 Rakeback Tiers",
            description="\n".join(lines) if lines else "No tiers configured in `/panel`.",
            color=0xA855F7,
        )
        embed.add_field(name="Your Wager", value=f"`{utils.fmt_pts(total_wagered)}`", inline=True)
        embed.add_field(
            name="Your Tier",
            value=f"**{current['name']}** ({int(float(current['rate']) * 100)}%)",
            inline=True,
        )
        embed.set_footer(text=f"Min claim: {utils.fmt_pts(rakeback_engine.get_min_withdrawal())}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rakeback(bot))
