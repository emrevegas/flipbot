"""Admin panel navigation — hub menus and consistent back/home helpers."""

from __future__ import annotations

import discord

from modules.database import get_data, get_server_data
from modules.translator import t
from modules.utils import format_balance, get_user_lang

HUB_CHANNELS = "channels"
HUB_PAYMENTS = "payments"
HUB_GAMES = "games"
HUB_REWARDS = "rewards"
HUB_TOOLS = "tools"

_HUB_BACK_KEYS = {
    HUB_CHANNELS: "admin_panel.hubs.back_section_channels",
    HUB_PAYMENTS: "admin_panel.hubs.back_section_payments",
    HUB_GAMES: "admin_panel.hubs.back_section_games",
    HUB_REWARDS: "admin_panel.hubs.back_section_rewards",
    HUB_TOOLS: "admin_panel.hubs.back_section_tools",
}


def _uid(user_id: int | str) -> str:
    return str(user_id)


def _not_set(user_id: int | str) -> str:
    return t("admin_panel.not_set", user_id=_uid(user_id))


def build_home_embed(interaction: discord.Interaction) -> discord.Embed:
    from cogs.admin_panel import _build_admin_panel_embed

    return _build_admin_panel_embed(interaction)


def build_hub_embed(hub: str, interaction: discord.Interaction, user_id: int | str) -> discord.Embed:
    guild = interaction.guild
    guild_id = str(guild.id) if guild else ""
    server_data = get_server_data(guild_id) if guild_id else {}
    root = get_server_data() or {}
    uid = _uid(user_id)

    def ch(v):
        return f"<#{v}>" if v else "`—`"

    if hub == HUB_CHANNELS:
        return discord.Embed(
            title=t("admin_panel.hubs.channels.title", user_id=uid),
            description=t("admin_panel.hubs.channels.description", user_id=uid),
            color=0x3498DB,
        ).add_field(
            name=t("admin_panel.hubs.current", user_id=uid),
            value=(
                f"**{t('admin_panel.routes.reg_channel', user_id=uid).replace('📢 ', '')}:** "
                f"{ch(server_data.get('registration_channel'))}\n"
                f"**{t('admin_panel.routes.private_category', user_id=uid).replace('📁 ', '')}:** "
                f"{ch(server_data.get('private_category_id'))}\n"
                f"**{t('admin_panel.routes.ticket_category', user_id=uid).replace('📁 ', '')}:** "
                f"{ch((get_data('server/ticket_settings') or {}).get('category_id'))}\n"
                f"**Self roles:** "
                f"{ch((server_data.get('self_roles') or {}).get('channel_id'))}"
            ),
            inline=False,
        )

    if hub == HUB_PAYMENTS:
        dep = get_data("server/deposit_settings") or {}
        return discord.Embed(
            title=t("admin_panel.hubs.payments.title", user_id=uid),
            description=t("admin_panel.hubs.payments.description", user_id=uid),
            color=0x2ECC71,
        ).add_field(
            name=t("admin_panel.hubs.current", user_id=uid),
            value=(
                t(
                    "admin_panel.hubs.payments.line_deposit",
                    user_id=uid,
                    dep=ch(server_data.get("deposit_category")),
                )
                + "\n"
                + t(
                    "admin_panel.hubs.payments.line_withdraw",
                    user_id=uid,
                    wd=ch(server_data.get("withdraw_channel")),
                )
                + "\n"
                + t(
                    "admin_panel.hubs.payments.line_min_wd",
                    user_id=uid,
                    amount=format_balance(server_data.get("min_withdrawal", 100), "real"),
                )
            ),
            inline=False,
        )

    if hub == HUB_GAMES:
        from modules.live_blackjack_tables import get_settings as lb_settings

        games = get_data("server/games") or {}
        enabled = sum(1 for g in games.values() if isinstance(g, dict) and g.get("enabled"))
        log_ch = server_data.get("game_log_channel") or server_data.get("pf_log_channel")
        lb_set = lb_settings()
        lb_cat = lb_set.get("category_id")
        mains = lb_set.get("main_table_channels") or []
        return discord.Embed(
            title=t("admin_panel.hubs.games.title", user_id=uid),
            description=t("admin_panel.hubs.games.description", user_id=uid),
            color=0xF1C40F,
        ).add_field(
            name=t("admin_panel.hubs.current", user_id=uid),
            value=(
                t(
                    "admin_panel.hubs.games.line_active",
                    user_id=uid,
                    count=enabled,
                    min=format_balance(root.get("minBet", 20), "real"),
                    max=format_balance(root.get("maxBet", 50000), "real"),
                )
                + "\n"
                + t("admin_panel.hubs.games.line_log", user_id=uid, log=ch(log_ch))
                + "\n"
                + t(
                    "admin_panel.hubs.games.line_live_bj",
                    user_id=uid,
                    category=ch(lb_cat),
                    tables=str(len([x for x in mains if x])),
                )
            ),
            inline=False,
        )

    if hub == HUB_REWARDS:
        import modules.bonus as bonus_engine

        bonuses = bonus_engine.get_bonus_templates() or {}
        active_b = sum(1 for b in bonuses.values() if isinstance(b, dict) and b.get("enabled"))
        return discord.Embed(
            title=t("admin_panel.hubs.rewards.title", user_id=uid),
            description=t("admin_panel.hubs.rewards.description", user_id=uid),
            color=0xE91E63,
        ).add_field(
            name=t("admin_panel.hubs.current", user_id=uid),
            value=t(
                "admin_panel.hubs.rewards.line_bonus",
                user_id=uid,
                active=active_b,
                configs=len(get_data("server/giveaway_configs") or {}),
            ),
            inline=False,
        )

    if hub == HUB_TOOLS:
        from modules.balance_cap import get_global_balance_cap

        global_cap = get_global_balance_cap()
        cap_line = (
            t(
                "admin_panel.hubs.tools.line_global_cap",
                user_id=uid,
                cap=format_balance(global_cap, "real"),
            )
            if global_cap
            else t("admin_panel.hubs.tools.line_global_cap_off", user_id=uid)
        )
        return discord.Embed(
            title=t("admin_panel.hubs.tools.title", user_id=uid),
            description=t("admin_panel.hubs.tools.description", user_id=uid),
            color=0x9B59B6,
        ).add_field(
            name=t("admin_panel.hubs.current", user_id=uid),
            value=t("admin_panel.hubs.tools.content", user_id=uid) + "\n" + cap_line,
            inline=False,
        )

    return discord.Embed(title=t("admin_panel.title", user_id=uid), color=0x2B2D31)


def hub_select_options(hub: str, user_id: int | str) -> tuple[list[discord.SelectOption], str]:
    """Build localized hub dropdown options and placeholder."""
    uid = _uid(user_id)
    back = discord.SelectOption(
        label=t("admin_panel.hubs.back_home", user_id=uid),
        value="back_home",
        emoji="🏠",
    )

    if hub == HUB_CHANNELS:
        return [
            discord.SelectOption(
                label=t("admin_panel.hubs.channels.opt_registration", user_id=uid),
                value="registration",
                emoji="📝",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.channels.opt_private_rooms", user_id=uid),
                value="private_rooms",
                emoji="🏠",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.channels.opt_tickets", user_id=uid),
                value="tickets",
                emoji="🎫",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.channels.opt_maintenance", user_id=uid),
                value="maintenance",
                emoji="🔧",
            ),
            discord.SelectOption(
                label="Self Roles",
                value="self_roles",
                emoji="🎭",
            ),
            back,
        ], t("admin_panel.hubs.channels.select_placeholder", user_id=uid)

    if hub == HUB_PAYMENTS:
        return [
            discord.SelectOption(
                label=t("admin_panel.hubs.payments.opt_deposit", user_id=uid),
                value="deposit",
                emoji="💳",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.payments.opt_withdraw", user_id=uid),
                value="withdraw",
                emoji="🏦",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.payments.opt_crypto", user_id=uid),
                value="crypto_deposits",
                emoji="🪙",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.payments.opt_finance", user_id=uid),
                value="finance_stats",
                emoji="📊",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.payments.opt_exchange", user_id=uid),
                value="exchange_rates",
                emoji="💱",
            ),
            back,
        ], t("admin_panel.hubs.payments.select_placeholder", user_id=uid)

    if hub == HUB_GAMES:
        return [
            discord.SelectOption(
                label=t("admin_panel.hubs.games.opt_management", user_id=uid),
                value="game_management",
                emoji="🎲",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.games.opt_game_log", user_id=uid),
                value="game_log",
                emoji="📢",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.games.opt_live_stats", user_id=uid),
                value="live_stats",
                emoji="📡",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.games.opt_community_cases", user_id=uid),
                value="community_cases",
                emoji="🌐",
            ),
            back,
        ], t("admin_panel.hubs.games.select_placeholder", user_id=uid)

    if hub == HUB_REWARDS:
        return [
            discord.SelectOption(
                label=t("admin_panel.hubs.rewards.opt_bonus", user_id=uid),
                value="bonus_settings",
                emoji="🎁",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.rewards.opt_promo", user_id=uid),
                value="promo_codes",
                emoji="🎟️",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.rewards.opt_giveaway", user_id=uid),
                value="giveaway_settings",
                emoji="🎊",
            ),
            discord.SelectOption(
                label=t("admin_panel.hubs.rewards.opt_race", user_id=uid),
                value="race_management",
                emoji="🏁",
            ),
            back,
        ], t("admin_panel.hubs.rewards.select_placeholder", user_id=uid)

    return [
        discord.SelectOption(
            label=t("admin_panel.hubs.tools.opt_global_cap", user_id=uid),
            value="global_balance_cap",
            emoji="🎯",
        ),
        discord.SelectOption(
            label=t("admin_panel.hubs.tools.opt_bot", user_id=uid),
            value="bot_settings",
            emoji="🤖",
        ),
        discord.SelectOption(
            label=t("admin_panel.hubs.tools.opt_broadcast", user_id=uid),
            value="broadcast_dm",
            emoji="📨",
        ),
        back,
    ], t("admin_panel.hubs.tools.select_placeholder", user_id=uid)


async def go_home(interaction: discord.Interaction, *, user_id: int = 0) -> None:
    from cogs.admin_panel import AdminPanelView

    uid = user_id or interaction.user.id
    await interaction.response.edit_message(
        embed=build_home_embed(interaction),
        view=AdminPanelView(uid),
    )


async def go_hub(interaction: discord.Interaction, hub: str, *, user_id: int = 0) -> None:
    from cogs.admin_panel import (
        ChannelsHubView,
        GamesHubView,
        PaymentsHubView,
        RewardsHubView,
        ToolsHubView,
    )

    uid = user_id or interaction.user.id
    views = {
        HUB_CHANNELS: ChannelsHubView,
        HUB_PAYMENTS: PaymentsHubView,
        HUB_GAMES: GamesHubView,
        HUB_REWARDS: RewardsHubView,
        HUB_TOOLS: ToolsHubView,
    }
    view_cls = views.get(hub)
    if not view_cls:
        return await go_home(interaction, user_id=uid)
    await interaction.response.edit_message(
        embed=build_hub_embed(hub, interaction, uid),
        view=view_cls(uid),
    )


def back_hub_label(hub: str, user_id: int | str) -> str:
    key = _HUB_BACK_KEYS.get(hub, "admin_panel.hubs.back_section")
    return t(key, user_id=_uid(user_id))
