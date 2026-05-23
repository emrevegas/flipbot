"""
Multi-table live blackjack cog — channels, seating, V2 UI, economy hooks.
"""

from __future__ import annotations

import asyncio
import random
import time

import discord
from discord.ext import commands, tasks

import modules.balance_cap as balance_cap
import modules.bonus as bonus_engine
import modules.live_blackjack_tables as tables
import modules.live_blackjack_v2 as lbv2
import modules.promo as promo_engine
import modules.race as race_engine
from Games import live_blackjack as lbj
from modules.database import get_data, set_data
from modules.live_blackjack_tables import get_settings
from modules.player import Player
from modules.utils import check_permission, format_balance
from modules import ui_v2

# Skip redundant message edits (major rate-limit saver on multi-table servers).
_last_table_ui_sig: dict[str, str] = {}


def _table_message_signature(table: dict, viewer_id: int | None) -> str:
    rnd = table.get("round") or {}
    seats = tuple(
        (
            s.get("user_id"),
            s.get("bet"),
            s.get("bet_confirmed"),
            s.get("pending_bet"),
        )
        for s in table.get("seats", [])
    )
    parts = (
        table.get("phase"),
        table.get("countdown_announce"),
        table.get("status_flash"),
        table.get("card_reveal"),
        table.get("dealer_show_count"),
        table.get("dealer_hole_hidden"),
        rnd.get("phase"),
        rnd.get("turn_idx"),
        rnd.get("turn_deadline"),
        len(rnd.get("seat_states") or []),
        bool(table.get("round_results")),
        bool(table.get("_deal_animating")),
        bool(table.get("_dealer_animating")),
        viewer_id,
        seats,
    )
    return "|".join(str(p) for p in parts)


def _ensure_live_blackjack_game_entry(games_data: dict) -> dict:
    if not isinstance(games_data, dict):
        games_data = {}
    lb = games_data.get("live_blackjack")
    if not isinstance(lb, dict):
        lb = {}
    lb.setdefault("name", "Live Blackjack")
    lb.setdefault("emoji", "🃏")
    lb.setdefault("enabled", True)
    lb.setdefault("min_bet", 10)
    lb.setdefault("max_bet", 10000)
    lb.setdefault("rigged_chance", 0.0)
    lb.setdefault("category", "special_games")
    games_data["live_blackjack"] = lb
    return games_data


async def maybe_create_overflow_table(
    bot: discord.Client, guild: discord.Guild, filled_table: dict
) -> dict | None:
    """When a table has no empty seats, open a temporary overflow table."""
    if tables.has_empty_seat(filled_table):
        return None
    if not tables.all_tables_full(guild.id):
        return None
    settings = get_settings()
    cat_id = settings.get("category_id")
    if not cat_id:
        return None
    category = guild.get_channel(int(cat_id))
    if not category:
        return None
    import uuid

    ch = await guild.create_text_channel(
        name=f"live-bj-{uuid.uuid4().hex[:6]}",
        category=category,
        reason="Live BJ overflow table",
    )
    tid = f"overflow_{uuid.uuid4().hex[:8]}"
    table = tables.new_table(
        table_id=tid,
        channel_id=ch.id,
        guild_id=guild.id,
        is_main=False,
    )
    msg = await ch.send(view=lbv2.build_table_layout(table, bot=bot))
    table["message_id"] = msg.id
    tables.save_table(table)
    return table


async def refresh_table_message(
    bot: discord.Client,
    table: dict,
    *,
    viewer_id: int | None = None,
    force: bool = False,
) -> None:
    ch_id = table.get("channel_id")
    msg_id = table.get("message_id")
    tid = str(table.get("id") or "")
    if not ch_id or not msg_id:
        return
    sig = _table_message_signature(table, viewer_id)
    if not force and tid and _last_table_ui_sig.get(tid) == sig:
        return
    channel = bot.get_channel(int(ch_id))
    if not channel:
        return
    try:
        msg = await channel.fetch_message(int(msg_id))
    except (discord.NotFound, discord.HTTPException):
        return
    view = lbv2.build_table_layout(table, viewer_id, bot=bot)
    for attempt in range(2):
        try:
            await msg.edit(view=view)
            if tid:
                _last_table_ui_sig[tid] = sig
            return
        except discord.HTTPException as exc:
            if exc.status == 429 and attempt == 0:
                wait = float(getattr(exc, "retry_after", 2) or 2)
                await asyncio.sleep(min(wait, 8.0))
                continue
            return


def _needs_dealer_animation(table: dict) -> bool:
    rnd = table.get("round") or {}
    return (
        rnd.get("phase") == "dealer_anim"
        and not table.get("_dealer_animating")
    )


async def _complete_round_settlement(bot: discord.Client, table: dict) -> None:
    """Pay winners and reset table after results were shown."""
    tid = table.get("id")
    guild = bot.get_guild(int(table.get("guild_id") or 0))
    if not guild:
        return
    results = table.get("round_results")
    if not results:
        results = lbj.settle_round(table, {m.id: m for m in guild.members})
    else:
        results = list(results)
    await _settle_table_economy(table, guild, results)
    tables.reset_seats_after_round(table)
    tables.save_table(table)
    if tid:
        fresh = tables.get_table(tid)
        if fresh:
            await refresh_table_message(bot, fresh)


async def run_dealer_animation(bot: discord.Client, table: dict) -> None:
    """Reveal dealer hole card, draw hits, then show win/loss results."""
    tid = table.get("id")
    if not tid or table.get("_dealer_animating"):
        return
    table["_dealer_animating"] = True
    tables.save_table(table)
    try:
        rnd = table.get("round") or {}
        if rnd.get("phase") != "dealer_anim":
            return
        final = list(rnd.get("dealer_final") or rnd.get("dealer") or [])
        if not final:
            lbj.finalize_round_display(table)
            tables.save_table(table)
            await refresh_table_message(bot, table)
            await asyncio.sleep(8)
            await _complete_round_settlement(bot, table)
            return

        table["dealer_show_count"] = min(2, len(final))
        table["dealer_hole_hidden"] = True
        tables.save_table(table)
        await refresh_table_message(bot, table, force=True)
        await asyncio.sleep(1.2)

        table = tables.get_table(tid) or table
        if (table.get("round") or {}).get("phase") != "dealer_anim":
            return
        table["dealer_hole_hidden"] = False
        table["dealer_show_count"] = min(2, len(final))
        tables.save_table(table)
        await refresh_table_message(bot, table, force=True)
        await asyncio.sleep(1.0)

        if len(final) > 2:
            table = tables.get_table(tid) or table
            if (table.get("round") or {}).get("phase") == "dealer_anim":
                table["dealer_show_count"] = len(final)
                tables.save_table(table)
                await refresh_table_message(bot, table, force=True)
                await asyncio.sleep(1.0)

        table = tables.get_table(tid) or table
        lbj.finalize_round_display(table)
        tables.save_table(table)
        await refresh_table_message(bot, table)
        await asyncio.sleep(8)

        table = tables.get_table(tid) or table
        if table.get("phase") == tables.PHASE_SETTLING:
            await _complete_round_settlement(bot, table)
    finally:
        table = tables.get_table(tid) or table
        table.pop("_dealer_animating", None)
        tables.save_table(table)


async def run_deal_animation(bot: discord.Client, table: dict) -> None:
    """Reveal first card, then full hands (solo-style deal pacing)."""
    tid = table.get("id")
    if not tid or table.get("_deal_animating"):
        return
    table["_deal_animating"] = True
    tables.save_table(table)
    try:
        if table.get("phase") != tables.PHASE_PLAYING:
            return
        table["card_reveal"] = 1
        tables.save_table(table)
        await refresh_table_message(bot, table, force=True)
        await asyncio.sleep(1.4)
        table = tables.get_table(tid) or table
        if table.get("phase") != tables.PHASE_PLAYING:
            return
        table["card_reveal"] = 2
        tables.save_table(table)
        await refresh_table_message(bot, table, force=True)
        await asyncio.sleep(0.6)
        table = tables.get_table(tid) or table
        table.pop("card_reveal", None)
        tables.save_table(table)
        await refresh_table_message(bot, table, force=True)
    finally:
        table = tables.get_table(tid) or table
        table.pop("_deal_animating", None)
        table.pop("card_reveal", None)
        tables.save_table(table)


class LiveBjCustomBetModal(discord.ui.Modal, title="Custom Bet"):
    def __init__(self, table_id: str):
        super().__init__(custom_id=f"lbj_bet_custom:{table_id}")
        self.table_id = table_id
        mn, mx = lbv2._bet_limits()
        self.amount = discord.ui.TextInput(
            label="Bet amount",
            placeholder=f"{mn:,} – {mx:,}",
            min_length=1,
            max_length=12,
            required=True,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        await handle_custom_bet_submit(
            interaction, self.table_id, self.amount.value
        )


async def handle_custom_bet_submit(
    interaction: discord.Interaction,
    table_id: str,
    raw: str,
) -> None:
    table = tables.get_table(table_id)
    if not table:
        return await ui_v2.send_ephemeral(
            interaction,
            ui_v2.error_panel("Table", "Table not found."),
        )
    uid = interaction.user.id
    try:
        bet = int(str(raw).replace(",", "").strip())
    except ValueError:
        return await ui_v2.send_ephemeral(
            interaction,
            ui_v2.error_panel("Bet", "Enter a valid whole number."),
        )
    mn, mx = lbv2._bet_limits()
    bet = max(mn, min(mx, bet))
    bet = tables.save_user_saved_bet(uid, bet)
    ok, err = tables.set_pending_bets(table, uid, bet)
    if not ok:
        return await ui_v2.send_ephemeral(interaction, ui_v2.error_panel("Bet", err))
    table["bet_select_gen"] = int(table.get("bet_select_gen", 0)) + 1
    tables.save_table(table)
    await interaction.response.defer()
    await refresh_table_message(interaction.client, table, viewer_id=uid)


async def _settle_table_economy(
    table: dict,
    guild: discord.Guild,
    results: list[dict],
) -> None:
    from Games.base_game import _apply_rakeback

    for r in results:
        uid = int(r["user_id"])
        try:
            member = guild.get_member(uid) or await guild.fetch_member(uid)
        except discord.HTTPException:
            member = None
        player = Player(uid)
        main_bet = int(r["bet"])
        total_return = int(r["total_return"])
        net = int(r["net"])
        result_label = "win" if net > 0 else ("tie" if net == 0 else "lose")

        if total_return > 0:
            player.add_balance("real", total_return)

        if not check_permission(uid, "admin"):
            player.update_stats("live_blackjack", main_bet, result_label, net, "real")
            current_bal = player.get_balance("real")
            active_bonus = bonus_engine.get_active_bonus(uid)
            if active_bonus and active_bonus.get("type") == "fixed":
                bonus_engine.check_balance_milestone(uid, current_bal)
                bonus_engine.check_forfeit(uid, current_bal)
            else:
                wager_done = bonus_engine.add_wager(uid, main_bet)
                if not wager_done:
                    bonus_engine.check_forfeit(uid, current_bal)
            promo_engine.on_real_bet_wagered(uid, main_bet)
            promo_engine.check_forfeit_promo(uid, current_bal)
            race_engine.add_entry(uid, main_bet, "wager")
            if member:
                _apply_rakeback(member, player, main_bet)
            try:
                from modules.live_stats_tracker import update_daily_game

                update_daily_game(uid, "live_blackjack", main_bet, result_label, net)
            except Exception:
                pass


class LiveBlackjackCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        if not self._table_tick.is_running():
            self._table_tick.start()

        async def _refresh_all_tables() -> None:
            await self.bot.wait_until_ready()
            await asyncio.sleep(2)
            for table in tables.list_tables():
                try:
                    await refresh_table_message(self.bot, table, force=True)
                    await asyncio.sleep(0.8)
                except Exception:
                    pass

        asyncio.create_task(_refresh_all_tables())

    async def cog_unload(self) -> None:
        self._table_tick.cancel()

    @tasks.loop(seconds=4)
    async def _table_tick(self) -> None:
        await self.bot.wait_until_ready()
        for table in tables.list_tables():
            try:
                changed = False
                ann = tables.tick_countdown(table)
                if ann:
                    changed = True
                if lbj.auto_stand_if_timed_out(table):
                    changed = True
                if _needs_dealer_animation(table):
                    tables.save_table(table)
                    asyncio.create_task(run_dealer_animation(self.bot, table))
                rnd = table.get("round")
                if (
                    table.get("phase") == tables.PHASE_SETTLING
                    and rnd
                    and rnd.get("phase") == "done"
                    and not table.get("_dealer_animating")
                    and int(table.get("result_display_until") or 0) <= int(time.time())
                ):
                    await _complete_round_settlement(self.bot, table)
                    changed = True
                started = (
                    table.get("phase") == tables.PHASE_PLAYING
                    and table.get("card_reveal") == 1
                    and not table.get("_deal_animating")
                )
                if changed:
                    tables.save_table(table)
                    if started:
                        asyncio.create_task(run_deal_animation(self.bot, table))
                    elif _needs_dealer_animation(table):
                        asyncio.create_task(run_dealer_animation(self.bot, table))
                    elif not table.get("_deal_animating") and not table.get(
                        "_dealer_animating"
                    ):
                        await refresh_table_message(self.bot, table)
                if tables.should_delete_overflow(table):
                    ch = self.bot.get_channel(int(table["channel_id"]))
                    if ch:
                        try:
                            await ch.delete(reason="Live BJ table empty 180s")
                        except discord.HTTPException:
                            pass
                    tables.delete_table(table["id"])
            except Exception:
                continue

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        cid = interaction.data.get("custom_id") if interaction.data else None
        if cid and str(cid).startswith("lbj_"):
            await self._route(interaction, str(cid))

    async def _route(self, interaction: discord.Interaction, custom_id: str) -> None:
        parts = custom_id.split(":", 2)
        kind = parts[0]
        table_id = parts[1] if len(parts) > 1 else ""
        table = tables.get_table(table_id)
        if not table:
            return await ui_v2.send_ephemeral(
                interaction,
                ui_v2.error_panel("Table", "Table not found."),
            )
        uid = interaction.user.id

        if kind == "lbj_sit" and len(parts) >= 3:
            ok, err = tables.sit_seat(table, uid, int(parts[2]))
            if not ok:
                return await ui_v2.send_ephemeral(interaction, ui_v2.error_panel("Sit", err))
            tables.save_table(table)
            await interaction.response.defer()
            if interaction.guild and not tables.has_empty_seat(table):
                overflow = await maybe_create_overflow_table(
                    self.bot, interaction.guild, table
                )
                if overflow:
                    await interaction.followup.send(
                        f"Table full — new table: <#{overflow['channel_id']}>",
                        ephemeral=True,
                    )
            await refresh_table_message(self.bot, table, viewer_id=uid)
            return

        if kind == "lbj_leave" and len(parts) >= 3:
            ok, err = tables.leave_seat(table, uid, int(parts[2]))
            if not ok:
                return await ui_v2.send_ephemeral(interaction, ui_v2.error_panel("Leave", err))
            tables.save_table(table)
            await interaction.response.defer()
            await refresh_table_message(self.bot, table, viewer_id=uid)
            return

        if kind == "lbj_bal":
            player = Player(uid)
            bal = player.get_balance("real")
            await interaction.response.send_message(
                f"💰 **Balance:** {format_balance(bal, 'real')}",
                ephemeral=True,
            )
            return

        if kind == "lbj_bet_sel":
            values = interaction.data.get("values") or []
            if not values:
                return
            if values[0] == "custom_bet":
                return await interaction.response.send_modal(
                    LiveBjCustomBetModal(table_id)
                )
            raw = values[0]
            if str(raw).startswith("bet_"):
                bet = int(str(raw).split("_", 1)[1])
            else:
                bet = int(raw)
            mn, mx = lbv2._bet_limits()
            bet = max(mn, min(mx, bet))
            bet = tables.save_user_saved_bet(uid, bet)
            ok, err = tables.set_pending_bets(table, uid, bet)
            if not ok:
                return await ui_v2.send_ephemeral(interaction, ui_v2.error_panel("Bet", err))
            table["bet_select_gen"] = int(table.get("bet_select_gen", 0)) + 1
            tables.save_table(table)
            await interaction.response.defer()
            await refresh_table_message(self.bot, table, viewer_id=uid)
            return

        if kind == "lbj_pp":
            if tables.count_user_seats(table, uid) == 0:
                return await ui_v2.send_ephemeral(
                    interaction,
                    ui_v2.error_panel("Bet", "Sit at a seat before betting."),
                )
            toggled = False
            for seat in table["seats"]:
                if int(seat.get("user_id") or 0) == uid:
                    pb = int(seat.get("pending_bet") or 0)
                    if not pb:
                        return await ui_v2.send_ephemeral(
                            interaction,
                            ui_v2.error_panel(
                                "Bet",
                                "Select a main bet amount first.",
                            ),
                        )
                    side = lbv2._side_bet_amount(pb)
                    seat["pending_side_pp"] = (
                        0 if int(seat.get("pending_side_pp") or 0) else side
                    )
                    toggled = True
            if not toggled:
                return
            tables.save_table(table)
            await interaction.response.defer()
            await refresh_table_message(self.bot, table, viewer_id=uid)
            return

        if kind == "lbj_213":
            if tables.count_user_seats(table, uid) == 0:
                return await ui_v2.send_ephemeral(
                    interaction,
                    ui_v2.error_panel("Bet", "Sit at a seat before betting."),
                )
            toggled = False
            for seat in table["seats"]:
                if int(seat.get("user_id") or 0) == uid:
                    pb = int(seat.get("pending_bet") or 0)
                    if not pb:
                        return await ui_v2.send_ephemeral(
                            interaction,
                            ui_v2.error_panel(
                                "Bet",
                                "Select a main bet amount first.",
                            ),
                        )
                    side = lbv2._side_bet_amount(pb)
                    seat["pending_side_21_3"] = (
                        0 if int(seat.get("pending_side_21_3") or 0) else side
                    )
                    toggled = True
            if not toggled:
                return
            tables.save_table(table)
            await interaction.response.defer()
            await refresh_table_message(self.bot, table, viewer_id=uid)
            return

        if kind == "lbj_bet_ok":
            player = Player(uid)
            total = 0
            for seat in table["seats"]:
                if int(seat.get("user_id") or 0) == uid:
                    total += int(seat.get("pending_bet") or 0)
                    total += int(seat.get("pending_side_pp") or 0)
                    total += int(seat.get("pending_side_21_3") or 0)
            if player.get_balance("real") < total:
                return await ui_v2.send_ephemeral(
                    interaction,
                    ui_v2.error_panel(
                        "Balance",
                        f"Need **{format_balance(total, 'real')}**.",
                    ),
                )
            player.remove_balance("real", total)
            ok, err = tables.confirm_bets(table, uid)
            if not ok:
                player.add_balance("real", total)
                return await ui_v2.send_ephemeral(interaction, ui_v2.error_panel("Bet", err))
            tables.save_table(table)
            started = table.get("phase") == tables.PHASE_PLAYING
            await interaction.response.defer()
            await refresh_table_message(self.bot, table, viewer_id=uid)
            if started:
                asyncio.create_task(run_deal_animation(self.bot, table))
            return

        if kind == "lbj_act" and len(parts) >= 3:
            action = parts[2]
            ok, msg = lbj.apply_action(table, uid, action)
            if not ok:
                return await ui_v2.send_ephemeral(interaction, ui_v2.error_panel("Action", msg))
            tables.save_table(table)
            await interaction.response.defer()
            if _needs_dealer_animation(table):
                asyncio.create_task(run_dealer_animation(self.bot, table))
            else:
                await refresh_table_message(self.bot, table, viewer_id=uid)
            return


async def setup(bot: commands.Bot):
    games = _ensure_live_blackjack_game_entry(get_data("server/games") or {})
    set_data("server/games", games)
    await bot.add_cog(LiveBlackjackCog(bot))
