"""Unified deposit method picker — crypto, in-game (Growtopia), panel payment methods."""

from __future__ import annotations

import discord

import modules.bonus as bonus_engine
import modules.crypto_deposit as crypto_engine
from modules.database import get_data
from modules.ingame_deposit import ensure_ingame_payment_method, get_ingame_config, is_ingame_method
from modules.translator import t
from modules.utils import get_user_lang


CRYPTO_METHOD_KEY = "__crypto__"


def collect_deposit_methods() -> dict[str, dict]:
    """Active deposit routes: crypto (if enabled) + panel payment methods."""
    methods: dict[str, dict] = {}

    settings = crypto_engine.get_settings()
    if settings.get("enabled") and crypto_engine.MNEMONIC:
        chains = []
        if settings.get("sol_enabled", True):
            chains.append("SOL")
        if settings.get("eth_enabled", True):
            chains.append("ETH")
        if settings.get("ltc_enabled", True):
            chains.append("LTC")
        chain_txt = " · ".join(chains) if chains else "SOL · ETH · LTC"
        methods[CRYPTO_METHOD_KEY] = {
            "name": "Crypto Deposit",
            "emoji": "🔐",
            "description": f"{chain_txt} — auto credit",
            "type": "crypto",
            "enabled": True,
        }

    ensure_ingame_payment_method()
    panel_methods = get_data("server/payment_methods") or {}
    if not isinstance(panel_methods, dict):
        panel_methods = {}

    for key, info in panel_methods.items():
        if not isinstance(info, dict):
            continue
        if not info.get("enabled", False):
            continue
        methods[key] = info

    return methods


def _method_select_options(methods: dict[str, dict], lang: str) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    ordered_keys: list[str] = []
    if CRYPTO_METHOD_KEY in methods:
        ordered_keys.append(CRYPTO_METHOD_KEY)
    if "ingame" in methods:
        ordered_keys.append("ingame")
    for key in methods:
        if key not in ordered_keys:
            ordered_keys.append(key)

    for key in ordered_keys:
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


async def route_deposit_after_bonus(
    interaction: discord.Interaction,
    user_id: int,
    method_key: str,
    methods: dict[str, dict],
    lang: str,
) -> None:
    """Continue deposit flow after optional bonus selection."""
    from cogs.crypto_deposit import CryptoDepositView, _build_deposit_embed
    from cogs.private_rooms import DepositAmountModal, GrowIDDepositModal, build_ingame_instructions_view
    from modules.ingame_deposit import is_ingame_configured

    method_info = methods.get(method_key, {})

    if method_key == CRYPTO_METHOD_KEY:
        embed, ok, sol, ltc, eth = _build_deposit_embed(user_id)
        if ok:
            view = CryptoDepositView(user_id, sol, ltc, eth)
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=None)
        return

    if is_ingame_method(method_key, method_info):
        cfg = get_ingame_config()
        if not is_ingame_configured(cfg):
            return await interaction.response.edit_message(
                embed=discord.Embed(
                    title=t("deposit.ingame_not_configured_title", lang=lang),
                    description=t("deposit.ingame_not_configured_description", lang=lang),
                    color=0xE74C3C,
                ),
                view=None,
            )
        await interaction.response.send_modal(
            GrowIDDepositModal(str(user_id), lang, cfg, skip_bonus=True)
        )
        return

    await interaction.response.send_modal(
        DepositAmountModal(
            str(user_id),
            method_key,
            method_info,
            lang,
            skip_bonus_field=True,
        )
    )


class DepositBonusStepSelect(discord.ui.Select):
    def __init__(self, user_id: int, method_key: str, methods: dict, lang: str):
        self._user_id = user_id
        self._method_key = method_key
        self._methods = methods
        self._lang = lang
        from cogs.deposit_bonus_ui import build_bonus_select_options

        bonuses = bonus_engine.get_enabled_bonus_templates()
        super().__init__(
            placeholder=t("bonus.select_placeholder", lang=lang),
            options=build_bonus_select_options(bonuses, lang),
            min_values=1,
            max_values=1,
            custom_id="prefix_deposit:bonus",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            return await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self._lang),
                ephemeral=True,
            )
        bid = None if self.values[0] == "__none__" else self.values[0]
        bonus_engine.set_pending_deposit_bonus(self._user_id, bid)
        await route_deposit_after_bonus(
            interaction,
            self._user_id,
            self._method_key,
            self._methods,
            self._lang,
        )


class DepositBonusStepView(discord.ui.View):
    def __init__(self, user_id: int, method_key: str, methods: dict, lang: str):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.method_key = method_key
        self.methods = methods
        self.lang = lang
        self.add_item(DepositBonusStepSelect(user_id, method_key, methods, lang))


class DepositMethodSelect(discord.ui.Select):
    def __init__(self, user_id: int, methods: dict[str, dict], lang: str):
        self._user_id = user_id
        self._methods = methods
        self._lang = lang
        super().__init__(
            placeholder=t("deposit.select_payment_method_placeholder", lang=lang),
            options=_method_select_options(methods, lang),
            min_values=1,
            max_values=1,
            custom_id="prefix_deposit:method",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            return await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self._lang),
                ephemeral=True,
            )

        method_key = self.values[0]
        bonuses = bonus_engine.get_enabled_bonus_templates()
        if bonuses:
            embed = discord.Embed(
                title=f"🎁 {t('bonus.picker_title', lang=self._lang)}",
                description=t("bonus.picker_description", lang=self._lang),
                color=0x5865F2,
            )
            view = DepositBonusStepView(self._user_id, method_key, self._methods, self._lang)
            return await interaction.response.edit_message(embed=embed, view=view)

        bonus_engine.set_pending_deposit_bonus(self._user_id, None)
        await route_deposit_after_bonus(
            interaction,
            self._user_id,
            method_key,
            self._methods,
            self._lang,
        )


class PrefixDepositView(discord.ui.View):
    def __init__(self, user_id: int, methods: dict[str, dict], lang: str):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.methods = methods
        self.lang = lang
        self.add_item(DepositMethodSelect(user_id, methods, lang))


def resolve_user_lang(user_id: int) -> str:
    try:
        from modules.player import Player

        return Player(user_id).language or get_user_lang(user_id)
    except Exception:
        return get_user_lang(user_id)


def build_deposit_hub_embed(lang: str) -> discord.Embed:
    return discord.Embed(
        title=f"💳 {t('deposit.select_payment_method_label', lang=lang)}",
        description=t("deposit.select_payment_method_description", lang=lang),
        color=0x2ECC71,
    )
