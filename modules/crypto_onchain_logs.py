"""Swoosh-style public deposit / payout feed (Components V2)."""

from __future__ import annotations

import discord
from discord import ui

from modules.crypto_deposit import get_settings as get_crypto_settings
from modules.ui_v2 import panel_with_controls, send_channel_v2

ONCHAIN_ACCENT = 0x9B59B6


def _short_tx(tx_id: str, left: int = 10, right: int = 6) -> str:
    tx_id = (tx_id or "").strip()
    if len(tx_id) <= left + right + 3:
        return tx_id
    return f"{tx_id[:left]}...{tx_id[-right:]}"


def explorer_tx_url(chain: str, tx_id: str) -> str | None:
    tx_id = (tx_id or "").strip()
    if not tx_id:
        return None
    c = (chain or "").upper()
    if c == "SOL":
        return f"https://solscan.io/tx/{tx_id}"
    if c == "LTC":
        return f"https://live.blockcypher.com/ltc/tx/{tx_id}/"
    if c == "ETH":
        h = tx_id if tx_id.startswith("0x") else f"0x{tx_id}"
        return f"https://etherscan.io/tx/{h}"
    return None


def _crypto_amount_line(amount_crypto: float, chain: str, tx_id: str | None) -> str:
    c = (chain or "").upper()
    sym = {"SOL": "SOL", "LTC": "LTC", "ETH": "ETH"}.get(c, c)
    amt = f"{float(amount_crypto):.6f}".rstrip("0").rstrip(".")
    if tx_id:
        return f"{amt} {sym} · `{_short_tx(tx_id)}`"
    return f"{amt} {sym}"


def _deposit_footer() -> str:
    return "✦ on-chain deposit ✦"


def _payout_footer() -> str:
    vouches = (get_crypto_settings().get("vouches_channel_name") or "#vouches").strip()
    if not vouches.startswith("#"):
        vouches = f"#{vouches}"
    return f"✦ real on-chain payout ✦ · happy? vouch in {vouches}"


def _feed_display_name(
    user_id: int,
    member: discord.Member | discord.User | None,
    bot: discord.Client | None,
) -> str:
    if member is not None:
        return member.display_name or member.name or str(user_id)
    if bot is not None:
        user = bot.get_user(user_id)
        if user is not None:
            return user.display_name or user.name or str(user_id)
    return str(user_id)


def _explorer_button(url: str | None) -> ui.Button | None:
    if not url:
        return None
    return ui.Button(
        label="View on explorer",
        style=discord.ButtonStyle.link,
        url=url,
        emoji="🔗",
    )


def build_deposit_feed_layout(
    *,
    display_name: str,
    amount_usd: float,
    amount_crypto: float,
    chain: str,
    tx_id: str | None = None,
) -> ui.LayoutView:
    body = (
        f"**{display_name}** deposited **${amount_usd:.2f}**\n"
        f"{_crypto_amount_line(amount_crypto, chain, tx_id)}"
    )
    ctrl = _explorer_button(explorer_tx_url(chain, tx_id or ""))
    return panel_with_controls(
        title="Deposit Received",
        body=body,
        footer=_deposit_footer(),
        emoji="⬇️",
        accent=ONCHAIN_ACCENT,
        controls=[ctrl] if ctrl else (),
    )


def build_payout_feed_layout(
    *,
    display_name: str,
    amount_usd: float,
    amount_crypto: float,
    chain: str,
    tx_id: str | None = None,
) -> ui.LayoutView:
    body = (
        f"**{display_name}** withdrew **${amount_usd:.2f}**\n"
        f"{_crypto_amount_line(amount_crypto, chain, tx_id)}"
    )
    ctrl = _explorer_button(explorer_tx_url(chain, tx_id or ""))
    return panel_with_controls(
        title="Payout Sent",
        body=body,
        footer=_payout_footer(),
        emoji="⬆️",
        accent=ONCHAIN_ACCENT,
        controls=[ctrl] if ctrl else (),
    )


async def _resolve_channel(bot: discord.Client, channel_id) -> discord.TextChannel | None:
    if not channel_id:
        return None
    ch = bot.get_channel(int(channel_id))
    if isinstance(ch, discord.TextChannel):
        return ch
    try:
        ch = await bot.fetch_channel(int(channel_id))
        return ch if isinstance(ch, discord.TextChannel) else None
    except Exception:
        return None


async def post_deposit_feed_logs(
    bot: discord.Client,
    user_id: int,
    credited: list[dict],
    *,
    member: discord.Member | discord.User | None = None,
) -> None:
    settings = get_crypto_settings()
    channel = await _resolve_channel(bot, settings.get("deposit_log_channel_id"))
    if not channel:
        return

    name = _feed_display_name(user_id, member, bot)
    for dep in credited:
        chain = dep.get("chain", "CRYPTO")
        layout = build_deposit_feed_layout(
            display_name=name,
            amount_usd=float(dep.get("amount_usd", 0)),
            amount_crypto=float(dep.get("amount_crypto", 0)),
            chain=chain,
            tx_id=dep.get("tx_id"),
        )
        try:
            await send_channel_v2(channel, layout)
        except Exception as e:
            print(f"[CryptoOnchainLog] deposit feed failed: {e}")


async def post_payout_feed_log(
    bot: discord.Client,
    withdrawal: dict,
    *,
    member: discord.Member | discord.User | None = None,
) -> None:
    settings = get_crypto_settings()
    channel = await _resolve_channel(bot, settings.get("withdraw_log_channel_id"))
    if not channel:
        return

    user_id = int(withdrawal.get("user_id", 0))
    chain = withdrawal.get("chain", "CRYPTO")
    tx_id = withdrawal.get("tx_id")
    name = _feed_display_name(user_id, member, bot)

    layout = build_payout_feed_layout(
        display_name=name,
        amount_usd=float(withdrawal.get("amount_usd", 0)),
        amount_crypto=float(withdrawal.get("amount_crypto", 0)),
        chain=chain,
        tx_id=tx_id,
    )
    try:
        await send_channel_v2(channel, layout)
    except Exception as e:
        print(f"[CryptoOnchainLog] payout feed failed: {e}")
