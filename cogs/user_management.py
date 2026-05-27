import discord
from discord import app_commands
from discord.ext import commands
from discord import Embed
from modules.database import (
    get_data, set_data, check_permission, get_user_data, set_user_data, get_user_stats,
    get_all_registered_user_ids, clear_user_account, delete_all_user_data, can_delete_user_data,
    is_super_admin, get_super_admin_id,
)
from modules.staff_permissions import (
    MODERATOR_PANEL_ACTIONS,
    can_open_user_panel,
    get_staff_permissions,
    is_moderator_only,
    normalize_permissions,
)
from modules import moderation_log
from modules.player import Player
from modules.translator import t
from modules.utils import format_balance, get_user_lang
import json
import time
from cogs.private_rooms import PlayerStatsView
import modules.bonus as bonus_engine


class UserManagement(commands.Cog):
    def __init__(self, client):
        self.client = client

    @app_commands.command(name="import_guild_emojis", description="Import emojis from another guild where the bot is present")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def import_guild_emojis(self, interaction: discord.Interaction):
        """Open a guild picker, then import selected emojis into the current guild."""
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

        if interaction.guild is None:
            return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)

        me = interaction.guild.get_member(interaction.client.user.id)
        if me is None:
            try:
                me = await interaction.guild.fetch_member(interaction.client.user.id)
            except Exception:
                me = None

        if me is None or not me.guild_permissions.manage_emojis_and_stickers:
            return await interaction.response.send_message(
                "❌ I need the 'Manage Emojis and Stickers' permission in this server.",
                ephemeral=True,
            )

        available_guilds = [g for g in self.client.guilds if g.id != interaction.guild.id]
        if not available_guilds:
            return await interaction.response.send_message(
                "❌ I am not in any other guild to import emojis from.",
                ephemeral=True,
            )

        view = GuildEmojiSourceView(interaction.user.id, interaction.guild.id, available_guilds)
        embed = Embed(
            title="🌐 Select Source Guild",
            description="Pick one guild. Then you will get emoji select menus in 25-item chunks.",
            color=0x3498db,
        )
        embed.set_footer(text="Only you can use this panel.")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
    async def _give_role_to_all_members(
        self,
        guild: discord.Guild,
        role: discord.Role,
        actor: discord.abc.User,
    ) -> tuple[int, int, str | None]:
        """Returns (success, failed, error_message)."""
        me = guild.me
        if me is None:
            try:
                me = await guild.fetch_member(self.client.user.id)
            except Exception:
                return 0, 0, "Bot member not found in this server."

        if role.managed:
            return 0, 0, "That role is managed by an integration and cannot be assigned manually."
        if role >= me.top_role:
            return 0, 0, "I cannot assign that role — it is above my highest role."
        if isinstance(actor, discord.Member) and role >= actor.top_role:
            if not actor.guild_permissions.administrator:
                return 0, 0, "You cannot assign a role that is above your highest role."

        if guild.member_count and len(guild.members) < guild.member_count:
            try:
                await guild.chunk()
            except Exception:
                pass

        success = 0
        failed = 0
        for member in guild.members:
            if member.bot or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason=f"giveroleall by {actor}")
                success += 1
            except Exception:
                failed += 1
        return success, failed, None

    @app_commands.command(
        name="giveroleall",
        description="Give a role to all members in the server (admin)",
    )
    @app_commands.describe(role="The role to give to all members")
    @app_commands.guild_only()
    async def giveroleall(self, interaction: discord.Interaction, role: discord.Role):
        """Give a role to every member — admin only."""
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command!",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        success, failed, err = await self._give_role_to_all_members(
            interaction.guild, role, interaction.user,
        )
        if err:
            return await interaction.followup.send(f"❌ {err}", ephemeral=True)

        embed = discord.Embed(
            title="✅ Role Given",
            description=(
                f"{role.mention} has been given to all members.\n\n"
                f"✅ Success: **{success}**\n❌ Failed: **{failed}**"
            ),
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="give_role_all",
        description="Alias for /giveroleall",
    )
    @app_commands.describe(role="The role to give to all members")
    @app_commands.guild_only()
    async def give_role_all(self, interaction: discord.Interaction, role: discord.Role):
        await self.giveroleall(interaction, role)

    @commands.command(name="giveroleall", aliases=["give_role_all"])
    @commands.guild_only()
    async def giveroleall_prefix(self, ctx: commands.Context, role: discord.Role):
        """Give a role to all members. Example: .giveroleall @VIP"""
        if check_permission(str(ctx.author.id), "admin"):
            return await ctx.send("❌ You don't have permission to use this command!")

        msg = await ctx.send(f"⏳ Giving {role.mention} to all members…")
        success, failed, err = await self._give_role_to_all_members(
            ctx.guild, role, ctx.author,
        )
        if err:
            return await msg.edit(content=f"❌ {err}")

        await msg.edit(
            content=(
                f"✅ {role.mention} given to all members.\n"
                f"Success: **{success}** · Failed: **{failed}**"
            )
        )

    @app_commands.command(name="remove_role_all", description="Remove a role from all members in the server")
    @app_commands.describe(role="The role to remove from all members")
    async def remove_role_all(self, interaction: discord.Interaction, role: discord.Role):
        """Remove a role from every member - Admin only"""
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        success = 0
        failed = 0
        for member in interaction.guild.members:
            if member.bot:
                continue
            if role not in member.roles:
                continue
            try:
                await member.remove_roles(role, reason=f"remove_role_all by {interaction.user}")
                success += 1
            except Exception:
                failed += 1

        embed = discord.Embed(
            title="✅ Role Removed",
            description=f"{role.mention} has been removed from all members.\n\n✅ Success: **{success}**\n❌ Failed: **{failed}**",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="user_panel", description="Manage user settings and data")
    @app_commands.describe(user="The user to manage")
    async def user_panel(self, interaction: discord.Interaction, user: discord.User):
        """User management panel - Admin only"""
        
        admin_id = interaction.user.id
        if not can_open_user_panel(admin_id):
            return await interaction.response.send_message(
                "❌ You don't have permission to use this command!",
                ephemeral=True,
            )

        if not check_permission(str(admin_id), "admin"):
            panel_perms = ["admin"]
        else:
            panel_perms = get_staff_permissions(admin_id)

        view = UserPanelView(user.id, admin_id, panel_perms)

        if is_moderator_only(admin_id) and interaction.guild:
            await moderation_log.log_moderation(
                interaction.client,
                interaction.guild,
                actor_id=admin_id,
                action="Opened **user panel**",
                target_user_id=user.id,
            )
        
        lang = get_user_lang(interaction.user.id)
        
        # Get user info
        player = Player(user.id)
        real_balance = player.get_balance("real")
        demo_balance = player.get_balance("demo")

        # Get registration info
        user_account = get_user_data(user.id, "account") or {}
        user_stats   = get_user_data(user.id, "stats")   or {}

        # Registration date
        created_at = user_account.get("created_at")
        if created_at:
            from datetime import datetime
            try:
                reg_date = datetime.fromtimestamp(float(created_at)).strftime("%d.%m.%Y")
            except Exception:
                reg_date = str(created_at)
        else:
            reg_date = "—"

        embed = Embed(
            title=f"👤 {user.name}",
            description=(
                f"<@{user.id}> · `{user.id}`\n"
                f"**Kayıt tarihi:** {reg_date}"
            ),
            color=0x2b2d31,
        )

        embed.set_thumbnail(url=user.display_avatar.url)

        # Registration info
        if user_account:
            name        = user_account.get("name",        "—")
            age         = user_account.get("age",         "—")
            source      = user_account.get("source",      "—")
            email       = user_account.get("email",       "—")
            referred_by = user_account.get("referred_by")
            if referred_by:
                try:
                    ref_member = interaction.guild.get_member(int(referred_by)) if interaction.guild else None
                    referrer_display = f"<@{ref_member.id}>" if ref_member else f"`{referred_by}`"
                except Exception:
                    referrer_display = f"`{referred_by}`"
            else:
                referrer_display = "*Yok*"
            embed.add_field(
                name="📝 Kayıt Bilgileri",
                value=(
                    f"**İsim:** {name}\n"
                    f"**Yaş:** {age}\n"
                    f"**Kaynak:** {source}\n"
                    f"**E-posta:** {email}\n"
                    f"**Referans:** {referrer_display}"
                ),
                inline=True,
            )
        else:
            embed.add_field(
                name="📝 Kayıt Bilgileri",
                value="*Kayıt bulunamadı*",
                inline=True,
            )

        # Balance info
        embed.add_field(
            name="💰 Bakiye",
            value=(
                f"**Gerçek:** {format_balance(real_balance, 'real')}\n"
                f"**Demo:** {format_balance(demo_balance, 'demo')}"
            ),
            inline=True,
        )

        # Stats summary
        total_wagered  = user_stats.get("total_wagered",  user_stats.get("wager", 0))
        total_deposit  = user_stats.get("total_deposit",  0)
        total_withdraw = user_stats.get("total_withdraw", 0)
        wins           = user_stats.get("wins",  0)
        losses         = user_stats.get("losses", 0)
        total_games    = wins + losses
        wr = f"{round(wins / total_games * 100)}%" if total_games else "—"
        embed.add_field(
            name="📊 İstatistikler",
            value=(
                f"**Bahis:** {format_balance(total_wagered, 'real')}\n"
                f"**Deposit:** {format_balance(total_deposit, 'real')}\n"
                f"**Çekim:** {format_balance(total_withdraw, 'real')}\n"
                f"**W/L:** {wins}/{losses} ({wr})"
            ),
            inline=True,
        )

        embed.set_footer(text=t('user_panel.footer', lang))
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="unregistered_users", description="List server members who have not registered yet")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def unregistered_users(self, interaction: discord.Interaction):
        """Show all guild members without a registration - Admin only"""
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        registered_ids = set(get_all_registered_user_ids())

        unregistered = []
        for member in interaction.guild.members:
            if member.bot:
                continue
            if str(member.id) not in registered_ids:
                unregistered.append(member)

        if not unregistered:
            embed = Embed(
                title="✅ All Members Registered",
                description="Every non-bot member in this server has a registration.",
                color=0x2ecc71
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        chunk_size = 30
        chunks = [unregistered[i:i + chunk_size] for i in range(0, len(unregistered), chunk_size)]

        embed = Embed(
            title=f"📋 Unregistered Members ({len(unregistered)})",
            description=f"The following **{len(unregistered)}** member(s) have not registered:",
            color=0xe67e22
        )

        for idx, chunk in enumerate(chunks[:6]):
            mentions = "\n".join(f"<@{m.id}>" for m in chunk)
            embed.add_field(
                name=f"Members {idx * chunk_size + 1}–{idx * chunk_size + len(chunk)}",
                value=mentions,
                inline=False
            )

        if len(chunks) > 6:
            embed.set_footer(text="Only first 180 unregistered members are shown.")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="reset_all_registrations", description="Delete all user registrations except admins")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def reset_all_registrations(self, interaction: discord.Interaction):
        """Wipe every user's registration data, skipping admins - Admin only"""
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message("❌ You don't have permission to use this command!", ephemeral=True)

        view = ConfirmResetView(interaction.user.id)
        embed = Embed(
            title="⚠️ Confirm Reset All Registrations",
            description=(
                "This will **permanently delete** all user registrations.\n\n"
                "**Admins will be skipped.**\n"
                "This action **cannot be undone**. Are you sure?"
            ),
            color=0xe74c3c
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class UserPanelView(discord.ui.View):
    """Main user panel view with select menu"""
    
    def __init__(self, target_user_id: int, admin_id: int, permissions: list):
        super().__init__(timeout=180)
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.permissions = permissions
        
        options = []
        lang = get_user_lang(admin_id)
        norm = [p.lower() for p in normalize_permissions(permissions)]
        is_admin = "admin" in norm
        mod_only = is_moderator_only(admin_id) or (
            "moderator" in norm and not is_admin
        )

        if is_admin or "cashier" in norm or mod_only:
            options.append(
                discord.SelectOption(
                    label=t('user_panel.add_balance', lang),
                    description=t('user_panel.add_balance_desc', lang),
                    emoji="💰",
                    value="add_balance"
                )
            )
            options.append(
                discord.SelectOption(
                    label=t('user_panel.remove_balance', lang),
                    description=t('user_panel.remove_balance_desc', lang),
                    emoji="💸",
                    value="remove_balance"
                )
            )

        if mod_only:
            options.extend([
                discord.SelectOption(
                    label=t('user_panel.game_history', lang),
                    description=t('user_panel.game_history_desc', lang),
                    emoji="📊",
                    value="game_history",
                ),
                discord.SelectOption(
                    label=t('user_panel.statistics', lang),
                    description=t('user_panel.statistics_desc', lang),
                    emoji="📈",
                    value="statistics",
                ),
                discord.SelectOption(
                    label="Referral Bilgisi",
                    description="Kullanıcının referral kodu, kazancı ve komisyonu",
                    emoji="🔗",
                    value="referral_info",
                ),
                discord.SelectOption(
                    label=t('user_panel.activity_option', lang),
                    description=t('user_panel.activity_option_desc', lang),
                    emoji="📋",
                    value="activity",
                ),
            ])
        elif is_admin or "ticketadmin" in norm:
            options.append(
                discord.SelectOption(
                    label=t('user_panel.game_history', lang),
                    description=t('user_panel.game_history_desc', lang),
                    emoji="📊",
                    value="game_history"
                )
            )
            options.append(
                discord.SelectOption(
                    label=t('user_panel.edit_registration', lang),
                    description=t('user_panel.edit_registration_desc', lang),
                    emoji="📝",
                    value="edit_registration"
                )
            )
            options.append(
                discord.SelectOption(
                    label=t('user_panel.ticket_history', lang),
                    description=t('user_panel.ticket_history_desc', lang),
                    emoji="🎫",
                    value="ticket_history"
                )
            )
            options.append(
                discord.SelectOption(
                    label=t('user_panel.statistics', lang),
                    description=t('user_panel.statistics_desc', lang),
                    emoji="📈",
                    value="statistics"
                )
            )
            options.append(
                discord.SelectOption(
                    label="Referral Bilgisi",
                    description="Kullanıcının referral kodu, kazancı ve komisyonu",
                    emoji="🔗",
                    value="referral_info"
                )
            )
            options.append(
                discord.SelectOption(
                    label=t('user_panel.activity_option', lang),
                    description=t('user_panel.activity_option_desc', lang),
                    emoji="📋",
                    value="activity"
                )
            )

        if is_admin:
            options.append(
                discord.SelectOption(
                    label="Manage Permissions",
                    description="Manage user's permission roles",
                    emoji="🔒",
                    value="manage_permissions"
                )
            )
        
        if is_admin:
            options.append(
                discord.SelectOption(
                    label=t("user_panel.wager_stats", lang),
                    description=t("user_panel.wager_stats_desc", lang),
                    emoji="🎲",
                    value="wager_stats"
                )
            )
            options.append(
                discord.SelectOption(
                    label="Level System",
                    description="View & manage user's level",
                    emoji="🏆",
                    value="level_system"
                )
            )

        if is_admin:
            options.append(
                discord.SelectOption(
                    label="Manage Staff",
                    description="View staff stats and manage staff permissions",
                    emoji="👥",
                    value="manage_staff"
                )
            )

        if is_admin:
            lang = get_user_lang(admin_id)
            options.append(
                discord.SelectOption(
                    label=t('user_panel.bonus_management', lang),
                    description=t('user_panel.bonus_management_desc', lang),
                    emoji="🎁",
                    value="bonus_management"
                )
            )
            options.append(
                discord.SelectOption(
                    label="Balance Cap (Kayıp Eşiği)",
                    description="Max bakiye eşiği — kullanıcı bu tutara ulaşamaz",
                    emoji="🎯",
                    value="balance_cap"
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No actions",
                    value="noop",
                    description="No permission for this panel",
                )
            )

        self.add_item(UserPanelSelect(options, self.target_user_id, self.admin_id, self.permissions))

        if is_admin:
            lang = get_user_lang(admin_id)
            self.add_item(DeleteRegistrationButton(self.target_user_id, self.admin_id, lang))


class DeleteRegistrationButton(discord.ui.Button):
    """Permanently wipe target user's registration and stored data."""

    def __init__(self, target_user_id: int, admin_id: int, lang: str):
        super().__init__(
            label=t("user_panel.delete_registration", lang=lang),
            style=discord.ButtonStyle.danger,
            emoji="🗑️",
            row=1,
        )
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.lang = lang

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message(
                t("user_panel.not_your_panel", lang=self.lang),
                ephemeral=True,
            )

        ok, reason = can_delete_user_data(self.target_user_id)
        if not ok:
            return await interaction.response.send_message(
                embed=Embed(
                    title=t("user_panel.delete_registration_blocked", lang=self.lang),
                    description=reason,
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        account = get_user_data(self.target_user_id, "account") or {}
        name = account.get("name") or str(self.target_user_id)

        embed = Embed(
            title=t("user_panel.delete_registration_confirm_title", lang=self.lang),
            description=t(
                "user_panel.delete_registration_confirm_desc",
                lang=self.lang,
                user=f"<@{self.target_user_id}>",
                name=name,
                uid=self.target_user_id,
            ),
            color=discord.Color.dark_red(),
        )
        await interaction.response.send_message(
            embed=embed,
            view=ConfirmDeleteRegistrationView(self.target_user_id, self.admin_id, self.lang),
            ephemeral=True,
        )


class ConfirmDeleteRegistrationView(discord.ui.View):
    def __init__(self, target_user_id: int, admin_id: int, lang: str):
        super().__init__(timeout=60)
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.lang = lang

    @discord.ui.button(
        label="Evet, kaydı sil",
        style=discord.ButtonStyle.danger,
        emoji="✅",
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message(
                t("user_panel.not_your_panel", lang=self.lang),
                ephemeral=True,
            )

        ok, reason = can_delete_user_data(self.target_user_id)
        if not ok:
            return await interaction.response.send_message(
                embed=Embed(
                    title=t("user_panel.delete_registration_blocked", lang=self.lang),
                    description=reason,
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        try:
            summary = delete_all_user_data(self.target_user_id)
        except Exception as exc:
            return await interaction.followup.send(
                embed=Embed(
                    title="❌ Error",
                    description=str(exc),
                    color=discord.Color.red(),
                ),
                ephemeral=True,
            )

        embed = Embed(
            title=t("user_panel.delete_registration_success", lang=self.lang),
            description=t(
                "user_panel.delete_registration_success_desc",
                lang=self.lang,
                uid=self.target_user_id,
                kv=summary.get("kv_keys", 0),
                blog=summary.get("balance_log_entries", 0),
            ),
            color=discord.Color.green(),
        )
        self.stop()
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="İptal", style=discord.ButtonStyle.secondary, emoji="❌")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message(
                t("user_panel.not_your_panel", lang=self.lang),
                ephemeral=True,
            )
        self.stop()
        await interaction.response.edit_message(
            embed=Embed(
                title=t("user_panel.delete_registration_cancelled", lang=self.lang),
                description=t("user_panel.delete_registration_cancelled_desc", lang=self.lang),
                color=0x95A5A6,
            ),
            view=None,
        )


class UserPanelSelect(discord.ui.Select):
    """Select menu for user panel actions"""
    
    def __init__(self, options: list, target_user_id: int, admin_id: int, permissions: list):
        lang = get_user_lang(admin_id)
        super().__init__(
            placeholder=t('user_panel.select_action', lang),
            options=options,
            min_values=1,
            max_values=1
        )
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.permissions = permissions
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            lang = get_user_lang(interaction.user.id)
            return await interaction.response.send_message(
                t('user_panel.not_your_panel', lang),
                ephemeral=True
            )
        
        lang = get_user_lang(self.admin_id)
        action = self.values[0]

        if action == "noop":
            return await interaction.response.send_message(
                "❌ No actions available for your role.",
                ephemeral=True,
            )

        if is_moderator_only(self.admin_id) and action not in MODERATOR_PANEL_ACTIONS:
            return await interaction.response.send_message(
                "❌ Moderators can only use balance, game history, statistics, referral, and activity log.",
                ephemeral=True,
            )

        if is_moderator_only(self.admin_id) and interaction.guild:
            await moderation_log.log_moderation(
                interaction.client,
                interaction.guild,
                actor_id=self.admin_id,
                action=f"User panel: **{action.replace('_', ' ')}**",
                target_user_id=self.target_user_id,
            )
        
        if action == "add_balance":
            modal = BalanceModal(self.target_user_id, "add", self.admin_id)
            await interaction.response.send_modal(modal)
        
        elif action == "remove_balance":
            modal = BalanceModal(self.target_user_id, "remove", self.admin_id)
            await interaction.response.send_modal(modal)
        
        elif action == "game_history":
            await self.show_game_history(interaction)
        
        elif action == "edit_registration":
            modal = EditRegistrationModal(self.target_user_id, self.admin_id)
            await interaction.response.send_modal(modal)
        
        elif action == "ticket_history":
            await self.show_ticket_history(interaction)
        
        elif action == "statistics":
            await self.show_statistics(interaction)
        
        elif action == "wager_stats":
            await self.show_wager_stats(interaction)

        elif action == "level_system":
            await self.show_level_system(interaction)

        elif action == "cashier_stats":
            await self.show_cashier_stats(interaction)

        elif action == "manage_staff":
            await self.show_manage_staff(interaction)

        elif action == "referral_info":
            await self.show_referral_info(interaction)

        elif action == "bonus_management":
            await self.show_bonus_management(interaction)

        elif action == "balance_cap":
            await self.show_balance_cap(interaction)

        elif action == "activity":
            await self.show_activity(interaction)

        elif action == "manage_permissions":
            # Check if user is full admin
            if "admin" not in self.permissions:
                return await interaction.response.send_message(
                    "❌ You don't have permission to manage user permissions!",
                    ephemeral=True
                )
            
            # Get current user permissions
            admins = get_data("server/admins") or {}
            current_perms = admins.get(str(self.target_user_id), [])
            
            # Create permission management view
            view = PermissionManagementView(self.target_user_id, self.admin_id, current_perms)
            
            # Get user info
            user = interaction.guild.get_member(self.target_user_id)
            if not user:
                user = await interaction.guild.fetch_member(self.target_user_id)
            
            embed = Embed(
                title=f"🔒 Manage Permissions - {user.name}",
                description=f"Configure permission roles for <@{self.target_user_id}>",
                color=0xe74c3c
            )
            
            # Show current permissions
            if current_perms:
                perm_display = {
                    "admin": "🔑 Admin",
                    "moderator": "🛡️ Moderator",
                    "ticketAdmin": "🎫 Ticket Admin",
                    "ban_hammer": "🔨 Ban Hammer",
                    "timeout_hammer": "⏱️ Timeout Hammer"
                }
                perm_text = "\n".join([perm_display.get(p, p) for p in current_perms])
                embed.add_field(
                    name="🏷️ Current Permissions",
                    value=perm_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏷️ Current Permissions",
                    value="No permissions assigned",
                    inline=False
                )
            
            embed.set_footer(text="Select a permission from the menu below")
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def show_balance_cap(self, interaction: discord.Interaction):
        """Admin balance ceiling + auto caps summary."""
        from modules.balance_cap import (
            get_admin_balance_cap,
            get_balance_ceiling,
            get_global_balance_cap,
            get_welcome_balance_cap,
        )
        from modules.player import Player

        lang = get_user_lang(self.admin_id)
        player = Player(self.target_user_id)
        balance = player.get_balance("real")
        admin_cap = get_admin_balance_cap(self.target_user_id)
        global_cap = get_global_balance_cap()
        effective = get_balance_ceiling(self.target_user_id, "real")
        welcome_cap = get_welcome_balance_cap()

        account = get_user_data(self.target_user_id, "account") or {}
        ap = get_user_data(self.target_user_id, "active_promo") or {}
        ab = bonus_engine.get_active_bonus(self.target_user_id)

        lines = [
            f"💰 **Balance:** {format_balance(balance, 'real')}",
            f"🎯 **Effective cap:** {format_balance(effective, 'real') if effective else '— (none)'}",
        ]
        if global_cap:
            lines.append(f"🌐 **Global cap:** {format_balance(global_cap, 'real')}")
        else:
            lines.append("🌐 **Global cap:** disabled")
        if admin_cap:
            lines.append(f"👤 **Admin cap:** {format_balance(admin_cap, 'real')}")
        else:
            lines.append("👤 **Admin cap:** disabled")
        user_welcome_cap = None
        if account.get("welcome_bonus_capped"):
            try:
                from modules.balance_cap import get_user_welcome_balance_cap
                user_welcome_cap = get_user_welcome_balance_cap(self.target_user_id)
            except Exception:
                user_welcome_cap = welcome_cap
            cap_show = user_welcome_cap or welcome_cap
            lines.append(f"🎁 **Welcome auto-cap:** {format_balance(cap_show, 'real')}")
        from modules.promo import get_promo_balance_ceiling

        pmx = get_promo_balance_ceiling(self.target_user_id)
        if pmx:
            lines.append(f"🎟️ **Promo max WD cap:** {format_balance(pmx, 'real')}")
        if ab and ab.get("max_withdrawal"):
            lines.append(f"🎁 **Bonus max WD cap:** {format_balance(int(ab['max_withdrawal']), 'real')}")

        embed = Embed(
            title=f"🎯 Balance Cap — <@{self.target_user_id}>",
            description="\n".join(lines),
            color=0x9B59B6,
        )
        embed.set_footer(text="Effective cap = lowest rule · Near cap wins may lose naturally")

        view = discord.ui.View(timeout=120)
        set_btn = discord.ui.Button(label="Set Cap", style=discord.ButtonStyle.primary, emoji="✏️")
        clear_btn = discord.ui.Button(label="Clear Cap", style=discord.ButtonStyle.secondary, emoji="🗑️")

        async def _set_cb(inter: discord.Interaction):
            await inter.response.send_modal(_SetBalanceCapModal(self.target_user_id, self.admin_id, admin_cap or 0))

        async def _clear_cb(inter: discord.Interaction):
            from modules.balance_cap import set_admin_balance_cap
            set_admin_balance_cap(self.target_user_id, 0, enabled=False)
            await inter.response.send_message(
                embed=Embed(
                    title="✅ Cap Cleared",
                    description="Admin balance cap removed. Auto rules (welcome/promo/bonus) still apply if applicable.",
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )

        set_btn.callback = _set_cb
        clear_btn.callback = _clear_cb
        view.add_item(set_btn)
        view.add_item(clear_btn)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_bonus_management(self, interaction: discord.Interaction):
        """Show the target user's active bonus details and allow cancellation."""
        lang = get_user_lang(self.admin_id)
        active = bonus_engine.get_active_bonus(self.target_user_id)

        try:
            target_user = await interaction.client.fetch_user(self.target_user_id)
            display_name = target_user.name
        except Exception:
            display_name = str(self.target_user_id)

        embed = Embed(
            title=t('user_panel.bonus_management_title', lang, name=display_name),
            color=0xf39c12,
        )

        if not active:
            embed.description = t('user_panel.bonus_no_active', lang)
            embed.color = 0x95a5a6
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        bonus_name = active.get("bonus_name", "—")
        btype = active.get("type", "fixed")
        deposit_amount = int(active.get("deposit_amount", 0))
        bonus_amount = int(active.get("bonus_amount", 0))
        wager_req = int(active.get("wager_requirement", 0))
        wagered_so_far = int(active.get("wagered_so_far", 0))
        max_withdrawal = active.get("max_withdrawal")
        min_balance_forfeit = int(active.get("min_balance_forfeit", 0))
        activated_at = active.get("activated_at", 0)

        import datetime
        activated_str = (
            discord.utils.format_dt(datetime.datetime.fromtimestamp(activated_at, tz=datetime.timezone.utc), style="f")
            if activated_at
            else "—"
        )

        btype_label = t('user_panel.bonus_type_fixed', lang) if btype == "fixed" else t('user_panel.bonus_type_pct', lang)
        embed.add_field(name=t('user_panel.bonus_name_field', lang), value=bonus_name, inline=True)
        embed.add_field(name=t('user_panel.bonus_type_field', lang), value=btype_label, inline=True)
        embed.add_field(name=t('user_panel.bonus_activated_field', lang), value=activated_str, inline=True)
        embed.add_field(
            name=t('user_panel.bonus_deposit_field', lang),
            value=format_balance(deposit_amount, "real"),
            inline=True,
        )
        if btype == "percentage" and bonus_amount:
            embed.add_field(
                name=t('user_panel.bonus_amount_field', lang),
                value=format_balance(bonus_amount, "real"),
                inline=True,
            )

        if btype == "fixed":
            player = Player(self.target_user_id)
            current_balance = player.get_balance("real")
            progress_pct = min(100, round(current_balance / wager_req * 100)) if wager_req else 0
            filled = round(progress_pct / 10)
            bar = "🟩" * filled + "⬛" * (10 - filled)
            embed.add_field(
                name=t('user_panel.bonus_wager_fixed', lang),
                value=(
                    f"{bar} **%{progress_pct}**\n"
                    f"{t('user_panel.bonus_current', lang)}: {format_balance(current_balance, 'real')} / "
                    f"{t('user_panel.bonus_target', lang)}: {format_balance(wager_req, 'real')}"
                ),
                inline=False,
            )
        else:
            progress_pct = min(100, round(wagered_so_far / wager_req * 100)) if wager_req else 0
            filled = round(progress_pct / 10)
            bar = "🟩" * filled + "⬛" * (10 - filled)
            remaining = max(wager_req - wagered_so_far, 0)
            embed.add_field(
                name=t('user_panel.bonus_wager_pct', lang),
                value=(
                    f"{bar} **%{progress_pct}**\n"
                    f"{t('user_panel.bonus_wagered', lang)}: {format_balance(wagered_so_far, 'real')} / "
                    f"{t('user_panel.bonus_required', lang)}: {format_balance(wager_req, 'real')}\n"
                    f"{t('user_panel.bonus_remaining', lang)}: {format_balance(remaining, 'real')}"
                ),
                inline=False,
            )

        if max_withdrawal:
            embed.add_field(
                name=t('user_panel.bonus_max_withdrawal', lang),
                value=format_balance(int(max_withdrawal), "real"),
                inline=True,
            )
        if min_balance_forfeit:
            embed.add_field(
                name=t('user_panel.bonus_min_balance', lang),
                value=format_balance(min_balance_forfeit, "real"),
                inline=True,
            )

        embed.set_footer(text=t('user_panel.bonus_footer', lang))
        view = UserBonusCancelView(self.target_user_id, self.admin_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_ticket_history(self, interaction: discord.Interaction):
        """Show user's ticket history"""
        lang = get_user_lang(self.admin_id)
        
        # Get ticket history from user's data
        ticket_history = get_user_data(self.target_user_id, "ticket_history") or []
        
        if not ticket_history:
            embed = Embed(
                title="🎫 Ticket History",
                description="This user has no ticket history.",
                color=0x95a5a6
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Show tickets with pagination if needed
        view = TicketHistoryView(ticket_history, self.target_user_id, self.admin_id)
        embed = view.get_embed(0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def show_game_history(self, interaction: discord.Interaction):
        """Show user's game history"""
        lang = get_user_lang(self.admin_id)
        
        # Get game history from user's data
        game_history = get_user_data(self.target_user_id, "game_history") or {}
        
        if not game_history:
            embed = Embed(
                title="📊 " + t('user_panel.game_history', lang),
                description=t('user_panel.no_history', lang),
                color=0x95a5a6
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Show last 15 games
        history_items = list(game_history.items())[-15:]
        
        total_wagered = 0
        total_won = 0
        total_lost = 0
        total_tied = 0
        
        lines = []
        for game_id, data in reversed(history_items):
            game_name  = data.get("game", "Unknown")
            bet_amount = data.get("bet", 0)
            result     = data.get("result", "lose")
            # payout: try multiple keys used by different games
            payout = data.get("payout", data.get("win", data.get("amount", 0)))
            mult   = data.get("multiplier")
            
            total_wagered += bet_amount
            
            if result == "win":
                total_won += payout
                result_emoji = "✅"
                net_str = f"+{format_balance(payout, 'real')}"
            elif result == "tie":
                total_tied += bet_amount
                result_emoji = "🔁"
                net_str = f"±{format_balance(bet_amount, 'real')}"
            else:
                total_lost += bet_amount
                result_emoji = "❌"
                net_str = f"-{format_balance(bet_amount, 'real')}"
            
            mult_str = f" `{mult}x`" if mult and float(mult) > 0 else ""
            ts = data.get("timestamp", "")
            try:
                from datetime import datetime
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                ts_str = dt.strftime('%d/%m %H:%M')
            except Exception:
                ts_str = ts or "—"
            lines.append(
                f"🎮 **{game_name}**{mult_str}\n"
                f"💰 `{bet_amount:,}` → {result_emoji} {net_str}\n"
                f"📅 `{ts_str}`"
            )
        
        history_text = "\n\n".join(lines)
        
        embed = Embed(
            title="📊 " + t('user_panel.game_history', lang),
            description=history_text or t('user_panel.no_history', lang),
            color=0x3498db
        )
        
        # Add summary
        net_profit = total_won - total_lost
        profit_emoji = "📈" if net_profit >= 0 else "📉"
        net_sign = "+" if net_profit >= 0 else "-"
        
        summary = (
            f"**{t('user_panel.total_wagered', lang)}:** {format_balance(total_wagered, 'real')}\n"
            f"**{t('user_panel.total_won', lang)}:** {format_balance(total_won, 'real')}\n"
            f"**{t('user_panel.total_lost', lang)}:** {format_balance(total_lost, 'real')}\n"
            f"**{t('user_panel.total_tied', lang)}:** {format_balance(total_tied, 'real')}\n"
            f"{profit_emoji} **{t('user_panel.net_profit', lang)}:** {net_sign}{format_balance(abs(net_profit), 'real')}"
        )
        
        embed.add_field(
            name=f"📋 {t('user_panel.summary', lang)}",
            value=summary,
            inline=False
        )
        
        embed.set_footer(text=t('user_panel.showing_last_games', lang).format(count=len(history_items)))
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_statistics(self, interaction: discord.Interaction):
        """Show the same rich statistics panel used in private rooms for the target user."""

        lang = get_user_lang(self.admin_id)
        stats = get_user_stats(self.target_user_id) or {}
        player = Player(self.target_user_id)

        if interaction.guild:
            member = interaction.guild.get_member(self.target_user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(self.target_user_id)
                except Exception:
                    member = None
        else:
            member = None

        if member is None:
            member = interaction.client.get_user(self.target_user_id)
        if member is None:
            member = await interaction.client.fetch_user(self.target_user_id)

        if not stats:
            embed = Embed(
                title=t("player_stats.no_stats_title", lang=lang),
                description=t("player_stats.no_stats", lang=lang),
                color=0x95a5a6
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = PlayerStatsView.build_overview_embed(member, stats, player, lang=lang)
        view = AdminPlayerStatsView(
            self.target_user_id,
            self.admin_id,
            stats,
            player,
            member,
            lang=lang,
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_level_system(self, interaction: discord.Interaction):
        from modules.levels import MAX_LEVEL, progress_info, chest_rewards_for_level, chest_coins_for_level
        player = Player(self.target_user_id)
        level  = player.level
        stats  = player.stats
        total_wagered = int(stats.get("total_wagered", 0))
        total_deposit = int(stats.get("total_deposit", 0))
        info   = progress_info(level, total_wagered, total_deposit)

        server_data = get_data("server/server") or {}
        ce = server_data.get("coin_emoji", "🪙")

        from modules.levels import get_coin_usd_rate
        rate = get_coin_usd_rate()

        chest_min_usd, chest_max_usd = chest_rewards_for_level(level)
        chest_min_c,   chest_max_c   = chest_coins_for_level(level)

        if level >= 80:   color = 0x00cfff
        elif level >= 50: color = 0xffd700
        elif level >= 25: color = 0xc0c0c0
        else:             color = 0xcd7f32

        embed = Embed(title=f"🏆 Level System — <@{self.target_user_id}>", color=color)
        embed.add_field(name="Level", value=f"**{level}** / {MAX_LEVEL}", inline=True)
        embed.add_field(
            name="Daily Chest",
            value=(
                f"{chest_min_c:,} {ce} (${chest_min_usd:.4f})"
                f" — {chest_max_c:,} {ce} (${chest_max_usd:.4f})"
            ),
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=False)

        wagered_usd = total_wagered * rate
        deposit_usd = total_deposit * rate
        embed.add_field(
            name="Total Wagered",
            value=f"{total_wagered:,} {ce} (${wagered_usd:,.2f})",
            inline=True,
        )
        embed.add_field(
            name="Total Deposited",
            value=f"{total_deposit:,} {ce} (${deposit_usd:,.2f})",
            inline=True,
        )

        if info["next_level"] is not None:
            wp = info["wager_progress_pct"]
            dp = info["deposit_progress_pct"]
            w_bar = "█" * (wp // 10) + "░" * (10 - wp // 10)
            d_bar = "█" * (dp // 10) + "░" * (10 - dp // 10)
            embed.add_field(
                name=f"Progress → Level {info['next_level']}",
                value=(
                    f"Wager: {w_bar} {wp}%"
                    + (f" (${info['wager_needed']:,.2f} left)" if info['wager_needed'] > 0 else " ✅")
                    + f"\nDeposit: {d_bar} {dp}%"
                    + (f" (${info['deposit_needed']:,.2f} left)" if info['deposit_needed'] > 0 else " ✅")
                ),
                inline=False,
            )
        else:
            embed.add_field(name="Progress", value="Max level reached!", inline=False)

        view = _LevelSystemView(self.target_user_id, self.admin_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_wager_stats(self, interaction: discord.Interaction):
        from modules.database import get_user_stats, set_user_data
        lang  = get_user_lang(self.admin_id)
        stats = get_user_stats(self.target_user_id) or {}
        player = Player(self.target_user_id)

        total_wagered = stats.get("total_wagered", 0)
        total_profit  = stats.get("total_profit", 0)
        total_games   = stats.get("total_games", 0)
        wins          = stats.get("wins", 0)
        losses        = stats.get("losses", 0)
        ties          = stats.get("ties", 0)
        real_balance  = player.get_balance("real")
        demo_balance  = player.get_balance("demo")

        net_emoji = "📈" if total_profit >= 0 else "📉"
        desc = (
            f"🎲 **{t('user_panel.total_games', lang)}:** {total_games}\n"
            f"✅ **{t('user_panel.wins', lang)}:** {wins} | "
            f"❌ **{t('user_panel.losses', lang)}:** {losses} | "
            f"🔁 **{t('user_panel.ties', lang)}:** {ties}\n\n"
            f"💸 **{t('user_panel.total_wagered', lang)}:** {format_balance(total_wagered, 'real')}\n"
            f"{net_emoji} **{t('user_panel.net_profit', lang)}:** {format_balance(total_profit, 'real')}\n\n"
            f"💰 **{t('user_panel.real_balance', lang)}:** {format_balance(real_balance, 'real')}\n"
            f"🎮 **{t('user_panel.demo_balance', lang)}:** {format_balance(demo_balance, 'real')}"
        )

        color = discord.Color.green() if total_profit >= 0 else discord.Color.red()
        embed = Embed(title=f"🎲 {t('user_panel.wager_stats', lang)} — <@{self.target_user_id}>", description=desc, color=color)

        view = _WagerStatsView(self.target_user_id, self.admin_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_cashier_stats(self, interaction: discord.Interaction):
        """Show detailed cashier activity — deposits and withdrawals processed."""
        lang       = get_user_lang(self.admin_id)
        cashier_id = self.target_user_id
        embed      = _build_dep_wdr_embed(cashier_id, lang)
        view       = _CashierStatsView(cashier_id, self.admin_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_manage_staff(self, interaction: discord.Interaction):
        """Show the Manage Staff sub-panel for the target user."""
        if "admin" not in self.permissions:
            return await interaction.response.send_message(
                "❌ You don't have permission to manage staff!",
                ephemeral=True
            )

        admins = get_data("server/admins") or {}
        current_perms = admins.get(str(self.target_user_id), [])

        perm_display = {
            "admin": "🔑 Admin",
            "moderator": "🛡️ Moderator",
            "cashier": "💳 Cashier",
            "ticketAdmin": "🎫 Ticket Admin",
            "ban_hammer": "🔨 Ban Hammer",
            "timeout_hammer": "⏱️ Timeout Hammer",
            "item_manager": "📦 Item Manager",
        }

        user = interaction.guild.get_member(self.target_user_id)
        if not user:
            try:
                user = await interaction.guild.fetch_member(self.target_user_id)
            except Exception:
                user = await interaction.client.fetch_user(self.target_user_id)

        embed = Embed(
            title=f"👥 Manage Staff — {user.name}",
            description=f"Staff management panel for <@{self.target_user_id}>",
            color=0xe74c3c,
        )
        if current_perms:
            perm_text = "\n".join([perm_display.get(p, p) for p in current_perms])
            embed.add_field(name="🏷️ Current Permissions", value=perm_text, inline=False)
        else:
            embed.add_field(
                name="🏷️ Current Permissions", value="No permissions assigned", inline=False
            )
        embed.set_footer(text="Use the buttons below to manage this staff member")

        view = ManageStaffView(self.target_user_id, self.admin_id, current_perms)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_referral_info(self, interaction: discord.Interaction):
        """Show the target user's referral data."""
        referrals_data = get_data("server/referrals") or {}
        referral_settings = get_data("server/referral_settings") or {}
        user_ref = referrals_data.get(str(self.target_user_id))

        try:
            target_user = await interaction.client.fetch_user(self.target_user_id)
            display_name = target_user.name
        except Exception:
            display_name = str(self.target_user_id)

        embed = Embed(
            title=f"🔗 Referral Bilgisi — {display_name}",
            color=0xf39c12,
        )

        if not user_ref:
            embed.description = "Bu kullanıcının henüz bir referral kaydı bulunmuyor."
            embed.color = 0x95a5a6
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        code = user_ref.get("code", "—")
        commission_rate = user_ref.get("commission_rate", referral_settings.get("default_commission", 10))
        total_earned = user_ref.get("total_earned", 0)
        available_balance = user_ref.get("available_balance", 0)
        today_earned = user_ref.get("today_earned", 0)
        referred_users = user_ref.get("referred_users", [])
        referral_earnings = user_ref.get("referral_earnings", [])

        embed.add_field(
            name="📋 Referral Kodu",
            value=f"`{code}`",
            inline=True,
        )
        embed.add_field(
            name="💹 Komisyon Oranı",
            value=f"`%{commission_rate}`",
            inline=True,
        )
        embed.add_field(
            name="👥 Davet Edilen",
            value=f"`{len(referred_users)}` kullanıcı",
            inline=True,
        )
        embed.add_field(
            name="💰 Toplam Kazanç",
            value=format_balance(total_earned, "real"),
            inline=True,
        )
        embed.add_field(
            name="💳 Çekilebilir Bakiye",
            value=format_balance(available_balance, "real"),
            inline=True,
        )
        embed.add_field(
            name="📅 Bugünkü Kazanç",
            value=format_balance(today_earned, "real"),
            inline=True,
        )

        if referral_earnings:
            recent = referral_earnings[-5:][::-1]
            lines = []
            for entry in recent:
                from datetime import datetime
                ts = entry.get("timestamp", 0)
                date_str = datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else "—"
                amount = format_balance(entry.get("amount", 0), "real")
                lines.append(f"`{date_str}` — {amount}")
            embed.add_field(
                name="🕐 Son Kazançlar",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text=f"Min. Çekim: {format_balance(referral_settings.get('min_withdrawal', 10), 'real')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def show_activity(self, interaction: discord.Interaction):
        """Show per-user transaction history: balance movements, promos, bonuses, referrer."""
        from modules.database import get_data

        uid = self.target_user_id
        lang = get_user_lang(self.admin_id)

        try:
            target_user = await interaction.client.fetch_user(uid)
            display_name = target_user.name
        except Exception:
            target_user = None
            display_name = str(uid)

        # ── Referrer ────────────────────────────────────────────────────────
        account = get_user_data(uid, "account") or {}
        referred_by_raw = account.get("referred_by")
        if referred_by_raw:
            try:
                referrer_user = await interaction.client.fetch_user(int(referred_by_raw))
                referrer_str = f"<@{referrer_user.id}> (`{referrer_user.name}`)"
            except Exception:
                referrer_str = f"`{referred_by_raw}`"
        else:
            referrer_str = f"*{t('common.none', lang)}*"

        referral_code_used = account.get("referral_code") or None
        ref_code_str = f"`{referral_code_used}`" if referral_code_used else "*—*"

        # ── Transaction log ─────────────────────────────────────────────────
        kv_key = f"user_txlog/{uid}"
        raw_log = get_data(kv_key) or {}
        if not isinstance(raw_log, dict):
            raw_log = {}

        entries = sorted(raw_log.values(), key=lambda e: e.get("timestamp", 0), reverse=True)[:20]

        embed = Embed(
            title=t('user_panel.activity_title', lang, name=display_name),
            color=0x2b2d31,
        )
        if target_user is not None:
            embed.set_thumbnail(url=target_user.display_avatar.url)

        embed.add_field(
            name=t('user_panel.activity_referral_field', lang),
            value=(
                f"**{t('user_panel.activity_referred_by', lang)}:** {referrer_str}\n"
                f"**{t('user_panel.activity_ref_code', lang)}:** {ref_code_str}"
            ),
            inline=False,
        )

        if not entries:
            embed.add_field(
                name=t('user_panel.activity_movements_title', lang),
                value=f"*{t('user_panel.activity_no_data', lang)}*",
                inline=False,
            )
        else:
            lines = []
            for entry in entries:
                ttype  = entry.get("type", "")
                amount = int(entry.get("amount", 0))
                reason = entry.get("reason", "")
                ts     = entry.get("timestamp", 0)
                ts_str = f"<t:{ts}:R>" if ts else "—"

                if ttype == "balance_add":
                    icon  = "✅"
                    label = t('user_panel.activity_add', lang, amount=format_balance(amount, 'real'))
                elif ttype == "balance_remove":
                    icon  = "❌"
                    label = t('user_panel.activity_remove', lang, amount=format_balance(amount, 'real'))
                elif ttype == "promo":
                    icon  = "🎟️"
                    label = t('user_panel.activity_promo', lang)
                    if amount:
                        label += f" · +{format_balance(amount, 'real')}"
                elif ttype == "bonus":
                    icon  = "🎁"
                    label = t('user_panel.activity_bonus', lang)
                    if amount:
                        label += f" · +{format_balance(amount, 'real')}"
                else:
                    icon  = "ℹ️"
                    label = ttype

                reason_part = f" · **{t('user_panel.activity_reason', lang, reason=reason)}**" if reason else ""
                lines.append(f"{icon} {label}{reason_part} · {ts_str}")

            embed.add_field(
                name=t('user_panel.activity_movements_title', lang),
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text=t('user_panel.activity_footer', lang))
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# Cashier stats helpers
# ---------------------------------------------------------------------------

def _collect_dep_wdr(cashier_id: int):
    """Return (dep_approved, dep_total, dep_rejected, wdr_approved, wdr_total, wdr_rejected)."""
    all_user_ids = get_all_registered_user_ids()
    dep_approved = dep_rejected = wdr_approved = wdr_rejected = 0
    dep_total = wdr_total = 0.0
    for uid in all_user_ids:
        dep_history = get_user_data(int(uid), "deposit_history") or {}
        for dep in dep_history.values():
            if str(dep.get("managed_by", "")) == str(cashier_id):
                if dep.get("status") in ("approved", "completed"):
                    dep_approved += 1
                    dep_total += float(dep.get("confirmed_amount") or dep.get("amount") or 0)
                elif dep.get("status") == "rejected":
                    dep_rejected += 1
        wdr_history = get_user_data(int(uid), "withdraw_history") or {}
        for wdr in wdr_history.values():
            if str(wdr.get("managed_by", "")) == str(cashier_id):
                if wdr.get("status") == "approved":
                    wdr_approved += 1
                    wdr_total += float(wdr.get("amount", 0))
                elif wdr.get("status") in ("rejected", "refunded"):
                    wdr_rejected += 1
    return dep_approved, dep_total, dep_rejected, wdr_approved, wdr_total, wdr_rejected


def _build_dep_wdr_embed(cashier_id: int, lang: str) -> Embed:
    dep_app, dep_total, dep_rej, wdr_app, wdr_total, wdr_rej = _collect_dep_wdr(cashier_id)
    net = dep_total - wdr_total
    net_color = discord.Color.green() if net >= 0 else discord.Color.red()
    net_sign  = "+" if net >= 0 else ""

    embed = Embed(
        title=f"💳 Kasiyer Özeti — <@{cashier_id}>",
        color=net_color,
    )
    embed.add_field(
        name="📥 Depozitolar",
        value=(
            f"✅ Onaylanan: **{dep_app}** işlem\n"
            f"💰 Toplam: **{format_balance(dep_total, 'real')}**\n"
            f"❌ Reddedilen: **{dep_rej}** işlem"
        ),
        inline=True,
    )
    embed.add_field(
        name="📤 Çekimler",
        value=(
            f"✅ Onaylanan: **{wdr_app}** işlem\n"
            f"💰 Toplam: **{format_balance(wdr_total, 'real')}**\n"
            f"❌ Reddedilen: **{wdr_rej}** işlem"
        ),
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(
        name="📊 Net (Dep − Çek)",
        value=f"**{net_sign}{format_balance(net, 'real')}**",
        inline=True,
    )
    embed.add_field(
        name="🔢 Toplam İşlem",
        value=f"**{dep_app + wdr_app}** onaylı",
        inline=True,
    )
    embed.set_footer(text="Kasiyer İstatistikleri • Dep/Çek Özeti")
    return embed


def _build_net_formula_embed(cashier_id: int, lang: str) -> Embed:
    _, dep_total, _, _, wdr_total, _ = _collect_dep_wdr(cashier_id)

    balance_log    = get_data("server/balance_log") or {}
    given_total    = removed_total = 0.0
    given_count    = removed_count = 0
    for entry in balance_log.values():
        if str(entry.get("admin_id", "")) == str(cashier_id):
            amt = float(entry.get("amount", 0))
            if entry.get("action") == "add":
                given_total  += amt
                given_count  += 1
            elif entry.get("action") == "remove":
                removed_total += amt
                removed_count += 1

    net_given = given_total - removed_total
    result    = wdr_total - dep_total + net_given
    result_color = discord.Color.green() if result >= 0 else discord.Color.red()
    result_sign  = "+" if result >= 0 else ""

    embed = Embed(
        title=f"📊 Net Muhasebe — <@{cashier_id}>",
        color=result_color,
    )
    embed.add_field(
        name="📤 Çekimler (oyuncuya verilen)",
        value=f"**{format_balance(wdr_total, 'real')}**",
        inline=True,
    )
    embed.add_field(
        name="📥 Depozitolar (bizden aldığı)",
        value=f"**{format_balance(dep_total, 'real')}**",
        inline=True,
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(
        name="💰 Bakiye Ekleme",
        value=(
            f"➕ Ekledi: **{given_count}** kez — **{format_balance(given_total, 'real')}**\n"
            f"➖ Çıkardı: **{removed_count}** kez — **{format_balance(removed_total, 'real')}**\n"
            f"📋 Net Verilen: **{format_balance(net_given, 'real')}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="🧮 Formül",
        value=(
            f"Çekimler − Depozitolar + Net Verilen\n"
            f"{format_balance(wdr_total, 'real')} − {format_balance(dep_total, 'real')} + {format_balance(net_given, 'real')}\n"
            f"= **{result_sign}{format_balance(result, 'real')}**"
        ),
        inline=False,
    )
    embed.set_footer(text="Kasiyer İstatistikleri • Net Muhasebe")
    return embed


def _build_bytecoin_embed(cashier_id: int, lang: str) -> Embed:
    """Show byte coin given/taken by this cashier via balance_log."""
    balance_log   = get_data("server/balance_log") or {}
    given_total   = removed_total = 0.0
    given_count   = removed_count = 0
    for entry in balance_log.values():
        if str(entry.get("admin_id", "")) == str(cashier_id):
            amt = float(entry.get("amount", 0))
            if entry.get("action") == "add":
                given_total  += amt
                given_count  += 1
            elif entry.get("action") == "remove":
                removed_total += amt
                removed_count += 1

    net = given_total - removed_total
    net_sign  = "+" if net >= 0 else ""
    net_color = discord.Color.green() if net >= 0 else discord.Color.red()

    embed = Embed(
        title=f"🪙 Byte Coin İşlemleri — <@{cashier_id}>",
        color=net_color,
    )
    embed.add_field(
        name="➕ Verilen",
        value=f"**{given_count}** kez\n**{format_balance(given_total, 'real')}**",
        inline=True,
    )
    embed.add_field(
        name="➖ Alınan",
        value=f"**{removed_count}** kez\n**{format_balance(removed_total, 'real')}**",
        inline=True,
    )
    embed.add_field(
        name="📋 Net",
        value=f"**{net_sign}{format_balance(net, 'real')}**",
        inline=True,
    )
    embed.set_footer(text="Kasiyer İstatistikleri • Byte Coin")
    return embed


class ManageStaffView(discord.ui.View):
    """Sub-panel for staff management: cashier stats + permission management."""

    def __init__(self, target_user_id: int, admin_id: int, current_perms: list):
        super().__init__(timeout=180)
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.current_perms = current_perms

    @discord.ui.button(label="🔒 Manage Permissions", style=discord.ButtonStyle.secondary, row=0)
    async def manage_permissions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)

        admins = get_data("server/admins") or {}
        current_perms = admins.get(str(self.target_user_id), [])

        perm_display = {
            "admin": "🔑 Admin",
            "moderator": "🛡️ Moderator",
            "cashier": "💳 Cashier",
            "ticketAdmin": "🎫 Ticket Admin",
            "ban_hammer": "🔨 Ban Hammer",
            "timeout_hammer": "⏱️ Timeout Hammer",
            "item_manager": "📦 Item Manager",
        }

        user = interaction.guild.get_member(self.target_user_id)
        if not user:
            try:
                user = await interaction.guild.fetch_member(self.target_user_id)
            except Exception:
                user = await interaction.client.fetch_user(self.target_user_id)

        embed = Embed(
            title=f"🔒 Manage Permissions — {user.name}",
            description=f"Configure permission roles for <@{self.target_user_id}>",
            color=0xe74c3c,
        )
        if current_perms:
            perm_text = "\n".join([perm_display.get(p, p) for p in current_perms])
            embed.add_field(name="🏷️ Current Permissions", value=perm_text, inline=False)
        else:
            embed.add_field(
                name="🏷️ Current Permissions", value="No permissions assigned", inline=False
            )
        embed.set_footer(text="Select a permission from the menu below")

        view = PermissionManagementView(self.target_user_id, self.admin_id, current_perms)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="💳 Cashier Stats", style=discord.ButtonStyle.primary, row=0)
    async def cashier_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)

        lang = get_user_lang(self.admin_id)
        embed = _build_dep_wdr_embed(self.target_user_id, lang)
        view = _CashierStatsView(self.target_user_id, self.admin_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class _CashierStatsView(discord.ui.View):
    def __init__(self, cashier_id: int, admin_id: int):
        super().__init__(timeout=120)
        self.cashier_id = cashier_id
        self.admin_id   = admin_id
        lang = get_user_lang(admin_id)

    @discord.ui.button(label="💳 Dep/Çek Özeti", style=discord.ButtonStyle.primary)
    async def dep_wdr_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.defer()
        lang  = get_user_lang(self.admin_id)
        embed = _build_dep_wdr_embed(self.cashier_id, lang)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="📊 Net Muhasebe", style=discord.ButtonStyle.secondary)
    async def net_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.defer()
        lang  = get_user_lang(self.admin_id)
        embed = _build_net_formula_embed(self.cashier_id, lang)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🪙 Byte Coin", style=discord.ButtonStyle.success)
    async def bytecoin_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.defer()
        lang  = get_user_lang(self.admin_id)
        embed = _build_bytecoin_embed(self.cashier_id, lang)
        await interaction.response.edit_message(embed=embed, view=self)


class _WagerStatsView(discord.ui.View):
    """Actions on the wager stats panel."""

    def __init__(self, target_user_id: int, admin_id: int):
        super().__init__(timeout=120)
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        lang = get_user_lang(admin_id)
        self.reset_wager_btn.label = t("user_panel.btn_reset_wager", lang)
        self.set_wager_btn.label   = t("user_panel.btn_set_wager", lang)

    @discord.ui.button(label="🔄 Reset Wager Stats", style=discord.ButtonStyle.danger)
    async def reset_wager_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            lang = get_user_lang(interaction.user.id)
            return await interaction.response.send_message(t("user_panel.not_your_panel", lang), ephemeral=True)
        await interaction.response.send_modal(_ResetWagerConfirmModal(self.target_user_id, self.admin_id))

    @discord.ui.button(label="✏️ Set Wager", style=discord.ButtonStyle.secondary)
    async def set_wager_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            lang = get_user_lang(interaction.user.id)
            return await interaction.response.send_message(t("user_panel.not_your_panel", lang), ephemeral=True)
        await interaction.response.send_modal(_SetWagerModal(self.target_user_id, self.admin_id))


class _ResetWagerConfirmModal(discord.ui.Modal):
    def __init__(self, target_user_id: int, admin_id: int):
        lang = get_user_lang(admin_id)
        super().__init__(title=t("user_panel.reset_wager_modal_title", lang))
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.confirm = discord.ui.TextInput(
            label=t("user_panel.reset_wager_confirm_label", lang),
            placeholder="RESET",
            max_length=10,
            required=True
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        lang = get_user_lang(self.admin_id)
        if self.confirm.value.strip().upper() != "RESET":
            return await interaction.response.send_message(t("user_panel.reset_wager_cancelled", lang), ephemeral=True)
        from modules.database import get_user_stats
        stats = get_user_stats(self.target_user_id) or {}
        for f in ["total_wagered", "total_profit", "total_games", "wins", "losses", "ties"]:
            stats[f] = 0
        set_user_data(self.target_user_id, "stats", stats)
        await interaction.response.send_message(
            embed=Embed(
                title=t("user_panel.reset_wager_success_title", lang),
                description=t("user_panel.reset_wager_success_desc", lang).format(user_id=self.target_user_id),
                color=discord.Color.green()
            ),
            ephemeral=True
        )


class _SetBalanceCapModal(discord.ui.Modal, title="🎯 Balance Cap"):
    ceiling_input = discord.ui.TextInput(
        label="Max balance (coins, 0 = disable admin cap)",
        placeholder="e.g. 500",
        max_length=12,
        required=True,
    )

    def __init__(self, target_user_id: int, admin_id: int, current: int = 0):
        super().__init__()
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        if current > 0:
            self.ceiling_input.default = str(current)

    async def on_submit(self, interaction: discord.Interaction):
        from modules.balance_cap import set_admin_balance_cap

        await interaction.response.defer(ephemeral=True)
        raw = self.ceiling_input.value.strip().replace(",", "")
        try:
            ceiling = int(raw)
        except ValueError:
            return await interaction.followup.send(
                embed=Embed(title="❌ Invalid", description="Enter a whole number.", color=discord.Color.red()),
                ephemeral=True,
            )
        try:
            set_admin_balance_cap(self.target_user_id, ceiling)
        except Exception as exc:
            return await interaction.followup.send(
                embed=Embed(title="❌ Error", description=str(exc), color=discord.Color.red()),
                ephemeral=True,
            )
        if ceiling <= 0:
            msg = "Admin cap disabled."
        else:
            msg = f"Admin cap set to **{format_balance(ceiling, 'real')}**."
        await interaction.followup.send(
            embed=Embed(title="✅ Updated", description=msg, color=discord.Color.green()),
            ephemeral=True,
        )


class _SetWagerModal(discord.ui.Modal):
    def __init__(self, target_user_id: int, admin_id: int):
        lang = get_user_lang(admin_id)
        super().__init__(title=t("user_panel.set_wager_modal_title", lang))
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.wager_input = discord.ui.TextInput(
            label=t("user_panel.set_wager_label", lang),
            placeholder=t("user_panel.set_wager_placeholder", lang),
            required=True,
            max_length=15
        )
        self.profit_input = discord.ui.TextInput(
            label=t("user_panel.set_profit_label", lang),
            placeholder=t("user_panel.set_profit_placeholder", lang),
            required=True,
            max_length=15
        )
        self.add_item(self.wager_input)
        self.add_item(self.profit_input)

    async def on_submit(self, interaction: discord.Interaction):
        lang = get_user_lang(self.admin_id)
        from modules.database import get_user_stats
        try:
            wager  = float(self.wager_input.value.replace(",", "").strip())
            profit = float(self.profit_input.value.replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message(t("user_panel.invalid_number", lang), ephemeral=True)
        stats = get_user_stats(self.target_user_id) or {}
        stats["total_wagered"] = wager
        stats["total_profit"]  = profit
        set_user_data(self.target_user_id, "stats", stats)
        await interaction.response.send_message(
            embed=Embed(
                title=t("user_panel.wager_updated_title", lang),
                description=t("user_panel.wager_updated_desc", lang).format(
                    wager=format_balance(wager, "real"),
                    profit=format_balance(profit, "real")
                ),
                color=discord.Color.green()
            ),
            ephemeral=True
        )


class _LevelSystemView(discord.ui.View):
    """View shown alongside the Level System panel — only contains Set Level button."""

    def __init__(self, target_user_id: int, admin_id: int):
        super().__init__(timeout=120)
        self.target_user_id = target_user_id
        self.admin_id = admin_id

    @discord.ui.button(label="✏️ Set Level", style=discord.ButtonStyle.primary, emoji="🏆")
    async def btn_set_level(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.defer()
        await interaction.response.send_modal(_SetLevelModal(self.target_user_id, self.admin_id))


class AdminPlayerStatsView(PlayerStatsView):
    """PlayerStatsView extended with Deposit History and Withdraw History buttons for admin user_panel."""

    def __init__(self, target_user_id: int, admin_id: int, stats: dict, player, member, lang: str = "en"):
        super().__init__(target_user_id, stats, player, member, lang=lang, viewer_id=admin_id)
        self.target_user_id = target_user_id
        self.admin_id = admin_id

    @staticmethod
    def _build_dep_history_embed(target_user_id: int) -> Embed:
        dep_history = get_user_data(target_user_id, "deposit_history") or {}
        if not dep_history:
            return Embed(title="📥 Deposit History", description="No deposit records found.", color=0x95a5a6)
        items = sorted(dep_history.values(), key=lambda x: x.get("timestamp", 0), reverse=True)[:15]
        embed = Embed(title=f"📥 Deposit History — <@{target_user_id}>", color=0x2ecc71)
        for dep in items:
            status  = dep.get("status", "unknown")
            amount  = dep.get("confirmed_amount") or dep.get("amount", 0)
            ts      = dep.get("timestamp", "")
            growid  = dep.get("growid", dep.get("user_growid", "N/A"))
            managed = dep.get("managed_by")
            s_emoji = {"approved": "✅", "completed": "✅", "rejected": "❌", "pending": "⏳"}.get(status, "❔")
            val = f"{s_emoji} **{status.title()}** — {format_balance(int(float(amount)), 'real')}"
            if growid and growid != "N/A":
                val += f"\nGrowID: `{growid}`"
            if managed:
                val += f"\nBy: <@{managed}>"
            if ts:
                val += f"\n{ts}"
            embed.add_field(name="\u200b", value=val, inline=True)
        embed.set_footer(text=f"Showing last {len(items)} deposits")
        return embed

    @staticmethod
    def _build_wdr_history_embed(target_user_id: int) -> Embed:
        wdr_history = get_user_data(target_user_id, "withdraw_history") or {}
        if not wdr_history:
            return Embed(title="📤 Withdraw History", description="No withdrawal records found.", color=0x95a5a6)
        items = sorted(wdr_history.values(), key=lambda x: x.get("timestamp", 0), reverse=True)[:15]
        embed = Embed(title=f"📤 Withdraw History — <@{target_user_id}>", color=0xe74c3c)
        for wdr in items:
            status  = wdr.get("status", "unknown")
            amount  = wdr.get("amount", 0)
            ts      = wdr.get("timestamp", "")
            growid  = wdr.get("growid", wdr.get("user_growid", "N/A"))
            managed = wdr.get("managed_by")
            s_emoji = {"approved": "✅", "rejected": "❌", "refunded": "🔄", "pending": "⏳"}.get(status, "❔")
            val = f"{s_emoji} **{status.title()}** — {format_balance(int(float(amount)), 'real')}"
            if growid and growid != "N/A":
                val += f"\nGrowID: `{growid}`"
            if managed:
                val += f"\nBy: <@{managed}>"
            if ts:
                val += f"\n{ts}"
            embed.add_field(name="\u200b", value=val, inline=True)
        embed.set_footer(text=f"Showing last {len(items)} withdrawals")
        return embed

    @discord.ui.button(label="📥 Deposit History", style=discord.ButtonStyle.secondary, row=1)
    async def btn_dep_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            return await interaction.response.defer()
        embed = self._build_dep_history_embed(self.target_user_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📤 Withdraw History", style=discord.ButtonStyle.secondary, row=1)
    async def btn_wdr_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.viewer_id:
            return await interaction.response.defer()
        embed = self._build_wdr_history_embed(self.target_user_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class _SetLevelModal(discord.ui.Modal, title="🏆 Set User Level"):
    level_input = discord.ui.TextInput(
        label="New Level (1–100)",
        placeholder="Enter a number between 1 and 100",
        min_length=1,
        max_length=3,
        required=True,
    )

    def __init__(self, target_user_id: int, admin_id: int):
        super().__init__()
        self.target_user_id = target_user_id
        self.admin_id = admin_id

    async def on_submit(self, interaction: discord.Interaction):
        from modules.levels import MAX_LEVEL
        try:
            new_level = int(self.level_input.value.strip())
        except ValueError:
            return await interaction.response.send_message("❌ Invalid number.", ephemeral=True)

        if not (1 <= new_level <= MAX_LEVEL):
            return await interaction.response.send_message(
                f"❌ Level must be between 1 and {MAX_LEVEL}.", ephemeral=True
            )

        player = Player(self.target_user_id)
        player.set_level(new_level)

        from modules.levels import chest_rewards_for_level, chest_coins_for_level
        chest_min_usd, chest_max_usd = chest_rewards_for_level(new_level)
        chest_min_c, chest_max_c     = chest_coins_for_level(new_level)

        server_data = get_data("server/server") or {}
        ce = server_data.get("coin_emoji", "🪙")

        embed = Embed(
            title="🏆 Level Updated",
            description=(
                f"<@{self.target_user_id}> is now **Level {new_level}**.\n\n"
                f"🎁 New daily chest: "
                f"{chest_min_c:,} {ce} (${chest_min_usd:.4f}) — "
                f"{chest_max_c:,} {ce} (${chest_max_usd:.4f})"
            ),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BalanceModal(discord.ui.Modal):
    """Modal for adding or removing balance"""
    
    def __init__(self, target_user_id: int, action: str, admin_id: int):
        lang = get_user_lang(admin_id)
        title = t('user_panel.add_balance', lang) if action == "add" else t('user_panel.remove_balance', lang)
        super().__init__(title=title)
        
        self.target_user_id = target_user_id
        self.action = action
        self.admin_id = admin_id
        
        self.amount_input = discord.ui.TextInput(
            label=t('user_panel.amount_label', lang),
            placeholder=t('user_panel.amount_placeholder', lang),
            required=True,
            style=discord.TextStyle.short
        )
        self.reason_input = discord.ui.TextInput(
            label=t('user_panel.reason_label', lang),
            placeholder=t('user_panel.reason_placeholder', lang),
            required=False,
            max_length=100,
            style=discord.TextStyle.short
        )
        self.add_item(self.amount_input)
        self.add_item(self.reason_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        lang = get_user_lang(self.admin_id)
        
        try:
            amount = int(self.amount_input.value)
            if amount <= 0:
                raise ValueError("Amount must be positive")
        except ValueError:
            embed = Embed(
                title="❌ " + t('user_panel.error', lang),
                description=t('user_panel.invalid_amount', lang),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        reason = self.reason_input.value.strip() if self.reason_input.value else ""
        player = Player(self.target_user_id)
        previous_balance = player.get_balance("real")
        
        if self.action == "add":
            player.add_balance("real", amount, by=self.admin_id, reason=reason)
            new_balance = player.get_balance("real")
            title = "✅ " + t('user_panel.balance_added', lang)
        else:
            player.remove_balance("real", amount, by=self.admin_id, reason=reason)
            new_balance = player.get_balance("real")
            title = "✅ " + t('user_panel.balance_removed', lang)

        embed = Embed(
            title=title,
            description=(
                f"**{t('user_panel.previous_balance_label', lang)}:** {format_balance(previous_balance, 'real')}\n"
                f"**{t('user_panel.new_balance_label', lang)}:** {format_balance(new_balance, 'real')}\n"
                f"**{t('user_panel.amount_changed', lang)}:** {format_balance(amount, 'real')}"
                + (f"\n**Reason:** {reason}" if reason else "")
            ),
            color=0x2ecc71
        )
        
        if is_moderator_only(self.admin_id) and interaction.guild:
            sign = "+" if self.action == "add" else "−"
            await moderation_log.log_moderation(
                interaction.client,
                interaction.guild,
                actor_id=self.admin_id,
                action=f"**{self.action.replace('_', ' ').title()} balance**",
                target_user_id=self.target_user_id,
                details=(
                    f"{sign}{format_balance(amount, 'real')}\n"
                    f"Before: {format_balance(previous_balance, 'real')}\n"
                    f"After: {format_balance(new_balance, 'real')}"
                    + (f"\nReason: {reason}" if reason else "")
                ),
                color=0x2ECC71 if self.action == "add" else 0xE74C3C,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


class EditRegistrationModal(discord.ui.Modal, title="Edit Registration"):
    """Modal for editing user registration info"""
    
    def __init__(self, target_user_id: int, admin_id: int):
        super().__init__()
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        
        # Get current registration info
        user_account = get_user_data(self.target_user_id, "account") or {}
        
        lang = get_user_lang(admin_id)
        
        self.name_input = discord.ui.TextInput(
            label=t('user_panel.name_label', lang),
            placeholder=t('user_panel.name_placeholder', lang),
            default=user_account.get("name", ""),
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.name_input)
        
        self.age_input = discord.ui.TextInput(
            label=t('user_panel.age_label', lang),
            placeholder=t('user_panel.age_placeholder', lang),
            default=str(user_account.get("age", "")),
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.age_input)
        
        self.source_input = discord.ui.TextInput(
            label=t('user_panel.source_label', lang),
            placeholder=t('user_panel.source_placeholder', lang),
            default=user_account.get("source", ""),
            required=True,
            style=discord.TextStyle.short
        )
        self.add_item(self.source_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        lang = get_user_lang(self.admin_id)
        
        try:
            age = int(self.age_input.value)
            if age < 18 or age > 99:
                raise ValueError("Invalid age")
        except ValueError:
            embed = Embed(
                title="❌ " + t('user_panel.error', lang),
                description=t('user_panel.invalid_age', lang),
                color=discord.Color.red()
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Update registration info
        account_data = {
            "name": self.name_input.value,
            "age": age,
            "source": self.source_input.value
        }
        
        set_user_data(self.target_user_id, "account", account_data)
        
        embed = Embed(
            title="✅ " + t('user_panel.registration_updated', lang),
            description=(
                f"**{t('user_panel.name', lang)}:** {self.name_input.value}\n"
                f"**{t('user_panel.age', lang)}:** {age}\n"
                f"**{t('user_panel.source', lang)}:** {self.source_input.value}"
            ),
            color=0x2ecc71
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TicketHistoryView(discord.ui.View):
    """View for displaying ticket history with pagination"""
    
    def __init__(self, tickets: list, user_id: int, admin_id: int):
        super().__init__(timeout=180)
        self.tickets = sorted(tickets, key=lambda x: x.get("closed_at", 0), reverse=True)
        self.user_id = user_id
        self.admin_id = admin_id
        self.current_page = 0
        self.max_page = len(tickets) - 1
    
    def get_embed(self, index: int):
        """Get embed for a specific ticket"""
        if index < 0 or index >= len(self.tickets):
            index = 0
        
        ticket = self.tickets[index]
        
        # Category names
        category_names = {
            "balance": "💰 Balance Operations",
            "technical": "🔧 Technical Support",
            "bug": "🐛 Bug Report",
            "general": "💬 General Support"
        }
        
        embed = Embed(
            title=f"🎫 Ticket History ({index + 1}/{len(self.tickets)})",
            description=f"**Category:** {category_names.get(ticket.get('category', 'unknown'), ticket.get('category', 'unknown'))}",
            color=0x3498db
        )
        
        # Ticket info
        import datetime
        created = datetime.datetime.fromtimestamp(ticket.get("created_at", 0))
        closed = datetime.datetime.fromtimestamp(ticket.get("closed_at", 0))
        
        embed.add_field(
            name="📅 Created",
            value=created.strftime("%Y-%m-%d %H:%M:%S"),
            inline=True
        )
        embed.add_field(
            name="🔒 Closed",
            value=closed.strftime("%Y-%m-%d %H:%M:%S"),
            inline=True
        )
        
        # Description
        embed.add_field(
            name="📝 Issue Description",
            value=ticket.get("description", "No description")[:1024],
            inline=False
        )
        
        # Message count
        messages = ticket.get("messages", [])
        embed.add_field(
            name="💬 Total Messages",
            value=str(len(messages)),
            inline=True
        )
        
        # Claimed by
        claimed_by = ticket.get("claimed_by")
        if claimed_by:
            embed.add_field(
                name="👤 Handled By",
                value=f"<@{claimed_by}>",
                inline=True
            )
        
        embed.set_footer(text=f"Ticket ID: {ticket.get('ticket_id', 'unknown')}")
        
        return embed
    
    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.primary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)
        
        if self.current_page > 0:
            self.current_page -= 1
        else:
            self.current_page = self.max_page
        
        embed = self.get_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)
    
    @discord.ui.button(label="View Messages", style=discord.ButtonStyle.success, emoji="💬")
    async def view_messages(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)
        
        ticket = self.tickets[self.current_page]
        messages = ticket.get("messages", [])
        
        if not messages:
            return await interaction.response.send_message("📭 No messages in this ticket.", ephemeral=True)
        
        # Create text file with messages
        import datetime
        text = f"Ticket ID: {ticket.get('ticket_id', 'unknown')}\n"
        text += f"Category: {ticket.get('category', 'unknown')}\n"
        text += f"Description: {ticket.get('description', 'No description')}\n"
        text += "=" * 50 + "\n\n"
        
        for msg in messages:
            timestamp = datetime.datetime.fromisoformat(msg.get("timestamp", ""))
            author = msg.get("author", "Unknown")
            content = msg.get("content", "")
            attachments = msg.get("attachments", [])
            
            text += f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {author}:\n"
            if content:
                text += f"{content}\n"
            if attachments:
                for att in attachments:
                    text += f"📎 {att}\n"
            text += "\n"
        
        # Send as file
        import io
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket_{ticket.get('ticket_id', 'unknown')}.txt")
        
        embed = Embed(
            title="💬 Ticket Messages",
            description=f"Total messages: {len(messages)}",
            color=0x2ecc71
        )
        
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)
    
    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)
        
        if self.current_page < self.max_page:
            self.current_page += 1
        else:
            self.current_page = 0
        
        embed = self.get_embed(self.current_page)
        await interaction.response.edit_message(embed=embed, view=self)


class PermissionManagementView(discord.ui.View):
    """View for managing user permissions"""
    
    def __init__(self, target_user_id: int, admin_id: int, current_perms: list):
        super().__init__(timeout=180)
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.current_perms = current_perms
        self.add_item(PermissionSelect(target_user_id, admin_id, current_perms))
        # Show cashier limit button only if target user has the cashier permission
        if "cashier" in current_perms:
            self.add_item(_CashierLimitButton(target_user_id))


class _CashierLimitButton(discord.ui.Button):
    def __init__(self, target_user_id: int):
        super().__init__(label="🔒 Set Cashier Limit", style=discord.ButtonStyle.secondary, row=1)
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_CashierLimitModal(self.target_user_id))


class _CashierLimitModal(discord.ui.Modal, title="🔒 Cashier Deposit Limit"):
    amount_input = discord.ui.TextInput(
        label="Max deposit amount (0 = no limit)",
        placeholder="e.g. 5000  —  0 to remove limit",
        required=True,
        max_length=15,
    )

    def __init__(self, target_user_id: int):
        super().__init__()
        self.target_user_id = target_user_id
        from modules.database import get_data
        deposit_settings = get_data("server/deposit_settings") or {}
        existing = deposit_settings.get("cashier_user_limits", {}).get(str(target_user_id))
        if existing:
            self.amount_input.default = str(int(existing))

    async def on_submit(self, interaction: discord.Interaction):
        from modules.database import get_data, set_data
        from modules.utils import format_balance
        raw = self.amount_input.value.strip().replace(",", "").replace(".", "")
        try:
            limit = float(raw)
            if limit < 0:
                raise ValueError()
        except ValueError:
            return await interaction.response.send_message(
                "❌ Enter a valid number ≥ 0 (0 removes the limit).", ephemeral=True
            )
        deposit_settings = get_data("server/deposit_settings") or {}
        user_limits = deposit_settings.get("cashier_user_limits", {})
        if limit == 0:
            user_limits.pop(str(self.target_user_id), None)
            desc = f"Personal cashier limit **removed** for <@{self.target_user_id}>."
        else:
            user_limits[str(self.target_user_id)] = limit
            desc = (
                f"Personal limit for <@{self.target_user_id}> set to "
                f"**{format_balance(limit, 'real')}**."
            )
        deposit_settings["cashier_user_limits"] = user_limits
        set_data("server/deposit_settings", deposit_settings)
        await interaction.response.send_message(
            embed=Embed(title="✅ Cashier Limit Updated", description=desc, color=0x2ecc71),
            ephemeral=True,
        )


class PermissionSelect(discord.ui.Select):
    """Select menu for permission management"""
    
    def __init__(self, target_user_id: int, admin_id: int, current_perms: list):
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        self.current_perms = current_perms
        
        options = [
            discord.SelectOption(
                label="Admin",
                description="Full admin access to all features",
                emoji="🔑",
                value="admin",
                default="admin" in current_perms
            ),
            discord.SelectOption(
                label="Moderator",
                description="Moderate users and manage basic settings",
                emoji="🛡️",
                value="moderator",
                default="moderator" in current_perms
            ),
            discord.SelectOption(
                label="Ticket Admin",
                description="Manage support tickets and help users",
                emoji="🎫",
                value="ticketAdmin",
                default="ticketAdmin" in current_perms
            ),
            discord.SelectOption(
                label="Ban Hammer",
                description="Permission to ban users",
                emoji="🔨",
                value="ban_hammer",
                default="ban_hammer" in current_perms
            ),
            discord.SelectOption(
                label="Timeout Hammer",
                description="Permission to timeout users",
                emoji="⏱️",
                value="timeout_hammer",
                default="timeout_hammer" in current_perms
            ),
            discord.SelectOption(
                label="Cashier",
                description="Approve and reject deposit tickets",
                emoji="💳",
                value="cashier",
                default="cashier" in current_perms
            ),
            discord.SelectOption(
                label="Item Manager",
                description="Manage items and cases (/items, /cases, /setprices)",
                emoji="📦",
                value="item_manager",
                default="item_manager" in current_perms
            ),
        ]
        
        super().__init__(
            placeholder="Select permissions to manage...",
            options=options,
            min_values=0,
            max_values=7
        )
    
    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message(
                "❌ Not your panel!",
                ephemeral=True
            )
        
        selected_perms = self.values  # List of selected permissions

        # Only super admin may change super admin's stored permissions; other admins cannot
        if is_super_admin(self.target_user_id) and not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Super Admin yetkisi yalnızca Super Admin tarafından değiştirilebilir.",
                ephemeral=True,
            )

        # Also block regular admins from granting 'admin' perm if they aren't super admin
        if "admin" in selected_perms and not is_super_admin(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Sadece Super Admin, başkasına `admin` yetkisi verebilir.",
                ephemeral=True,
            )

        # Load current admins data
        admins = get_data("server/admins") or {}
        user_key = str(self.target_user_id)
        user_perms = admins.get(user_key, [])
        
        # Find added and removed permissions
        added_perms = [p for p in selected_perms if p not in user_perms]
        removed_perms = [p for p in user_perms if p not in selected_perms]
        
        # Permission display names
        perm_names = {
            "admin": "🔑 Admin",
            "moderator": "🛡️ Moderator",
            "ticketAdmin": "🎫 Ticket Admin",
            "ban_hammer": "🔨 Ban Hammer",
            "timeout_hammer": "⏱️ Timeout Hammer",
            "cashier": "💳 Cashier",
            "item_manager": "📦 Item Manager",
        }
        
        # Update permissions
        if added_perms or removed_perms:
            # Apply changes
            updated_perms = selected_perms if selected_perms else []
            
            if updated_perms:
                admins[user_key] = updated_perms
            elif user_key in admins:
                del admins[user_key]
            
            set_data("server/admins", admins)
            
            # Build response embed
            embed = Embed(
                title="✅ Permissions Updated",
                description=f"Updated permissions for <@{self.target_user_id}>",
                color=0x2ecc71
            )
            
            # Show added permissions
            if added_perms:
                added_text = "\n".join([perm_names.get(p, p) for p in added_perms])
                embed.add_field(
                    name="➕ Added",
                    value=added_text,
                    inline=True
                )
            
            # Show removed permissions
            if removed_perms:
                removed_text = "\n".join([perm_names.get(p, p) for p in removed_perms])
                embed.add_field(
                    name="➖ Removed",
                    value=removed_text,
                    inline=True
                )
            
            # Show current permissions
            if updated_perms:
                current_perms_text = "\n".join([perm_names.get(p, p) for p in updated_perms])
                embed.add_field(
                    name="🏷️ Current Permissions",
                    value=current_perms_text,
                    inline=False
                )
            else:
                embed.add_field(
                    name="🏷️ Current Permissions",
                    value="No permissions assigned",
                    inline=False
                )
        else:
            # No changes
            embed = Embed(
                title="ℹ️ No Changes",
                description="Selected permissions are already assigned.",
                color=0x3498db
            )
        
        # Update view with new permissions
        new_view = PermissionManagementView(self.target_user_id, self.admin_id, selected_perms if selected_perms else [])
        
        # First, defer the interaction
        await interaction.response.defer()
        
        # Update original message
        user = interaction.guild.get_member(self.target_user_id)
        if not user:
            user = await interaction.guild.fetch_member(self.target_user_id)
        
        main_embed = Embed(
            title=f"🔒 Manage Permissions - {user.name}",
            description=f"Configure permission roles for <@{self.target_user_id}>",
            color=0xe74c3c
        )
        
        updated_perms = selected_perms if selected_perms else []
        if updated_perms:
            perm_text = "\n".join([perm_names.get(p, p) for p in updated_perms])
            main_embed.add_field(
                name="🏷️ Current Permissions",
                value=perm_text,
                inline=False
            )
        else:
            main_embed.add_field(
                name="🏷️ Current Permissions",
                value="No permissions assigned",
                inline=False
            )
        
        main_embed.set_footer(text="Select a permission from the menu below")
        
        # Edit the original message
        await interaction.followup.edit_message(interaction.message.id, embed=main_embed, view=new_view)
        
        # Send ephemeral message with changes
        await interaction.followup.send(embed=embed, ephemeral=True)


class GuildEmojiSourceView(discord.ui.View):
    """Guild picker for selecting the emoji source server."""

    def __init__(self, admin_id: int, target_guild_id: int, guilds: list[discord.Guild]):
        super().__init__(timeout=300)
        self.admin_id = admin_id
        self.target_guild_id = target_guild_id

        sorted_guilds = sorted(guilds, key=lambda g: g.name.lower())
        capped = sorted_guilds[:125]
        for index in range(5):
            start = index * 25
            end = start + 25
            chunk = capped[start:end]
            if not chunk:
                break
            self.add_item(GuildEmojiSourceSelect(admin_id, target_guild_id, chunk, start + 1))


class GuildEmojiSourceSelect(discord.ui.Select):
    """Select menu that lists guilds by name."""

    def __init__(self, admin_id: int, target_guild_id: int, guilds: list[discord.Guild], start_index: int):
        self.admin_id = admin_id
        self.target_guild_id = target_guild_id
        self.guild_map = {str(g.id): g for g in guilds}

        options = []
        for guild in guilds:
            options.append(
                discord.SelectOption(
                    label=guild.name[:100],
                    description=f"ID: {guild.id}",
                    value=str(guild.id),
                )
            )

        end_index = start_index + len(options) - 1

        super().__init__(
            placeholder=f"Select source guild ({start_index}-{end_index})...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ This panel is not for you.", ephemeral=True)

        source_guild = self.guild_map.get(self.values[0])
        if source_guild is None:
            return await interaction.response.send_message("❌ Source guild not found.", ephemeral=True)

        emojis = list(source_guild.emojis)
        if not emojis:
            return await interaction.response.send_message("❌ This guild has no custom emojis.", ephemeral=True)

        view = EmojiImportView(
            admin_id=self.admin_id,
            target_guild_id=self.target_guild_id,
            source_guild_id=source_guild.id,
            emojis=emojis,
        )

        max_supported = min(len(emojis), 125)
        embed = Embed(
            title=f"😀 Emojis from {source_guild.name}",
            description=(
                f"Select which emojis to import into this server.\n"
                f"Available here: **{len(emojis)}**\n"
                f"Shown in menus: **{max_supported}**"
            ),
            color=0x2ecc71,
        )
        if len(emojis) > 125:
            embed.add_field(
                name="⚠️ Limit",
                value="Only first 125 emojis are shown because this panel uses 5 select menus.",
                inline=False,
            )
        embed.set_footer(text="You can use any menu multiple times.")

        await interaction.response.edit_message(embed=embed, view=view)


class EmojiImportView(discord.ui.View):
    """Contains up to 5 emoji select menus (1-25, 26-50, ...)."""

    def __init__(self, admin_id: int, target_guild_id: int, source_guild_id: int, emojis: list[discord.Emoji]):
        super().__init__(timeout=600)
        self.admin_id = admin_id
        self.target_guild_id = target_guild_id
        self.source_guild_id = source_guild_id

        # Keep exactly 5 chunks max (up to 125 emojis)
        capped = emojis[:125]
        for index in range(5):
            start = index * 25
            end = start + 25
            chunk = capped[start:end]
            if not chunk:
                break
            self.add_item(EmojiChunkSelect(admin_id, target_guild_id, source_guild_id, chunk, start + 1))


class EmojiChunkSelect(discord.ui.Select):
    """One chunked select menu for importing emojis."""

    def __init__(
        self,
        admin_id: int,
        target_guild_id: int,
        source_guild_id: int,
        chunk: list[discord.Emoji],
        start_index: int,
    ):
        self.admin_id = admin_id
        self.target_guild_id = target_guild_id
        self.source_guild_id = source_guild_id
        self.emoji_map = {str(e.id): e for e in chunk}

        end_index = start_index + len(chunk) - 1
        options = []
        for emoji in chunk:
            emoji_type = "animated" if emoji.animated else "static"
            options.append(
                discord.SelectOption(
                    label=emoji.name[:100],
                    description=f"{emoji_type} | ID: {emoji.id}",
                    value=str(emoji.id),
                )
            )

        super().__init__(
            placeholder=f"Emojis {start_index}-{end_index}",
            options=options,
            min_values=1,
            max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ This panel is not for you.", ephemeral=True)

        target_guild = interaction.client.get_guild(self.target_guild_id)
        if target_guild is None:
            return await interaction.response.send_message("❌ Target guild not found.", ephemeral=True)

        source_guild = interaction.client.get_guild(self.source_guild_id)
        if source_guild is None:
            return await interaction.response.send_message("❌ Source guild not found.", ephemeral=True)

        me = target_guild.get_member(interaction.client.user.id)
        if me is None:
            try:
                me = await target_guild.fetch_member(interaction.client.user.id)
            except Exception:
                me = None

        if me is None or not me.guild_permissions.manage_emojis_and_stickers:
            return await interaction.response.send_message(
                "❌ I need the 'Manage Emojis and Stickers' permission in the target guild.",
                ephemeral=True,
            )

        existing_names = {e.name for e in target_guild.emojis}
        added = []
        skipped = []
        failed = []

        await interaction.response.defer(ephemeral=True, thinking=True)

        for emoji_id in self.values:
            emoji = self.emoji_map.get(emoji_id) or source_guild.get_emoji(int(emoji_id))
            if emoji is None:
                failed.append(f"ID {emoji_id} (not found)")
                continue

            if emoji.name in existing_names:
                skipped.append(f"{emoji.name} (name exists)")
                continue

            try:
                image_bytes = await emoji.read()
                await target_guild.create_custom_emoji(
                    name=emoji.name,
                    image=image_bytes,
                    reason=f"Emoji import by {interaction.user} from {source_guild.name}",
                )
                existing_names.add(emoji.name)
                added.append(emoji.name)
            except Exception as exc:
                failed.append(f"{emoji.name} ({exc.__class__.__name__})")

        result = Embed(
            title="✅ Emoji Import Result",
            color=0x2ecc71 if added else 0xe67e22,
        )
        result.add_field(name="Added", value=str(len(added)), inline=True)
        result.add_field(name="Skipped", value=str(len(skipped)), inline=True)
        result.add_field(name="Failed", value=str(len(failed)), inline=True)

        if added:
            result.add_field(name="Imported Emojis", value="\n".join(added[:20]), inline=False)
        if skipped:
            result.add_field(name="Skipped Details", value="\n".join(skipped[:10]), inline=False)
        if failed:
            result.add_field(name="Failed Details", value="\n".join(failed[:10]), inline=False)

        await interaction.followup.send(embed=result, ephemeral=True)


class UserBonusCancelView(discord.ui.View):
    """View for cancelling a user's active bonus from the admin user panel."""

    def __init__(self, target_user_id: int, admin_id: int):
        super().__init__(timeout=120)
        self.target_user_id = target_user_id
        self.admin_id = admin_id
        lang = get_user_lang(admin_id)
        self.children[0].label = t('user_panel.bonus_cancel_btn', lang)

    @discord.ui.button(label="🚫 Cancel Active Bonus", style=discord.ButtonStyle.danger)
    async def cancel_bonus(self, interaction: discord.Interaction, button: discord.ui.Button):
        lang = get_user_lang(self.admin_id)
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message(
                t('user_panel.bonus_not_your_panel', lang), ephemeral=True
            )

        if not bonus_engine.has_active_bonus(self.target_user_id):
            return await interaction.response.send_message(
                embed=Embed(
                    title="ℹ️",
                    description=t('user_panel.bonus_cancel_not_found', lang),
                    color=0x95a5a6,
                ),
                ephemeral=True,
            )

        bonus_engine.forfeit_bonus(self.target_user_id)

        button.disabled = True
        await interaction.response.edit_message(view=self)

        try:
            target_user = await interaction.client.fetch_user(self.target_user_id)
            display_name = target_user.name
        except Exception:
            display_name = str(self.target_user_id)

        embed = Embed(
            title=t('user_panel.bonus_cancel_success_title', lang),
            description=t('user_panel.bonus_cancel_success_desc', lang, name=display_name),
            color=0x2ecc71,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()


class ConfirmResetView(discord.ui.View):
    """Confirmation view for reset_all_registrations"""

    def __init__(self, admin_id: int):
        super().__init__(timeout=60)
        self.admin_id = admin_id

    @discord.ui.button(label="✅ Yes, reset all", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        admins_data = get_data("server/admins") or {}
        admin_ids = set(admins_data.keys())

        registered_ids = get_all_registered_user_ids()
        cleared = 0
        skipped = 0

        for uid in registered_ids:
            if uid in admin_ids:
                skipped += 1
                continue
            clear_user_account(uid)
            cleared += 1

        embed = Embed(
            title="✅ Registrations Reset",
            description=(
                f"**{cleared}** registration(s) deleted.\n"
                f"**{skipped}** admin account(s) skipped."
            ),
            color=0x2ecc71
        )
        self.stop()
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message("❌ Not your panel!", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(
            embed=Embed(title="❌ Cancelled", description="No changes were made.", color=0x95a5a6),
            view=None
        )


async def setup(client):
    await client.add_cog(UserManagement(client))
