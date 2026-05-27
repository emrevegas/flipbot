"""Components V2 layouts for private-room panels (subset used by FlipBot)."""

from __future__ import annotations

import discord
from discord import ui

from modules.database import get_data
from modules.translator import t
from modules.ui_v2 import (
    ACCENT_BRAND,
    ACCENT_SUCCESS,
    ACCENT_WARNING,
    add_section,
    build_detail_panel,
    build_layout,
    new_container,
    panel_markdown,
    send_ephemeral,
)
from modules.utils import format_balance


class RakebackWithdrawButton(ui.Button):
    def __init__(self, user_id: int, can_withdraw: bool, min_withdrawal: int, lang: str):
        label = (
            t("rakeback.withdraw_button", lang=lang)
            if can_withdraw
            else t("rakeback.withdraw_button_disabled", lang=lang, min=format_balance(min_withdrawal, "real"))
        )
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success if can_withdraw else discord.ButtonStyle.secondary,
            disabled=not can_withdraw,
        )
        self.user_id = user_id
        self.lang = lang

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)

        from database import db as flip_db
        from modules.rakeback_engine import get_min_withdrawal

        user = await flip_db.get_user(self.user_id)
        accumulated = int(float((user or {}).get("rakeback_accumulated", 0)))
        min_w = get_min_withdrawal()

        if accumulated < min_w:
            return await send_ephemeral(
                interaction,
                build_detail_panel(
                    title=t("rakeback.title", lang=self.lang),
                    body=t(
                        "rakeback.withdraw_error_insufficient",
                        lang=self.lang,
                        min=format_balance(min_w, "real"),
                        current=format_balance(accumulated, "real"),
                    ),
                    accent=ACCENT_WARNING,
                    emoji="⚠️",
                ),
            )

        claimed = await flip_db.claim_rakeback(self.user_id)
        await send_ephemeral(
            interaction,
            build_detail_panel(
                title=t("rakeback.withdraw_success_title", lang=self.lang),
                body=t(
                    "rakeback.withdraw_success_description",
                    lang=self.lang,
                    amount=format_balance(int(claimed), "real"),
                ),
                accent=ACCENT_SUCCESS,
                emoji="✅",
            ),
        )


def build_rakeback_layout(
    user_id: int,
    *,
    best_tier,
    accumulated: int,
    total_earned: int,
    min_withdrawal: int,
    total_wagered: int,
    can_withdraw: bool,
    lang: str,
) -> ui.LayoutView:
    from modules.rakeback_engine import format_rakeback_pct

    if best_tier and best_tier.get("role_id"):
        pct = format_rakeback_pct(best_tier.get("percentage", 0))
        tier_txt = f"<@&{best_tier['role_id']}> — **{pct}** per bet"
    elif best_tier and float(best_tier.get("percentage", 0)) > 0:
        pct = format_rakeback_pct(best_tier.get("percentage", 0))
        tier_txt = f"**{best_tier.get('role_name', 'Tier')}** — **{pct}** per bet"
    else:
        tier_txt = t("rakeback.no_tier", lang=lang)

    fields = {
        t("rakeback.tier_field", lang=lang): tier_txt,
        t("rakeback.accumulated_field", lang=lang): format_balance(accumulated, "real"),
        t("rakeback.total_earned_field", lang=lang): format_balance(total_earned, "real"),
        t("rakeback.min_withdrawal_field", lang=lang): format_balance(min_withdrawal, "real"),
        t("rakeback.total_wagered_field", lang=lang): format_balance(total_wagered, "real"),
    }
    c = new_container(accent=ACCENT_BRAND)
    c.add_item(
        ui.TextDisplay(
            panel_markdown(
                title=t("rakeback.title", lang=lang),
                body=t("rakeback.description", lang=lang),
                footer=t("rakeback.footer", lang=lang),
                emoji="💸",
            )
        )
    )
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    lines = "\n".join(f"**{k}**\n{v}" for k, v in fields.items())
    c.add_item(ui.TextDisplay(lines[:4000]))
    add_section(
        c,
        t("private_rooms.section_actions", lang=lang),
        RakebackWithdrawButton(user_id, can_withdraw, min_withdrawal, lang),
    )
    return build_layout(c, timeout=120)
