"""Components V2 — progressive Hot/Cold coinflip (GIF + action row, HiLo-style)."""

from __future__ import annotations

import re
from typing import Awaitable, Callable

import discord
from discord import ui

from modules.ui_v2 import ACCENT_BRAND, ACCENT_SUCCESS, panel_with_controls
from modules.utils import format_balance

COINFLIP_GIF = "coinflip.gif"
CashoutFn = Callable[[discord.Interaction], Awaitable[None]]
FlipFn = Callable[[discord.Interaction, str], Awaitable[None]]


def parse_button_emoji(raw: str) -> str | discord.PartialEmoji | None:
    """Unicode or <:name:id> for Button.emoji."""
    s = (raw or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"<a?:(\w+):(\d+)>", s)
    if m:
        return discord.PartialEmoji(
            name=m.group(1),
            id=int(m.group(2)),
            animated=s.startswith("<a:"),
        )
    return s


class _CfProgCashOutBtn(ui.Button):
    def __init__(self, label: str):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success,
            emoji="💰",
        )

    async def callback(self, interaction: discord.Interaction):
        view: CoinflipProgressiveView = self.view  # type: ignore[assignment]
        view.mark_done()
        await view.on_cashout(interaction)


class _CfProgHotBtn(ui.Button):
    def __init__(self, emoji: str | discord.PartialEmoji | None):
        super().__init__(
            label="Hot",
            style=discord.ButtonStyle.primary,
            emoji=emoji,
        )

    async def callback(self, interaction: discord.Interaction):
        view: CoinflipProgressiveView = self.view  # type: ignore[assignment]
        view.mark_done()
        await view.on_flip(interaction, "HOT")


class _CfProgColdBtn(ui.Button):
    def __init__(self, emoji: str | discord.PartialEmoji | None):
        super().__init__(
            label="Cold",
            style=discord.ButtonStyle.primary,
            emoji=emoji,
        )

    async def callback(self, interaction: discord.Interaction):
        view: CoinflipProgressiveView = self.view  # type: ignore[assignment]
        view.mark_done()
        await view.on_flip(interaction, "COLD")


class CoinflipProgressiveView(ui.LayoutView):
    """GIF gallery + Cash Out / Hot / Cold — same pattern as HiLo V2."""

    def __init__(
        self,
        *,
        user_id: int,
        cashout_net: int,
        hot_emoji: str,
        cold_emoji: str,
        on_cashout: CashoutFn,
        on_flip: FlipFn,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.on_cashout = on_cashout
        self.on_flip = on_flip
        self._done = False
        self._message: discord.Message | None = None

        container = ui.Container(accent_color=discord.Colour(ACCENT_BRAND))
        gallery = ui.MediaGallery()
        gallery.add_item(media=f"attachment://{COINFLIP_GIF}")
        container.add_item(gallery)
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))

        row = ui.ActionRow()
        row.add_item(_CfProgCashOutBtn(f"Cash Out {format_balance(cashout_net, 'real')}"))
        row.add_item(_CfProgHotBtn(parse_button_emoji(hot_emoji)))
        row.add_item(_CfProgColdBtn(parse_button_emoji(cold_emoji)))
        container.add_item(row)
        self.add_item(container)

    def attach_message(self, message: discord.Message) -> None:
        self._message = message

    def mark_done(self) -> None:
        self._done = True
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ This is not your coin flip.", ephemeral=True,
            )
            return False
        if self._done:
            await interaction.response.send_message(
                "This round already ended.", ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self._done:
            return
        from modules.coinflip_flow import progressive_timeout_cashout

        await progressive_timeout_cashout(self.user_id, self._message)


def build_progressive_win_layout(
    *,
    user_id: int,
    cashout_net: int,
    hot_emoji: str,
    cold_emoji: str,
    on_cashout: CashoutFn,
    on_flip: FlipFn,
    timeout: float = 120,
) -> CoinflipProgressiveView:
    return CoinflipProgressiveView(
        user_id=user_id,
        cashout_net=cashout_net,
        hot_emoji=hot_emoji,
        cold_emoji=cold_emoji,
        on_cashout=on_cashout,
        on_flip=on_flip,
        timeout=timeout,
    )


def build_progressive_done_layout(
    *,
    title: str,
    body: str,
    accent: int = ACCENT_SUCCESS,
) -> ui.LayoutView:
    return panel_with_controls(
        title=title,
        body=body,
        footer="VegasBet · Progressive Coinflip",
        emoji="✅",
        accent=accent,
    )
