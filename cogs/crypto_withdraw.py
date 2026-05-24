"""Crypto withdrawal — saved addresses, modals, admin approve/reject from log channel."""

from __future__ import annotations

import asyncio
import time
import uuid

import discord
from discord.ext import commands, tasks

import modules.bonus as bonus_engine
import modules.crypto_deposit as engine
from modules.database import check_permission, get_data, get_server_data, get_user_data, replace_data, set_user_data
from modules.player import Player
from modules.utils import format_balance
from modules.translator import t

WITHDRAW_KEY = "server/crypto_withdrawals"
PENDING_TX_KEY = "server/crypto_pending_withdrawals"

_CHAIN_RATE = {"SOL": "sol_usd", "LTC": "ltc_usd", "ETH": "eth_usd"}
_CHAIN_DECIMALS = {"SOL": 6, "LTC": 8, "ETH": 8}


def _chain_emoji(chain: str) -> str:
    s = engine.get_settings()
    key = {"SOL": "sol_emoji", "LTC": "ltc_emoji", "ETH": "eth_emoji"}.get(chain, "")
    defaults = {"SOL": "🟣", "LTC": "🔘", "ETH": "💎"}
    return s.get(key, defaults.get(chain, "💰")) if key else defaults.get(chain, "💰")


def _get_withdrawals() -> dict:
    return get_data(WITHDRAW_KEY) or {}


def _save_withdrawals(d: dict) -> None:
    replace_data(WITHDRAW_KEY, d)


def _get_pending_txs() -> dict:
    return get_data(PENDING_TX_KEY) or {}


def _save_pending_txs(d: dict) -> None:
    replace_data(PENDING_TX_KEY, d)


def _get_user_addresses(user_id: int) -> dict:
    data = get_user_data(user_id, "crypto_addresses") or {}
    return {
        "sol": list(data.get("sol") or []),
        "ltc": list(data.get("ltc") or []),
        "eth": list(data.get("eth") or []),
    }


def _save_user_addresses(user_id: int, addrs: dict) -> None:
    set_user_data(user_id, "crypto_addresses", addrs)


def _build_approval_embed(w: dict) -> discord.Embed:
    chain = w["chain"]
    emoji = _chain_emoji(chain)
    embed = discord.Embed(
        title=f"💸 Withdrawal Request — {emoji} {chain}",
        color=0xFFA500,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="User", value=f"<@{w['user_id']}> (`{w['user_id']}`)", inline=True)
    embed.add_field(
        name="Amount",
        value=f"`{w['amount_crypto']} {chain}` (~${w['amount_usd']:.2f})",
        inline=True,
    )
    embed.add_field(name="Deducted", value=f"`{format_balance(w['amount_coins'], 'real')}`", inline=True)
    embed.add_field(name="Destination", value=f"```{w['address']}```", inline=False)
    embed.add_field(name="ID", value=f"`{w['id']}`", inline=True)
    embed.set_footer(text="VegasBet · Approve or Reject below")
    return embed


def _staff_can_approve(user_id: int) -> bool:
    return not check_permission(user_id, "admin") or not check_permission(user_id, "cashier")


async def _process_withdrawal(
    interaction: discord.Interaction,
    chain: str,
    address: str,
    user_id: int,
    usd_raw: str,
) -> None:
    try:
        usd = float(usd_raw.replace(",", "."))
        if usd <= 0:
            raise ValueError
    except ValueError:
        return await interaction.response.send_message(
            "❌ Enter a valid positive USD amount.", ephemeral=True,
        )

    s = engine.get_settings()
    min_usd = float(s.get("min_deposit_usd", 1.0))
    if usd < min_usd:
        return await interaction.response.send_message(
            f"❌ Minimum withdrawal is **${min_usd:.2f} USD**.", ephemeral=True,
        )

    rates = engine.get_rates()
    rate_key = _CHAIN_RATE.get(chain)
    price = rates.get(rate_key, 0) if rate_key else 0
    if price <= 0:
        return await interaction.response.send_message(
            "❌ Could not fetch exchange rate. Try again.", ephemeral=True,
        )

    exch = get_data("server/exchange_rates") or {}
    coin_usd = float(exch.get("coin_usd_rate", 0))
    if coin_usd <= 0:
        return await interaction.response.send_message(
            "❌ Server exchange rate not configured.", ephemeral=True,
        )

    coins_needed = int(usd / coin_usd)
    decimals = _CHAIN_DECIMALS.get(chain, 6)
    amount_crypto = round(usd / price, decimals)

    p = Player(user_id)
    bal = p.get_balance("real")

    guild_id = str(interaction.guild_id) if interaction.guild_id else ""
    server_data = get_server_data(guild_id) if guild_id else {}
    min_coins = int(server_data.get("min_withdrawal", 0) or 0)
    if min_coins > 0 and coins_needed < min_coins:
        return await interaction.response.send_message(
            f"❌ Minimum withdrawal is **{format_balance(min_coins, 'real')}** "
            f"(~${min_coins * coin_usd:.2f} USD).",
            ephemeral=True,
        )

    if bal < coins_needed:
        return await interaction.response.send_message(
            f"❌ Insufficient balance.\n"
            f"You have `{format_balance(bal, 'real')}` but need `{format_balance(coins_needed, 'real')}`.",
            ephemeral=True,
        )

    stats = get_user_data(user_id, "stats") or {}
    multiplier = float(server_data.get("withdraw_min_multiplier", 0) or 0)
    if multiplier > 0:
        last_deposit = int(stats.get("last_deposit_amount", 0))
        if last_deposit > 0:
            required_wager = int(last_deposit * multiplier)
            total_wagered = int(stats.get("total_wagered", 0))
            wagered_at_dep = int(stats.get("wagered_at_last_deposit", 0))
            wagered_since = max(0, total_wagered - wagered_at_dep)

            active_bonus = bonus_engine.get_active_bonus(str(user_id))
            if active_bonus:
                required_wager += int(active_bonus.get("wager_requirement", 0))

            wager_remaining = max(0, required_wager - wagered_since)
            if wager_remaining > 0:
                wg_pct = int(wagered_since / required_wager * 100) if required_wager else 0
                return await interaction.response.send_message(
                    embed=discord.Embed(
                        title="🎲 Wager Requirement Not Met",
                        description=(
                            f"You must wager **{format_balance(required_wager, 'real')}** before withdrawing.\n\n"
                            f"Progress: **{format_balance(wagered_since, 'real')}** / "
                            f"**{format_balance(required_wager, 'real')}** ({wg_pct}%)\n"
                            f"Still needed: **{format_balance(wager_remaining, 'real')}**"
                        ),
                        color=0xF59E0B,
                    ),
                    ephemeral=True,
                )

    active_bonus = bonus_engine.get_active_bonus(str(user_id))
    if active_bonus and active_bonus.get("type") == "percentage":
        req = int(active_bonus.get("wager_requirement", 0))
        done = int(active_bonus.get("wagered_so_far", 0))
        if done < req:
            remaining = req - done
            pct = int(done / req * 100) if req else 0
            lang_data = get_user_data(user_id, "lang") or {}
            lang = lang_data.get("language", "en") if isinstance(lang_data, dict) else "en"
            return await interaction.response.send_message(
                embed=discord.Embed(
                    title=t("bonus.wager_not_met_title", lang=lang),
                    description=t(
                        "bonus.wager_not_met_desc",
                        lang=lang,
                        bonus_name=active_bonus.get("bonus_name", "Bonus"),
                        done=format_balance(done, "real"),
                        req=format_balance(req, "real"),
                        pct=pct,
                        remaining=format_balance(remaining, "real"),
                    ),
                    color=0xF59E0B,
                ),
                ephemeral=True,
            )

    p.remove_balance("real", coins_needed)
    p.record_withdraw(coins_needed)
    bonus_engine.complete_bonus_on_withdraw(str(user_id))

    wid = str(uuid.uuid4())[:8].upper()
    w = {
        "id": wid,
        "user_id": user_id,
        "chain": chain,
        "address": address,
        "amount_usd": round(usd, 2),
        "amount_crypto": amount_crypto,
        "amount_coins": coins_needed,
        "status": "pending",
        "tx_id": None,
        "created_at": int(time.time()),
        "log_channel_id": None,
        "log_message_id": None,
    }
    withdrawals = _get_withdrawals()
    withdrawals[wid] = w
    _save_withdrawals(withdrawals)

    emoji = _chain_emoji(chain)
    await interaction.response.send_message(
        embed=discord.Embed(
            title="⏳ Withdrawal Submitted",
            description=(
                f"{emoji} **{amount_crypto} {chain}** (~${usd:.2f})\n"
                f"📍 To: `{address}`\n\n"
                f"Your balance has been deducted. Staff will review your request.\n"
                f"🆔 ID: `{wid}`"
            ),
            color=discord.Color.orange(),
        ),
        ephemeral=True,
    )

    log_ch_id = s.get("withdraw_log_channel_id") or s.get("sweep_log_channel_id")
    if log_ch_id:
        channel = interaction.client.get_channel(int(log_ch_id))
        if channel:
            view = WithdrawApprovalView(wid)
            msg = await channel.send(embed=_build_approval_embed(w), view=view)
            withdrawals = _get_withdrawals()
            if wid in withdrawals:
                withdrawals[wid]["log_channel_id"] = channel.id
                withdrawals[wid]["log_message_id"] = msg.id
                _save_withdrawals(withdrawals)


class WithdrawAmountModal(discord.ui.Modal):
    def __init__(self, chain: str, address: str, user_id: int):
        super().__init__(title=f"Withdraw {chain} — Amount", timeout=300)
        self.chain = chain
        self.address = address
        self.user_id = user_id
        self.amount_input = discord.ui.TextInput(
            label="Amount in USD",
            placeholder="e.g. 10.00",
            required=True,
            max_length=12,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your withdrawal.", ephemeral=True)
        await _process_withdrawal(
            interaction, self.chain, self.address, self.user_id, self.amount_input.value,
        )


class NewAddressModal(discord.ui.Modal):
    def __init__(self, chain: str, user_id: int):
        super().__init__(title=f"Withdraw {chain} — Address & Amount", timeout=300)
        self.chain = chain
        self.user_id = user_id
        self.addr_input = discord.ui.TextInput(
            label=f"Your {chain} withdrawal address",
            placeholder="Destination wallet address",
            required=True,
            max_length=120,
        )
        self.amount_input = discord.ui.TextInput(
            label="Amount in USD",
            placeholder="e.g. 10.00",
            required=True,
            max_length=12,
        )
        self.add_item(self.addr_input)
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your withdrawal.", ephemeral=True)
        addr = self.addr_input.value.strip()
        if not addr:
            return await interaction.response.send_message("❌ Address cannot be empty.", ephemeral=True)

        addrs = _get_user_addresses(self.user_id)
        chain_key = self.chain.lower()
        if addr not in addrs.get(chain_key, []):
            addrs.setdefault(chain_key, []).append(addr)
            _save_user_addresses(self.user_id, addrs)

        await _process_withdrawal(
            interaction, self.chain, addr, self.user_id, self.amount_input.value,
        )


class WithdrawApprovalView(discord.ui.View):
    def __init__(self, withdrawal_id: str):
        super().__init__(timeout=None)
        self.withdrawal_id = withdrawal_id
        approve = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"withdraw_approve_{withdrawal_id}",
        )
        approve.callback = self._approve
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"withdraw_reject_{withdrawal_id}",
        )
        reject.callback = self._reject
        self.add_item(approve)
        self.add_item(reject)

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def _approve(self, interaction: discord.Interaction):
        if not _staff_can_approve(interaction.user.id):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        withdrawals = _get_withdrawals()
        w = withdrawals.get(self.withdrawal_id)
        if not w:
            return await interaction.response.send_message("❌ Withdrawal not found.", ephemeral=True)
        if w["status"] != "pending":
            return await interaction.response.send_message(f"❌ Already **{w['status']}**.", ephemeral=True)

        await interaction.response.defer()

        chain = w["chain"]
        amount_crypto = w["amount_crypto"]
        tx_id = None
        error = None
        loop = asyncio.get_event_loop()
        try:
            if chain == "SOL":
                amount_lamports = int(amount_crypto * 1e9)
                tx_id = await loop.run_in_executor(
                    None, engine.send_sol_from_treasury, w["address"], amount_lamports,
                )
            elif chain == "LTC":
                amount_satoshis = int(amount_crypto * 1e8)
                tx_id = await loop.run_in_executor(
                    None, engine.send_ltc_from_treasury, w["address"], amount_satoshis,
                )
            elif chain == "ETH":
                amount_wei = int(amount_crypto * 1e18)
                tx_id = await loop.run_in_executor(
                    None, engine.send_eth_from_treasury, w["address"], amount_wei,
                )
            else:
                error = f"Unsupported chain: {chain}"
        except Exception as e:
            error = str(e)

        if error or not tx_id:
            Player(w["user_id"]).add_balance("real", w["amount_coins"])
            withdrawals[self.withdrawal_id]["status"] = "failed"
            _save_withdrawals(withdrawals)
            embed = _build_approval_embed(w)
            embed.color = discord.Color.red()
            embed.add_field(
                name="❌ FAILED — Balance Refunded",
                value=f"`{error or 'No TX returned'}`",
                inline=False,
            )
            self._disable_all()
            await interaction.edit_original_response(embed=embed, view=self)
            return

        withdrawals[self.withdrawal_id]["status"] = "approved"
        withdrawals[self.withdrawal_id]["tx_id"] = tx_id
        withdrawals[self.withdrawal_id]["approved_by"] = interaction.user.id
        _save_withdrawals(withdrawals)

        pending = _get_pending_txs()
        pending[self.withdrawal_id] = {
            "chain": chain,
            "tx_id": tx_id,
            "user_id": w["user_id"],
        }
        _save_pending_txs(pending)

        embed = _build_approval_embed(w)
        embed.color = discord.Color.green()
        embed.add_field(
            name="✅ APPROVED",
            value=f"By <@{interaction.user.id}>\n🔗 TX: `{tx_id}`",
            inline=False,
        )
        self._disable_all()
        await interaction.edit_original_response(embed=embed, view=self)

        emoji = _chain_emoji(chain)
        user = interaction.client.get_user(int(w["user_id"]))
        if user:
            try:
                await user.send(embed=discord.Embed(
                    title=f"✅ Withdrawal Approved — {emoji} {chain}",
                    description=(
                        f"**{amount_crypto} {chain}** (~${w['amount_usd']:.2f}) approved.\n\n"
                        f"📍 `{w['address']}`\n🔗 TX: `{tx_id}`"
                    ),
                    color=discord.Color.green(),
                ))
            except discord.Forbidden:
                pass

    async def _reject(self, interaction: discord.Interaction):
        if not _staff_can_approve(interaction.user.id):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        withdrawals = _get_withdrawals()
        w = withdrawals.get(self.withdrawal_id)
        if not w:
            return await interaction.response.send_message("❌ Withdrawal not found.", ephemeral=True)
        if w["status"] != "pending":
            return await interaction.response.send_message(f"❌ Already **{w['status']}**.", ephemeral=True)

        Player(w["user_id"]).add_balance("real", w["amount_coins"])
        withdrawals[self.withdrawal_id]["status"] = "rejected"
        withdrawals[self.withdrawal_id]["rejected_by"] = interaction.user.id
        _save_withdrawals(withdrawals)

        embed = _build_approval_embed(w)
        embed.color = discord.Color.red()
        embed.add_field(
            name="❌ REJECTED — Balance Refunded",
            value=f"By <@{interaction.user.id}>",
            inline=False,
        )
        self._disable_all()
        await interaction.response.edit_message(embed=embed, view=self)

        emoji = _chain_emoji(w["chain"])
        user = interaction.client.get_user(int(w["user_id"]))
        if user:
            try:
                await user.send(embed=discord.Embed(
                    title=f"❌ Withdrawal Rejected — {emoji} {w['chain']}",
                    description=(
                        f"**{w['amount_crypto']} {w['chain']}** (~${w['amount_usd']:.2f}) was rejected.\n\n"
                        f"`{format_balance(w['amount_coins'], 'real')}` refunded."
                    ),
                    color=discord.Color.red(),
                ))
            except discord.Forbidden:
                pass


class CryptoWithdraw(commands.Cog):
    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._confirm_task.start()

    def cog_unload(self):
        self._confirm_task.cancel()

    @tasks.loop(seconds=60)
    async def _confirm_task(self):
        pending = _get_pending_txs()
        if not pending:
            return

        confirmed: list[str] = []
        loop = asyncio.get_event_loop()

        for wid, info in list(pending.items()):
            chain = info["chain"]
            tx_id = info["tx_id"]
            uid = int(info["user_id"])
            try:
                if chain == "SOL":
                    done = await loop.run_in_executor(None, engine.check_sol_tx_finalized, tx_id)
                elif chain == "ETH":
                    done = await loop.run_in_executor(None, engine.check_eth_tx_confirmed, tx_id)
                else:
                    done = await loop.run_in_executor(None, engine.check_ltc_tx_confirmed, tx_id)
            except Exception:
                continue

            if not done:
                continue

            confirmed.append(wid)
            withdrawals = _get_withdrawals()
            if wid in withdrawals:
                withdrawals[wid]["status"] = "confirmed"
                _save_withdrawals(withdrawals)

            w = _get_withdrawals().get(wid, {})
            emoji = _chain_emoji(chain)
            user = self.bot.get_user(uid)
            if user:
                try:
                    await user.send(embed=discord.Embed(
                        title=f"🎉 Withdrawal Confirmed — {emoji} {chain}",
                        description=(
                            f"**{w.get('amount_crypto', '?')} {chain}** sent to your wallet.\n\n"
                            f"📍 `{w.get('address', '?')}`\n🔗 `{tx_id}`"
                        ),
                        color=discord.Color.green(),
                    ))
                except discord.Forbidden:
                    pass

        if confirmed:
            pending = _get_pending_txs()
            for wid in confirmed:
                pending.pop(wid, None)
            _save_pending_txs(pending)

    @_confirm_task.before_loop
    async def _before_confirm(self):
        await self.bot.wait_until_ready()

    def _validate_crypto_ready(self) -> tuple[bool, str, bool]:
        s = engine.get_settings()
        if not s.get("enabled", False):
            return False, "Crypto withdrawals are disabled.", False
        if not engine.TREASURY_MNEMONIC:
            return False, "Treasury wallet is not configured (`TREASURY_MNEMONIC`).", True
        enabled = any([
            s.get("sol_enabled", True),
            s.get("ltc_enabled", True),
            s.get("eth_enabled", True),
        ])
        if not enabled:
            return False, "No crypto chains are enabled.", False
        return True, "", False

    async def start_withdrawal(self, interaction: discord.Interaction) -> None:
        from cogs.crypto_withdraw_v2 import build_withdraw_coin_layout, build_withdraw_disabled_layout
        from modules.ui_v2 import send_ephemeral

        ok, msg, warning = self._validate_crypto_ready()
        if not ok:
            return await send_ephemeral(
                interaction,
                build_withdraw_disabled_layout("Unavailable", msg, warning=warning),
            )
        await send_ephemeral(interaction, build_withdraw_coin_layout(interaction.user.id))

    async def start_withdrawal_from_ctx(self, ctx: commands.Context) -> None:
        from cogs.crypto_withdraw_v2 import build_withdraw_coin_layout, build_withdraw_disabled_layout

        ok, msg, warning = self._validate_crypto_ready()
        if not ok:
            layout = build_withdraw_disabled_layout("Unavailable", msg, warning=warning)
            return await ctx.send(view=layout)

        await ctx.send(
            content=f"<@{ctx.author.id}> — select a coin to withdraw (only you can use this menu).",
            view=build_withdraw_coin_layout(ctx.author.id),
        )


async def setup(bot: discord.Client):
    await bot.add_cog(CryptoWithdraw(bot))
