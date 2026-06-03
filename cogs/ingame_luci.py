"""In-game Luci deposit/withdraw — DM flows + file queue poll."""

from __future__ import annotations

import logging
import time

import discord
from discord.ext import commands, tasks

from modules.database import get_data, get_user_data, set_user_data
from modules.ingame_deposit import (
    format_dl_coin_rate,
    get_ingame_config,
    is_ingame_configured,
)
from modules.ingame_withdraw import create_withdraw_order, get_dl_to_coin_rate, validate_withdraw_request
from modules.luci_queue import ensure_queue_dirs, process_deposit_inbox, process_withdraw_results, set_deposit_watch
from modules.player import Player
from modules.translator import t
from modules.ui_v2 import ACCENT_BRAND, ACCENT_SUCCESS, build_detail_panel, send_channel_v2
from modules.utils import format_balance, get_user_lang

log = logging.getLogger("flipbot.luci")


async def send_dm_notice(ctx: commands.Context, *, ok_text: str, fail_text: str) -> bool:
    try:
        dm = await ctx.author.create_dm()
        await dm.send(ok_text)
        await ctx.send("📬 **Check your DMs** — the panel was sent there.", delete_after=15)
        return True
    except discord.Forbidden:
        await ctx.send(fail_text, delete_after=20)
        return False


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
            ephemeral=True,
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


class IngameDepositStartView(discord.ui.View):
    def __init__(self, user_id: int, lang: str, cfg: dict):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.lang = lang
        self.cfg = cfg

    @discord.ui.button(label="Set GrowID & Instructions", style=discord.ButtonStyle.success, emoji="🎮")
    async def start(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not your panel.", ephemeral=True)
        from cogs.private_rooms import GrowIDDepositModal

        await interaction.response.send_modal(
            GrowIDDepositModal(str(self.user_id), self.lang, self.cfg, skip_bonus=True)
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

    async def open_deposit_dm(self, user: discord.User | discord.Member) -> bool:
        cfg = get_ingame_config()
        lang = get_user_lang(user.id)
        if not cfg.get("enabled") or not is_ingame_configured(cfg):
            try:
                dm = await user.create_dm()
                await dm.send(
                    embed=discord.Embed(
                        title=t("deposit.ingame_not_configured_title", lang=lang),
                        description=t("deposit.ingame_not_configured_description", lang=lang),
                        color=0xE74C3C,
                    )
                )
            except discord.Forbidden:
                return False
            return True

        set_deposit_watch(seconds=900, user_id=user.id)
        rate = format_dl_coin_rate(float(cfg.get("dl_to_coin_rate", 0) or 0))
        world = str(cfg.get("world", "")).strip()
        bot_name = str(cfg.get("bot_name", "")).strip()

        body = (
            f"**World:** `{world}`\n"
            f"**Bot:** `{bot_name}`\n"
            f"**Rate:** **{rate}** coins = **1 DL**\n\n"
            "The Growtopia bot is going to the **home world** to listen for donations.\n"
            "Use the button below to confirm your GrowID, then donate via the **Donation Box**."
        )
        view = build_detail_panel(
            title="💎 In-Game Deposit",
            body=body,
            accent=ACCENT_SUCCESS,
            emoji="💎",
            footer=t("deposit.footer", lang=lang),
        )
        ui_view = IngameDepositStartView(user.id, lang, cfg)
        try:
            dm = await user.create_dm()
            await send_channel_v2(dm, view)
            await dm.send(view=ui_view)
            return True
        except discord.Forbidden:
            return False

    async def open_withdraw_dm(self, user: discord.User | discord.Member) -> bool:
        cfg = get_ingame_config()
        if not cfg.get("enabled") or not is_ingame_configured(cfg):
            try:
                dm = await user.create_dm()
                await dm.send("❌ In-game withdraw is not configured.")
            except discord.Forbidden:
                return False
            return True

        growid_data = get_user_data(user.id, "growid") or {}
        growid = (growid_data.get("growid") or "").strip() if isinstance(growid_data, dict) else ""
        if not growid:
            try:
                dm = await user.create_dm()
                await dm.send(
                    "❌ Set your GrowID first with `.deposit` → **Set GrowID**, or in a private room finance menu."
                )
            except discord.Forbidden:
                return False
            return True

        deposit_settings = get_data("server/deposit_settings") or {}
        min_w = int(deposit_settings.get("min_withdrawal", 100) or 100)
        rate = format_dl_coin_rate(get_dl_to_coin_rate())
        player = Player(user.id)
        balance = player.get_balance("real")

        panel = build_detail_panel(
            title="💸 In-Game Withdrawal",
            body=(
                f"**Balance:** {format_balance(balance, 'real')}\n"
                f"**Minimum:** {format_balance(min_w, 'real')}\n"
                f"**Rate:** **{rate}** coins = **1 DL**\n"
                f"**GrowID:** `{growid}`\n\n"
                "Enter amount + world with a **Display Box (1422)**. Delivery is automatic via Luci bot."
            ),
            accent=ACCENT_BRAND,
            emoji="💸",
        )
        ui_view = IngameWithdrawStartView(user.id, growid, min_w, rate)
        try:
            dm = await user.create_dm()
            await send_channel_v2(dm, panel)
            await dm.send(view=ui_view)
            return True
        except discord.Forbidden:
            return False


async def setup(bot: commands.Bot):
    await bot.add_cog(IngameLuci(bot))
