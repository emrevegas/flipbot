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
        today_earning = 0.0
        for ref in refs:
            t = await db.get_affiliate_today_net(ref["referred_id"])
            today_earning += t["earned_today"]
        buf = await image_gen.render_affiliate_card(
            username=self.member.display_name,
            code=self.aff["code"],
            referrals=len(refs),
            net_earnings=float(self.aff.get("net_earnings", 0)),
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

    @panel_group.command(name="home", description="Admin panel home — platform overview.")
    async def panel_home(self, interaction: discord.Interaction):
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
        active_sessions = (await (await dbc.execute("SELECT COUNT(*) FROM game_sessions")).fetchone())[0]
        total_promos = (await (await dbc.execute("SELECT COUNT(*) FROM promo_codes")).fetchone())[0] or 0

        embed = discord.Embed(title="🎛️ FlipBot Admin Panel", color=0x5865F2)
        if interaction.guild:
            embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else "")
        embed.add_field(name="👥 Users", value=str(total_users), inline=True)
        embed.add_field(name="💰 Total Balance", value=f"`{utils.fmt_pts(total_bal)} pts`", inline=True)
        embed.add_field(name="🎲 Total Wagered", value=f"`{utils.fmt_pts(total_wagered)} pts`", inline=True)
        embed.add_field(name="📥 Total Deposited", value=f"`{utils.fmt_pts(total_deposited)} pts`", inline=True)
        embed.add_field(name="🤝 Affiliates", value=str(total_affiliates), inline=True)
        embed.add_field(name="🎯 Active Games", value=str(active_sessions), inline=True)
        embed.add_field(name="🎟️ Promo Codes", value=str(total_promos), inline=True)
        embed.set_footer(text=f"Use /panel <section> to manage each area  •  Admin: {interaction.user.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        total_promo_uses = (await (await dbc.execute("SELECT SUM(uses) FROM promo_codes")).fetchone())[0] or 0

        embed = discord.Embed(title="📊 Platform Stats", color=0x5865F2)
        embed.add_field(name="Users", value=str(total_users), inline=True)
        embed.add_field(name="Total Balance", value=f"`{utils.fmt_pts(total_bal)} pts`", inline=True)
        embed.add_field(name="Total Wagered", value=f"`{utils.fmt_pts(total_wagered)} pts`", inline=True)
        embed.add_field(name="Total Deposited", value=f"`{utils.fmt_pts(total_deposited)} pts`", inline=True)
        embed.add_field(name="Affiliates", value=str(total_affiliates), inline=True)
        embed.add_field(name="Promo Redemptions", value=str(total_promo_uses), inline=True)
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
        is_banned = await db.is_banned(member.id)
        is_muted = await db.is_muted(member.id)

        embed = discord.Embed(title=f"🛠️ Admin — {member.display_name}", color=0xF59E0B)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Balance", value=f"`{utils.fmt_pts(float(user['balance']))} pts`", inline=True)
        embed.add_field(name="Wagered", value=f"`{utils.fmt_pts(float(user['total_wagered']))} pts`", inline=True)
        embed.add_field(name="Deposited", value=f"`{utils.fmt_pts(float(user['total_deposited']))} pts`", inline=True)
        embed.add_field(name="Rakeback Acc.", value=f"`{utils.fmt_pts(float(user['rakeback_accumulated']))} pts`", inline=True)
        if aff:
            embed.add_field(name="Affiliate", value=f"`{aff['code']}`", inline=True)
        status_flags = []
        if is_banned:
            status_flags.append("🚫 Banned")
        if is_muted:
            status_flags.append("🔇 Muted")
        if status_flags:
            embed.add_field(name="Status", value=" | ".join(status_flags), inline=False)

        view = _AdminUserView(member, interaction.user, is_banned=is_banned, is_muted=is_muted)
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

    @panel_group.command(name="rakeback", description="Manage rakeback tiers.")
    async def panel_rakeback(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                embed=utils.error_embed("Admins only."), ephemeral=True
            )
        tiers = await db.get_rakeback_tiers()
        embed = discord.Embed(title="♻️ Rakeback Tiers", color=0xA855F7)
        lines = [
            f"**{t['name']}** — min wager: `{utils.fmt_pts(t['min_wagered'])} pts` — rate: `{int(t['rate']*100)}%`"
            for t in tiers
        ]
        embed.description = "\n".join(lines) or "No tiers configured."
        embed.set_footer(text="Use the buttons below to manage tiers.")
        view = _RakebackTierView(interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @panel_group.command(name="games", description="Manage game settings (enable/disable, bet limits, house edge).")
    async def panel_games(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                embed=utils.error_embed("Admins only."), ephemeral=True
            )
        games = await db.get_all_games()
        embed = discord.Embed(title="🎮 Game Settings", color=0xFEE75C)
        for g in games:
            status = "✅ Enabled" if g["enabled"] else "❌ Disabled"
            embed.add_field(
                name=f"{g['name']} (`{g['id']}`)",
                value=(
                    f"{status}\n"
                    f"Bet: `{utils.fmt_pts(g['min_bet'])}` – `{utils.fmt_pts(g['max_bet'])} pts`\n"
                    f"House edge: `{float(g['house_edge'])*100:.1f}%`"
                ),
                inline=True,
            )
        embed.set_footer(text="Use the buttons below to configure a game.")
        view = _GameSettingsView(interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @panel_group.command(name="promo", description="View and manage promo codes.")
    async def panel_promo(self, interaction: discord.Interaction):
        if not self._is_admin(interaction):
            return await interaction.response.send_message(
                embed=utils.error_embed("Admins only."), ephemeral=True
            )
        dbc = await db.get_db()
        rows = await (await dbc.execute(
            "SELECT code, reward, uses, max_uses, enabled FROM promo_codes ORDER BY created_at DESC LIMIT 20"
        )).fetchall()

        embed = discord.Embed(title="🎟️ Promo Codes", color=0x5865F2)
        if rows:
            lines = []
            for row in rows:
                status = "✅" if row["enabled"] else "❌"
                uses_str = f"{row['uses']}/{row['max_uses']}" if row["max_uses"] else f"{row['uses']}/∞"
                lines.append(
                    f"{status} `{row['code']}` — **{utils.fmt_pts(row['reward'])} pts** — {uses_str} uses"
                )
            embed.description = "\n".join(lines)
        else:
            embed.description = "No promo codes yet."
        embed.set_footer(text="Use the buttons to create or delete promo codes.")
        view = _PromoManageView(interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _AdminUserView(discord.ui.View):
    def __init__(
        self,
        target: discord.Member,
        admin: discord.Member | discord.User,
        is_banned: bool = False,
        is_muted: bool = False,
    ):
        super().__init__(timeout=120)
        self.target = target
        self.admin = admin
        self.is_banned = is_banned
        self.is_muted = is_muted

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

    @discord.ui.button(label="Ban / Unban", style=discord.ButtonStyle.danger, emoji="🚫", row=1)
    async def ban_toggle(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.admin.id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        if self.is_banned:
            await db.unban_user(self.target.id)
            self.is_banned = False
            msg = f"✅ **{self.target.display_name}** has been **unbanned**."
        else:
            await interaction.response.send_modal(_BanModal(self.target))
            return
        await interaction.response.send_message(embed=utils.success_embed(msg), ephemeral=True)

    @discord.ui.button(label="Mute / Unmute", style=discord.ButtonStyle.secondary, emoji="🔇", row=1)
    async def mute_toggle(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.admin.id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        if self.is_muted:
            await db.unmute_user(self.target.id)
            self.is_muted = False
            msg = f"✅ **{self.target.display_name}** has been **unmuted**."
        else:
            await db.mute_user(self.target.id, banned_by=str(interaction.user.id))
            self.is_muted = True
            msg = f"🔇 **{self.target.display_name}** has been **muted** from games."
        await interaction.response.send_message(embed=utils.success_embed(msg), ephemeral=True)

    @discord.ui.button(label="Clear Game Session", style=discord.ButtonStyle.secondary, emoji="🗑️", row=1)
    async def clear_session(self, interaction: discord.Interaction, _):
        if interaction.user.id != self.admin.id:
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        sess = await db.get_game_session(self.target.id)
        if not sess:
            return await interaction.response.send_message(
                embed=utils.error_embed("No active game session."), ephemeral=True
            )
        await db.clear_game_session(self.target.id)
        await interaction.response.send_message(
            embed=utils.success_embed(f"Cleared **{sess['game']}** session for {self.target.mention}."),
            ephemeral=True,
        )


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


class _BanModal(discord.ui.Modal, title="Ban User"):
    reason_input = discord.ui.TextInput(
        label="Reason", placeholder="e.g. Chargeback / abuse", required=False, max_length=200
    )

    def __init__(self, target: discord.Member):
        super().__init__()
        self.target = target

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value or "No reason provided"
        await db.ban_user(self.target.id, reason=reason, banned_by=str(interaction.user.id))
        await interaction.response.send_message(
            embed=utils.success_embed(
                f"🚫 **{self.target.display_name}** has been banned.\nReason: {reason}"
            ),
            ephemeral=True,
        )


# ── Rakeback tier management views ─────────────────────────────────────────────

class _RakebackTierView(discord.ui.View):
    def __init__(self, admin: discord.Member | discord.User):
        super().__init__(timeout=180)
        self.admin = admin

    def _check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin.id

    @discord.ui.button(label="Add / Edit Tier", style=discord.ButtonStyle.success, emoji="➕")
    async def add_tier(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_TierUpsertModal())

    @discord.ui.button(label="Delete Tier", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def del_tier(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_TierDeleteModal())

    @discord.ui.button(label="Refresh List", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        tiers = await db.get_rakeback_tiers()
        lines = [
            f"**{t['name']}** — min wager: `{utils.fmt_pts(t['min_wagered'])} pts` — rate: `{int(t['rate']*100)}%`"
            for t in tiers
        ]
        embed = discord.Embed(
            title="♻️ Rakeback Tiers",
            description="\n".join(lines) or "No tiers configured.",
            color=0xA855F7,
        )
        embed.set_footer(text="Use the buttons below to manage tiers.")
        await interaction.response.edit_message(embed=embed, view=self)


class _TierUpsertModal(discord.ui.Modal, title="Add / Edit Rakeback Tier"):
    name_input = discord.ui.TextInput(label="Tier Name", placeholder="e.g. Gold", max_length=32)
    min_input  = discord.ui.TextInput(label="Min Wagered (pts)", placeholder="e.g. 25000")
    rate_input = discord.ui.TextInput(label="Rate (%)", placeholder="e.g. 8  →  means 8%")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            min_w = float(self.min_input.value.replace(",", "").replace("_", ""))
            rate  = float(self.rate_input.value.strip().rstrip("%")) / 100
        except ValueError:
            return await interaction.response.send_message(
                embed=utils.error_embed("Invalid number. Min wagered and rate must be numbers."),
                ephemeral=True,
            )
        if not (0 < rate <= 1):
            return await interaction.response.send_message(
                embed=utils.error_embed("Rate must be between 0% and 100%."), ephemeral=True
            )
        name = self.name_input.value.strip()
        await db.upsert_rakeback_tier(name, min_w, rate)
        await utils.refresh_tier_cache()
        await interaction.response.send_message(
            embed=utils.success_embed(
                f"Tier **{name}** saved — min: `{utils.fmt_pts(min_w)} pts`, rate: `{int(rate*100)}%`"
            ),
            ephemeral=True,
        )


class _TierDeleteModal(discord.ui.Modal, title="Delete Rakeback Tier"):
    name_input = discord.ui.TextInput(label="Tier Name to Delete", placeholder="e.g. Silver", max_length=32)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.name_input.value.strip()
        try:
            await db.delete_rakeback_tier(name)
        except ValueError as e:
            return await interaction.response.send_message(embed=utils.error_embed(str(e)), ephemeral=True)
        except Exception:
            return await interaction.response.send_message(
                embed=utils.error_embed(f"Tier `{name}` not found."), ephemeral=True
            )
        await utils.refresh_tier_cache()
        await interaction.response.send_message(
            embed=utils.success_embed(f"Tier **{name}** deleted."), ephemeral=True
        )


# ── Game settings management ────────────────────────────────────────────────────

class _GameSettingsView(discord.ui.View):
    def __init__(self, admin: discord.Member | discord.User):
        super().__init__(timeout=180)
        self.admin = admin

    def _check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin.id

    @discord.ui.button(label="Edit Game", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def edit_game(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_GameEditModal())

    @discord.ui.button(label="Toggle Enable", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def toggle_game(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_GameToggleModal())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔃")
    async def refresh_games(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        games = await db.get_all_games()
        embed = discord.Embed(title="🎮 Game Settings", color=0xFEE75C)
        for g in games:
            status = "✅ Enabled" if g["enabled"] else "❌ Disabled"
            embed.add_field(
                name=f"{g['name']} (`{g['id']}`)",
                value=(
                    f"{status}\n"
                    f"Bet: `{utils.fmt_pts(g['min_bet'])}` – `{utils.fmt_pts(g['max_bet'])} pts`\n"
                    f"House edge: `{float(g['house_edge'])*100:.1f}%`"
                ),
                inline=True,
            )
        embed.set_footer(text="Use the buttons below to configure a game.")
        await interaction.response.edit_message(embed=embed, view=self)


class _GameEditModal(discord.ui.Modal, title="Edit Game Settings"):
    game_id_input = discord.ui.TextInput(label="Game ID", placeholder="e.g. blackjack, mines, slots", max_length=20)
    min_bet_input = discord.ui.TextInput(label="Min Bet (pts)", placeholder="e.g. 10", required=False)
    max_bet_input = discord.ui.TextInput(label="Max Bet (pts)", placeholder="e.g. 100000", required=False)
    house_edge_input = discord.ui.TextInput(label="House Edge (%)", placeholder="e.g. 2  → means 2%", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        game_id = self.game_id_input.value.strip().lower()
        cfg = await db.get_game_config(game_id)
        if not cfg:
            return await interaction.response.send_message(
                embed=utils.error_embed(f"Game `{game_id}` not found."), ephemeral=True
            )

        updates = {}
        if self.min_bet_input.value.strip():
            try:
                updates["min_bet"] = float(self.min_bet_input.value.replace(",", ""))
            except ValueError:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Invalid min bet."), ephemeral=True
                )
        if self.max_bet_input.value.strip():
            try:
                updates["max_bet"] = float(self.max_bet_input.value.replace(",", ""))
            except ValueError:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Invalid max bet."), ephemeral=True
                )
        if self.house_edge_input.value.strip():
            try:
                he = float(self.house_edge_input.value.strip().rstrip("%")) / 100
                updates["house_edge"] = he
            except ValueError:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Invalid house edge."), ephemeral=True
                )

        if not updates:
            return await interaction.response.send_message(
                embed=utils.error_embed("No changes provided."), ephemeral=True
            )

        dbc = await db.get_db()
        set_parts = ", ".join(f"{k}=?" for k in updates)
        await dbc.execute(
            f"UPDATE games SET {set_parts} WHERE id=?",
            (*updates.values(), game_id),
        )
        await dbc.commit()

        changed = ", ".join(
            f"{k}=`{utils.fmt_pts(v) + ' pts' if 'bet' in k else str(round(v*100,1))+'%'}`"
            for k, v in updates.items()
        )
        await interaction.response.send_message(
            embed=utils.success_embed(f"Game **{cfg['name']}** updated: {changed}"),
            ephemeral=True,
        )


class _GameToggleModal(discord.ui.Modal, title="Toggle Game"):
    game_id_input = discord.ui.TextInput(label="Game ID", placeholder="e.g. blackjack, mines", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        game_id = self.game_id_input.value.strip().lower()
        cfg = await db.get_game_config(game_id)
        if not cfg:
            return await interaction.response.send_message(
                embed=utils.error_embed(f"Game `{game_id}` not found."), ephemeral=True
            )
        new_state = 0 if cfg["enabled"] else 1
        dbc = await db.get_db()
        await dbc.execute("UPDATE games SET enabled=? WHERE id=?", (new_state, game_id))
        await dbc.commit()
        status = "✅ Enabled" if new_state else "❌ Disabled"
        await interaction.response.send_message(
            embed=utils.success_embed(f"Game **{cfg['name']}** is now {status}."),
            ephemeral=True,
        )


# ── Promo code management ───────────────────────────────────────────────────────

class _PromoManageView(discord.ui.View):
    def __init__(self, admin: discord.Member | discord.User):
        super().__init__(timeout=180)
        self.admin = admin

    def _check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.admin.id

    @discord.ui.button(label="Create Promo", style=discord.ButtonStyle.success, emoji="➕")
    async def create_promo(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_PromoCreateModal())

    @discord.ui.button(label="Delete Promo", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_promo(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        await interaction.response.send_modal(_PromoDeleteModal())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, _):
        if not self._check(interaction):
            return await interaction.response.send_message(embed=utils.error_embed("Not your panel."), ephemeral=True)
        dbc = await db.get_db()
        rows = await (await dbc.execute(
            "SELECT code, reward, uses, max_uses, enabled FROM promo_codes ORDER BY created_at DESC LIMIT 20"
        )).fetchall()
        embed = discord.Embed(title="🎟️ Promo Codes", color=0x5865F2)
        if rows:
            lines = []
            for row in rows:
                status = "✅" if row["enabled"] else "❌"
                uses_str = f"{row['uses']}/{row['max_uses']}" if row["max_uses"] else f"{row['uses']}/∞"
                lines.append(
                    f"{status} `{row['code']}` — **{utils.fmt_pts(row['reward'])} pts** — {uses_str} uses"
                )
            embed.description = "\n".join(lines)
        else:
            embed.description = "No promo codes yet."
        embed.set_footer(text="Use the buttons to create or delete promo codes.")
        await interaction.response.edit_message(embed=embed, view=self)


class _PromoCreateModal(discord.ui.Modal, title="Create Promo Code"):
    code_input = discord.ui.TextInput(label="Code", placeholder="e.g. WELCOME100", max_length=32)
    reward_input = discord.ui.TextInput(label="Reward (pts)", placeholder="e.g. 500")
    max_uses_input = discord.ui.TextInput(label="Max Uses (0 = unlimited)", placeholder="e.g. 100 or 0", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        code = self.code_input.value.strip().upper()
        try:
            reward = float(self.reward_input.value.replace(",", ""))
        except ValueError:
            return await interaction.response.send_message(
                embed=utils.error_embed("Invalid reward amount."), ephemeral=True
            )
        try:
            max_uses = int(self.max_uses_input.value or 0)
        except ValueError:
            max_uses = 0

        existing = await db.get_promo(code)
        if existing:
            return await interaction.response.send_message(
                embed=utils.error_embed(f"Code `{code}` already exists."), ephemeral=True
            )

        dbc = await db.get_db()
        import time as _time
        await dbc.execute(
            """INSERT INTO promo_codes (code, reward, max_uses, uses, enabled, created_at)
               VALUES (?, ?, ?, 0, 1, ?)""",
            (code, reward, max_uses if max_uses > 0 else None, int(_time.time())),
        )
        await dbc.commit()
        max_str = str(max_uses) if max_uses > 0 else "unlimited"
        await interaction.response.send_message(
            embed=utils.success_embed(
                f"✅ Promo code **`{code}`** created!\n"
                f"Reward: `{utils.fmt_pts(reward)} pts` | Max uses: `{max_str}`"
            ),
            ephemeral=True,
        )


class _PromoDeleteModal(discord.ui.Modal, title="Delete Promo Code"):
    code_input = discord.ui.TextInput(label="Code to Delete", placeholder="e.g. WELCOME100", max_length=32)

    async def on_submit(self, interaction: discord.Interaction):
        code = self.code_input.value.strip().upper()
        existing = await db.get_promo(code)
        if not existing:
            return await interaction.response.send_message(
                embed=utils.error_embed(f"Code `{code}` not found."), ephemeral=True
            )
        dbc = await db.get_db()
        await dbc.execute("DELETE FROM promo_codes WHERE code=?", (code,))
        await dbc.commit()
        await interaction.response.send_message(
            embed=utils.success_embed(f"Promo code **`{code}`** deleted."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(UserPanel(bot))
    await bot.add_cog(AdminPanel(bot))
