"""In-game deposit webhook listener and notifications (Components V2)."""

import discord
from discord.ext import commands

from modules.database import get_data
from modules.ingame_deposit import (
    ensure_ingame_payment_method,
    extract_log_text,
    get_ingame_config,
    parse_deposit_log,
    process_deposit_from_log,
)
from modules.translator import t
from modules.ui_v2 import (
    ACCENT_ERROR,
    ACCENT_SUCCESS,
    ACCENT_WARNING,
    build_detail_panel,
    send_channel_v2,
)
from modules.utils import format_balance


class IngameDeposit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_ingame_payment_method()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return

        cfg = get_ingame_config()
        if not cfg.get("enabled"):
            return

        channel_id = cfg.get("webhook_channel_id")
        if not channel_id:
            return
        try:
            if message.channel.id != int(channel_id):
                return
        except (TypeError, ValueError):
            return

        text = extract_log_text(message.content or "", message.embeds)
        if not text:
            return

        parsed = None
        matched_line = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            candidate = parse_deposit_log(line)
            if candidate:
                parsed = candidate
                matched_line = line
                break

        if not parsed:
            return

        growid, amount_units = parsed
        ok, code, result = process_deposit_from_log(
            growid,
            amount_units,
            message_id=message.id,
            raw_log=matched_line or text,
        )

        if code == "duplicate":
            return

        if code == "unknown_growid":
            await self._log_staff(message.guild, message.channel, growid, amount_units, code)
            return

        if not ok:
            if code == "below_minimum_dl":
                await self._notify_below_minimum_dl(result)
                await self._log_below_minimum_dl(message.channel, growid, amount_units, result)
                return
            if code == "below_minimum_coins":
                await self._notify_below_minimum_coins(result)
                await self._log_below_minimum_coins(message.channel, growid, amount_units, result)
                return
            if code in ("disabled", "not_configured", "zero_coins"):
                await self._log_staff(message.guild, message.channel, growid, amount_units, code, result)
            return

        await self._notify_user(result)
        await self._log_deposit(message.guild, result, matched_line or text)

    async def _notify_user(self, result: dict):
        user_id = result["user_id"]
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        if not user:
            return

        from modules.utils import get_user_lang

        lang = get_user_lang(user_id)
        coins_fmt = format_balance(result["coins"], "real")
        fields = {
            t("deposit.previous_balance", lang=lang): format_balance(result["prev_balance"], "real"),
            t("deposit.new_balance", lang=lang): format_balance(result["new_balance"], "real"),
        }
        if result.get("bonus_amount", 0) > 0:
            fields[t("bonus.credited_field", lang=lang)] = t(
                "bonus.credited_value",
                lang=lang,
                amount=format_balance(result["bonus_amount"], "real"),
            )

        view = build_detail_panel(
            title=t("deposit.ingame_auto_dm_title", lang=lang),
            body=t(
                "deposit.ingame_auto_dm_description",
                lang=lang,
                coins=coins_fmt,
                growid=result["growid"],
                dl_amount=f"{result['dl_amount']:.2f}",
            ),
            fields=fields,
            accent=ACCENT_SUCCESS,
            emoji="✅",
            footer=t("deposit.footer", lang=lang),
        )
        await send_channel_v2(user, view)

    async def _log_deposit(self, guild: discord.Guild, result: dict, raw_log: str):
        deposit_settings = get_data("server/deposit_settings") or {}
        log_channel_id = deposit_settings.get("channel_id")
        if not log_channel_id:
            return
        channel = guild.get_channel(int(log_channel_id))
        if not isinstance(channel, discord.TextChannel):
            return

        view = build_detail_panel(
            title="In-Game Deposit Auto-Credited",
            body=f"📋 `{raw_log}`",
            fields={
                "👤 User": f"<@{result['user_id']}>",
                "🎮 GrowID": f"`{result['growid']}`",
                "💰 Credited": format_balance(result["coins"], "real"),
                "💎 DL": f"{result['dl_amount']:.2f}",
            },
            accent=ACCENT_SUCCESS,
            emoji="✅",
            footer="Vegas Casino | In-Game Deposit",
        )
        await send_channel_v2(channel, view)

    async def _send_reject_dm(self, user_id: int, view) -> None:
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
        if not user:
            return
        await send_channel_v2(user, view)

    async def _notify_below_minimum_dl(self, result: dict | None):
        if not result:
            return
        user_id = result.get("user_id")
        if not user_id:
            return
        from modules.utils import get_user_lang

        lang = get_user_lang(user_id)
        view = build_detail_panel(
            title=t("deposit.ingame_below_minimum_dm_title", lang=lang),
            body=t(
                "deposit.ingame_below_minimum_dm_description",
                lang=lang,
                dl_amount=f"{result.get('dl_amount', 0):.2f}",
                min_dl=f"{result.get('min_dl', 5):.2f}",
                growid=result.get("growid", "?"),
            ),
            accent=ACCENT_ERROR,
            emoji="❌",
            footer=t("deposit.footer", lang=lang),
        )
        await self._send_reject_dm(user_id, view)

    async def _notify_below_minimum_coins(self, result: dict | None):
        if not result:
            return
        user_id = result.get("user_id")
        if not user_id:
            return
        from modules.utils import get_user_lang

        lang = get_user_lang(user_id)
        view = build_detail_panel(
            title=t("deposit.ingame_below_minimum_coins_dm_title", lang=lang),
            body=t(
                "deposit.ingame_below_minimum_coins_dm_description",
                lang=lang,
                dl_amount=f"{result.get('dl_amount', 0):.2f}",
                coins=format_balance(result.get("coins", 0), "real"),
                min_deposit=format_balance(result.get("min_deposit", 0), "real"),
                growid=result.get("growid", "?"),
            ),
            accent=ACCENT_ERROR,
            emoji="❌",
            footer=t("deposit.footer", lang=lang),
        )
        await self._send_reject_dm(user_id, view)

    async def _log_below_minimum_dl(
        self,
        source_channel: discord.abc.GuildChannel,
        growid: str,
        amount_units: int,
        result: dict | None,
    ):
        dl = (result or {}).get("dl_amount", amount_units / 100.0)
        min_dl = (result or {}).get("min_dl", 5.0)
        fields = {"Log": f"`{growid} {amount_units}`"}
        uid = (result or {}).get("user_id")
        if uid:
            fields["👤 User"] = f"<@{uid}>"
        view = build_detail_panel(
            title="In-Game Deposit — Below DL Minimum",
            body=(
                f"Deposit rejected: **{dl:.2f} DL** is below the **{min_dl:.2f} DL** minimum."
            ),
            fields=fields,
            accent=ACCENT_ERROR,
            emoji="⚠️",
        )
        if isinstance(source_channel, discord.TextChannel):
            await send_channel_v2(source_channel, view, delete_after=120)

    async def _log_below_minimum_coins(
        self,
        source_channel: discord.abc.GuildChannel,
        growid: str,
        amount_units: int,
        result: dict | None,
    ):
        dl = (result or {}).get("dl_amount", amount_units / 100.0)
        coins = (result or {}).get("coins", 0)
        min_deposit = (result or {}).get("min_deposit", 0)
        fields = {"Log": f"`{growid} {amount_units}` ({dl:.2f} DL)"}
        uid = (result or {}).get("user_id")
        if uid:
            fields["👤 User"] = f"<@{uid}>"
        view = build_detail_panel(
            title="In-Game Deposit — Below Coin Minimum",
            body=(
                f"Deposit rejected: **{format_balance(coins, 'real')}** is below "
                f"the server minimum of **{format_balance(min_deposit, 'real')}**."
            ),
            fields=fields,
            accent=ACCENT_ERROR,
            emoji="⚠️",
        )
        if isinstance(source_channel, discord.TextChannel):
            await send_channel_v2(source_channel, view, delete_after=120)

    async def _log_staff(
        self,
        guild: discord.Guild,
        source_channel: discord.abc.GuildChannel,
        growid: str,
        amount_units: int,
        code: str,
        extra: dict | None = None,
    ):
        if code != "unknown_growid":
            return
        dl = amount_units / 100.0
        view = build_detail_panel(
            title="In-Game Deposit — No Linked User",
            body=(
                f"No Discord account linked to GrowID **`{growid}`**.\n"
                f"Log: `{growid} {amount_units}` ({dl:.2f} DL)"
            ),
            accent=ACCENT_WARNING,
            emoji="⚠️",
        )
        if isinstance(source_channel, discord.TextChannel):
            await send_channel_v2(source_channel, view, delete_after=120)


async def setup(bot: commands.Bot):
    await bot.add_cog(IngameDeposit(bot))
