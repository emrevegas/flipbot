"""Crypto Deposit Cog — SOL, LTC & ETH auto-detection, user UI, admin management.

User flow:
  /crypto_deposit  →  shows unique address embed  →  "✅ Check Now" button
  Background task checks active users every 2 minutes.
  On deposit detected: credit coins + DM user.

Admin flow:
  Admin Panel → Crypto Deposits → enable/disable, set min, toggle chains.
"""

import discord
from discord.ext import commands, tasks
import time
import asyncio

from modules.database import (
    check_permission, get_data, set_data, replace_data, get_server_data,
)
from modules.utils import format_balance, get_user_lang
from modules.constants import FOOTER_TEXT
import modules.crypto_deposit as engine


SOL_EXPLORER = "https://solscan.io/account"
LTC_EXPLORER = "https://live.blockcypher.com/ltc/address"
ETH_EXPLORER = "https://etherscan.io/address"


class _CopyButton(discord.ui.Button):
    """Sends the address as plain ephemeral text for easy copying."""

    def __init__(self, label: str, address: str, style: discord.ButtonStyle, row: int | None = None):
        super().__init__(label=label, style=style, row=row)
        self.address = address

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(self.address, ephemeral=True)


class _AddressCopyView(discord.ui.View):
    """Generic view with copy buttons for one or more addresses."""
    def __init__(self, addresses: list):
        super().__init__(timeout=120)
        for label, addr, style, row in addresses:
            if addr:
                self.add_item(_CopyButton(label, addr, style, row))


# ── Embed builders ─────────────────────────────────────────────────────────────

def _build_deposit_embed(user_id: int) -> tuple[discord.Embed, bool, str, str]:
    """Build the user's deposit address embed. Returns (embed, success, sol_addr, ltc_addr)."""
    settings = engine.get_settings()
    if not settings.get("enabled", False):
        return discord.Embed(
            title="❌ Crypto Deposits Disabled",
            description="Crypto deposits are currently disabled. Contact an admin.",
            color=discord.Color.red(),
        ), False, "", "", ""

    if not engine.MNEMONIC:
        return discord.Embed(
            title="⚙️ Not Configured",
            description="The bot owner has not set up `CRYPTO_MNEMONIC` yet.",
            color=discord.Color.orange(),
        ), False, "", "", ""

    try:
        wallet = engine.get_or_create_addresses(user_id)
    except Exception as e:
        return discord.Embed(
            title="❌ Wallet Error",
            description=f"Could not generate your deposit address.\n`{e}`",
            color=discord.Color.red(),
        ), False, "", "", ""

    rates    = engine.get_rates()
    exch     = get_data("server/exchange_rates") or {}
    coin_usd = float(exch.get("coin_usd_rate", 0))
    min_usd  = float(settings.get("min_deposit_usd", 1.0))

    sol_price = rates.get("sol_usd", 0)
    ltc_price = rates.get("ltc_usd", 0)
    eth_price = rates.get("eth_usd", 0)
    min_sol   = round(min_usd / sol_price, 6) if sol_price > 0 else "?"
    min_ltc   = round(min_usd / ltc_price, 8) if ltc_price > 0 else "?"
    min_eth   = round(min_usd / eth_price, 8) if eth_price > 0 else "?"
    coins_per_usd = round(1 / coin_usd) if coin_usd > 0 else "?"

    embed = discord.Embed(
        title="🔐  Crypto Deposit",
        description=(
            f"Send crypto to your **personal** addresses below.\n"
            f"Deposits are checked automatically every **5 minutes**.\n"
            f"Minimum deposit: **${min_usd:.2f} USD**"
        ),
        color=0x9945FF,
    )

    sol_addr = ""
    ltc_addr = ""
    eth_addr = ""

    sol_emoji, ltc_emoji, eth_emoji = _get_emojis()

    if settings.get("sol_enabled", True) and "sol" in wallet:
        sol_addr = wallet["sol"]["address"]
        embed.add_field(
            name=f"{sol_emoji}  Solana  (SOL)",
            value=(
                f"```{sol_addr}```"
                f"💵 **${sol_price:,.2f}** / SOL  ·  Min: `{min_sol} SOL`\n"
                f"[🔎 Solscan]({SOL_EXPLORER}/{sol_addr})"
            ),
            inline=False,
        )

    if settings.get("ltc_enabled", True) and "ltc" in wallet:
        ltc_addr = wallet["ltc"]["address"]
        embed.add_field(
            name=f"{ltc_emoji}  Litecoin  (LTC)",
            value=(
                f"```{ltc_addr}```"
                f"💵 **${ltc_price:,.2f}** / LTC  ·  Min: `{min_ltc} LTC`\n"
                f"[🔎 BlockCypher]({LTC_EXPLORER}/{ltc_addr}/)"
            ),
            inline=False,
        )

    if settings.get("eth_enabled", True) and "eth" in wallet:
        eth_addr = wallet["eth"]["address"]
        embed.add_field(
            name=f"{eth_emoji}  Ethereum  (ETH)",
            value=(
                f"```{eth_addr}```"
                f"💵 **${eth_price:,.2f}** / ETH  ·  Min: `{min_eth} ETH`\n"
                f"[🔎 Etherscan]({ETH_EXPLORER}/{eth_addr})"
            ),
            inline=False,
        )

    embed.add_field(
        name="💱  Conversion",
        value=f"$1 USD = **{coins_per_usd}** coins  (server exchange rate)",
        inline=False,
    )
    embed.set_footer(text="Vegas Casino  ·  Deposits credited within 5 min  ·  Network fees apply")
    if not eth_addr and settings.get("eth_enabled", True) and "eth" in wallet:
        eth_addr = wallet["eth"]["address"]
    return embed, True, sol_addr, ltc_addr, eth_addr


def _build_deposit_layout(user_id: int) -> tuple[discord.ui.LayoutView, bool, str, str]:
    """Components V2 deposit panel — controls inside container."""
    from discord import ui
    from modules.ui_v2 import (
        ACCENT_CRYPTO,
        ACCENT_ERROR,
        ACCENT_WARNING,
        add_action_row,
        add_section,
        add_text,
        build_layout,
        error_panel,
        new_container,
        panel_markdown,
        warning_panel,
    )

    settings = engine.get_settings()
    if not settings.get("enabled", False):
        return (
            error_panel(
                "Crypto Deposits Disabled",
                "Crypto deposits are currently disabled. Contact an admin.",
            ),
            False,
            "",
            "",
        )

    if not engine.MNEMONIC:
        return (
            warning_panel(
                "Not Configured",
                "The bot owner has not set up `CRYPTO_MNEMONIC` yet.",
            ),
            False,
            "",
            "",
        )

    try:
        wallet = engine.get_or_create_addresses(user_id)
    except Exception as e:
        return (
            error_panel("Wallet Error", f"Could not generate your deposit address.\n`{e}`"),
            False,
            "",
            "",
        )

    rates = engine.get_rates()
    exch = get_data("server/exchange_rates") or {}
    coin_usd = float(exch.get("coin_usd_rate", 0))
    min_usd = float(settings.get("min_deposit_usd", 1.0))
    sol_price = rates.get("sol_usd", 0)
    ltc_price = rates.get("ltc_usd", 0)
    eth_price = rates.get("eth_usd", 0)
    min_sol = round(min_usd / sol_price, 6) if sol_price > 0 else "?"
    min_ltc = round(min_usd / ltc_price, 8) if ltc_price > 0 else "?"
    min_eth = round(min_usd / eth_price, 8) if eth_price > 0 else "?"
    coins_per_usd = round(1 / coin_usd) if coin_usd > 0 else "?"

    sol_addr = ""
    ltc_addr = ""
    sol_emoji, ltc_emoji, eth_emoji = _get_emojis()
    blocks: list[str] = [
        "Send crypto to your **personal** addresses below.\n"
        "Deposits are checked automatically every **5 minutes**.\n"
        f"Minimum deposit: **${min_usd:.2f} USD**",
    ]

    if settings.get("sol_enabled", True) and "sol" in wallet:
        sol_addr = wallet["sol"]["address"]
        blocks.append(
            f"### {sol_emoji} Solana (SOL)\n"
            f"```{sol_addr}```\n"
            f"**${sol_price:,.2f}** / SOL  ·  Min: `{min_sol} SOL`\n"
            f"[Solscan]({SOL_EXPLORER}/{sol_addr})"
        )

    if settings.get("ltc_enabled", True) and "ltc" in wallet:
        ltc_addr = wallet["ltc"]["address"]
        blocks.append(
            f"### {ltc_emoji} Litecoin (LTC)\n"
            f"```{ltc_addr}```\n"
            f"**${ltc_price:,.2f}** / LTC  ·  Min: `{min_ltc} LTC`\n"
            f"[BlockCypher]({LTC_EXPLORER}/{ltc_addr}/)"
        )

    if settings.get("eth_enabled", True) and "eth" in wallet:
        eth_addr = wallet["eth"]["address"]
        blocks.append(
            f"### {eth_emoji} Ethereum (ETH)\n"
            f"```{eth_addr}```\n"
            f"**${eth_price:,.2f}** / ETH  ·  Min: `{min_eth} ETH`\n"
            f"[Etherscan]({ETH_EXPLORER}/{eth_addr})"
        )

    blocks.append(f"### 💱 Conversion\n$1 USD = **{coins_per_usd}** coins (server rate)")

    c = new_container(accent=ACCENT_CRYPTO)
    add_text(
        c,
        panel_markdown(
            title="Crypto Deposit",
            body="\n\n".join(blocks),
            footer="Vegas Casino · Credited within ~5 min · Network fees apply",
            emoji="🔐",
        ),
    )
    buttons: list[ui.Button] = []
    if sol_addr:
        buttons.append(_CopyButton("◎ Copy SOL", sol_addr, discord.ButtonStyle.primary))
    if ltc_addr:
        buttons.append(_CopyButton("Ł Copy LTC", ltc_addr, discord.ButtonStyle.primary))
    if settings.get("eth_enabled", True) and "eth" in wallet:
        buttons.append(_CopyButton("Ξ Copy ETH", wallet["eth"]["address"], discord.ButtonStyle.primary))
    buttons.append(_CheckNowButton(user_id))
    add_section(c, "Actions", *buttons)

    return build_layout(c, timeout=120), True, sol_addr, ltc_addr


class _CheckNowButton(discord.ui.Button):
    def __init__(self, user_id: int):
        super().__init__(label="🔄 Check Now", style=discord.ButtonStyle.success)
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "❌ This panel is not yours.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)
        try:
            credited = engine.check_user_deposits(self.user_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Check failed: `{e}`", ephemeral=True)
            return

        if credited:
            cog = interaction.client.get_cog("CryptoDeposit")
            if cog:
                member = interaction.user if isinstance(interaction.user, discord.Member) else None
                await cog._notify_user(self.user_id, credited, member=member)

            lines = []
            for dep in credited:
                lines.append(
                    f"{dep['symbol']} **{dep['amount_crypto']} {dep['chain']}** "
                    f"(${dep['amount_usd']:.2f}) → **+{format_balance(dep['coins'], 'real')}** credited!"
                )
            from modules.ui_v2 import success_panel, send_ephemeral

            await send_ephemeral(
                interaction,
                success_panel("Deposit Detected!", "\n".join(lines)),
            )
        else:
            from modules.ui_v2 import info_panel, send_ephemeral

            await send_ephemeral(
                interaction,
                info_panel(
                    "No deposit yet",
                    "No new deposits found. Transactions can take a few minutes to confirm.",
                ),
            )


def _get_emojis() -> tuple[str, str, str]:
    """Return (sol_emoji, ltc_emoji, eth_emoji) from settings, with safe defaults."""
    s = engine.get_settings()
    return (
        s.get("sol_emoji", "🟣"),
        s.get("ltc_emoji", "🔘"),
        s.get("eth_emoji", "🔷"),
    )


def _chain_emoji(chain: str) -> str:
    sol, ltc, eth = _get_emojis()
    return {"SOL": sol, "LTC": ltc, "ETH": eth}.get(chain, "💰")


def _short_addr(addr: str, n: int = 8) -> str:
    if not addr or addr == "*Not set*":
        return "*Not set*"
    return addr if len(addr) <= n * 2 + 3 else f"{addr[:n]}...{addr[-6:]}"


def _build_admin_embed(section: str = "overview") -> discord.Embed:
    """Build the admin stats embed. section: 'overview' | 'wallets' | 'sweep'"""
    settings  = engine.get_settings()
    enabled   = settings.get("enabled", False)
    sol_en    = settings.get("sol_enabled", True)
    ltc_en    = settings.get("ltc_enabled", True)
    eth_en    = settings.get("eth_enabled", True)
    min_usd   = float(settings.get("min_deposit_usd", 1.0))
    auto_sw   = settings.get("auto_sweep", False)
    sol_sw    = settings.get("sol_sweep_address", "") or ""
    ltc_sw    = settings.get("ltc_sweep_address", "") or ""
    eth_sw    = settings.get("eth_sweep_address", "") or ""
    log_ch    = settings.get("sweep_log_channel_id", None)
    rates     = engine.get_rates()
    sol_price = rates.get("sol_usd", 0)
    ltc_price = rates.get("ltc_usd", 0)
    eth_price = rates.get("eth_usd", 0)

    color = 0x9945FF if enabled else 0x747f8d

    sol_emoji, ltc_emoji, eth_emoji = _get_emojis()

    if section == "wallets":
        wallets   = get_data("server/crypto_wallets") or {}
        idx       = (get_data("server/crypto_wallet_index") or {}).get("next_index", 0)
        active    = len(engine.get_active_user_ids())

        dep_log_ch = settings.get("deposit_log_channel_id", None)

        embed = discord.Embed(title="📊  Crypto — Wallet Stats", color=color)
        embed.add_field(
            name="📁  Wallet Registry",
            value=(
                f"**Total Wallets Created:** `{len(wallets)}`\n"
                f"**Next HD Index:** `{idx}`\n"
                f"**Actively Monitored:** `{active}`\n"
                f"**Mnemonic:** {'✅ Set' if engine.MNEMONIC else '❌ Missing'}"
            ),
            inline=False,
        )
        embed.add_field(
            name="💵  Live Rates",
            value=(
                f"{sol_emoji} **SOL:** ${sol_price:,.2f}\n"
                f"{ltc_emoji} **LTC:** ${ltc_price:,.2f}\n"
                f"{eth_emoji} **ETH:** ${eth_price:,.2f}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🔗  Chains",
            value=(
                f"{sol_emoji} **SOL:** {'✅ Enabled' if sol_en else '❌ Disabled'}\n"
                f"{ltc_emoji} **LTC:** {'✅ Enabled' if ltc_en else '❌ Disabled'}\n"
                f"{eth_emoji} **ETH:** {'✅ Enabled' if eth_en else '❌ Disabled'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="🎨  Chain Emojis",
            value=f"SOL: {sol_emoji}  │  LTC: {ltc_emoji}  │  ETH: {eth_emoji}",
            inline=True,
        )
        embed.add_field(
            name="🟢  Deposit Log Channel",
            value=f"<#{dep_log_ch}>" if dep_log_ch else "*Not set*",
            inline=True,
        )
        embed.set_footer(text="Use 'Fetch Total Balances' to scan all on-chain balances (slow).")
        return embed

    if section == "sweep":
        withdraw_log_ch = settings.get("withdraw_log_channel_id", None)
        withdraw_approval_ch = settings.get("withdraw_approval_channel_id", None)
        try:
            treasury_sol = engine.get_treasury_address("SOL") if engine.TREASURY_MNEMONIC else "*No TREASURY_MNEMONIC*"
            treasury_ltc = engine.get_treasury_address("LTC") if engine.TREASURY_MNEMONIC else "*No TREASURY_MNEMONIC*"
            treasury_eth = engine.get_treasury_address("ETH") if engine.TREASURY_MNEMONIC else "*No TREASURY_MNEMONIC*"
        except Exception:
            treasury_sol = treasury_ltc = treasury_eth = "*Error deriving address*"

        embed = discord.Embed(title="💸  Crypto — Sweep & Withdrawal Settings", color=color)
        embed.add_field(
            name="⚡  Auto-Sweep",
            value=f"**Status:** {'✅ Active' if auto_sw else '❌ Off'}",
            inline=False,
        )
        embed.add_field(
            name=f"{sol_emoji}  SOL Sweep Address",
            value=f"`{_short_addr(sol_sw, 12) if sol_sw else '*Not set*'}`",
            inline=True,
        )
        embed.add_field(
            name=f"{ltc_emoji}  LTC Sweep Address",
            value=f"`{_short_addr(ltc_sw, 12) if ltc_sw else '*Not set*'}`",
            inline=True,
        )
        embed.add_field(
            name=f"{eth_emoji}  ETH Sweep Address",
            value=f"`{_short_addr(eth_sw, 12) if eth_sw else '*Not set*'}`",
            inline=True,
        )
        embed.add_field(
            name="📢  Sweep Log Channel",
            value=f"<#{log_ch}>" if log_ch else "*Not set*",
            inline=True,
        )
        embed.add_field(
            name="📝  Withdraw Log Channel",
            value=(
                f"<#{withdraw_log_ch}>" if withdraw_log_ch else "*Not set* · public payout feed"
            ),
            inline=True,
        )
        embed.add_field(
            name="✅  Withdraw Approval Channel",
            value=(
                f"<#{withdraw_approval_ch}>"
                if withdraw_approval_ch
                else (
                    f"<#{log_ch}> (sweep log fallback)"
                    if log_ch
                    else "*Not set* · staff approve/reject"
                )
            ),
            inline=True,
        )
        embed.add_field(
            name="🏦  Treasury Wallet  (TREASURY_MNEMONIC)",
            value=(
                f"{'✅ Set' if engine.TREASURY_MNEMONIC else '❌ TREASURY_MNEMONIC not in .env'}\n"
                f"◎ `{_short_addr(treasury_sol, 10)}`\n"
                f"Ł `{_short_addr(treasury_ltc, 10)}`\n"
                f"Ξ `{_short_addr(treasury_eth, 10)}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Fund the treasury wallet so withdrawals can be processed.")
        return embed

    # ── overview (default) ────────────────────────────────────────────────────
    embed = discord.Embed(title="💰  Crypto Deposits — Admin Panel", color=color)
    embed.add_field(
        name="⚙️  System Status",
        value=(
            f"**Deposits:** {'✅ Enabled' if enabled else '❌ Disabled'}\n"
            f"**SOL:** {'✅' if sol_en else '❌'}  │  **LTC:** {'✅' if ltc_en else '❌'}  │  **ETH:** {'✅' if eth_en else '❌'}\n"
            f"**Min Deposit:** `${min_usd:.2f} USD`\n"
            f"**Auto-Sweep:** {'✅ On' if auto_sw else '❌ Off'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="💵  Live Rates",
        value=(
            f"**SOL:** `${sol_price:,.2f}`\n"
            f"**LTC:** `${ltc_price:,.2f}`\n"
            f"**ETH:** `${eth_price:,.2f}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="📢  Sweep Log Channel",
        value=f"<#{log_ch}>" if log_ch else "*Not configured*",
        inline=True,
    )
    embed.set_footer(text="Select a category below to manage settings.")
    return embed


# ── Admin Modals ───────────────────────────────────────────────────────────────

class CryptoEmojiModal(discord.ui.Modal):
    def __init__(self, current_sol: str, current_ltc: str, current_eth: str):
        super().__init__(title="Set Chain Emojis", timeout=300)
        self.sol_emoji = discord.ui.TextInput(
            label="SOL emoji",
            placeholder="e.g.  🟣  or  <:sol:1234567890>",
            default=current_sol,
            required=True,
            max_length=64,
        )
        self.ltc_emoji = discord.ui.TextInput(
            label="LTC emoji",
            placeholder="e.g.  🔘  or  <:ltc:1234567890>",
            default=current_ltc,
            required=True,
            max_length=64,
        )
        self.eth_emoji = discord.ui.TextInput(
            label="ETH emoji",
            placeholder="e.g.  🔷  or  <:eth:1234567890>",
            default=current_eth,
            required=True,
            max_length=64,
        )
        self.add_item(self.sol_emoji)
        self.add_item(self.ltc_emoji)
        self.add_item(self.eth_emoji)

    async def on_submit(self, interaction: discord.Interaction):
        s = engine.get_settings()
        s["sol_emoji"] = self.sol_emoji.value.strip()
        s["ltc_emoji"] = self.ltc_emoji.value.strip()
        s["eth_emoji"] = self.eth_emoji.value.strip()
        engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("wallets"), view=CryptoWalletView())


class CryptoMinDepositModal(discord.ui.Modal):
    def __init__(self, current: float):
        super().__init__(title="Set Minimum Deposit", timeout=300)
        self.amount = discord.ui.TextInput(
            label="Minimum deposit (USD)",
            placeholder="e.g.  1.00",
            default=str(current),
            required=True,
            max_length=10,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = float(self.amount.value.replace(",", "."))
            if val <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Please enter a valid positive number.", ephemeral=True
            )
        s = engine.get_settings()
        s["min_deposit_usd"] = round(val, 2)
        engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("overview"), view=CryptoAdminView())


class SweepAddressModal(discord.ui.Modal):
    def __init__(self, current_sol: str, current_ltc: str, current_eth: str):
        super().__init__(title="Sweep Addresses", timeout=300)
        self.sol_addr = discord.ui.TextInput(
            label="SOL sweep address (blank = clear)",
            placeholder="Solana address",
            default=current_sol,
            required=False,
            max_length=100,
        )
        self.ltc_addr = discord.ui.TextInput(
            label="LTC sweep address (blank = clear)",
            placeholder="Litecoin address",
            default=current_ltc,
            required=False,
            max_length=60,
        )
        self.eth_addr = discord.ui.TextInput(
            label="ETH sweep address (blank = clear)",
            placeholder="0x… Ethereum address",
            default=current_eth,
            required=False,
            max_length=100,
        )
        self.add_item(self.sol_addr)
        self.add_item(self.ltc_addr)
        self.add_item(self.eth_addr)

    async def on_submit(self, interaction: discord.Interaction):
        s = engine.get_settings()
        s["sol_sweep_address"] = self.sol_addr.value.strip()
        s["ltc_sweep_address"] = self.ltc_addr.value.strip()
        s["eth_sweep_address"] = self.eth_addr.value.strip()
        engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("sweep"), view=CryptoSweepView())


# ── Admin Button Views ────────────────────────────────────────────────────────

class CryptoAdminView(discord.ui.View):
    """Overview panel — quick toggle + navigation buttons."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="✅ Enable Deposits", style=discord.ButtonStyle.success, row=0)
    async def enable(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings(); s["enabled"] = True; engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("overview"), view=CryptoAdminView())

    @discord.ui.button(label="❌ Disable Deposits", style=discord.ButtonStyle.danger, row=0)
    async def disable(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings(); s["enabled"] = False; engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("overview"), view=CryptoAdminView())

    @discord.ui.button(label="💰 House & Treasury", style=discord.ButtonStyle.primary, row=0)
    async def balances(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await _send_balance_scan(interaction)

    @discord.ui.button(label="📊 Wallet Stats", style=discord.ButtonStyle.secondary, row=1)
    async def wallets(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(embed=_build_admin_embed("wallets"), view=CryptoWalletView())

    @discord.ui.button(label="💸 Sweep & Withdraw", style=discord.ButtonStyle.secondary, row=1)
    async def sweep(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(embed=_build_admin_embed("sweep"), view=CryptoSweepView())

    @discord.ui.button(label="⬅️ Back to Admin", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        from cogs.admin_panel import AdminPanelView, _build_admin_panel_embed
        await interaction.response.edit_message(
            embed=_build_admin_panel_embed(interaction),
            view=AdminPanelView(interaction.user.id),
        )


class CryptoWalletView(discord.ui.View):
    """Wallet sub-section — chain toggles, min deposit, emojis, log channel."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="🟣 Toggle SOL", style=discord.ButtonStyle.primary, row=0)
    async def toggle_sol(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings(); s["sol_enabled"] = not s.get("sol_enabled", True); engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("wallets"), view=CryptoWalletView())

    @discord.ui.button(label="🔘 Toggle LTC", style=discord.ButtonStyle.primary, row=0)
    async def toggle_ltc(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings(); s["ltc_enabled"] = not s.get("ltc_enabled", True); engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("wallets"), view=CryptoWalletView())

    @discord.ui.button(label="🔷 Toggle ETH", style=discord.ButtonStyle.primary, row=0)
    async def toggle_eth(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings()
        s["eth_enabled"] = not s.get("eth_enabled", True)
        engine.save_settings(s)
        if s["eth_enabled"]:
            n = await asyncio.get_event_loop().run_in_executor(None, engine.backfill_eth_wallets)
            if n:
                print(f"[CryptoDeposit] ETH enabled — backfilled {n} wallet(s)")
        await interaction.response.edit_message(embed=_build_admin_embed("wallets"), view=CryptoWalletView())

    @discord.ui.button(label="💲 Min Deposit", style=discord.ButtonStyle.secondary, row=1)
    async def min_dep(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings()
        await interaction.response.send_modal(CryptoMinDepositModal(float(s.get("min_deposit_usd", 1.0))))

    @discord.ui.button(label="🎨 Chain Emojis", style=discord.ButtonStyle.secondary, row=1)
    async def emojis(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings()
        await interaction.response.send_modal(CryptoEmojiModal(
            current_sol=s.get("sol_emoji", "🟣"),
            current_ltc=s.get("ltc_emoji", "🔘"),
            current_eth=s.get("eth_emoji", "🔷"),
        ))

    @discord.ui.button(label="🟢 Deposit Log Channel", style=discord.ButtonStyle.secondary, row=1)
    async def dep_log(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "**Set Deposit Log Channel** 🟢\nSelect the channel where confirmed deposits will be logged.",
            ephemeral=True,
            view=_LogChannelPickerView("deposit_log_channel_id"),
        )

    @discord.ui.button(label="⬅️ Overview", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(embed=_build_admin_embed("overview"), view=CryptoAdminView())


class CryptoSweepView(discord.ui.View):
    """Sweep sub-section — toggle, addresses, log channels, manual sweep."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="⚡ Toggle Auto-Sweep", style=discord.ButtonStyle.primary, row=0)
    async def toggle_sweep(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings(); s["auto_sweep"] = not s.get("auto_sweep", False); engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("sweep"), view=CryptoSweepView())

    @discord.ui.button(label="📍 Set Sweep Addresses", style=discord.ButtonStyle.secondary, row=0)
    async def set_addrs(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings()
        await interaction.response.send_modal(SweepAddressModal(
            current_sol=s.get("sol_sweep_address", ""),
            current_ltc=s.get("ltc_sweep_address", ""),
            current_eth=s.get("eth_sweep_address", ""),
        ))

    @discord.ui.button(label="🏦 Use Treasury as Sweep", style=discord.ButtonStyle.primary, row=0)
    async def use_treasury_as_sweep(self, interaction: discord.Interaction, _: discord.ui.Button):
        if not engine.TREASURY_MNEMONIC:
            return await interaction.response.send_message(
                "❌ `TREASURY_MNEMONIC` is not set in `.env`.", ephemeral=True
            )
        try:
            sol_treasury = engine.get_treasury_address("SOL")
            ltc_treasury = engine.get_treasury_address("LTC")
            eth_treasury = engine.get_treasury_address("ETH")
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Could not derive treasury address: {e}", ephemeral=True
            )
        s = engine.get_settings()
        s["sol_sweep_address"] = sol_treasury
        s["ltc_sweep_address"] = ltc_treasury
        s["eth_sweep_address"] = eth_treasury
        engine.save_settings(s)
        await interaction.response.edit_message(embed=_build_admin_embed("sweep"), view=CryptoSweepView())

    @discord.ui.button(label="💰 Treasury Balance", style=discord.ButtonStyle.secondary, row=1)
    async def treasury_bal(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not engine.TREASURY_MNEMONIC:
            return await interaction.followup.send(
                "❌ `TREASURY_MNEMONIC` is not set in `.env`.", ephemeral=True
            )
        sol_emoji, ltc_emoji, eth_emoji = _get_emojis()
        rates     = engine.get_rates()
        sol_price = rates.get("sol_usd", 0)
        ltc_price = rates.get("ltc_usd", 0)
        eth_price = rates.get("eth_usd", 0)
        try:
            t_sol_addr = engine.get_treasury_address("SOL")
        except Exception:
            t_sol_addr = ""
        try:
            t_ltc_addr = engine.get_treasury_address("LTC")
        except Exception:
            t_ltc_addr = ""
        try:
            t_eth_addr = engine.get_treasury_address("ETH")
        except Exception:
            t_eth_addr = ""

        async def _fetch(fn, addr):
            if not addr:
                return 0
            try:
                val = await asyncio.get_event_loop().run_in_executor(None, fn, addr)
                return max(0, val)
            except Exception:
                return 0

        t_sol_lam = await _fetch(engine.sol_balance, t_sol_addr)
        t_ltc_sat = await _fetch(engine.ltc_balance, t_ltc_addr)
        t_eth_wei = await _fetch(engine.eth_balance, t_eth_addr)
        t_sol = t_sol_lam / 1e9
        t_ltc = t_ltc_sat / 1e8
        t_eth = t_eth_wei / 1e18
        t_usd = t_sol * sol_price + t_ltc * ltc_price + t_eth * eth_price

        embed = discord.Embed(title="🏦  Treasury Balance", color=0x9945FF, timestamp=discord.utils.utcnow())
        embed.add_field(
            name="Balances",
            value=(
                f"{sol_emoji} `{t_sol:.6f} SOL`  ≈ ${t_sol * sol_price:.2f}\n"
                f"{ltc_emoji} `{t_ltc:.8f} LTC`  ≈ ${t_ltc * ltc_price:.2f}\n"
                f"{eth_emoji} `{t_eth:.8f} ETH`  ≈ ${t_eth * eth_price:.2f}\n"
                f"**Total ≈ ${t_usd:.2f} USD**"
            ),
            inline=False,
        )
        if t_sol_addr:
            embed.add_field(name="SOL Address", value=f"`{t_sol_addr}`", inline=False)
        if t_ltc_addr:
            embed.add_field(name="LTC Address", value=f"`{t_ltc_addr}`", inline=False)
        if t_eth_addr:
            embed.add_field(name="ETH Address", value=f"`{t_eth_addr}`", inline=False)
        embed.set_footer(text="Vegas Casino  ·  Live on-chain data")
        addr_view = _AddressCopyView([
            ("📋 Copy SOL Address", t_sol_addr, discord.ButtonStyle.secondary, 0),
            ("📋 Copy LTC Address", t_ltc_addr, discord.ButtonStyle.secondary, 0),
            ("📋 Copy ETH Address", t_eth_addr, discord.ButtonStyle.secondary, 0),
        ])
        await interaction.followup.send(embed=embed, view=addr_view, ephemeral=True)

    @discord.ui.button(label="🚀 Sweep Now!", style=discord.ButtonStyle.danger, row=1)
    async def sweep_now(self, interaction: discord.Interaction, _: discord.ui.Button):
        s = engine.get_settings()
        sol_addr = s.get("sol_sweep_address", "")
        ltc_addr = s.get("ltc_sweep_address", "")
        eth_addr = s.get("eth_sweep_address", "")
        if not sol_addr and not ltc_addr and not eth_addr:
            await interaction.response.send_message(
                "❌ No sweep addresses configured. Set them first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await _do_manual_sweep(interaction, s, sol_addr, ltc_addr, eth_addr)

    @discord.ui.button(label="📢 Sweep Log Channel", style=discord.ButtonStyle.secondary, row=2)
    async def sweep_log_ch(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "**Set Sweep Log Channel** 📢\nSelect the channel where auto-sweep events will be logged.",
            ephemeral=True,
            view=_LogChannelPickerView("sweep_log_channel_id"),
        )

    @discord.ui.button(label="📝 Withdraw Log", style=discord.ButtonStyle.secondary, row=2)
    async def withdraw_log_ch(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "**Set Withdraw Log Channel** 📝\nPublic on-chain payout feed (Components V2). "
            "Approve/reject is not posted here.",
            ephemeral=True,
            view=_LogChannelPickerView("withdraw_log_channel_id"),
        )

    @discord.ui.button(label="✅ Withdraw Approval", style=discord.ButtonStyle.secondary, row=2)
    async def withdraw_approval_ch(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_message(
            "**Set Withdraw Approval Channel** ✅\nStaff channel for pending withdrawal "
            "approve/reject buttons.",
            ephemeral=True,
            view=_LogChannelPickerView("withdraw_approval_channel_id"),
        )

    @discord.ui.button(label="⬅️ Overview", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.edit_message(embed=_build_admin_embed("overview"), view=CryptoAdminView())


class _LogChannelPickerView(discord.ui.View):
    """Ephemeral helper: channel select that saves to a given settings key."""
    def __init__(self, settings_key: str = "sweep_log_channel_id"):
        super().__init__(timeout=120)
        self.settings_key = settings_key
        select = discord.ui.ChannelSelect(
            placeholder="Select a channel…",
            channel_types=[discord.ChannelType.text],
            row=0,
        )
        select.callback = self._pick_channel
        self.add_item(select)

    async def _pick_channel(self, interaction: discord.Interaction):
        channel_id = int(interaction.data["values"][0])
        s = engine.get_settings()
        s[self.settings_key] = channel_id
        engine.save_settings(s)
        ch = interaction.guild.get_channel(channel_id) if interaction.guild else None
        mention = f"<#{channel_id}>" if ch is None else ch.mention
        await interaction.response.edit_message(
            content=f"✅ Channel set to {mention}.",
            view=None,
        )


async def _do_manual_sweep(
    interaction: discord.Interaction, s: dict, sol_addr: str, ltc_addr: str, eth_addr: str = "",
):
    """Sweep all HD wallets to the main address immediately and report results."""
    import asyncio
    wallets = get_data("server/crypto_wallets") or {}
    sol_prefetch = [
        w["sol"]["address"] for w in wallets.values() if w.get("sol", {}).get("address")
    ]
    if sol_prefetch:
        await asyncio.get_event_loop().run_in_executor(
            None, engine.sol_prefetch_balances, sol_prefetch
        )
    eth_prefetch = [
        w["eth"]["address"] for w in wallets.values() if w.get("eth", {}).get("address")
    ]
    if eth_prefetch:
        await asyncio.get_event_loop().run_in_executor(
            None, engine.eth_prefetch_balances, eth_prefetch
        )
    rates   = engine.get_rates()
    sol_price = rates.get("sol_usd", 0)
    ltc_price = rates.get("ltc_usd", 0)
    eth_price = rates.get("eth_usd", 0)

    sol_swept = 0.0
    ltc_swept = 0.0
    eth_swept = 0.0
    sol_txs: list[str] = []
    ltc_txs: list[str] = []
    eth_txs: list[str] = []
    errors:  list[str] = []

    skipped = 0
    for uid, w in wallets.items():
        idx = w.get("index", 0)

        # SOL
        if sol_addr and "sol" in w:
            try:
                bal = engine.sol_balance(w["sol"]["address"])
                if bal > engine.SOL_FEE_LAMPORTS:
                    sig = await asyncio.get_event_loop().run_in_executor(None, engine.sweep_sol, idx, bal, sol_addr)
                    if sig:
                        sol_swept += (bal - engine.SOL_FEE_LAMPORTS) / 1e9
                        sol_txs.append(sig)
                        # Update stored balance so deposit checker doesn't re-credit
                        w["sol"]["last_balance"] = 0
                        wallets[uid] = w
            except Exception as e:
                errors.append(f"SOL uid={uid}: {e}")

        # LTC
        if ltc_addr and "ltc" in w:
            try:
                live_ltc = engine.ltc_balance(w["ltc"]["address"])
                if live_ltc > engine.LTC_FEE_SATOSHIS:
                    txid = await asyncio.get_event_loop().run_in_executor(None, engine.sweep_ltc, idx, ltc_addr)
                    if txid:
                        ltc_swept += max(0, (live_ltc - engine.LTC_FEE_SATOSHIS)) / 1e8
                        ltc_txs.append(txid)
                        w["ltc"]["last_balance"] = 0
                        wallets[uid] = w
            except Exception as e:
                errors.append(f"LTC uid={uid}: {e}")

        # ETH
        if eth_addr and "eth" in w:
            try:
                bal = engine.eth_balance(w["eth"]["address"])
                fee_wei = engine.eth_transfer_fee_wei()
                if bal > fee_wei:
                    tx_hash = await asyncio.get_event_loop().run_in_executor(
                        None, engine.sweep_eth, idx, bal, eth_addr
                    )
                    if tx_hash:
                        eth_swept += (bal - fee_wei) / 1e18
                        eth_txs.append(tx_hash)
                        w["eth"]["last_balance"] = 0
                        wallets[uid] = w
            except Exception as e:
                errors.append(f"ETH uid={uid}: {e}")

    # Persist updated last_balance values
    if sol_swept > 0 or ltc_swept > 0 or eth_swept > 0:
        from modules.database import replace_data
        replace_data("server/crypto_wallets", wallets)

    sol_emoji, ltc_emoji, eth_emoji = _get_emojis()
    lines: list[str] = []

    if sol_swept > 0:
        lines.append(f"{sol_emoji} **{sol_swept:.6f} SOL** swept  (~${sol_swept * sol_price:,.2f})  — {len(sol_txs)} tx(s)")
    if ltc_swept > 0:
        lines.append(f"{ltc_emoji} **{ltc_swept:.8f} LTC** swept  (~${ltc_swept * ltc_price:,.2f})  — {len(ltc_txs)} tx(s)")
    if eth_swept > 0:
        lines.append(f"{eth_emoji} **{eth_swept:.8f} ETH** swept  (~${eth_swept * eth_price:,.2f})  — {len(eth_txs)} tx(s)")
    if not lines:
        lines.append(f"No wallets had a balance above the network fee.{f' ({skipped} wallet(s) skipped — dust balance)' if skipped else ''}")
    if errors:
        lines.append("\n⚠️ **Errors:**\n" + "\n".join(f"• `{e}`" for e in errors))

    embed = discord.Embed(
        title="💸  Manual Sweep Complete",
        description="\n".join(lines),
        color=discord.Color.green() if (sol_swept > 0 or ltc_swept > 0 or eth_swept > 0) else discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    if sol_addr:
        embed.add_field(name="SOL Destination", value=f"`{sol_addr}`", inline=False)
    if ltc_addr:
        embed.add_field(name="LTC Destination", value=f"`{ltc_addr}`", inline=False)
    if eth_addr:
        embed.add_field(name="ETH Destination", value=f"`{eth_addr}`", inline=False)
    embed.set_footer(text="Vegas Casino  ·  Manual Sweep")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _send_balance_scan(interaction: discord.Interaction):
    """Fetch house + treasury on-chain balances and send as ephemeral embed."""
    try:
        data = await asyncio.get_event_loop().run_in_executor(
            None, engine.get_house_and_treasury_balances
        )
        rates     = engine.get_rates()
        sol_price = rates.get("sol_usd", 0)
        ltc_price = rates.get("ltc_usd", 0)
        eth_price = rates.get("eth_usd", 0)
        sol_emoji, ltc_emoji, eth_emoji = _get_emojis()

        h_sol = data["house_sol_lamports"] / 1e9
        h_ltc = data["house_ltc_satoshis"] / 1e8
        h_eth = data.get("house_eth_wei", 0) / 1e18
        t_sol = data["treasury_sol_lamports"] / 1e9
        t_ltc = data["treasury_ltc_satoshis"] / 1e8
        t_eth = data.get("treasury_eth_wei", 0) / 1e18

        h_usd = h_sol * sol_price + h_ltc * ltc_price + h_eth * eth_price
        t_usd = t_sol * sol_price + t_ltc * ltc_price + t_eth * eth_price
        total_usd = h_usd + t_usd

        embed = discord.Embed(
            title="💰  House & Treasury Balances",
            color=0x9945FF,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="🏠  House Wallet  (Sweep Address)",
            value=(
                f"{sol_emoji} `{h_sol:.6f} SOL`  ≈ ${h_sol * sol_price:,.2f}\n"
                f"{ltc_emoji} `{h_ltc:.8f} LTC`  ≈ ${h_ltc * ltc_price:,.2f}\n"
                f"{eth_emoji} `{h_eth:.8f} ETH`  ≈ ${h_eth * eth_price:,.2f}\n"
                f"**≈ ${h_usd:,.2f} USD**"
            ),
            inline=False,
        )
        embed.add_field(
            name="🏦  Treasury Wallet  (TREASURY_MNEMONIC)",
            value=(
                f"{sol_emoji} `{t_sol:.6f} SOL`  ≈ ${t_sol * sol_price:,.2f}\n"
                f"{ltc_emoji} `{t_ltc:.8f} LTC`  ≈ ${t_ltc * ltc_price:,.2f}\n"
                f"{eth_emoji} `{t_eth:.8f} ETH`  ≈ ${t_eth * eth_price:,.2f}\n"
                f"**≈ ${t_usd:,.2f} USD**"
                if engine.TREASURY_MNEMONIC else "*TREASURY_MNEMONIC not set*"
            ),
            inline=False,
        )
        embed.add_field(
            name="💵  Combined",
            value=f"**≈ ${total_usd:,.2f} USD**",
            inline=False,
        )
        if data["house_sol_address"]:
            embed.add_field(name="House SOL", value=f"`{data['house_sol_address']}`", inline=True)
        if data["house_ltc_address"]:
            embed.add_field(name="House LTC", value=f"`{data['house_ltc_address']}`", inline=True)
        if data.get("treasury_sol_address"):
            embed.add_field(name="Treasury SOL", value=f"`{data['treasury_sol_address']}`", inline=True)
        if data.get("treasury_ltc_address"):
            embed.add_field(name="Treasury LTC", value=f"`{data['treasury_ltc_address']}`", inline=True)
        if data.get("house_eth_address"):
            embed.add_field(name="House ETH", value=f"`{data['house_eth_address']}`", inline=True)
        if data.get("treasury_eth_address"):
            embed.add_field(name="Treasury ETH", value=f"`{data['treasury_eth_address']}`", inline=True)
        embed.set_footer(text=f"SOL ${sol_price:,.2f}  ·  LTC ${ltc_price:,.2f}  ·  ETH ${eth_price:,.2f}")
        addr_view = _AddressCopyView([
            ("📋 Copy House SOL",     data.get("house_sol_address", ""),     discord.ButtonStyle.secondary, 0),
            ("📋 Copy House LTC",     data.get("house_ltc_address", ""),     discord.ButtonStyle.secondary, 0),
            ("📋 Copy House ETH",     data.get("house_eth_address", ""),     discord.ButtonStyle.secondary, 0),
            ("📋 Copy Treasury SOL", data.get("treasury_sol_address", ""), discord.ButtonStyle.primary,   1),
            ("📋 Copy Treasury LTC", data.get("treasury_ltc_address", ""), discord.ButtonStyle.primary,   1),
            ("📋 Copy Treasury ETH", data.get("treasury_eth_address", ""), discord.ButtonStyle.primary,   1),
        ])
        await interaction.followup.send(embed=embed, view=addr_view, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(title="❌ Scan Failed", description=str(e), color=discord.Color.red()),
            ephemeral=True,
        )




# ── User deposit flow ──────────────────────────────────────────────────────────

async def start_crypto_deposit_flow(
    interaction: discord.Interaction,
    user_id: int,
    lang: str,
) -> None:
    """Bonus picker (if any) → deposit address embed."""
    from cogs.deposit_bonus_ui import show_bonus_picker_or_skip

    async def _show_addresses(inter: discord.Interaction, _bonus_id: str | None):
        from modules.ui_v2 import send_ephemeral

        view, ok, _sol, _ltc = _build_deposit_layout(user_id)
        await send_ephemeral(inter, view)

    await show_bonus_picker_or_skip(
        interaction,
        user_id,
        lang,
        _show_addresses,
        title_key="bonus.picker_crypto_title",
        description_key="bonus.picker_crypto_description",
    )


# ── User deposit view ──────────────────────────────────────────────────────────

class CryptoDepositView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        sol_address: str = "",
        ltc_address: str = "",
        eth_address: str = "",
    ):
        super().__init__(timeout=120)
        self.user_id     = user_id
        self.sol_address = sol_address
        self.ltc_address = ltc_address
        self.eth_address = eth_address

        if sol_address:
            self.add_item(_CopyButton("◎ Copy SOL Address", sol_address, discord.ButtonStyle.primary, row=0))
        if ltc_address:
            self.add_item(_CopyButton("Ł Copy LTC Address", ltc_address, discord.ButtonStyle.primary, row=0))
        if eth_address:
            self.add_item(_CopyButton("Ξ Copy ETH Address", eth_address, discord.ButtonStyle.primary, row=0))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This panel is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="🔄 Check Now", style=discord.ButtonStyle.success, row=1)
    async def check_now(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            credited = engine.check_user_deposits(self.user_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Check failed: `{e}`", ephemeral=True)
            return

        if credited:
            cog = interaction.client.get_cog("CryptoDeposit")
            if cog:
                member = interaction.user if isinstance(interaction.user, discord.Member) else None
                await cog._notify_user(self.user_id, credited, member=member)

            lines = []
            for dep in credited:
                lines.append(
                    f"{dep['symbol']} **{dep['amount_crypto']} {dep['chain']}** "
                    f"(${dep['amount_usd']:.2f}) → **+{format_balance(dep['coins'], 'real')}** credited!"
                )
            await interaction.followup.send(
                embed=discord.Embed(
                    title="✅ Deposit Detected!",
                    description="\n".join(lines),
                    color=discord.Color.green(),
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "No new deposits found yet. Transactions can take a few minutes to confirm.",
                ephemeral=True,
            )

    @discord.ui.button(label="🔁 Refresh Rates", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button):
        embed, ok, sol_addr, ltc_addr, eth_addr = _build_deposit_embed(self.user_id)
        sol = sol_addr or self.sol_address
        ltc = ltc_addr or self.ltc_address
        eth = eth_addr or self.eth_address
        view = CryptoDepositView(self.user_id, sol, ltc, eth) if ok else None
        await interaction.response.edit_message(embed=embed, view=view)


# ── Cog ────────────────────────────────────────────────────────────────────────

# ── Cog ────────────────────────────────────────────────────────────────────────

class CryptoDeposit(commands.Cog):
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._monitor_task.start()

    async def cog_load(self):
        try:
            n = await asyncio.get_event_loop().run_in_executor(None, engine.backfill_eth_wallets)
            if n:
                print(f"[CryptoDeposit] Backfilled ETH addresses for {n} existing wallet(s)")
        except ImportError as e:
            print(f"[CryptoDeposit] ETH backfill skipped: {e}")
        except Exception as e:
            print(f"[CryptoDeposit] ETH backfill on load: {e}")

    def cog_unload(self):
        self._monitor_task.cancel()

    @tasks.loop(seconds=300)
    async def _monitor_task(self):
        """Check active deposit addresses every 5 minutes, then run sweep pass."""
        try:
            active_uids = engine.get_active_user_ids()

            # Pre-fetch ALL wallet LTC balances in a single sochain pass
            # (covers both active monitoring + sweep_all, so no individual API calls needed)
            all_wallets = get_data("server/crypto_wallets") or {}
            ltc_addrs = [
                w["ltc"]["address"]
                for w in all_wallets.values()
                if "ltc" in w and w["ltc"].get("address")
            ]
            if ltc_addrs:
                await asyncio.get_event_loop().run_in_executor(
                    None, engine.ltc_prefetch_balances, ltc_addrs
                )

            sol_addrs = [
                all_wallets[uid]["sol"]["address"]
                for uid in active_uids
                if uid in all_wallets
                and all_wallets[uid].get("sol", {}).get("address")
            ]
            if sol_addrs:
                await asyncio.get_event_loop().run_in_executor(
                    None, engine.sol_prefetch_balances, sol_addrs
                )

            eth_addrs = [
                all_wallets[uid]["eth"]["address"]
                for uid in active_uids
                if uid in all_wallets
                and all_wallets[uid].get("eth", {}).get("address")
            ]
            if eth_addrs:
                await asyncio.get_event_loop().run_in_executor(
                    None, engine.eth_prefetch_balances, eth_addrs
                )

            for uid in active_uids:
                try:
                    credited = engine.check_user_deposits(int(uid))
                    if credited:
                        member = self.bot.get_user(int(uid))
                        await self._notify_user(int(uid), credited, member=member)
                except Exception as e:
                    print(f"[CryptoDeposit] Error checking {uid}: {e}")
        except Exception as e:
            print(f"[CryptoDeposit] Monitor error: {e}")

        # Independent sweep pass: prefetch all SOL once (sweep_all uses cache).
        try:
            all_sol = [
                w["sol"]["address"]
                for w in all_wallets.values()
                if w.get("sol", {}).get("address")
            ]
            if all_sol:
                await asyncio.get_event_loop().run_in_executor(
                    None, engine.sol_prefetch_balances, all_sol
                )
        except Exception as e:
            print(f"[CryptoDeposit] SOL prefetch error: {e}")

        try:
            all_eth = [
                w["eth"]["address"]
                for w in all_wallets.values()
                if w.get("eth", {}).get("address")
            ]
            if all_eth:
                await asyncio.get_event_loop().run_in_executor(
                    None, engine.eth_prefetch_balances, all_eth
                )
        except Exception as e:
            print(f"[CryptoDeposit] ETH prefetch error: {e}")

        # Independent sweep pass: catches balances that were not swept during
        # deposit detection (e.g. auto_sweep was disabled when deposit arrived).
        try:
            await asyncio.get_event_loop().run_in_executor(None, engine.sweep_all_positive_wallets)
        except Exception as e:
            print(f"[CryptoDeposit] SweepAll error: {e}")

        # Post any queued sweep logs
        try:
            await self._post_sweep_logs()
        except Exception as e:
            print(f"[CryptoDeposit] Sweep log error: {e}")

    @_monitor_task.before_loop
    async def _before_monitor(self):
        await self.bot.wait_until_ready()

    async def _notify_user(
        self,
        user_id: int,
        credited: list[dict],
        *,
        member: discord.Member | discord.User | None = None,
    ):
        """DM the user and post public feed logs."""
        from modules.crypto_onchain_logs import post_deposit_feed_logs

        lines = []
        for dep in credited:
            emoji = _chain_emoji(dep["chain"])
            lines.append(
                f"{emoji} **{dep['amount_crypto']} {dep['chain']}** "
                f"(~${dep['amount_usd']:.2f} USD) → **+{format_balance(dep['coins'], 'real')}** credited!"
            )

        user = member or self.bot.get_user(user_id)
        if user:
            try:
                await user.send(embed=discord.Embed(
                    title="💰  Crypto Deposit Received!",
                    description="\n".join(lines),
                    color=discord.Color.green(),
                ).set_footer(text="Vegas Casino  ·  Your balance has been updated."))
            except discord.Forbidden:
                pass

        await post_deposit_feed_logs(self.bot, user_id, credited, member=user)

    async def _post_sweep_logs(self):
        """Post queued sweep log entries to the configured log channel."""
        logs = engine.pop_sweep_logs()
        if not logs:
            return
        settings = engine.get_settings()
        log_channel_id = settings.get("sweep_log_channel_id")
        if not log_channel_id:
            return
        channel = self.bot.get_channel(int(log_channel_id))
        if not channel:
            return
        for log in logs:
            chain   = log["chain"]
            amount  = log["amount"]
            to_addr = log["to_address"]
            tx_id   = log["tx_id"]
            emoji   = _chain_emoji(chain)
            unit    = chain
            rates   = engine.get_rates()
            price   = rates.get(
                {"SOL": "sol_usd", "LTC": "ltc_usd", "ETH": "eth_usd"}.get(chain, "sol_usd"), 0
            )
            usd_val = amount * price

            embed = discord.Embed(
                title=f"💸  Auto-Sweep — {emoji} {chain}",
                color=0x9945FF,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Amount Swept", value=f"`{amount} {unit}` (~${usd_val:,.2f})", inline=True)
            embed.add_field(name="Destination",  value=f"`{to_addr}`", inline=False)
            embed.add_field(name="TX / Signature", value=f"`{tx_id}`", inline=False)
            embed.set_footer(text="Vegas Casino  ·  Auto-Sweep")
            try:
                await channel.send(embed=embed)
            except Exception:
                pass


async def setup(bot: discord.Client):
    await bot.add_cog(CryptoDeposit(bot))
