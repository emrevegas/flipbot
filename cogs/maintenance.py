"""Maintenance mode cog — global block + admin settings UI."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from modules.database import check_permission
from modules.maintenance import (
    can_bypass_maintenance,
    get_maintenance_message,
    is_maintenance_enabled,
    send_maintenance_notice,
    set_maintenance_enabled,
    set_maintenance_message,
    should_block_user,
)
from modules.translator import t
from modules.utils import get_user_lang


def _build_maintenance_embed(lang: str) -> discord.Embed:
    enabled = is_maintenance_enabled()
    embed = discord.Embed(
        title=t("maintenance.settings_title", lang=lang),
        description=t("maintenance.settings_description", lang=lang),
        color=discord.Color.orange() if enabled else discord.Color.green(),
    )
    embed.add_field(
        name=t("maintenance.status_label", lang=lang),
        value=t("maintenance.status_on", lang=lang)
        if enabled
        else t("maintenance.status_off", lang=lang),
        inline=True,
    )
    msg = get_maintenance_message()
    embed.add_field(
        name=t("maintenance.message_label", lang=lang),
        value=msg[:1024],
        inline=False,
    )
    embed.set_footer(text=t("maintenance.admin_footer", lang=lang))
    return embed


class MaintenanceMessageModal(discord.ui.Modal, title="Maintenance message"):
    def __init__(self, lang: str):
        super().__init__()
        self.lang = lang
        self.message_input = discord.ui.TextInput(
            label="Custom message (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
            default=(_root_msg() or "")[:500] or None,
            placeholder="Leave empty for default translated message",
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", lang=self.lang), ephemeral=True
            )
        text = (self.message_input.value or "").strip()
        set_maintenance_message(text or None)
        embed = _build_maintenance_embed(self.lang)
        await interaction.response.edit_message(embed=embed, view=MaintenanceSettingsView(self.lang))


def _root_msg():
    from modules.maintenance import _root_server

    return _root_server().get("maintenance_message")


class MaintenanceSettingsView(discord.ui.View):
    def __init__(self, lang: str = "en"):
        super().__init__(timeout=300)
        self.lang = lang

    @discord.ui.button(label="Toggle maintenance", style=discord.ButtonStyle.danger, custom_id="maintenance:toggle")
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", lang=self.lang), ephemeral=True
            )
        new_state = not is_maintenance_enabled()
        set_maintenance_enabled(new_state)
        await _update_presence(interaction.client)
        embed = _build_maintenance_embed(self.lang)
        status = t("maintenance.toggled_on", lang=self.lang) if new_state else t("maintenance.toggled_off", lang=self.lang)
        await interaction.response.edit_message(
            content=status,
            embed=embed,
            view=MaintenanceSettingsView(self.lang),
        )

    @discord.ui.button(label="Edit message", style=discord.ButtonStyle.primary, custom_id="maintenance:edit_msg")
    async def edit_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", lang=self.lang), ephemeral=True
            )
        await interaction.response.send_modal(MaintenanceMessageModal(self.lang))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="maintenance:back")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.admin_panel import ServerSettingsView

        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", lang=self.lang), ephemeral=True
            )
        embed = discord.Embed(
            title=t("admin_panel.server_title", lang=self.lang),
            description=t("admin_panel.server_description", lang=self.lang),
            color=discord.Color.blue(),
        )
        await interaction.response.edit_message(embed=embed, view=ServerSettingsView())


async def _update_presence(bot: commands.Bot):
    try:
        if is_maintenance_enabled():
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="Maintenance — back soon",
            )
            await bot.change_presence(status=discord.Status.dnd, activity=activity)
        else:
            if hasattr(bot, "update_presence"):
                bot.update_presence.restart()
    except Exception:
        pass


class Maintenance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_check(self._command_check)

    async def cog_unload(self):
        self.bot.remove_check(self._command_check)

    async def _command_check(self, ctx: commands.Context) -> bool:
        if ctx.author.bot or not should_block_user(ctx.author.id):
            return True
        lang = get_user_lang(ctx.author.id)
        await ctx.send(t("maintenance.default_message", lang=lang))
        return False

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.user.bot:
            return
        if not should_block_user(interaction.user.id):
            return
        # Allow slash /panel for admins — already bypassed above
        if interaction.type == discord.InteractionType.application_command:
            await send_maintenance_notice(interaction)
            return
        if interaction.type in (
            discord.InteractionType.component,
            discord.InteractionType.modal_submit,
        ):
            await send_maintenance_notice(interaction)
            return

    @app_commands.command(name="maintenance", description="Toggle maintenance mode (admin)")
    async def maintenance_cmd(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                t("errors.no_permission", lang=get_user_lang(interaction.user.id)),
                ephemeral=True,
            )
        lang = get_user_lang(interaction.user.id)
        embed = _build_maintenance_embed(lang)
        await interaction.response.send_message(
            embed=embed,
            view=MaintenanceSettingsView(lang),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Maintenance(bot))
    await _update_presence(bot)
