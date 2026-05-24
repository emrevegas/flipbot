"""Components V2 layouts with MediaGallery GIF attachments."""

from __future__ import annotations

import discord
from discord import ui


def gif_media_layout(
    gif_filename: str,
    *,
    timeout: float | None = 120,
    extra_rows: list[ui.ActionRow] | None = None,
    accent: discord.Colour | None = None,
) -> ui.LayoutView:
    """LayoutView: Container → MediaGallery + optional ActionRows."""

    class _GifLayout(ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=timeout)
            container = ui.Container(
                accent_colour=accent or discord.Colour.gold(),
            )
            gallery = ui.MediaGallery()
            gallery.add_item(media=f"attachment://{gif_filename}")
            container.add_item(gallery)
            for row in extra_rows or []:
                container.add_item(row)
            self.add_item(container)

    return _GifLayout()


def challenge_text_layout(
    body: str,
    controls: list[ui.Item],
    *,
    timeout: float = 30,
    accent: discord.Colour | None = None,
) -> ui.LayoutView:
    """V2 challenge panel: markdown text + buttons in one message."""

    class _ChallengeLayout(ui.LayoutView):
        def __init__(self):
            super().__init__(timeout=timeout)
            container = ui.Container(accent_colour=accent or discord.Colour.gold())
            container.add_item(ui.TextDisplay(body))
            container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
            row = ui.ActionRow()
            for ctrl in controls[:5]:
                row.add_item(ctrl)
            container.add_item(row)
            self.add_item(container)

    return _ChallengeLayout()
