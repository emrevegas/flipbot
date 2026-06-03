"""In-game Luci deposit/withdraw — DM flows + file queue poll."""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

from modules.database import get_user_data, set_user_data
from modules.ingame_withdraw import create_withdraw_order, validate_withdraw_request
from modules.luci_queue import ensure_queue_dirs, process_deposit_inbox, process_withdraw_results
from modules.player import Player
from modules.utils import format_balance

log = logging.getLogger("flipbot.luci")


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

        history = get_user_data(self.user_id, "withdraw_history") or {}
        history[str(order["id"])] = {
            "withdraw_id": str(order["id"]),
            "amount": coins,
            "method_key": "ingame",
            "method_name": "In-Game (Luci)",
            "growid": self.growid,
            "world_name": world,
            "item_type": order["item_type"],
            "quantity": order["quantity"],
            "status": "processing",
            "timestamp": str(int(time.time())),
            "user_id": self.user_id,
        }
        set_user_data(self.user_id, "withdraw_history", history)

        await interaction.response.send_message(
            embed=discord.Embed(
                title="⏳ Withdrawal Queued",
                description=(
                    f"**{format_balance(coins, 'real')}** → **{order['quantity']}x {order['item_type'].upper()}**\n"
                    f"World: `{world}` · GrowID: `{self.growid}`\n\n"
                    "The Growtopia bot will deliver to your **Display Box**. "
                    "You'll get another DM when it's done."
                ),
                color=0xF59E0B,
            ),
        )


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
