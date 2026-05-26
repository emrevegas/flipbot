"""Components V2 layouts with MediaGallery GIF attachments."""



from __future__ import annotations



from collections.abc import Awaitable, Callable



import discord

from discord import ui



RebetCallback = Callable[[discord.Interaction, int, float], Awaitable[None]]





def _make_container(*, accent: discord.Colour | None) -> ui.Container:

    if accent is None:

        return ui.Container()

    return ui.Container(accent_colour=accent)





def gif_media_layout(

    gif_filename: str,

    *,

    timeout: float | None = 120,

    extra_rows: list[ui.ActionRow] | None = None,

    accent: discord.Colour | None = None,

) -> ui.LayoutView:

    """LayoutView: Container → MediaGallery + optional ActionRows (no accent by default)."""



    class _GifLayout(ui.LayoutView):

        def __init__(self):

            super().__init__(timeout=timeout)

            container = _make_container(accent=accent)

            gallery = ui.MediaGallery()

            gallery.add_item(media=f"attachment://{gif_filename}")

            container.add_item(gallery)

            for row in extra_rows or []:

                container.add_item(row)

            self.add_item(container)



    return _GifLayout()





def gif_result_layout(

    gif_filename: str,

    *,

    user_id: int,

    bet: float,

    rebet_cb: RebetCallback,

    timeout: float | None = 120,

) -> ui.LayoutView:

    """Result GIF with Re-bet / 2× Bet — no container accent colour."""



    class _GifResultLayout(ui.LayoutView):

        def __init__(self):

            super().__init__(timeout=timeout)

            container = ui.Container()

            gallery = ui.MediaGallery()

            gallery.add_item(media=f"attachment://{gif_filename}")

            container.add_item(gallery)

            if user_id and bet > 0:
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                row = ui.ActionRow()

                rb = ui.Button(label="Re-bet", style=discord.ButtonStyle.secondary, emoji="🔄")

                rb.callback = self._make_cb(user_id, bet)

                row.add_item(rb)

                x2 = ui.Button(label="2× Bet", style=discord.ButtonStyle.primary, emoji="⬆️")

                x2.callback = self._make_cb(user_id, bet * 2)

                row.add_item(x2)

                container.add_item(row)



            self.add_item(container)



        def _make_cb(self, uid: int, amount: float):

            async def _cb(interaction: discord.Interaction):

                if interaction.user.id != uid:

                    from modules import flip_utils as utils

                    return await interaction.response.send_message(

                        embed=utils.error_embed("Not your game."),

                        ephemeral=True,

                    )

                await rebet_cb(interaction, uid, amount)



            return _cb



    return _GifResultLayout()





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

            container = _make_container(accent=accent)

            container.add_item(ui.TextDisplay(body))

            if controls:
                container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
                row = ui.ActionRow()
                for ctrl in controls[:5]:
                    row.add_item(ctrl)
                container.add_item(row)

            self.add_item(container)



    return _ChallengeLayout()

