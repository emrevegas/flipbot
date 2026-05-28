"""Horse race Components V2 — bets PNG + race GIF galleries."""

from __future__ import annotations

import discord
from discord import ui

BETS_ATTACHMENT = "horse_bets.png"
RACE_ATTACHMENT = "horse_race.gif"
WAITING_ATTACHMENT = "horse_waiting.png"


def horse_race_layout(
    *,
    header: str,
    footer: str = "",
    race_is_gif: bool = False,
    timeout: float | None = 180,
) -> ui.LayoutView:
    """Separator → bets gallery → separator → race gallery → separator."""

    race_file = RACE_ATTACHMENT if race_is_gif else WAITING_ATTACHMENT

    class _Layout(ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=timeout)
            c = ui.Container(accent_color=discord.Colour(0xC9A227))
            c.add_item(ui.TextDisplay(header))
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            g1 = ui.MediaGallery()
            g1.add_item(media=f"attachment://{BETS_ATTACHMENT}")
            c.add_item(g1)
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            g2 = ui.MediaGallery()
            g2.add_item(media=f"attachment://{race_file}")
            c.add_item(g2)
            c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            if footer:
                c.add_item(ui.TextDisplay(footer))
            self.add_item(c)

    return _Layout()
