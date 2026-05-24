"""Shared Accept / Decline & Cancel flow for PvP game challenges."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PVP_CHALLENGE_TIMEOUT = 30


class PvpChallengeView(discord.ui.View):
    """30s timeout; opponent accepts; either player may Decline & Cancel."""

    def __init__(
        self,
        challenger_id: int,
        opponent_id: int,
        *,
        game_name: str = "Challenge",
    ):
        super().__init__(timeout=PVP_CHALLENGE_TIMEOUT)
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.game_name = game_name
        self._message: discord.Message | None = None
        self._done = False

    def attach_message(self, message: discord.Message) -> None:
        self._message = message

    def _mark_done(self) -> None:
        self._done = True
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.challenger_id, self.opponent_id):
            await interaction.response.send_message(
                "This challenge is not for you.", ephemeral=True,
            )
            return False
        if self._done:
            await interaction.response.send_message(
                "This challenge is no longer active.", ephemeral=True,
            )
            return False
        return True

    async def handle_accept(self, interaction: discord.Interaction) -> None:
        raise NotImplementedError

    async def _cancel(self, interaction: discord.Interaction, text: str) -> None:
        self._mark_done()
        await interaction.response.edit_message(content=text, embed=None, view=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            return await interaction.response.send_message(
                "Only the challenged player can accept.", ephemeral=True,
            )
        await self.handle_accept(interaction)

    @discord.ui.button(label="Decline & Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid == self.challenger_id:
            text = f"❌ {interaction.user.display_name} cancelled the **{self.game_name}** challenge."
        elif uid == self.opponent_id:
            text = f"❌ {interaction.user.display_name} declined the **{self.game_name}** challenge."
        else:
            return
        await self._cancel(interaction, text)

    async def on_timeout(self) -> None:
        if self._done:
            return
        self._done = True
        for item in self.children:
            item.disabled = True
        if not self._message:
            return
        try:
            embed = discord.Embed(
                title=f"⏱️ {self.game_name} — Expired",
                description=(
                    "Challenge was not accepted within "
                    f"**{PVP_CHALLENGE_TIMEOUT} seconds** and was cancelled."
                ),
                color=0x95A5A6,
            )
            await self._message.edit(embed=embed, content=None, view=self)
        except Exception:
            pass
