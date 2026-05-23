"""Shared deposit bonus selection UI (Components V2)."""

from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

import modules.bonus as bonus_engine
from modules.translator import t
from modules.ui_v2 import ACCENT_BRAND, brand_footer, panel_with_controls, send_ephemeral

OnBonusChosen = Callable[[discord.Interaction, str | None], Awaitable[None]]


def build_bonus_select_options(bonuses: dict, lang: str) -> list[discord.SelectOption]:
    options = [
        discord.SelectOption(
            label=t("bonus.option_no_bonus", lang=lang),
            description=t("bonus.option_no_bonus_desc", lang=lang)[:100],
            emoji="➖",
            value="__none__",
        )
    ]
    for bid, info in bonuses.items():
        btype = info.get("type", "fixed")
        if btype == "fixed":
            wt = info.get("wager_target_multiplier", 2)
            mw = info.get("max_withdrawal_multiplier", 4)
            desc = t("bonus.option_fixed_desc", lang=lang, wt=wt, mw=mw)
        else:
            pct = info.get("percentage", 0)
            wm = info.get("wager_multiplier", 1)
            desc = t("bonus.option_pct_desc", lang=lang, pct=pct, wm=wm)
        if info.get("description"):
            desc = info["description"]
        options.append(
            discord.SelectOption(
                label=info.get("name", bid)[:100],
                description=desc[:100],
                emoji="🎁",
                value=bid,
            )
        )
    return options


class DepositBonusSelect(discord.ui.Select):
    def __init__(self, bonuses: dict, lang: str, *, custom_id: str = "deposit:bonus_flow_select"):
        self._lang = lang
        super().__init__(
            placeholder=t("bonus.select_placeholder", lang=lang),
            options=build_bonus_select_options(bonuses, lang),
            min_values=1,
            max_values=1,
            custom_id=custom_id,
        )

    async def callback(self, interaction: discord.Interaction):
        view: DepositBonusPickerLayout = self.view  # type: ignore[assignment]
        await view._finish(interaction, self.values[0])


class DepositBonusPickerLayout(ui.LayoutView):
    def __init__(
        self,
        user_id: int,
        lang: str,
        on_done: OnBonusChosen,
        *,
        title_key: str = "bonus.picker_title",
        description_key: str = "bonus.picker_description",
    ):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.lang = lang
        self.on_done = on_done

        bonuses = bonus_engine.get_enabled_bonus_templates()
        controls: list[ui.Item] = []
        if bonuses:
            controls.append(DepositBonusSelect(bonuses, lang))

        inner = panel_with_controls(
            title=t(title_key, lang=lang),
            body=t(description_key, lang=lang),
            footer=brand_footer(),
            emoji="🎁",
            accent=ACCENT_BRAND,
            controls=controls,
            section_label=t("bonus.select_section", lang=lang) if bonuses else None,
        )
        for item in inner.children:
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                t("deposit.not_your_panel", lang=self.lang),
                ephemeral=True,
            )
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, bonus_id: str | None):
        bid = None if bonus_id == "__none__" else bonus_id
        bonus_engine.set_pending_deposit_bonus(self.user_id, bid)
        for child in self.walk_children():
            if hasattr(child, "disabled"):
                child.disabled = True
        await send_ephemeral(interaction, self, edit=True)
        await self.on_done(interaction, bid)

    async def on_timeout(self):
        for child in self.walk_children():
            if hasattr(child, "disabled"):
                child.disabled = True


DepositBonusPickerView = DepositBonusPickerLayout


async def show_bonus_picker_or_skip(
    interaction: discord.Interaction,
    user_id: int,
    lang: str,
    on_done: OnBonusChosen,
    *,
    title_key: str = "bonus.picker_title",
    description_key: str = "bonus.picker_description",
) -> None:
    bonuses = bonus_engine.get_enabled_bonus_templates()
    if not bonuses:
        bonus_engine.set_pending_deposit_bonus(user_id, None)
        await on_done(interaction, None)
        return

    view = DepositBonusPickerLayout(
        user_id, lang, on_done, title_key=title_key, description_key=description_key
    )
    await send_ephemeral(interaction, view)
