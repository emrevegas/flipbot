"""Jackpot Components V2 — lobby PNG + spin GIF with separators."""

from __future__ import annotations

import discord
from discord import ui

LOBBY_ATTACHMENT = "jackpot_lobby.png"
SPIN_ATTACHMENT = "jackpot.gif"


def jackpot_lobby_layout(
    *,
    header: str,
    footer: str = "",
    timeout: float | None = None,
) -> ui.LayoutView:
    """Waiting / collecting — single MediaGallery (lobby image)."""

    class _Lobby(ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=timeout)
            c = ui.Container()
            c.add_item(ui.TextDisplay(header))
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            g = ui.MediaGallery()
            g.add_item(media=f"attachment://{LOBBY_ATTACHMENT}")
            c.add_item(g)
            if footer:
                c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                c.add_item(ui.TextDisplay(footer))
            self.add_item(c)

    return _Lobby()


def jackpot_spin_layout(
    *,
    header: str,
    timeout: float | None = None,
) -> ui.LayoutView:
    """Running round — lobby gallery, separator, animation gallery, separator."""

    class _Spin(ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=timeout)
            c = ui.Container()
            c.add_item(ui.TextDisplay(header))
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            g1 = ui.MediaGallery()
            g1.add_item(media=f"attachment://{LOBBY_ATTACHMENT}")
            c.add_item(g1)
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            g2 = ui.MediaGallery()
            g2.add_item(media=f"attachment://{SPIN_ATTACHMENT}")
            c.add_item(g2)
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            self.add_item(c)

    return _Spin()


def jackpot_winner_layout(
    *,
    body: str,
    spin_gif: bool = True,
    timeout: float | None = 120,
) -> ui.LayoutView:
    """Post-round result menu."""

    class _Winner(ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=timeout)
            c = ui.Container()
            c.add_item(ui.TextDisplay(body))
            if spin_gif:
                c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                g = ui.MediaGallery()
                g.add_item(media=f"attachment://{SPIN_ATTACHMENT}")
                c.add_item(g)
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            c.add_item(
                ui.TextDisplay(
                    f"Join the next round with `.jp <bet>` or `.jackpot <bet>`"
                )
            )
            self.add_item(c)

    return _Winner()
