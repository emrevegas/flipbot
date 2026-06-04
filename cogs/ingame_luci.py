"""In-game Luci deposit/withdraw — DM flows, staff approval, file queue poll."""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

import modules.crypto_deposit as crypto_engine
from modules.database import check_permission, get_user_data, set_user_data
from modules.ingame_withdraw import (
    approve_withdrawal,
    create_withdraw_order,
    get_pending_withdrawal,
    reject_withdrawal,
    update_withdrawal_log_ids,
    validate_withdraw_request,
)
from modules.luci_queue import ensure_queue_dirs, process_deposit_inbox, process_withdraw_results
from modules.player import Player
from modules.utils import format_balance

log = logging.getLogger("flipbot.luci")


def _staff_can_approve(user_id: int) -> bool:
    return not check_permission(user_id, "admin") or not check_permission(user_id, "cashier")


def _delivery_label(w: dict) -> str:
    return str(w.get("delivery_summary") or f"{w.get('quantity', '?')}x {str(w.get('item_type', 'dl')).upper()}")


def _build_ingame_approval_embed(w: dict) -> discord.Embed:
    embed = discord.Embed(
        title="💸 In-Game Withdrawal Request",
        color=0xFFA500,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="User", value=f"<@{w['user_id']}> (`{w['user_id']}`)", inline=True)
    embed.add_field(
        name="Delivery",
        value=f"`{_delivery_label(w)}` → **`{w.get('world_name', '?')}`**",
        inline=True,
    )
    embed.add_field(
        name="Deducted",
        value=f"`{format_balance(int(w.get('coins_paid') or 0), 'real')}`",
        inline=True,
    )
    embed.add_field(name="GrowID", value=f"`{w.get('growid', '?')}`", inline=True)
    embed.add_field(name="Rate", value=str(w.get("rate_display") or "—"), inline=True)
    embed.add_field(name="ID", value=f"`{w['id']}`", inline=True)
    embed.set_footer(text="FlipBot · Approve sends Luci bot · Reject refunds balance")
    return embed


class LuciWithdrawApprovalView(discord.ui.View):
    def __init__(self, withdrawal_id: str):
        super().__init__(timeout=None)
        self.withdrawal_id = str(withdrawal_id)
        approve = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id=f"ingame_withdraw_approve_{self.withdrawal_id}",
        )
        approve.callback = self._approve
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id=f"ingame_withdraw_reject_{self.withdrawal_id}",
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

        w = get_pending_withdrawal(self.withdrawal_id)
        if not w:
            return await interaction.response.send_message("❌ Withdrawal not found.", ephemeral=True)
        if w.get("status") != "pending":
            return await interaction.response.send_message(
                f"❌ Already **{w.get('status')}**.", ephemeral=True
            )

        ok, code, w = approve_withdrawal(self.withdrawal_id, interaction.user.id)
        if not ok:
            if code == "insufficient_bot_stock" and w:
                from modules.ingame_withdraw import validate_bot_stock

                required = int(w.get("required_wl_units") or 0)
                msg = validate_bot_stock(required) or "Bot has insufficient locks."
                return await interaction.response.send_message(f"❌ {msg}", ephemeral=True)
            return await interaction.response.send_message(f"❌ Could not approve ({code}).", ephemeral=True)

        uid = int(w["user_id"])
        history = get_user_data(uid, "withdraw_history") or {}
        wkey = str(w["id"])
        entry = history.get(wkey) or {}
        entry["status"] = "processing"
        history[wkey] = entry
        set_user_data(uid, "withdraw_history", history)

        embed = _build_ingame_approval_embed(w)
        embed.color = discord.Color.green()
        embed.add_field(
            name="✅ APPROVED — Luci queued",
            value=f"By <@{interaction.user.id}>",
            inline=False,
        )
        self._disable_all()
        await interaction.response.edit_message(embed=embed, view=self)

        user = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)
        if user:
            try:
                await user.send(
                    embed=discord.Embed(
                        title="✅ In-Game Withdrawal Approved",
                        description=(
                            f"**{_delivery_label(w)}** → **`{w.get('world_name')}`**\n"
                            f"GrowID: `{w.get('growid')}`\n\n"
                            "The bot is delivering to your Display Box. You'll get another DM when done."
                        ),
                        color=discord.Color.green(),
                    )
                )
            except discord.Forbidden:
                pass

    async def _reject(self, interaction: discord.Interaction):
        if not _staff_can_approve(interaction.user.id):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)

        w = get_pending_withdrawal(self.withdrawal_id)
        if not w:
            return await interaction.response.send_message("❌ Withdrawal not found.", ephemeral=True)
        if w.get("status") != "pending":
            return await interaction.response.send_message(
                f"❌ Already **{w.get('status')}**.", ephemeral=True
            )

        ok, code, w = reject_withdrawal(self.withdrawal_id, interaction.user.id)
        if not ok:
            return await interaction.response.send_message(f"❌ Could not reject ({code}).", ephemeral=True)

        uid = int(w["user_id"])
        coins = int(w.get("coins_paid") or 0)
        Player(uid).add_balance(
            "real",
            coins,
            by="ingame_withdraw_reject",
            reason="In-game withdrawal rejected by staff",
        )

        history = get_user_data(uid, "withdraw_history") or {}
        wkey = str(w["id"])
        entry = history.get(wkey) or {}
        entry["status"] = "rejected"
        entry["refunded"] = True
        history[wkey] = entry
        set_user_data(uid, "withdraw_history", history)

        embed = _build_ingame_approval_embed(w)
        embed.color = discord.Color.red()
        embed.add_field(
            name="❌ REJECTED — Balance Refunded",
            value=f"By <@{interaction.user.id}>",
            inline=False,
        )
        self._disable_all()
        await interaction.response.edit_message(embed=embed, view=self)

        user = interaction.client.get_user(uid) or await interaction.client.fetch_user(uid)
        if user:
            try:
                await user.send(
                    embed=discord.Embed(
                        title="❌ In-Game Withdrawal Rejected",
                        description=(
                            f"Your request for **`{w.get('world_name')}`** was rejected.\n\n"
                            f"**{format_balance(coins, 'real')}** refunded to your balance."
                        ),
                        color=discord.Color.red(),
                    )
                )
            except discord.Forbidden:
                pass


class IngameWithdrawModal(discord.ui.Modal, title="In-Game Withdrawal"):
    amount_input = discord.ui.TextInput(
        label="Amount (coins)",
        placeholder="e.g. 1000",
        max_length=12,
    )
    world_input = discord.ui.TextInput(
        label="World (Display Box)",
        placeholder="WORLD or WORLD|DOOR",
        max_length=40,
    )

    def __init__(self, user_id: int, growid: str, min_withdrawal: int, rate_display: str):
        super().__init__()
        self.user_id = user_id
        self.growid = growid
        self.min_withdrawal = min_withdrawal
        self.rate_display = rate_display

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        try:
            coins = int(self.amount_input.value.replace(",", "").strip())
        except ValueError:
            return await interaction.response.send_message("Invalid amount.", ephemeral=True)

        player = Player(self.user_id)
        balance = player.get_balance("real")
        err = validate_withdraw_request(coins, balance, self.min_withdrawal)
        if err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)

        world = self.world_input.value.strip().upper()
        if len(world) < 3:
            return await interaction.response.send_message("Invalid world name.", ephemeral=True)

        player.remove_balance("real", coins)
        player.record_withdraw(coins)
        order = create_withdraw_order(
            user_id=self.user_id,
            growid=self.growid,
            world_name=world,
            coins=coins,
        )
        wid = str(order["id"])

        history = get_user_data(self.user_id, "withdraw_history") or {}
        history[wid] = {
            "withdraw_id": wid,
            "amount": coins,
            "method_key": "ingame",
            "method_name": "In-Game (Luci)",
            "growid": self.growid,
            "world_name": world,
            "item_type": order["item_type"],
            "quantity": order["quantity"],
            "delivery_summary": order.get("delivery_summary"),
            "status": "pending_approval",
            "timestamp": str(int(time.time())),
            "user_id": self.user_id,
        }
        set_user_data(self.user_id, "withdraw_history", history)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Withdrawal Submitted",
                description=(
                    f"**{format_balance(coins, 'real')}** → **{order.get('delivery_summary', order['item_type'].upper())}**\n"
                    f"World: `{world}` · GrowID: `{self.growid}`\n\n"
                    "Your balance has been deducted. Staff will review your request.\n"
                    f"🆔 `{wid}`"
                ),
                color=discord.Color.orange(),
            ),
        )

        s = crypto_engine.get_settings()
        log_ch_id = s.get("withdraw_approval_channel_id") or s.get("sweep_log_channel_id")
        if log_ch_id:
            channel = interaction.client.get_channel(int(log_ch_id))
            if channel:
                w = get_pending_withdrawal(wid) or order
                view = LuciWithdrawApprovalView(wid)
                msg = await channel.send(embed=_build_ingame_approval_embed(w), view=view)
                update_withdrawal_log_ids(wid, channel.id, msg.id)


class IngameWithdrawStartView(discord.ui.View):
    def __init__(self, user_id: int, growid: str, min_withdrawal: int, rate_display: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.growid = growid
        self.min_withdrawal = min_withdrawal
        self.rate_display = rate_display

    @discord.ui.button(label="Start Withdrawal", style=discord.ButtonStyle.primary, emoji="💸")
    async def start(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        await interaction.response.send_modal(
            IngameWithdrawModal(self.user_id, self.growid, self.min_withdrawal, self.rate_display)
        )


class IngameLuci(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        ensure_queue_dirs()
        if not self.luci_poll.is_running():
            self.luci_poll.start()

    async def cog_unload(self):
        self.luci_poll.cancel()

    @tasks.loop(seconds=5.0)
    async def luci_poll(self):
        try:
            dep = await process_deposit_inbox(self.bot)
            wd = await process_withdraw_results(self.bot)
            if dep or wd:
                log.info("Luci poll: deposits=%s withdraws=%s", dep, wd)
        except Exception:
            log.exception("Luci poll error")

    @luci_poll.before_loop
    async def before_luci_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(IngameLuci(bot))
