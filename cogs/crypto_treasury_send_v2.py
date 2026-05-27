"""Components V2 — admin treasury payout (.sent)."""

from __future__ import annotations

import discord
from discord import ui

import modules.crypto_deposit as engine
from cogs.crypto_withdraw import _chain_emoji, can_treasury_send, send_treasury_payout
from modules.crypto_onchain_logs import explorer_tx_url
from modules.ui_v2 import (
    ACCENT_CRYPTO,
    ACCENT_ERROR,
    ACCENT_SUCCESS,
    panel_with_controls,
    send_ephemeral,
)


def _not_sender(interaction: discord.Interaction, admin_id: int) -> bool:
    return interaction.user.id != admin_id


class TreasurySentCoinSelect(ui.Select):
    def __init__(self, admin_id: int):
        self.admin_id = admin_id
        s = engine.get_settings()
        options: list[discord.SelectOption] = []
        if s.get("sol_enabled", True):
            options.append(discord.SelectOption(label="Solana (SOL)", value="SOL", emoji="🟣"))
        if s.get("ltc_enabled", True):
            options.append(discord.SelectOption(label="Litecoin (LTC)", value="LTC", emoji="🔘"))
        if s.get("eth_enabled", True):
            options.append(discord.SelectOption(label="Ethereum (ETH)", value="ETH", emoji="💎"))
        if not options:
            options.append(discord.SelectOption(label="No coins available", value="none"))
        super().__init__(
            placeholder="Select coin to send from treasury…",
            options=options,
            custom_id="treasury_sent_coin_v2",
        )

    async def callback(self, interaction: discord.Interaction):
        if _not_sender(interaction, self.admin_id):
            return await interaction.response.send_message(
                "❌ This menu belongs to someone else.", ephemeral=True,
            )
        if not can_treasury_send(interaction.user):
            from modules.ui_v2 import error_panel
            return await send_ephemeral(
                interaction, error_panel("No permission", "Admin access required."),
            )
        chain = self.values[0]
        if chain == "none":
            from modules.ui_v2 import error_panel
            return await send_ephemeral(
                interaction, error_panel("Unavailable", "No crypto chains are enabled."),
            )
        await interaction.response.send_modal(TreasurySentModal(chain=chain, admin_id=self.admin_id))


class TreasurySentModal(ui.Modal):
    def __init__(self, chain: str, admin_id: int):
        super().__init__(title=f"Treasury Send {chain}", timeout=300)
        self.chain = chain
        self.admin_id = admin_id
        self.addr_input = discord.ui.TextInput(
            label=f"{chain} destination address",
            placeholder="Wallet address",
            required=True,
            max_length=120,
        )
        self.amount_input = discord.ui.TextInput(
            label="Amount in USD",
            placeholder="e.g. 25.00",
            required=True,
            max_length=14,
        )
        self.add_item(self.addr_input)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            return await interaction.response.send_message(
                "❌ Not your menu.", ephemeral=True,
            )
        if not can_treasury_send(interaction.user):
            return await interaction.response.send_message(
                "❌ Admin access required.", ephemeral=True,
            )

        address = self.addr_input.value.strip()
        if not address:
            return await interaction.response.send_message(
                "❌ Address cannot be empty.", ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        tx_id, amount_crypto, usd, err = await send_treasury_payout(
            self.chain, address, self.amount_input.value,
        )
        emoji = _chain_emoji(self.chain)

        if err or not tx_id:
            from modules.ui_v2 import error_panel
            return await interaction.followup.send(
                view=error_panel(
                    "Treasury send failed",
                    err or "No transaction ID returned.",
                    emoji="❌",
                ),
                ephemeral=True,
            )

        url = explorer_tx_url(self.chain, tx_id)
        explorer_line = f"\n[View on explorer]({url})" if url else ""
        body = (
            f"{emoji} **{amount_crypto} {self.chain}** (~${usd:.2f} USD)\n"
            f"📍 `{address}`\n"
            f"🔗 TX: `{tx_id}`{explorer_line}"
        )
        await interaction.followup.send(
            view=panel_with_controls(
                title="Treasury payout sent",
                body=body,
                footer=f"Sent by {interaction.user.display_name}",
                emoji="✅",
                accent=ACCENT_SUCCESS,
            ),
            ephemeral=True,
        )


def build_treasury_sent_coin_layout(admin_id: int) -> ui.LayoutView:
    return panel_with_controls(
        title="Treasury Send",
        body=(
            "Send crypto **from the treasury wallet** to any address.\n"
            "Select coin, then enter destination and USD amount.\n\n"
            "*Admin only · No user balance deducted · No approval queue*"
        ),
        footer="VegasBet | Treasury",
        emoji="📤",
        accent=ACCENT_CRYPTO,
        controls=[TreasurySentCoinSelect(admin_id)],
        section_label="Coin",
    )


def build_treasury_sent_disabled(title: str, body: str) -> ui.LayoutView:
    return panel_with_controls(
        title=title,
        body=body,
        footer="VegasBet | Treasury",
        emoji="❌",
        accent=ACCENT_ERROR,
    )
