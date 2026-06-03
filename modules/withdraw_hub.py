"""Unified withdraw method picker — crypto, in-game Luci, panel methods."""

from __future__ import annotations

import discord

import modules.crypto_deposit as crypto_engine
from modules.database import get_data
from modules.deposit_hub import CRYPTO_METHOD_KEY
from modules.ingame_deposit import (
    ensure_ingame_payment_method,
    get_ingame_config,
    is_ingame_configured,
    is_ingame_method,
)
from modules.player import Player
from modules.translator import t
from modules.utils import format_balance, get_user_lang

INGAME_WITHDRAW_KEY = "ingame"


def collect_withdraw_methods() -> dict[str, dict]:
    methods: dict[str, dict] = {}

    settings = crypto_engine.get_settings()
    if settings.get("enabled") and crypto_engine.TREASURY_MNEMONIC:
        chains = []
        if settings.get("sol_enabled", True):
            chains.append("SOL")
        if settings.get("ltc_enabled", True):
            chains.append("LTC")
        if settings.get("eth_enabled", True):
            chains.append("ETH")
        chain_txt = " · ".join(chains) if chains else "SOL · LTC · ETH"
        methods[CRYPTO_METHOD_KEY] = {
            "name": "Crypto Withdraw",
            "emoji": "🔐",
            "description": f"{chain_txt} — on-chain payout",
            "type": "crypto",
            "enabled": True,
        }

    ensure_ingame_payment_method()
    ingame_cfg = get_ingame_config()
    if ingame_cfg.get("enabled") and is_ingame_configured(ingame_cfg):
        from modules.ingame_deposit import format_dl_coin_rate

        rate = format_dl_coin_rate(float(ingame_cfg.get("dl_to_coin_rate", 0) or 0))
        methods[INGAME_WITHDRAW_KEY] = {
            "name": "In-Game (Growtopia)",
            "emoji": "🎮",
            "description": f"Auto delivery — {rate} coins = 1 DL",
            "type": "ingame_luci",
            "enabled": True,
        }

    panel_methods = get_data("server/payment_methods") or {}
    if isinstance(panel_methods, dict):
        for key, info in panel_methods.items():
            if not isinstance(info, dict):
                continue
            if not info.get("enabled", True):
                continue
            if key == INGAME_WITHDRAW_KEY or is_ingame_method(key, info):
                continue
            methods[key] = info

    return methods


def _withdraw_options(methods: dict[str, dict], lang: str) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    order: list[str] = []
    if CRYPTO_METHOD_KEY in methods:
        order.append(CRYPTO_METHOD_KEY)
    if INGAME_WITHDRAW_KEY in methods:
        order.append(INGAME_WITHDRAW_KEY)
    for key in methods:
        if key not in order:
            order.append(key)

    for key in order:
        info = methods[key]
        emoji = info.get("emoji", "💳")
        options.append(
            discord.SelectOption(
                label=str(info.get("name", key))[:100],
                description=(info.get("description") or "")[:100] or None,
                emoji=emoji if not str(emoji).startswith("<") else None,
                value=key,
            )
        )
    return options[:25]


async def route_withdraw_method(
    interaction: discord.Interaction,
    user_id: int,
    method_key: str,
    methods: dict[str, dict],
    lang: str,
) -> None:
    if method_key == CRYPTO_METHOD_KEY:
        cog = interaction.client.get_cog("CryptoWithdraw")
        if cog is None:
            return await interaction.response.send_message(
                "Crypto withdraw is not available.", ephemeral=True
            )
        await cog.start_withdrawal(interaction, edit=True)
        return

    if method_key == INGAME_WITHDRAW_KEY:
        from cogs.ingame_luci import IngameWithdrawStartView
        from modules.database import get_user_data
        from modules.ingame_withdraw import get_dl_to_coin_rate
        from modules.ingame_deposit import format_dl_coin_rate

        growid_data = get_user_data(user_id, "growid") or {}
        growid = (growid_data.get("growid") or "").strip() if isinstance(growid_data, dict) else ""
        if not growid:
            return await interaction.response.send_message(
                "❌ Set your GrowID first (use **In-Game Deposit** in `.deposit`).",
                ephemeral=True,
            )
        deposit_settings = get_data("server/deposit_settings") or {}
        min_w = int(deposit_settings.get("min_withdrawal", 100) or 100)
        rate = format_dl_coin_rate(get_dl_to_coin_rate())
        view = IngameWithdrawStartView(user_id, growid, min_w, rate)
        embed = discord.Embed(
            title="🎮 In-Game Withdrawal",
            description=(
                f"GrowID: `{growid}`\n"
                f"Rate: **{rate}** coins = **1 DL**\n\n"
                "Press **Start Withdrawal** — world must have a **Display Box (1422)**."
            ),
            color=0x5865F2,
        )
        await interaction.response.edit_message(embed=embed, view=view)
        return

    from cogs.private_rooms import WithdrawAmountModal, WithdrawGoldAmountModal

    method_info = methods.get(method_key, {})
    deposit_settings = get_data("server/deposit_settings") or {}
    min_w = int(deposit_settings.get("min_withdrawal", 100) or 100)
    use_ticket = method_key == "gold" or method_info.get("ticket", False)
    if use_ticket:
        modal = WithdrawGoldAmountModal(str(user_id), method_info, min_w)
    else:
        modal = WithdrawAmountModal(str(user_id), method_key, method_info, min_w)
    await interaction.response.send_modal(modal)


class WithdrawMethodSelect(discord.ui.Select):
    def __init__(self, user_id: int, methods: dict[str, dict], lang: str):
        self._user_id = user_id
        self._methods = methods
        self._lang = lang
        super().__init__(
            placeholder="Select withdrawal method…",
            options=_withdraw_options(methods, lang),
            min_values=1,
            max_values=1,
            custom_id="prefix_withdraw:method",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            return await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self._lang),
                ephemeral=True,
            )
        await route_withdraw_method(
            interaction,
            self._user_id,
            self.values[0],
            self._methods,
            self._lang,
        )


class PrefixWithdrawView(discord.ui.View):
    def __init__(self, user_id: int, methods: dict[str, dict], lang: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.methods = methods
        self.lang = lang
        self.add_item(WithdrawMethodSelect(user_id, methods, lang))


def build_withdraw_hub_embed(user_id: int, lang: str) -> discord.Embed:
    player = Player(user_id)
    balance = player.get_balance("real")
    deposit_settings = get_data("server/deposit_settings") or {}
    min_w = int(deposit_settings.get("min_withdrawal", 100) or 100)
    return discord.Embed(
        title=f"💸 {t('private_rooms.withdraw_option', lang=lang)}",
        description=(
            f"**Balance:** {format_balance(balance, 'real')}\n"
            f"**Minimum:** {format_balance(min_w, 'real')}\n\n"
            "Choose a withdrawal method below. All steps continue in this DM."
        ),
        color=0xF59E0B,
    )
