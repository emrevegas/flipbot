"""
Discord Components V2 UI kit for Vegasbot.

All interactive controls (buttons, selects) belong inside Container + ActionRow,
never as loose root-level components on LayoutView.

Requires discord.py >= 2.6.
"""

from __future__ import annotations

from typing import Optional, Sequence

import discord
from discord import ui

from modules.constants import FOOTER_TEXT

# --- Brand accents ---
ACCENT_BRAND = 0xF5A623
ACCENT_SUCCESS = 0x2ECC71
ACCENT_ERROR = 0xE74C3C
ACCENT_WARNING = 0xE67E22
ACCENT_INFO = 0x3498DB
ACCENT_CRYPTO = 0x9945FF
ACCENT_NEUTRAL = 0x5865F2

MAX_TEXT = 4000
MAX_SELECT_DESCRIPTION = 100


def clip_select_description(text: str, limit: int = MAX_SELECT_DESCRIPTION) -> str:
    """Discord SelectOption.description max length is 100."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def panel_markdown(
    *,
    title: Optional[str] = None,
    body: str = "",
    footer: Optional[str] = None,
    emoji: Optional[str] = None,
) -> str:
    parts: list[str] = []
    if title:
        prefix = f"{emoji} " if emoji else ""
        parts.append(f"## {prefix}{title}".strip())
    if body:
        parts.append(body)
    if footer:
        parts.append(f"-# {footer}")
    return _clip("\n\n".join(parts))


def brand_footer(kind: str = "Vegasbot") -> str:
    return FOOTER_TEXT.format(değişken=kind)


def new_container(*, accent: int = ACCENT_BRAND) -> ui.Container:
    return ui.Container(accent_color=discord.Colour(accent))


def add_text(container: ui.Container, text: str) -> ui.Container:
    container.add_item(ui.TextDisplay(_clip(text)))
    return container


def add_section(
    container: ui.Container,
    label: str,
    *controls: ui.Item,
    divider: bool = True,
) -> ui.Container:
    """Section label + controls in ActionRows (selects get their own row)."""
    if divider:
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    container.add_item(ui.TextDisplay(f"### {label}"))
    if controls:
        add_controls_to_container(container, controls)
    return container


def add_action_row(container: ui.Container, *controls: ui.Item) -> ui.Container:
    row = ui.ActionRow()
    for ctrl in controls:
        row.add_item(ctrl)
    container.add_item(row)
    return container


def add_controls_to_container(
    container: ui.Container,
    controls: Sequence[ui.Item],
) -> ui.Container:
    """Place controls in ActionRows respecting Discord limits.

    Each Select must be alone in its row. Buttons may share a row (max 5).
    """
    pending_buttons: list[ui.Item] = []
    pending_row: int | None = None

    def flush_buttons() -> None:
        nonlocal pending_buttons, pending_row
        while pending_buttons:
            batch = pending_buttons[:5]
            pending_buttons = pending_buttons[5:]
            add_action_row(container, *batch)
        pending_row = None

    for ctrl in controls:
        if isinstance(ctrl, discord.ui.Select):
            flush_buttons()
            add_action_row(container, ctrl)
        elif isinstance(ctrl, discord.ui.Button):
            btn_row = getattr(ctrl, "row", None)
            if (
                pending_buttons
                and pending_row is not None
                and btn_row is not None
                and btn_row != pending_row
            ):
                flush_buttons()
            pending_buttons.append(ctrl)
            if btn_row is not None:
                pending_row = btn_row
        else:
            pending_buttons.append(ctrl)
    flush_buttons()
    return container


def build_layout(
    *containers: ui.Container,
    timeout: Optional[float] = 180,
) -> ui.LayoutView:
    view = ui.LayoutView(timeout=timeout)
    for c in containers:
        view.add_item(c)
    return view


def panel_container(
    *,
    title: Optional[str] = None,
    body: str = "",
    footer: Optional[str] = None,
    emoji: Optional[str] = None,
    accent: int = ACCENT_BRAND,
) -> ui.Container:
    c = new_container(accent=accent)
    add_text(c, panel_markdown(title=title, body=body, footer=footer, emoji=emoji))
    return c


def panel_with_controls(
    *,
    title: Optional[str] = None,
    body: str = "",
    footer: Optional[str] = None,
    emoji: Optional[str] = None,
    accent: int = ACCENT_BRAND,
    controls: Sequence[ui.Item] = (),
    section_label: Optional[str] = None,
) -> ui.LayoutView:
    """Single container: header text, optional section label, controls in ActionRow."""
    c = panel_container(
        title=title, body=body, footer=footer, emoji=emoji, accent=accent
    )
    if controls:
        if section_label:
            add_section(c, section_label, *controls, divider=True)
        else:
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            add_controls_to_container(c, controls)
    return build_layout(c, timeout=180)


def build_status_panel(
    *,
    title: str,
    body: str,
    accent: int,
    emoji: Optional[str] = None,
    footer: Optional[str] = None,
) -> ui.LayoutView:
    return build_layout(
        panel_container(
            title=title, body=body, accent=accent, emoji=emoji, footer=footer or brand_footer()
        )
    )


def success_panel(title: str, body: str, *, footer: Optional[str] = None) -> ui.LayoutView:
    return build_status_panel(
        title=title, body=body, accent=ACCENT_SUCCESS, emoji="✅", footer=footer
    )


def error_panel(title: str, body: str, *, footer: Optional[str] = None) -> ui.LayoutView:
    return build_status_panel(
        title=title, body=body, accent=ACCENT_ERROR, emoji="❌", footer=footer
    )


def warning_panel(title: str, body: str, *, footer: Optional[str] = None) -> ui.LayoutView:
    return build_status_panel(
        title=title, body=body, accent=ACCENT_WARNING, emoji="⚠️", footer=footer
    )


def info_panel(title: str, body: str, *, footer: Optional[str] = None) -> ui.LayoutView:
    return build_status_panel(
        title=title, body=body, accent=ACCENT_INFO, emoji="ℹ️", footer=footer
    )


def brand_panel(title: str, body: str, *, footer: Optional[str] = None) -> ui.LayoutView:
    return build_status_panel(
        title=title, body=body, accent=ACCENT_BRAND, emoji="🎰", footer=footer
    )


def build_detail_panel(
    *,
    title: str,
    body: str = "",
    fields: Optional[dict[str, str]] = None,
    accent: int = ACCENT_BRAND,
    emoji: Optional[str] = None,
    footer: Optional[str] = None,
) -> ui.LayoutView:
    c = panel_container(title=title, body=body, accent=accent, emoji=emoji, footer=footer)
    if fields:
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        lines = "\n".join(f"**{name}**\n{value}" for name, value in fields.items())
        add_text(c, lines)
    return build_layout(c)


async def send_ephemeral(
    interaction: discord.Interaction,
    view: ui.LayoutView,
    *,
    edit: bool = False,
) -> None:
    if edit:
        await interaction.response.edit_message(content=None, embed=None, view=view)
        return
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


async def send_channel_v2(
    channel: discord.abc.Messageable,
    view: ui.LayoutView,
    *,
    delete_after: Optional[float] = None,
) -> Optional[discord.Message]:
    try:
        return await channel.send(view=view, delete_after=delete_after)
    except (discord.Forbidden, discord.HTTPException):
        return None
