"""Self-assignable roles via button menu in a channel."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from modules.database import check_permission
from modules.self_roles_store import (
    MAX_ROLES,
    add_role,
    format_roles_list,
    get_config,
    remove_role,
    save_config,
    set_channel_message,
    set_panel_text,
)

log = logging.getLogger(__name__)

_STYLE_TO_DISCORD = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def _parse_emoji(emoji_str: str):
    if not emoji_str:
        return None
    s = emoji_str.strip()
    if s.startswith("<") and ":" in s and s.endswith(">"):
        try:
            return discord.PartialEmoji.from_str(s)
        except Exception:
            return None
    return s or None


def build_self_roles_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    embed = discord.Embed(
        title=cfg.get("title") or "Self Roles",
        description=cfg.get("description") or "",
        color=0x5865F2,
    )
    roles = cfg.get("roles") or []
    if roles:
        lines = []
        for r in roles[:25]:
            rid = int(r.get("role_id", 0))
            role = guild.get_role(rid)
            label = r.get("label") or (role.name if role else str(rid))
            em = (r.get("emoji") or "").strip()
            prefix = f"{em} " if em else ""
            lines.append(f"{prefix}**{label}**")
        embed.add_field(
            name="Available roles",
            value="\n".join(lines) or "—",
            inline=False,
        )
    embed.set_footer(text="Click a button to toggle that role on or off.")
    return embed


class SelfRoleToggleButton(discord.ui.Button):
    def __init__(self, guild_id: int, entry: dict):
        self.guild_id = guild_id
        self.role_id = int(entry["role_id"])
        label = (entry.get("label") or "").strip() or "Role"
        style_key = (entry.get("style") or "secondary").lower()
        super().__init__(
            label=label[:80],
            emoji=_parse_emoji(entry.get("emoji") or ""),
            style=_STYLE_TO_DISCORD.get(style_key, discord.ButtonStyle.secondary),
            custom_id=f"selfrole:{guild_id}:{self.role_id}",
            row=max(0, min(4, int(entry.get("row", 0)))),
        )

    async def callback(self, interaction: discord.Interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            return await interaction.response.send_message(
                "This menu is not for this server.", ephemeral=True
            )
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message(
                "Could not resolve your member profile.", ephemeral=True
            )
        role = interaction.guild.get_role(self.role_id)
        if role is None:
            return await interaction.response.send_message(
                "This role no longer exists. Ask an admin to refresh the menu.",
                ephemeral=True,
            )
        me = interaction.guild.me
        if me is None or role >= me.top_role:
            return await interaction.response.send_message(
                "I cannot assign this role (it is above my highest role).",
                ephemeral=True,
            )
        if role.managed:
            return await interaction.response.send_message(
                "This role is managed by an integration and cannot be toggled.",
                ephemeral=True,
            )
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Self-role toggle (remove)")
                await interaction.response.send_message(
                    f"Removed {role.mention}.", ephemeral=True
                )
            else:
                await member.add_roles(role, reason="Self-role toggle (add)")
                await interaction.response.send_message(
                    f"Added {role.mention}.", ephemeral=True
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permission to change that role.", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"Could not update roles: {e}", ephemeral=True
            )


class SelfRolesMenuView(discord.ui.View):
    def __init__(self, guild_id: int, roles: list | None = None):
        super().__init__(timeout=None)
        for entry in roles or []:
            try:
                self.add_item(SelfRoleToggleButton(guild_id, entry))
            except (KeyError, ValueError, TypeError):
                continue


def build_menu_view(guild_id: int, cfg: dict) -> SelfRolesMenuView:
    return SelfRolesMenuView(guild_id, cfg.get("roles") or [])


async def post_or_refresh_menu(
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    client: discord.Client,
    edit_existing: bool = True,
) -> tuple[bool, str]:
    cfg = get_config(guild.id)
    roles = cfg.get("roles") or []
    if not roles:
        return False, "Add at least one role before posting the menu."
    embed = build_self_roles_embed(guild, cfg)
    view = build_menu_view(guild.id, cfg)
    client.add_view(view)

    ch_id = cfg.get("channel_id")
    msg_id = cfg.get("message_id")
    if edit_existing and ch_id and msg_id and int(ch_id) == channel.id:
        try:
            old_msg = await channel.fetch_message(int(msg_id))
            await old_msg.edit(embed=embed, view=view)
            set_channel_message(guild.id, channel.id, old_msg.id)
            return True, f"Updated menu in {channel.mention}."
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    try:
        msg = await channel.send(embed=embed, view=view)
    except discord.Forbidden:
        return False, "I cannot send messages in that channel."
    set_channel_message(guild.id, channel.id, msg.id)
    return True, f"Posted self-role menu in {channel.mention}."


# ── Admin panel UI ────────────────────────────────────────────────────────────

def build_admin_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    ns = "*(not set)*"
    ch = cfg.get("channel_id")
    embed = discord.Embed(
        title="🎭 Self Roles",
        description=(
            "Configure roles users can toggle with buttons in a channel.\n"
            "Set the channel, add roles, then **Post / refresh menu**."
        ),
        color=0x9B59B6,
    )
    embed.add_field(
        name="Menu channel",
        value=f"<#{ch}>" if ch else ns,
        inline=True,
    )
    embed.add_field(
        name="Message ID",
        value=str(cfg.get("message_id") or "—"),
        inline=True,
    )
    embed.add_field(
        name=f"Roles ({len(cfg.get('roles') or [])}/{MAX_ROLES})",
        value=format_roles_list(guild, cfg),
        inline=False,
    )
    return embed


class SelfRolesSetChannelButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(
            label="Set channel",
            style=discord.ButtonStyle.primary,
            row=0,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "No permission.", ephemeral=True
            )
        await interaction.response.send_modal(_SelfRolesChannelModal(self.user_id))


class _SelfRolesChannelModal(discord.ui.Modal, title="Self roles channel"):
    channel_input = discord.ui.TextInput(
        label="Channel ID or #mention",
        placeholder="#self-roles or channel ID",
        required=True,
        max_length=100,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        raw = self.channel_input.value.strip()
        ch_id = None
        if raw.startswith("<#") and raw.endswith(">"):
            ch_id = int(raw[2:-1])
        elif raw.isdigit():
            ch_id = int(raw)
        ch = interaction.guild.get_channel(ch_id) if ch_id else None
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message(
                "Invalid text channel.", ephemeral=True
            )
        cfg = get_config(interaction.guild.id)
        cfg["channel_id"] = ch.id
        save_config(interaction.guild.id, cfg)
        cfg = get_config(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_admin_embed(interaction.guild, cfg),
            view=SelfRolesAdminView.for_guild(interaction.guild, self.user_id),
        )


class SelfRolesAddRoleSelect(discord.ui.RoleSelect):
    def __init__(self, user_id: int):
        super().__init__(
            placeholder="Add a role to the menu…",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "No permission.", ephemeral=True
            )
        if not interaction.guild:
            return
        role = self.values[0]
        ok, err = add_role(
            interaction.guild.id,
            role.id,
            label=role.name,
            emoji="",
            style="secondary",
            row=0,
        )
        if not ok:
            return await interaction.response.send_message(err, ephemeral=True)
        cfg = get_config(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_admin_embed(interaction.guild, cfg),
            view=SelfRolesAdminView.for_guild(interaction.guild, self.user_id),
        )


class SelfRolesRemoveSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, cfg: dict, user_id: int):
        self.user_id = user_id
        options = []
        for r in (cfg.get("roles") or [])[:25]:
            rid = int(r.get("role_id", 0))
            role = guild.get_role(rid)
            label = (r.get("label") or (role.name if role else str(rid)))[:100]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=str(rid),
                    description="Remove from menu",
                )
            )
        super().__init__(
            placeholder="Remove role from menu…",
            options=options or [discord.SelectOption(label="(empty)", value="0")],
            disabled=not options,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "No permission.", ephemeral=True
            )
        if not interaction.guild or self.values[0] == "0":
            return
        remove_role(interaction.guild.id, int(self.values[0]))
        cfg = get_config(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_admin_embed(interaction.guild, cfg),
            view=SelfRolesAdminView.for_guild(interaction.guild, self.user_id),
        )


class SelfRolesPostButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(
            label="Post / refresh menu",
            style=discord.ButtonStyle.success,
            row=3,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "No permission.", ephemeral=True
            )
        if not interaction.guild:
            return
        cfg = get_config(interaction.guild.id)
        ch_id = cfg.get("channel_id")
        if not ch_id:
            return await interaction.response.send_message(
                "Set a menu channel first.", ephemeral=True
            )
        ch = interaction.guild.get_channel(int(ch_id))
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message(
                "Menu channel not found. Set it again.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        ok, msg = await post_or_refresh_menu(
            interaction.guild, ch, client=interaction.client
        )
        cfg = get_config(interaction.guild.id)
        await interaction.followup.send(msg, ephemeral=True)
        if ok:
            await interaction.message.edit(
                embed=build_admin_embed(interaction.guild, cfg),
                view=SelfRolesAdminView.for_guild(interaction.guild, self.user_id),
            )


class SelfRolesTextButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(
            label="Edit title & text",
            style=discord.ButtonStyle.secondary,
            row=3,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "No permission.", ephemeral=True
            )
        cfg = get_config(interaction.guild.id) if interaction.guild else {}
        await interaction.response.send_modal(
            _SelfRolesTextModal(
                self.user_id,
                title=cfg.get("title") or "",
                description=cfg.get("description") or "",
            )
        )


class _SelfRolesTextModal(discord.ui.Modal, title="Self roles panel text"):
    def __init__(self, user_id: int, *, title: str, description: str):
        super().__init__()
        self.user_id = user_id
        self.title_input = discord.ui.TextInput(
            label="Embed title",
            default=title[:256],
            max_length=256,
            required=True,
        )
        self.desc_input = discord.ui.TextInput(
            label="Embed description",
            default=description[:4000],
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=False,
        )
        self.add_item(self.title_input)
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        set_panel_text(
            interaction.guild.id,
            title=self.title_input.value,
            description=self.desc_input.value,
        )
        cfg = get_config(interaction.guild.id)
        await interaction.response.edit_message(
            embed=build_admin_embed(interaction.guild, cfg),
            view=SelfRolesAdminView.for_guild(interaction.guild, self.user_id),
        )


class SelfRolesAdminView(discord.ui.View):
    def __init__(self, user_id: int = 0):
        super().__init__(timeout=300)
        self.user_id = user_id

    @classmethod
    def for_guild(cls, guild: discord.Guild, user_id: int) -> SelfRolesAdminView:
        view = cls(user_id)
        cfg = get_config(guild.id)
        view.add_item(SelfRolesSetChannelButton(user_id))
        view.add_item(SelfRolesAddRoleSelect(user_id))
        view.add_item(SelfRolesRemoveSelect(guild, cfg, user_id))
        view.add_item(SelfRolesPostButton(user_id))
        view.add_item(SelfRolesTextButton(user_id))
        from cogs.admin_panel import BackToServerSettingsButton

        view.add_item(BackToServerSettingsButton(hub="channels", user_id=user_id))
        return view


class SelfRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await self._register_views()

    async def _register_views(self):
        for guild in self.bot.guilds:
            cfg = get_config(guild.id)
            if cfg.get("roles"):
                self.bot.add_view(build_menu_view(guild.id, cfg))
        log.info("Self-role persistent views registered")


async def setup(bot: commands.Bot):
    await bot.add_cog(SelfRoles(bot))
    cog = bot.get_cog("SelfRoles")
    if cog:
        await cog._register_views()
