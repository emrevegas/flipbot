"""
Auto Giveaway System
- Multiple auto-giveaway configs per guild (each targets a channel)
- Configurable duration, winner count, prize (real balance), eligibility conditions
- Conditions: total_wagered, total_deposit, total_withdraw, deposit_last_Nh, deposit_last_1h
- Starts automatically, loops on finish
- Admin panel integration via GiveawayPanelView
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from modules.database import (
    get_data, set_data, replace_data,
    get_user_data, set_user_data,
    get_server_data, check_permission,
)
from modules.player import Player
from modules.utils import format_balance

# ─── Storage helpers ──────────────────────────────────────────────────────────

GIVEAWAY_KEY = "server/giveaway_configs"
ACTIVE_KEY   = "server/giveaway_active"

# Interval (seconds) at which bio requirement is verified for active participants
BIO_CHECK_INTERVAL = 60


def _load_configs() -> dict:
    return get_data(GIVEAWAY_KEY) or {}


def _save_configs(configs: dict):
    replace_data(GIVEAWAY_KEY, configs)


def _load_active() -> dict:
    return get_data(ACTIVE_KEY) or {}


def _save_active(active: dict):
    replace_data(ACTIVE_KEY, active)


# ─── Eligibility check ────────────────────────────────────────────────────────

def _check_eligibility(user_id: int, conditions: dict, guild: discord.Guild) -> bool:
    """Return True if user meets all configured conditions (excludes bio — async only)."""
    if not conditions:
        return True  # no conditions → open to everyone

    member = guild.get_member(user_id)
    if member is None:
        return False

    stats = get_user_data(user_id, "stats") or {}

    # total_wagered threshold
    if "total_wagered" in conditions:
        if int(stats.get("total_wagered", 0)) < int(conditions["total_wagered"]):
            return False

    # total_deposit threshold
    if "total_deposit" in conditions:
        if int(stats.get("total_deposit", 0)) < int(conditions["total_deposit"]):
            return False

    # total_withdraw threshold
    if "total_withdraw" in conditions:
        if int(stats.get("total_withdraw", 0)) < int(conditions["total_withdraw"]):
            return False

    # deposit in last N hours
    if "deposit_last_hours" in conditions:
        hours     = int(conditions["deposit_last_hours"])
        min_amt   = int(conditions.get("deposit_last_hours_amount", 1))
        since     = int(time.time()) - hours * 3600
        dep_hist  = get_user_data(user_id, "deposit_history") or {}
        recent    = sum(
            float(d.get("confirmed_amount") or d.get("amount", 0))
            for d in dep_hist.values()
            if d.get("status") == "completed" and int(d.get("timestamp", 0)) >= since
        )
        if recent < min_amt:
            return False

    # role requirement
    if "required_role_id" in conditions:
        role_id = int(conditions["required_role_id"])
        if not any(r.id == role_id for r in member.roles):
            return False

    # status_contains — sync check via member cache (requires presences intent)
    if "status_contains" in conditions:
        required_status = conditions["status_contains"]
        if not _check_status(member, required_status):
            return False

    # bio_contains is NOT checked here (requires async fetch) — handled at join time by caller
    return True


async def _check_bio(bot: commands.Bot, user_id: int, required_text: str) -> bool:
    """Fetch the user's profile via HTTP and check their bio contains required_text."""
    try:
        user = await bot.fetch_user(user_id)
        bio: str = getattr(user, "bio", None) or ""
        return required_text.lower() in bio.lower()
    except Exception:
        return False


def _check_status(member: discord.Member, required_text: str) -> bool:
    """Check if the member's custom Discord status contains required_text."""
    return required_text.lower() in _get_member_status_text(member).lower()


def _get_member_status_text(member: discord.Member) -> str:
    """Return the member's current custom status text (empty string if none)."""
    if member is None:
        return ""
    for activity in member.activities:
        if isinstance(activity, discord.CustomActivity):
            return activity.state or ""
    return ""


# ─── Active giveaway state ────────────────────────────────────────────────────

class ActiveGiveaway:
    """Runtime state for one running giveaway instance."""

    def __init__(self, config_id: str, cfg: dict, message_id: int):
        self.config_id = config_id
        self.cfg       = cfg
        self.message_id = message_id
        self.participants: list[int] = []
        self.participants_status: dict = {}  # {str(user_id): saved_status_text}
        self.started_at: int = int(time.time())
        self.ends_at: int = self.started_at + int(cfg["duration_minutes"]) * 60

    def to_dict(self) -> dict:
        return {
            "config_id":           self.config_id,
            "message_id":          self.message_id,
            "participants":        self.participants,
            "participants_status": self.participants_status,
            "started_at":          self.started_at,
            "ends_at":             self.ends_at,
        }

    @classmethod
    def from_dict(cls, config_id: str, cfg: dict, data: dict) -> "ActiveGiveaway":
        obj = cls.__new__(cls)
        obj.config_id          = config_id
        obj.cfg                = cfg
        obj.message_id         = data["message_id"]
        obj.participants       = data.get("participants", [])
        obj.participants_status = data.get("participants_status", {})
        obj.started_at         = data["started_at"]
        obj.ends_at            = data["ends_at"]
        return obj


# ─── Embeds ───────────────────────────────────────────────────────────────────

def _giveaway_embed(cfg: dict, active: ActiveGiveaway) -> discord.Embed:
    prize        = int(cfg.get("prize", 0))
    winner_count = int(cfg.get("winner_count", 1))
    conditions   = cfg.get("conditions", {})
    participants = len(active.participants)
    ends_at      = active.ends_at

    embed = discord.Embed(
        title="🎉 AUTO GIVEAWAY",
        description=(
            f"**Prize:** 💰 {format_balance(prize, 'real')}\n"
            f"**Winners:** {winner_count}\n"
            f"**Ends:** <t:{ends_at}:R> (<t:{ends_at}:t>)\n"
            f"**Participants:** {participants}\n\n"
            "Click the button below to enter!"
        ),
        color=0xf1c40f,
    )

    if conditions:
        lines = []
        if "total_wagered" in conditions:
            lines.append(f"🎲 Min total wagered: {format_balance(conditions['total_wagered'], 'real')}")
        if "total_deposit" in conditions:
            lines.append(f"💳 Min total deposit: {format_balance(conditions['total_deposit'], 'real')}")
        if "total_withdraw" in conditions:
            lines.append(f"🏦 Min total withdraw: {format_balance(conditions['total_withdraw'], 'real')}")
        if "deposit_last_hours" in conditions:
            lines.append(
                f"⏰ Min {format_balance(conditions.get('deposit_last_hours_amount', 1), 'real')} deposited in last {conditions['deposit_last_hours']}h"
            )
        if "required_role_id" in conditions:
            lines.append(f"🏷️ Required role: <@&{conditions['required_role_id']}>")
        if "bio_contains" in conditions:
            lines.append(f"📝 Bio must contain: `{conditions['bio_contains']}`")
        if "status_contains" in conditions:
            lines.append(f"🟢 Status must contain: `{conditions['status_contains']}`")
        if lines:
            embed.add_field(name="📋 Requirements", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📋 Requirements", value="No requirements — open to everyone!", inline=False)
    embed.set_footer(text="Vegas Casino | Auto Giveaway")
    return embed


def _ended_embed(cfg: dict, winners: list[int]) -> discord.Embed:
    prize = int(cfg.get("prize", 0))
    embed = discord.Embed(
        title="🎊 GIVEAWAY ENDED",
        color=discord.Color.green(),
    )
    if winners:
        mentions = "\n".join(f"🥇 <@{uid}>" for uid in winners)
        embed.description = (
            f"**Prize:** 💰 {format_balance(prize, 'real')}\n\n"
            f"**Winners:**\n{mentions}"
        )
    else:
        embed.description = "The giveaway ended but there were no eligible participants. 😔"
    embed.set_footer(text="Vegas Casino | Auto Giveaway")
    return embed


# ─── Giveaway message view ────────────────────────────────────────────────────

class JoinGiveawayView(discord.ui.View):
    """Persistent giveaway join button."""

    def __init__(self, config_id: str):
        super().__init__(timeout=None)
        self.config_id = config_id

    @discord.ui.button(
        label="🎉 Enter Giveaway",
        style=discord.ButtonStyle.primary,
        custom_id="giveaway:join",
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        config_id = None
        # Find active giveaway for this message
        active_data = _load_active()
        for cid, adict in active_data.items():
            if adict.get("message_id") == interaction.message.id:
                config_id = cid
                break

        if not config_id:
            return await interaction.response.send_message(
                "❌ This giveaway is no longer active.", ephemeral=True
            )

        configs = _load_configs()
        cfg = configs.get(config_id)
        if not cfg or not cfg.get("enabled"):
            return await interaction.response.send_message(
                "❌ This giveaway has been disabled.", ephemeral=True
            )

        uid = interaction.user.id
        adict = active_data[config_id]

        if uid in adict.get("participants", []):
            return await interaction.response.send_message(
                "✅ You have already entered this giveaway!", ephemeral=True
            )

        # Eligibility check (sync conditions)
        if not _check_eligibility(uid, cfg.get("conditions", {}), interaction.guild):
            return await interaction.response.send_message(
                "❌ You do not meet the requirements for this giveaway.", ephemeral=True
            )

        # Bio check (async — HTTP fetch required)
        bio_required = cfg.get("conditions", {}).get("bio_contains")
        if bio_required:
            has_bio = await _check_bio(interaction.client, uid, bio_required)
            if not has_bio:
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ Bio Requirement Not Met",
                        description=(
                            f"Your Discord bio must contain **`{bio_required}`** to enter this giveaway.\n\n"
                            "Add it to your profile bio and try again."
                        ),
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )

        # Status check (sync — already handled in _check_eligibility via cache,
        # but re-check here to give a descriptive error message)
        status_required = cfg.get("conditions", {}).get("status_contains")
        if status_required:
            member = interaction.guild.get_member(uid)
            if not _check_status(member, status_required):
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ Status Requirement Not Met",
                        description=(
                            f"Your Discord custom status must contain **`{status_required}`** to enter this giveaway.\n\n"
                            "Update your status and try again."
                        ),
                        color=discord.Color.red(),
                    ),
                    ephemeral=True,
                )

        # Save current status text so offline users aren't incorrectly removed
        member = interaction.guild.get_member(uid)
        saved_status = _get_member_status_text(member)

        adict.setdefault("participants", []).append(uid)
        adict.setdefault("participants_status", {})[str(uid)] = saved_status
        active_data[config_id] = adict
        _save_active(active_data)

        # Update embed participant count
        configs = _load_configs()
        cfg = configs.get(config_id, {})
        active_obj = ActiveGiveaway.from_dict(config_id, cfg, adict)
        try:
            embed = _giveaway_embed(cfg, active_obj)
            await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass

        await interaction.response.send_message(
            "🎉 You have entered the giveaway! Good luck!", ephemeral=True
        )


# ─── Admin panel views ────────────────────────────────────────────────────────

class GiveawayListView(discord.ui.View):
    """Shows all configured giveaways with add/remove controls."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="➕ Add New Giveaway", style=discord.ButtonStyle.success, row=0)
    async def add_new(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = GiveawayChannelSelectView()
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📡 Select Giveaway Channel",
                description="Choose the text channel where the giveaway will be posted.",
                color=0xf1c40f,
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="🗑️ Remove Giveaway", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        configs = _load_configs()
        if not configs:
            return await interaction.response.send_message(
                "❌ No giveaway configurations found.", ephemeral=True
            )
        view = discord.ui.View(timeout=300)
        view.add_item(_GiveawayRemoveSelect(configs))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🗑️ Remove Giveaway",
                description="Select the giveaway you want to remove:",
                color=discord.Color.red(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="⚙️ Toggle Enable/Disable", style=discord.ButtonStyle.secondary, row=1)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        configs = _load_configs()
        if not configs:
            return await interaction.response.send_message(
                "❌ No giveaway configurations found.", ephemeral=True
            )
        view = discord.ui.View(timeout=300)
        view.add_item(_GiveawayToggleSelect(configs))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚙️ Toggle Giveaway",
                description="Select the giveaway to enable or disable:",
                color=discord.Color.orange(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="📋 Edit Conditions", style=discord.ButtonStyle.primary, row=1)
    async def edit_conditions(self, interaction: discord.Interaction, button: discord.ui.Button):
        configs = _load_configs()
        if not configs:
            return await interaction.response.send_message(
                "❌ No giveaway configurations found.", ephemeral=True
            )
        view = discord.ui.View(timeout=300)
        view.add_item(_GiveawayConditionSelect(configs))
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📋 Edit Conditions",
                description="Select the giveaway whose conditions you want to edit:",
                color=discord.Color.blurple(),
            ),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.admin_panel import AdminPanelView, _build_admin_panel_embed
        embed = _build_admin_panel_embed(interaction)
        await interaction.response.edit_message(embed=embed, view=AdminPanelView())


def _giveaway_list_embed() -> discord.Embed:
    configs = _load_configs()
    active  = _load_active()
    embed   = discord.Embed(
        title="🎉 Auto Giveaway System",
        description="All configured auto giveaways are listed below.",
        color=0xf1c40f,
    )
    if not configs:
        embed.add_field(name="No Giveaways Configured", value="Click ➕ Add New to create one.", inline=False)
        return embed

    for cid, cfg in configs.items():
        status   = "✅ Enabled" if cfg.get("enabled") else "❌ Disabled"
        running  = "🟢 Running" if cid in active else "⭕ Waiting"
        ch       = f"<#{cfg.get('channel_id', 0)}>"
        prize    = format_balance(cfg.get("prize", 0), "real")
        duration = cfg.get("duration_minutes", 0)
        winners  = cfg.get("winner_count", 1)
        conds    = cfg.get("conditions", {})
        cond_txt = f"{len(conds)} condition(s)" if conds else "No requirements"
        embed.add_field(
            name=f"🎰 {cfg.get('name', cid)}",
            value=(
                f"**Channel:** {ch}\n"
                f"**Prize:** {prize}\n"
                f"**Duration:** {duration} min  **Winners:** {winners}\n"
                f"**Conditions:** {cond_txt}\n"
                f"**Status:** {status} | {running}"
            ),
            inline=True,
        )
    return embed


class _GiveawayRemoveSelect(discord.ui.Select):
    def __init__(self, configs: dict):
        options = [
            discord.SelectOption(
                label=cfg.get("name", cid)[:100],
                description=f"Channel: {cfg.get('channel_id')} | Prize: {cfg.get('prize')}",
                value=cid,
            )
            for cid, cfg in configs.items()
        ]
        super().__init__(placeholder="Select a giveaway to remove...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cid = self.values[0]
        configs = _load_configs()
        name = configs.pop(cid, {}).get("name", cid)
        _save_configs(configs)

        # Stop active if running
        active = _load_active()
        active.pop(cid, None)
        _save_active(active)

        await interaction.response.send_message(
            f"✅ **{name}** has been removed.", ephemeral=True
        )


class _GiveawayToggleSelect(discord.ui.Select):
    def __init__(self, configs: dict):
        options = [
            discord.SelectOption(
                label=cfg.get("name", cid)[:100],
                description="✅ Enabled" if cfg.get("enabled") else "❌ Disabled",
                value=cid,
            )
            for cid, cfg in configs.items()
        ]
        super().__init__(placeholder="Select a giveaway to toggle...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cid = self.values[0]
        configs = _load_configs()
        if cid not in configs:
            return await interaction.response.send_message("❌ Not found.", ephemeral=True)
        configs[cid]["enabled"] = not configs[cid].get("enabled", True)
        _save_configs(configs)
        state = "✅ Enabled" if configs[cid]["enabled"] else "❌ Disabled"
        await interaction.response.send_message(
            f"**{configs[cid].get('name', cid)}** → {state}", ephemeral=True
        )


class _GiveawayConditionSelect(discord.ui.Select):
    def __init__(self, configs: dict):
        options = [
            discord.SelectOption(label=cfg.get("name", cid)[:100], value=cid)
            for cid, cfg in configs.items()
        ]
        super().__init__(placeholder="Select a giveaway to edit conditions...", options=options)

    async def callback(self, interaction: discord.Interaction):
        cid = self.values[0]
        configs = _load_configs()
        cfg = configs.get(cid, {})
        view = GiveawayConditionView(cid, cfg)
        await interaction.response.send_message(
            embed=view._build_embed(),
            view=view,
            ephemeral=True,
        )


# ─── Setup flow ──────────────────────────────────────────────────────────────

class GiveawayChannelSelectView(discord.ui.View):
    """Step 1: pick a text channel, then open the setup modal."""

    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Select the giveaway channel...",
        channel_types=[discord.ChannelType.text],
    )
    async def channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect,
    ):
        channel = select.values[0]
        modal = GiveawaySetupModal(channel.id)
        await interaction.response.send_modal(modal)


class GiveawaySetupModal(discord.ui.Modal, title="🎉 New Auto Giveaway"):
    name_input = discord.ui.TextInput(
        label="Giveaway Name",
        placeholder="e.g. Weekly Bonus Giveaway",
        max_length=80,
    )
    duration_input = discord.ui.TextInput(
        label="Duration (minutes)",
        placeholder="e.g. 60",
        max_length=6,
    )
    prize_input = discord.ui.TextInput(
        label="Prize Amount (balance)",
        placeholder="e.g. 5000",
        max_length=12,
    )
    winner_count_input = discord.ui.TextInput(
        label="Number of Winners",
        placeholder="e.g. 1",
        max_length=3,
        default="1",
    )

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration     = int(self.duration_input.value.strip())
            prize        = int(self.prize_input.value.strip().replace(",", "").replace(".", ""))
            winner_count = int(self.winner_count_input.value.strip())
            if duration < 1 or prize < 1 or winner_count < 1:
                raise ValueError("Values must be greater than 0.")
        except (ValueError, TypeError) as e:
            return await interaction.response.send_message(f"❌ Invalid input: {e}", ephemeral=True)

        channel = interaction.guild.get_channel(self.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Channel not found. Please go back and select a valid text channel.", ephemeral=True
            )

        import os
        config_id = str(int(time.time())) + os.urandom(2).hex()
        new_cfg = {
            "name":             self.name_input.value.strip(),
            "channel_id":       self.channel_id,
            "duration_minutes": duration,
            "prize":            prize,
            "winner_count":     winner_count,
            "enabled":          True,
            "conditions":       {},
            "created_at":       int(time.time()),
        }
        configs = _load_configs()
        configs[config_id] = new_cfg
        _save_configs(configs)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="✅ Giveaway Created",
                description=(
                    f"**{new_cfg['name']}** has been saved.\n\n"
                    f"**Channel:** {channel.mention}\n"
                    f"**Duration:** {duration} minutes\n"
                    f"**Prize:** {format_balance(prize, 'real')}\n"
                    f"**Winners:** {winner_count}\n\n"
                    "The giveaway will start automatically on the next loop cycle."
                ),
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )


# ─── Condition flow ──────────────────────────────────────────────────────────

class GiveawayAmountsModal(discord.ui.Modal, title="📋 Set Min Amounts"):
    """Sub-modal opened from GiveawayConditionView to set numeric thresholds."""

    min_wagered = discord.ui.TextInput(
        label="Min Total Wagered (empty = no requirement)",
        placeholder="e.g. 100000",
        required=False,
        max_length=15,
    )
    min_deposit = discord.ui.TextInput(
        label="Min Total Deposited (empty = no requirement)",
        placeholder="e.g. 50000",
        required=False,
        max_length=15,
    )

    def __init__(self, parent_view: "GiveawayConditionView"):
        super().__init__()
        self.parent_view = parent_view
        conds = parent_view.conditions
        if "total_wagered" in conds:
            self.min_wagered.default = str(conds["total_wagered"])
        if "total_deposit" in conds:
            self.min_deposit.default = str(conds["total_deposit"])

    async def on_submit(self, interaction: discord.Interaction):
        errors: list[str] = []

        def _parse(val: str, label: str) -> Optional[int]:
            v = val.strip().replace(",", "").replace(".", "")
            if not v:
                return None
            try:
                return int(v)
            except ValueError:
                errors.append(f"❌ {label}: invalid number")
                return None

        tw = _parse(self.min_wagered.value, "Min Wagered")
        td = _parse(self.min_deposit.value, "Min Deposit")

        if errors:
            return await interaction.response.send_message("\n".join(errors), ephemeral=True)

        conds = self.parent_view.conditions
        if tw is not None:
            conds["total_wagered"] = tw
        else:
            conds.pop("total_wagered", None)
        if td is not None:
            conds["total_deposit"] = td
        else:
            conds.pop("total_deposit", None)

        await interaction.response.edit_message(
            embed=self.parent_view._build_embed(),
            view=self.parent_view,
        )


class _ConditionRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent_view: "GiveawayConditionView"):
        super().__init__(
            placeholder="Required role (leave blank = no role requirement)",
            min_values=0,
            max_values=1,
            row=0,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if self.values:
            self.parent_view.conditions["required_role_id"] = self.values[0].id
        else:
            self.parent_view.conditions.pop("required_role_id", None)
        await interaction.response.edit_message(
            embed=self.parent_view._build_embed(),
            view=self.parent_view,
        )


class GiveawayConditionView(discord.ui.View):
    """View for editing giveaway conditions — role via RoleSelect, amounts via sub-modal."""

    def __init__(self, config_id: str, cfg: dict):
        super().__init__(timeout=300)
        self.config_id  = config_id
        self.cfg_name   = cfg.get("name", config_id)
        self.conditions = dict(cfg.get("conditions", {}))
        self.add_item(_ConditionRoleSelect(self))

    def _build_embed(self) -> discord.Embed:
        conds = self.conditions
        lines = []
        if "total_wagered" in conds:
            lines.append(f"🎲 Min Wagered: {format_balance(conds['total_wagered'], 'real')}")
        if "total_deposit" in conds:
            lines.append(f"💳 Min Deposit: {format_balance(conds['total_deposit'], 'real')}")
        if "required_role_id" in conds:
            lines.append(f"🏷️ Required Role: <@&{conds['required_role_id']}>")
        if "bio_contains" in conds:
            lines.append(f"📝 Bio must contain: `{conds['bio_contains']}`")
        if "status_contains" in conds:
            lines.append(f"🟢 Status must contain: `{conds['status_contains']}`")
        desc = "\n".join(lines) if lines else "No requirements — open to everyone."
        return discord.Embed(
            title=f"📋 Edit Conditions — {self.cfg_name}",
            description=desc,
            color=discord.Color.blurple(),
        ).set_footer(text="Select a role above, then set numeric amounts or save directly.")

    @discord.ui.button(label="📝 Set Min Amounts", style=discord.ButtonStyle.secondary, row=1)
    async def set_amounts(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = GiveawayAmountsModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📝 Bio Contains", style=discord.ButtonStyle.secondary, row=1)
    async def set_bio(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = GiveawayBioModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🟢 Custom Status", style=discord.ButtonStyle.secondary, row=2)
    async def set_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = GiveawayStatusModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Clear All", style=discord.ButtonStyle.danger, row=3)
    async def clear_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.conditions.clear()
        await interaction.response.edit_message(
            embed=self._build_embed(),
            view=self,
        )

    @discord.ui.button(label="✅ Save", style=discord.ButtonStyle.success, row=3)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        configs = _load_configs()
        if self.config_id not in configs:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)
        configs[self.config_id]["conditions"] = self.conditions
        _save_configs(configs)
        conds = self.conditions
        lines = []
        if "total_wagered" in conds:
            lines.append(f"• Min Wagered: {format_balance(conds['total_wagered'], 'real')}")
        if "total_deposit" in conds:
            lines.append(f"• Min Deposit: {format_balance(conds['total_deposit'], 'real')}")
        if "required_role_id" in conds:
            lines.append(f"• Required Role: <@&{conds['required_role_id']}>")
        if "bio_contains" in conds:
            lines.append(f"• Bio must contain: `{conds['bio_contains']}`")
        if "status_contains" in conds:
            lines.append(f"• Status must contain: `{conds['status_contains']}`")
        summary = "\n".join(lines) or "All requirements cleared — open to everyone."
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Conditions Saved",
                description=f"Requirements for **{self.cfg_name}**:\n\n{summary}",
                color=discord.Color.green(),
            ),
            view=None,
        )


class GiveawayBioModal(discord.ui.Modal, title="📝 Bio Requirement"):
    """Modal to set a required text in the user's Discord bio."""

    bio_text = discord.ui.TextInput(
        label="Required bio text (empty = remove)",
        placeholder="e.g. .gg/pixelluck",
        required=False,
        max_length=100,
    )

    def __init__(self, parent_view: "GiveawayConditionView"):
        super().__init__()
        self.parent_view = parent_view
        current = parent_view.conditions.get("bio_contains", "")
        if current:
            self.bio_text.default = current

    async def on_submit(self, interaction: discord.Interaction):
        text = self.bio_text.value.strip()
        if text:
            self.parent_view.conditions["bio_contains"] = text
        else:
            self.parent_view.conditions.pop("bio_contains", None)
        await interaction.response.edit_message(
            embed=self.parent_view._build_embed(),
            view=self.parent_view,
        )


class GiveawayStatusModal(discord.ui.Modal, title="🟢 Custom Status Requirement"):
    """Modal to set a required text in the user's custom Discord status."""

    status_text = discord.ui.TextInput(
        label="Required status text (empty = remove)",
        placeholder="e.g. .gg/pixelluck",
        required=False,
        max_length=100,
    )

    def __init__(self, parent_view: "GiveawayConditionView"):
        super().__init__()
        self.parent_view = parent_view
        current = parent_view.conditions.get("status_contains", "")
        if current:
            self.status_text.default = current

    async def on_submit(self, interaction: discord.Interaction):
        text = self.status_text.value.strip()
        if text:
            self.parent_view.conditions["status_contains"] = text
        else:
            self.parent_view.conditions.pop("status_contains", None)
        await interaction.response.edit_message(
            embed=self.parent_view._build_embed(),
            view=self.parent_view,
        )


# ─── Cog ─────────────────────────────────────────────────────────────────────

class GiveawayCog(commands.Cog):
    """Auto Giveaway System"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._loop_running = False
        self.giveaway_loop.start()
        self.bio_check_loop.start()

    def cog_unload(self):
        self.giveaway_loop.cancel()
        self.bio_check_loop.cancel()

    # ── Main loop ─────────────────────────────────────────────────────────────

    @tasks.loop(seconds=15)
    async def giveaway_loop(self):
        """Check for ended giveaways and start new ones as needed."""
        await self._process_giveaways()

    @giveaway_loop.before_loop
    async def before_giveaway_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)  # Give other cogs time to load

    # ── Bio verification loop ──────────────────────────────────────────────────

    @tasks.loop(seconds=BIO_CHECK_INTERVAL)
    async def bio_check_loop(self):
        """Periodically verify bio requirements and remove non-compliant participants."""
        configs = _load_configs()
        active  = _load_active()
        changed = False

        for config_id, adict in list(active.items()):
            cfg = configs.get(config_id, {})
            conditions = cfg.get("conditions", {})
            bio_required    = conditions.get("bio_contains")
            status_required = conditions.get("status_contains")
            if not bio_required and not status_required:
                continue

            channel_id = cfg.get("channel_id")
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            guild   = channel.guild if channel else None

            participants: list[int] = list(adict.get("participants", []))
            removed: list[int] = []
            removal_reasons: dict[int, str] = {}

            for uid in participants:
                member = guild.get_member(uid) if guild else None

                # Bio check (async HTTP)
                if bio_required:
                    has_bio = await _check_bio(self.bot, uid, bio_required)
                    if not has_bio:
                        removed.append(uid)
                        removal_reasons[uid] = f"bio no longer contains **`{bio_required}`**"
                        continue

                # Status check (sync cache)
                if status_required:
                    saved_status = adict.get("participants_status", {}).get(str(uid), "")
                    if status_required.lower() not in saved_status.lower():
                        removed.append(uid)
                        removal_reasons[uid] = f"custom status no longer contains **`{status_required}`**"

            if removed:
                new_participants = [u for u in participants if u not in removed]
                adict["participants"] = new_participants
                active[config_id] = adict
                changed = True

                # Update giveaway embed
                active_obj = ActiveGiveaway.from_dict(config_id, cfg, adict)
                if channel:
                    try:
                        msg = await channel.fetch_message(adict["message_id"])
                        await msg.edit(embed=_giveaway_embed(cfg, active_obj))
                    except Exception:
                        pass

                # DM removed participants
                for uid in removed:
                    member = guild.get_member(uid) if guild else None
                    reason = removal_reasons.get(uid, "a requirement is no longer met")
                    try:
                        target = member or await self.bot.fetch_user(uid)
                        await target.send(
                            embed=discord.Embed(
                                title="⚠️ Giveaway Removal Notice",
                                description=(
                                    f"You have been **removed** from the **{cfg.get('name', 'giveaway')}** giveaway "
                                    f"because your {reason}.\n\n"
                                    "Fix your profile and re-enter the giveaway."
                                ),
                                color=discord.Color.orange(),
                            )
                        )
                    except Exception:
                        pass

        if changed:
            _save_active(active)

    @bio_check_loop.before_loop
    async def before_bio_check_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(15)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Update saved status when a participant's custom status changes."""
        active_data = _load_active()
        configs     = _load_configs()
        uid = after.id
        changed = False

        new_status = _get_member_status_text(after)

        for config_id, adict in list(active_data.items()):
            if uid not in adict.get("participants", []):
                continue

            cfg = configs.get(config_id, {})
            status_required = cfg.get("conditions", {}).get("status_contains")
            if not status_required:
                continue

            # Always update the saved status to reflect latest
            adict.setdefault("participants_status", {})[str(uid)] = new_status
            active_data[config_id] = adict
            changed = True

            # If new status no longer meets requirement — remove from giveaway
            if status_required.lower() not in new_status.lower():
                adict["participants"] = [p for p in adict["participants"] if p != uid]
                adict["participants_status"].pop(str(uid), None)
                active_data[config_id] = adict
                _save_active(active_data)
                changed = False  # already saved

                # Update giveaway embed
                channel_id = cfg.get("channel_id")
                channel = self.bot.get_channel(int(channel_id)) if channel_id else None
                if channel:
                    try:
                        active_obj = ActiveGiveaway.from_dict(config_id, cfg, adict)
                        msg = await channel.fetch_message(adict["message_id"])
                        await msg.edit(embed=_giveaway_embed(cfg, active_obj))
                    except Exception:
                        pass

                # DM the user
                try:
                    await after.send(
                        embed=discord.Embed(
                            title="⚠️ Giveaway Removal Notice",
                            description=(
                                f"You have been **removed** from the **{cfg.get('name', 'giveaway')}** giveaway "
                                f"because your custom status no longer contains **`{status_required}`**.\n\n"
                                "Update your status and re-enter the giveaway."
                            ),
                            color=discord.Color.orange(),
                        )
                    )
                except Exception:
                    pass

        if changed:
            _save_active(active_data)

    async def _process_giveaways(self):
        configs = _load_configs()
        active  = _load_active()
        now     = int(time.time())

        for config_id, cfg in configs.items():
            if not cfg.get("enabled"):
                continue

            channel_id = cfg.get("channel_id")
            if not channel_id:
                continue

            channel = self.bot.get_channel(int(channel_id))
            if not channel or not isinstance(channel, discord.TextChannel):
                continue

            # Determine which guild this channel belongs to
            guild = channel.guild

            adict = active.get(config_id)

            if adict:
                # Check if ended
                if now >= adict["ends_at"]:
                    await self._finish_giveaway(config_id, cfg, adict, channel, guild)
                    active.pop(config_id, None)
                    _save_active(active)
                    # Start new round immediately
                    await asyncio.sleep(5)
                    new_active = _load_active()
                    new_adict = await self._start_giveaway(config_id, cfg, channel)
                    if new_adict:
                        new_active[config_id] = new_adict.to_dict()
                        _save_active(new_active)
            else:
                # No active giveaway — start one
                new_adict = await self._start_giveaway(config_id, cfg, channel)
                if new_adict:
                    active[config_id] = new_adict.to_dict()
                    _save_active(active)

    async def _start_giveaway(
        self, config_id: str, cfg: dict, channel: discord.TextChannel
    ) -> Optional[ActiveGiveaway]:
        try:
            view = JoinGiveawayView(config_id)
            # Dummy active to compute ends_at for embed
            dummy = ActiveGiveaway(config_id, cfg, 0)
            embed = _giveaway_embed(cfg, dummy)
            msg = await channel.send(embed=embed, view=view)
            active = ActiveGiveaway(config_id, cfg, msg.id)
            # Update embed with real message_id (ends_at already correct)
            return active
        except Exception as e:
            print(f"[Giveaway] Failed to start giveaway {config_id}: {e}")
            return None

    async def _finish_giveaway(
        self,
        config_id: str,
        cfg: dict,
        adict: dict,
        channel: discord.TextChannel,
        guild: discord.Guild,
    ):
        participants = adict.get("participants", [])
        winner_count = int(cfg.get("winner_count", 1))
        prize        = int(cfg.get("prize", 0))

        # Filter still-valid members (must exist in guild)
        eligible = [
            uid for uid in participants
            if guild.get_member(uid) is not None
        ]

        # Filter by saved status condition (prevents offline bypass)
        status_required = cfg.get("conditions", {}).get("status_contains")
        if status_required:
            participants_status = adict.get("participants_status", {})
            eligible = [
                uid for uid in eligible
                if status_required.lower() in participants_status.get(str(uid), "").lower()
            ]

        winners: list[int] = []
        if eligible:
            count   = min(winner_count, len(eligible))
            winners = random.sample(eligible, count)

        # Award prize
        for uid in winners:
            try:
                player = Player(uid)
                player.add_balance("real", prize)
                # Try DM
                member = guild.get_member(uid)
                if member:
                    try:
                        dm_embed = discord.Embed(
                            title="🎉 You Won a Giveaway!",
                            description=(
                                f"You won the **{cfg.get('name', 'Auto Giveaway')}** giveaway!\n"
                                f"💰 **{format_balance(prize, 'real')}** has been added to your balance."
                            ),
                            color=discord.Color.gold(),
                        )
                        await member.send(embed=dm_embed)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[Giveaway] Prize award error for {uid}: {e}")

        # Edit or send ended embed
        try:
            msg = await channel.fetch_message(adict["message_id"])
            ended_embed = _ended_embed(cfg, winners)
            await msg.edit(embed=ended_embed, view=None)
        except Exception:
            try:
                ended_embed = _ended_embed(cfg, winners)
                await channel.send(embed=ended_embed)
            except Exception as e:
                print(f"[Giveaway] Failed to send ended embed: {e}")

        # Winner announcement
        if winners:
            try:
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                await channel.send(
                    f"🎊 Congratulations {mentions}! You won **{format_balance(prize, 'real')}**! 🎉"
                )
            except Exception:
                pass

    # ── Slash command ─────────────────────────────────────────────────────────

    @app_commands.command(name="giveaway", description="Manage the auto giveaway system (Admin only)")
    async def giveaway_panel(self, interaction: discord.Interaction):
        if check_permission(interaction.user.id, "admin"):
            return await interaction.response.send_message(
                "❌ You need admin permission to use this command.", ephemeral=True
            )
        embed = _giveaway_list_embed()
        view  = GiveawayListView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
