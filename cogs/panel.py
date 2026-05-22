"""Slash-command panels: /panel /user_panel — Components V2 style."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database import db
from modules import image_gen, utils
import config


# ── /user_panel ────────────────────────────────────────────────────────────────

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
            total_claimed=float(self.user["rakeback_total_claimed"]),
            total_wagered=wagered,
            tier_name=tier["name"],
            tier_rate=tier["rate"],
            next_tier_name=next_tier["name"] if next_tier else None,
            next_tier_min=next_tier["min_wagered"] if next_tier else None,
        )
        await interaction.response.send_message(
            file=discord.File(buf, "rakeback.png"), ephemeral=True
        )

    @discord.ui.button(label="Affiliate Card", style=discord.ButtonStyle.secondary, emoji="🤝")
    async def affiliate_card(self, interaction: discord.Interaction, _):
        if not self.aff:
            return await interaction.response.send_message(
                embed=utils.error_embed("No affiliate code. Use `.affiliate create <CODE>`"),
                ephemeral=True,
            )
        refs = await db.get_affiliate_refs(self.member.id)
        ftd_count = sum(1 for r in refs if r["ftd_paid"])
        buf = await image_gen.render_affiliate_card(
            username=self.member.display_name,
            code=self.aff["code"],
            referrals=len(refs),
            ftd=ftd_count,
            ftd_earnings=float(self.aff["ftd_earnings"]),
            edge_earnings=float(self.aff["edge_earnings"]),
            claimable=float(self.aff["claimable"]),
            total_claimed=float(self.aff["total_claimed"]),
        )
        await interaction.response.send_message(
            file=discord.File(buf, "affiliate.png"), ephemeral=True
        )


# ── /panel (admin) ─────────────────────────────────────────────────────────────

class AdminPanel(commands.Cog):
    panel_group = app_commands.Group(name="panel", description="Admin panel")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in config.OWNER_IDS:
            return True
        if interaction.guild and interaction.user.guild_permissions.administrator:
            return True
        return False

    @panel_group.command(name="stats", description="Platform-wide stats.")
    async def panel_stats(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                embed=utils.error_embed("Admins only."), ephemeral=True
            )
        dbc = await db.get_db()

        total_users = (await (await dbc.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        total_bal = (await (await dbc.execute("SELECT SUM(balance) FROM users")).fetchone())[0] or 0
        total_wagered = (await (await dbc.execute("SELECT SUM(total_wagered) FROM users")).fetchone())[0] or 0
        total_deposited = (await (await dbc.execute("SELECT SUM(total_deposited) FROM users")).fetchone())[0] or 0
        total_affiliates = (await (await dbc.execute("SELECT COUNT(*) FROM affiliates")).fetchone())[0]
        total_promos = (await (await dbc.execute("SELECT SUM(uses) FROM promo_codes")).fetchone())[0] or 0

        embed = discord.Embed(title="📊 Platform Stats", color=0x5865F2)
        embed.add_field(name="Users", value=str(total_users), inline=True)
        embed.add_field(name="Total Balance", value=f"`{utils.fmt_pts(total_bal)} pts`", inline=True)
        embed.add_field(name="Total Wagered", value=f"`{utils.fmt_pts(total_wagered)} pts`", inline=True)
        embed.add_field(name="Total Deposited", value=f"`{utils.fmt_pts(total_deposited)} pts`", inline=True)
        embed.add_field(name="Affiliates", value=str(total_affiliates), inline=True)
        embed.add_field(name="Promo Redemptions", value=str(total_promos), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @panel_group.command(name="user", description="View/modify a specific user.")
    @app_commands.describe(member="The user to manage")
    async def panel_user(self, interaction: discord.Interaction, member: discord.Member):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                embed=utils.error_embed("Admins only."), ephemeral=True
            )
        await db.ensure_user(member.id, member.name)
        user = await db.get_user(member.id)
        aff = await db.get_affiliate(member.id)

        embed = discord.Embed(title=f"🛠️ Admin — {member.display_name}", color=0xF59E0B)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Balance", value=f"`{utils.fmt_pts(float(user['balance']))} pts`", inline=True)
        embed.add_field(name="Wagered", value=f"`{utils.fmt_pts(float(user['total_wagered']))} pts`", inline=True)
        embed.add_field(name="Deposited", value=f"`{utils.fmt_pts(float(user['total_deposited']))} pts`", inline=True)
        embed.add_field(name="Rakeback Acc.", value=f"`{utils.fmt_pts(float(user['rakeback_accumulated']))} pts`", inline=True)
        if aff:
            embed.add_field(name="Affiliate", value=f"`{aff['code']}`", inline=True)

        view = _AdminUserView(member, interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @panel_group.command(name="leaderboard", description="View top balances.")
    async def panel_lb(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                embed=utils.error_embed("Admins only."), ephemeral=True
            )
        rows = await db.leaderboard(10)
        buf = await image_gen.render_leaderboard_card(rows, self.bot)
        await interaction.response.send_message(
            file=discord.File(buf, "leaderboard.png"), ephemeral=True
        )


class _AdminUserView(discord.ui.View):
    def __init__(self, target: discord.Member, admin: discord.Member):
        super().__init__(timeout=120)
        self.target = target
        self.admin = admin

    @discord.ui.button(label="Add Balance", style=discord.ButtonStyle.success, emoji="➕")
    async def add_bal(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.admin.id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_BalanceModal(self.target, "add"))

    @discord.ui.button(label="Remove Balance", style=discord.ButtonStyle.danger, emoji="➖")
    async def remove_bal(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.admin.id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_BalanceModal(self.target, "remove"))

    @discord.ui.button(label="Set Balance", style=discord.ButtonStyle.secondary, emoji="🎯")
    async def set_bal(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.admin.id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_BalanceModal(self.target, "set"))


class _BalanceModal(discord.ui.Modal):
    amount_input = discord.ui.TextInput(label="Amount (pts)", placeholder="e.g. 500", max_length=20)
    note_input = discord.ui.TextInput(label="Note (optional)", required=False, max_length=100)

    def __init__(self, target: discord.Member, action: str):
        super().__init__(title=f"{action.title()} Balance — {target.display_name}")
        self.target = target
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = float(self.amount_input.value.replace(",", ""))
        except ValueError:
            return await interaction.response.send_message(
                embed=utils.error_embed("Invalid amount."), ephemeral=True
            )
        note = self.note_input.value or f"panel {self.action}"
        if self.action == "add":
            new_bal = await db.add_balance(self.target.id, amount, note=note, by=str(interaction.user.id))
        elif self.action == "remove":
            new_bal = await db.add_balance(self.target.id, -amount, note=note, by=str(interaction.user.id))
        else:
            new_bal = await db.set_balance(self.target.id, amount, note=note, by=str(interaction.user.id))

        await interaction.response.send_message(
            embed=utils.success_embed(
                f"{self.action.title()} **{utils.fmt_pts(amount)} pts** for {self.target.mention}.\n"
                f"New balance: **{utils.fmt_pts(new_bal)} pts**"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(UserPanel(bot))
    await bot.add_cog(AdminPanel(bot))
