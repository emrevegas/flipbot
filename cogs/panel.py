"""Slash-command user panel: /user_panel — Components V2 style."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from modules import image_gen
from modules import flip_utils as utils
import config


class UserPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="user_panel", description="View your full profile panel.")
    @app_commands.describe(member="User to inspect (admin only)")
    async def user_panel(self, interaction: discord.Interaction, member: discord.Member | None = None):
        if member and member.id != interaction.user.id:
            if not (interaction.user.guild_permissions.administrator or
                    interaction.user.id in config.OWNER_IDS):
                return await interaction.response.send_message(
                    embed=utils.error_embed("Admins only."), ephemeral=True
                )

        target = member or interaction.user
        await db.ensure_user(target.id, target.name)
        user = await db.get_user(target.id)
        aff = await db.get_affiliate(target.id)

        embed = discord.Embed(title=f"👤 {target.display_name}", color=0x5865F2)
        embed.set_thumbnail(url=target.display_avatar.url)

        bal = float(user["balance"])
        wagered = float(user["total_wagered"])
        deposited = float(user["total_deposited"])
        embed.add_field(name="💰 Balance", value=f"`{utils.fmt_pts(bal)} pts`\n${utils.pts_to_usd(bal):.2f} USD", inline=True)
        embed.add_field(name="🎲 Wagered", value=f"`{utils.fmt_pts(wagered)} pts`", inline=True)
        embed.add_field(name="📥 Deposited", value=f"`{utils.fmt_pts(deposited)} pts`", inline=True)

        tier = utils.get_rakeback_tier(wagered)
        rb_accum = float(user["rakeback_accumulated"])
        rb_claimed = float(user["rakeback_total_claimed"])
        embed.add_field(
            name=f"♻️ Rakeback ({tier['name']})",
            value=f"Accumulated: `{utils.fmt_pts(rb_accum)} pts`\nClaimed: `{utils.fmt_pts(rb_claimed)} pts`",
            inline=True,
        )

        if aff:
            refs = await db.get_affiliate_refs(target.id)
            embed.add_field(
                name="🤝 Affiliate",
                value=(
                    f"Code: `{aff['code']}`\n"
                    f"Referrals: **{len(refs)}** | Claimable: `{utils.fmt_pts(float(aff['claimable']))} pts`"
                ),
                inline=True,
            )

        view = _UserPanelView(target, user, aff)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _UserPanelView(discord.ui.View):
    def __init__(self, member: discord.Member, user: dict, aff: dict | None):
        super().__init__(timeout=120)
        self.member = member
        self.user = user
        self.aff = aff

    @discord.ui.button(label="Balance Card", style=discord.ButtonStyle.primary, emoji="💳")
    async def balance_card(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.member.id and interaction.user.id not in config.OWNER_IDS:
            if not interaction.user.guild_permissions.administrator:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Not your panel."), ephemeral=True
                )
        buf = await image_gen.render_balance_card(
            username=self.member.display_name,
            balance=float(self.user["balance"]),
            avatar_url=str(self.member.display_avatar.url),
            total_wagered=float(self.user["total_wagered"]),
            total_deposited=float(self.user["total_deposited"]),
        )
        await interaction.response.send_message(
            file=discord.File(buf, "balance.png"), ephemeral=True
        )

    @discord.ui.button(label="Rakeback Card", style=discord.ButtonStyle.secondary, emoji="♻️")
    async def rakeback_card(self, interaction: discord.Interaction, _):
        wagered = float(self.user["total_wagered"])
        tier = utils.get_rakeback_tier(wagered)
        next_tier = utils.get_next_rakeback_tier(wagered)
        buf = await image_gen.render_rakeback_card(
            username=self.member.display_name,
            accumulated=float(self.user["rakeback_accumulated"]),
            tier_name=tier["name"],
            tier_rate=tier["rate"],
            total_wagered=wagered,
            next_tier=next_tier,
        )
        await interaction.response.send_message(
            file=discord.File(buf, "rakeback.png"), ephemeral=True
        )

    @discord.ui.button(label="Affiliate Card", style=discord.ButtonStyle.secondary, emoji="🤝")
    async def affiliate_card(self, interaction: discord.Interaction, _):
        if not self.aff:
            return await interaction.response.send_message(
                embed=utils.error_embed("You don't have an affiliate code."), ephemeral=True
            )
        refs = await db.get_affiliate_refs(self.member.id)
        today_earning = await db.get_affiliate_today_earning(self.member.id)
        buf = await image_gen.render_affiliate_card(
            username=self.member.display_name,
            code=self.aff["code"],
            referrals=len(refs),
            claimable=float(self.aff["claimable"]),
            total_claimed=float(self.aff["total_claimed"]),
            today_earning=today_earning,
        )
        await interaction.response.send_message(
            file=discord.File(buf, "affiliate.png"), ephemeral=True
        )

    @discord.ui.button(label="Stats Card", style=discord.ButtonStyle.secondary, emoji="📊")
    async def stats_card(self, interaction: discord.Interaction, _):
        stats = await db.get_user_stats(self.member.id)
        wagered = float(self.user["total_wagered"])
        loop = __import__("asyncio").get_event_loop()
        buf = await loop.run_in_executor(
            None,
            image_gen.render_stats_card,
            self.member.display_name,
            stats,
            wagered,
        )
        await interaction.response.send_message(
            file=discord.File(buf, "stats.png"), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(UserPanel(bot))
