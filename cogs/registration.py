import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from modules.database import (
    check_permission,
    get_data,
    get_server_data,
    get_user_data,
    set_data,
    set_user_data,
)
from modules.player import Player
from modules.translator import t


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _is_registered(user_id: int) -> bool:
    """Return True if the user already has a completed registration."""
    account = get_user_data(user_id, "account")
    return bool(account and account.get("name"))


def _error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.red())


def _success_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=discord.Color.green())


# ──────────────────────────────────────────────────────────────────────────────
# Registration Modal
# ──────────────────────────────────────────────────────────────────────────────

class RegistrationModal(discord.ui.Modal):
    """The actual registration form, opened after language selection."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        uid_str = str(user_id)

        super().__init__(
            title=t("registration.modal_title", user_id=uid_str),
            timeout=300,
        )

        self.name_input = discord.ui.TextInput(
            label=t("registration.name_label", user_id=uid_str),
            placeholder=t("registration.name_placeholder", user_id=uid_str),
            required=True,
            max_length=50,
            style=discord.TextStyle.short,
        )
        self.add_item(self.name_input)

        self.age_input = discord.ui.TextInput(
            label=t("registration.age_label", user_id=uid_str),
            placeholder=t("registration.age_placeholder", user_id=uid_str),
            required=True,
            max_length=2,
            style=discord.TextStyle.short,
        )
        self.add_item(self.age_input)

        self.email_input = discord.ui.TextInput(
            label=t("registration.email_label", user_id=uid_str),
            placeholder=t("registration.email_placeholder", user_id=uid_str),
            required=True,
            max_length=100,
            style=discord.TextStyle.short,
        )
        self.add_item(self.email_input)

        self.source_input = discord.ui.TextInput(
            label=t("registration.source_label", user_id=uid_str),
            placeholder=t("registration.source_placeholder", user_id=uid_str),
            required=True,
            max_length=100,
            style=discord.TextStyle.short,
        )
        self.add_item(self.source_input)

        self.referral_input = discord.ui.TextInput(
            label=t("registration.referral_label", user_id=uid_str),
            placeholder=t("registration.referral_placeholder", user_id=uid_str),
            required=False,
            max_length=20,
            style=discord.TextStyle.short,
        )
        self.add_item(self.referral_input)

    # ── Submission ─────────────────────────────────────────────────────────────

    async def on_submit(self, interaction: discord.Interaction) -> None:
        uid = interaction.user.id
        uid_str = str(uid)

        # Double-check: still not registered (edge case: two panels open at once)
        if _is_registered(uid):
            return await interaction.response.send_message(
                embed=_error_embed(
                    "Already Registered",
                    t("registration.error_already_registered", user_id=uid_str),
                ),
                ephemeral=True,
            )

        # ── Age validation ────────────────────────────────────────────────────
        try:
            age = int(self.age_input.value.strip())
            if not (18 <= age <= 99):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                embed=_error_embed(
                    t("registration.error_age_title", user_id=uid_str),
                    t("registration.error_age_description", user_id=uid_str),
                ),
                ephemeral=True,
            )

        # ── Referral code handling ────────────────────────────────────────────
        referral_code = self.referral_input.value.strip().upper() if self.referral_input.value else ""
        referral_owner_id: Optional[str] = None

        # Welcome bonus applies to ALL new registrations
        settings = get_data("server/referral_settings") or {}
        welcome_bonus = settings.get("welcome_bonus", 0)

        if referral_code:
            referrals_data = get_data("server/referrals") or {}
            for owner_id, owner_data in referrals_data.items():
                if owner_data.get("code", "").upper() != referral_code:
                    continue

                if owner_id == uid_str:
                    return await interaction.response.send_message(
                        embed=_error_embed(
                            "Invalid Referral Code",
                            t("registration.error_own_referral", user_id=uid_str),
                        ),
                        ephemeral=True,
                    )

                referral_owner_id = owner_id
                referred = owner_data.setdefault("referred_users", [])
                if uid_str not in referred:
                    referred.append(uid_str)
                owner_data.setdefault("referral_earnings", {})[uid_str] = {
                    "total_earned": 0,
                    "joined_at": int(time.time()),
                }
                referrals_data[owner_id] = owner_data
                set_data("server/referrals", referrals_data)
                break

        # ── Save account ──────────────────────────────────────────────────────
        none_label = t("common.none", user_id=uid_str)
        welcome_cap_coins = 0
        if welcome_bonus > 0:
            try:
                from modules.balance_cap import get_welcome_balance_cap
                welcome_cap_coins = get_welcome_balance_cap()
            except Exception:
                welcome_cap_coins = 500

        account_data = {
            "name": self.name_input.value.strip(),
            "age": str(age),
            "email": self.email_input.value.strip(),
            "source": self.source_input.value.strip(),
            "referral_code": referral_code or none_label,
            "referred_by": referral_owner_id,
            "welcome_bonus_capped": welcome_bonus > 0,
            "welcome_balance_cap": welcome_cap_coins if welcome_bonus > 0 else 0,
        }
        set_user_data(uid, "account", account_data)
        if welcome_bonus > 0:
            set_user_data(uid, "welcome_cap", {
                "capped": True,
                "ceiling": welcome_cap_coins,
            })

        # ── Starting balances ─────────────────────────────────────────────────
        player = Player(uid)
        player.set_balance("real", player.balance + welcome_bonus)
        player.set_balance("demo", 10000)

        # ── Assign member role / remove unregistered role ─────────────────────
        role_text = ""
        try:
            if interaction.guild:
                server_data = get_server_data(str(interaction.guild.id))
                member_role_id = server_data.get("member_role")
                if member_role_id:
                    role = interaction.guild.get_role(int(member_role_id))
                    if role:
                        await interaction.user.add_roles(role)
                        role_text = t("registration.role_assigned",
                                      user_id=uid_str, role=role.mention)
                unregistered_role_id = server_data.get("unregistered_role")
                if unregistered_role_id:
                    unrole = interaction.guild.get_role(int(unregistered_role_id))
                    if unrole and unrole in interaction.user.roles:
                        await interaction.user.remove_roles(unrole)
        except Exception as exc:
            print(f"[Registration] Role assignment error for {uid}: {exc}")

        # ── Build success embed ───────────────────────────────────────────────
        embed = _success_embed(
            t("registration.success_title", user_id=uid_str),
            t(
                "registration.success_description",
                user_id=uid_str,
                name=account_data["name"],
                age=age,
                email=account_data["email"],
                source=account_data["source"],
                referral=referral_code or none_label,
            ),
        )

        if welcome_bonus > 0:
            from modules.utils import format_balance
            embed.add_field(
                name=t("referral.bonus_field_title", user_id=uid_str),
                value=t("referral.welcome_bonus_received", user_id=uid_str,
                        amount=format_balance(welcome_bonus, "real")),
                inline=False,
            )

        if role_text:
            embed.add_field(name="\u200b", value=role_text, inline=False)

        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        uid_str = str(interaction.user.id)
        embed = _error_embed(
            "Error",
            t("errors.unknown_error", user_id=uid_str, error=str(error)),
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


# ──────────────────────────────────────────────────────────────────────────────
# Language Selection  (shown before opening the modal)
# ──────────────────────────────────────────────────────────────────────────────

class RegistrationLanguageSelect(discord.ui.Select):
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        super().__init__(
            placeholder="Select language / Dil secin / Pilih bahasa...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="English", emoji="\U0001f1ec\U0001f1e7", value="en"),
                discord.SelectOption(label="Turkce", emoji="\U0001f1f9\U0001f1f7", value="tr"),
                discord.SelectOption(label="Bahasa Indonesia", emoji="\U0001f1ee\U0001f1e9", value="id"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "This is not your registration panel!", ephemeral=True
            )
        set_user_data(self.user_id, "lang", {"language": self.values[0]})
        await interaction.response.send_modal(RegistrationModal(self.user_id))


class RegistrationLanguageView(discord.ui.View):
    def __init__(self, user_id: int) -> None:
        super().__init__(timeout=180)
        self.add_item(RegistrationLanguageSelect(user_id))


# ──────────────────────────────────────────────────────────────────────────────
# Persistent Register Button
# ──────────────────────────────────────────────────────────────────────────────

class RegistrationView(discord.ui.View):
    """Persistent view — survives bot restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register / Kayit Ol / Daftar",
        style=discord.ButtonStyle.primary,
        emoji="\U0001f4dd",
        custom_id="registration:register_button",
    )
    async def register_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        uid = interaction.user.id

        if _is_registered(uid):
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title="Already Registered",
                    description=t("registration.error_already_registered", user_id=str(uid)),
                    color=discord.Color.orange(),
                ),
                ephemeral=True,
            )

        from cogs.registration_v2 import build_registration_language_layout
        from modules.ui_v2 import send_ephemeral

        await send_ephemeral(interaction, build_registration_language_layout(uid))


# ──────────────────────────────────────────────────────────────────────────────
# Cog
# ──────────────────────────────────────────────────────────────────────────────

class Registration(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="send_registration",
        description="Send the registration menu to a channel (Admin only)",
    )
    @app_commands.describe(channel="Target channel (defaults to current channel)")
    @app_commands.guild_only()
    async def send_registration(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if check_permission(str(interaction.user.id), "admin"):
            return await interaction.response.send_message(
                embed=_error_embed(
                    "No Permission",
                    t("errors.no_permission", user_id=str(interaction.user.id)),
                ),
                ephemeral=True,
            )

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await interaction.response.send_message(
                embed=_error_embed(
                    "Invalid Channel",
                    t("errors.invalid_channel", user_id=str(interaction.user.id)),
                ),
                ephemeral=True,
            )

        from cogs.registration_v2 import RegistrationChannelLayout
        from modules.ui_v2 import send_channel_v2

        await send_channel_v2(target, RegistrationChannelLayout())
        await interaction.response.send_message(
            embed=_success_embed(
                "Sent",
                t("registration.admin_sent",
                  user_id=str(interaction.user.id),
                  channel=target.mention),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Registration(bot))
    bot.add_view(RegistrationView())