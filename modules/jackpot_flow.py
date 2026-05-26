"""Jackpot round lifecycle — join, countdown, spin, payout, channel purge."""

from __future__ import annotations

import asyncio
import logging
import time
import discord
from discord.ext import commands

from database import db
from Games.jackpot import pick_winner_index, player_chance, winner_payout
from modules import image_gen
from modules.jackpot_media_v2 import (
    LOBBY_ATTACHMENT,
    SPIN_ATTACHMENT,
    jackpot_lobby_layout,
    jackpot_spin_layout,
    jackpot_winner_layout,
)
from modules.jackpot_store import (
    COUNTDOWN_BASE_SEC,
    COUNTDOWN_EXTEND_SEC,
    MAX_PLAYERS,
    MIN_PLAYERS,
    can_cancel,
    get_round,
    is_jackpot_channel,
    new_round,
    player_in_round,
    pool_total,
    set_round,
)

log = logging.getLogger("flipbot.jackpot")

_channel_locks: dict[int, asyncio.Lock] = {}
_countdown_tasks: dict[int, asyncio.Task] = {}


def _lock(channel_id: int) -> asyncio.Lock:
    if channel_id not in _channel_locks:
        _channel_locks[channel_id] = asyncio.Lock()
    return _channel_locks[channel_id]


def static_avatar_url(user: discord.abc.User) -> str:
    av = user.display_avatar
    return str(av.replace(format=discord.AvatarFormat.png, size=128))


def _countdown_remaining(round_data: dict) -> int:
    ends = int(round_data.get("countdown_ends") or 0)
    if ends <= 0:
        return 0
    return max(0, ends - int(time.time()))


def _start_countdown(round_data: dict) -> dict:
    secs = int(round_data.get("countdown_secs") or COUNTDOWN_BASE_SEC)
    round_data["status"] = "countdown"
    round_data["countdown_ends"] = int(time.time()) + secs
    return round_data


async def _get_house_edge() -> float:
    cfg = await db.get_game_config("jackpot")
    if cfg:
        return float(cfg.get("house_edge") or 0.02)
    return 0.02


async def _house_edge_percent() -> float:
    he = await _get_house_edge()
    return he * 100.0 if he < 1.0 else he


async def refresh_lobby_message(
    bot: discord.Client,
    channel: discord.TextChannel,
    round_data: dict,
) -> discord.Message | None:
    """Edit or create the main jackpot V2 message."""
    players = round_data.get("players") or []
    pool = pool_total(players)
    rem = _countdown_remaining(round_data) if round_data.get("status") == "countdown" else None

    lobby_buf = await image_gen.render_jackpot_lobby_png(
        players,
        pool=pool,
        countdown_secs=rem,
    )
    lobby_buf.seek(0)

    n = len(players)
    if n < MIN_PLAYERS:
        footer = f"Waiting for players — **{n}/{MIN_PLAYERS}** minimum. Join with `.jp <bet>`"
    else:
        footer = f"**{n}** players in pool. Countdown runs when the timer ends."

    header = (
        f"## 🎰 Jackpot\n"
        f"**Pool:** {pool:,.2f} pts  •  **Players:** {n}\n"
        f"Win chance = your bet ÷ total pool (2% house fee on payout)."
    )
    layout = jackpot_lobby_layout(header=header, footer=footer, timeout=None)
    files = [discord.File(lobby_buf, LOBBY_ATTACHMENT)]

    msg_id = round_data.get("message_id")
    message = None
    if msg_id:
        try:
            message = await channel.fetch_message(int(msg_id))
        except Exception:
            message = None

    if message:
        await message.edit(content=None, embed=None, attachments=files, view=layout)
    else:
        message = await channel.send(files=files, view=layout)
        round_data["message_id"] = message.id

    set_round(channel.id, round_data)
    return message


async def _cancel_countdown_task(channel_id: int) -> None:
    task = _countdown_tasks.pop(channel_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _schedule_countdown(bot: discord.Client, channel: discord.TextChannel) -> None:
    cid = channel.id
    if cid in _countdown_tasks and not _countdown_tasks[cid].done():
        return

    async def _runner():
        last_paint = -1
        try:
            while True:
                await asyncio.sleep(1)
                rd = get_round(cid)
                if not rd or rd.get("status") not in ("countdown",):
                    break
                rem = _countdown_remaining(rd)
                ch = bot.get_channel(cid)
                if not isinstance(ch, discord.TextChannel):
                    try:
                        fetched = await bot.fetch_channel(cid)
                        ch = fetched if isinstance(fetched, discord.TextChannel) else channel
                    except Exception:
                        ch = channel
                if rem > 0:
                    if rem != last_paint and (rem <= 10 or rem % 5 == 0):
                        last_paint = rem
                        try:
                            await refresh_lobby_message(bot, ch, rd)
                        except Exception:
                            pass
                    continue
                async with _lock(cid):
                    rd = get_round(cid)
                    if not rd or rd.get("status") != "countdown":
                        break
                    players = rd.get("players") or []
                    if len(players) < MIN_PLAYERS:
                        rd["countdown_secs"] = int(rd.get("countdown_secs") or COUNTDOWN_BASE_SEC) + COUNTDOWN_EXTEND_SEC
                        rd = _start_countdown(rd)
                        set_round(cid, rd)
                        await refresh_lobby_message(bot, ch, rd)
                        last_paint = _countdown_remaining(rd)
                        continue
                    await _run_spin(bot, ch, rd)
                break
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Jackpot countdown error ch=%s", cid)
        finally:
            _countdown_tasks.pop(cid, None)

    _countdown_tasks[cid] = asyncio.create_task(_runner())


async def join_jackpot(
    ctx: commands.Context,
    bet: float,
    *,
    join_message: discord.Message | None = None,
) -> None:
    """Add player to current round in jackpot channel."""
    if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
        return await ctx.send(embed=_err_embed("Jackpot only works in a server text channel."))

    if not is_jackpot_channel(ctx.channel.id):
        return await ctx.send(
            embed=_err_embed("This is not the Jackpot room. Ask an admin to set it in **Panel → Games → Jackpot**.")
        )

    from cogs.games import _check_game

    if not await _check_game(ctx, "jackpot", bet):
        return

    uid = ctx.author.id
    async with _lock(ctx.channel.id):
        rd = get_round(ctx.channel.id)
        if not rd:
            rd = new_round(ctx.channel.id)
        status = rd.get("status")
        if status in ("spinning", "finished"):
            return await ctx.send(embed=_err_embed("A round is already running. Wait for the next lobby."))
        if player_in_round(rd, uid):
            return await ctx.send(embed=_err_embed("You are already in this jackpot round."))
        if len(rd.get("players") or []) >= MAX_PLAYERS:
            return await ctx.send(embed=_err_embed(f"Round is full (max {MAX_PLAYERS} players)."))

        await db.add_balance(uid, -bet, note="jackpot bet")
        await db.add_wager(uid, bet)
        from cogs.games import _earn_rakeback

        await _earn_rakeback(uid, bet, ctx.author if isinstance(ctx.author, discord.Member) else None)

        entry = {
            "user_id": uid,
            "username": ctx.author.display_name,
            "avatar_url": static_avatar_url(ctx.author),
            "bet": bet,
            "message_id": join_message.id if join_message else ctx.message.id,
        }
        players = list(rd.get("players") or [])
        players.append(entry)
        rd["players"] = players

        if status == "waiting" and players:
            rd = _start_countdown(rd)
            rd["countdown_secs"] = COUNTDOWN_BASE_SEC

        set_round(ctx.channel.id, rd)
        await refresh_lobby_message(ctx.bot, ctx.channel, rd)
        _schedule_countdown(ctx.bot, ctx.channel)

    try:
        mid = get_round(ctx.channel.id)
        bot_msg = int(mid.get("message_id") or 0) if mid else 0
        if join_message and join_message.id != bot_msg:
            await join_message.delete()
    except Exception:
        pass


async def cancel_jackpot(ctx: commands.Context) -> None:
    if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
        return await ctx.send(embed=_err_embed("Jackpot only works in a server text channel."))
    if not is_jackpot_channel(ctx.channel.id):
        return await ctx.send(embed=_err_embed("This is not the Jackpot room."))

    uid = ctx.author.id
    async with _lock(ctx.channel.id):
        rd = get_round(ctx.channel.id)
        if not rd or not can_cancel(rd):
            return await ctx.send(embed=_err_embed("No cancellable jackpot round (game may have already started)."))
        players = rd.get("players") or []
        mine = [p for p in players if int(p.get("user_id", 0)) == uid]
        if not mine:
            return await ctx.send(embed=_err_embed("You are not in this jackpot round."))

        for p in mine:
            await db.add_balance(uid, float(p.get("bet") or 0), note="jackpot cancel refund")
        players = [p for p in players if int(p.get("user_id", 0)) != uid]
        rd["players"] = players

        if not players:
            await _cancel_countdown_task(ctx.channel.id)
            rd["status"] = "waiting"
            rd["countdown_ends"] = 0
            set_round(ctx.channel.id, rd)
            msg_id = rd.get("message_id")
            if msg_id:
                try:
                    m = await ctx.channel.fetch_message(int(msg_id))
                    await m.edit(
                        content=None,
                        embed=None,
                        attachments=[],
                        view=jackpot_lobby_layout(
                            header="## 🎰 Jackpot\nRound cancelled — join with `.jp <bet>`",
                            footer="",
                            timeout=None,
                        ),
                    )
                except Exception:
                    pass
            return await ctx.send(embed=_ok_embed("Jackpot round cancelled. Your bet was refunded."))

        if len(players) < MIN_PLAYERS:
            rd["countdown_secs"] = int(rd.get("countdown_secs") or COUNTDOWN_BASE_SEC) + COUNTDOWN_EXTEND_SEC
            rd = _start_countdown(rd)
        set_round(ctx.channel.id, rd)
        await refresh_lobby_message(ctx.bot, ctx.channel, rd)
        _schedule_countdown(ctx.bot, ctx.channel)

    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send(embed=_ok_embed("You left the jackpot. Bet refunded."), delete_after=6)


async def _run_spin(
    bot: discord.Client,
    channel: discord.TextChannel,
    round_data: dict,
) -> None:
    await _cancel_countdown_task(channel.id)
    players = list(round_data.get("players") or [])
    if len(players) < MIN_PLAYERS:
        return

    round_data["status"] = "spinning"
    set_round(channel.id, round_data)

    pool = pool_total(players)
    he = await _get_house_edge()
    he_pct = await _house_edge_percent()
    win_idx = pick_winner_index(players)
    winner = players[win_idx]
    payout = winner_payout(pool, he)
    wid = int(winner["user_id"])

    lobby_buf = await image_gen.render_jackpot_lobby_png(players, pool=pool, status_line="Spinning…")
    gif_buf = await image_gen.render_jackpot_spin_gif(
        players,
        win_idx,
        pool=pool,
        payout=payout,
        house_edge_pct=he_pct,
    )
    lobby_buf.seek(0)
    gif_buf.seek(0)

    header = f"## 🎰 Jackpot — **SPINNING**\n**Pool:** {pool:,.2f} pts"
    layout = jackpot_spin_layout(header=header, timeout=None)
    files = [
        discord.File(lobby_buf, LOBBY_ATTACHMENT),
        discord.File(gif_buf, SPIN_ATTACHMENT),
    ]

    msg_id = round_data.get("message_id")
    message = None
    if msg_id:
        try:
            message = await channel.fetch_message(int(msg_id))
            await message.edit(content=None, embed=None, attachments=files, view=layout)
        except Exception:
            message = None
    if not message:
        message = await channel.send(files=files, view=layout)
        round_data["message_id"] = message.id

    await asyncio.sleep((image_gen.JACKPOT_SPIN_MS + image_gen.JACKPOT_RESULT_HOLD_MS) / 1000.0 + 0.5)

    bal = float((await db.get_user(wid) or {}).get("balance", 0))
    from modules import flip_balance_cap as bc

    capped = await bc.apply_balance_cap(wid, bal + payout)
    pay_net = max(0.0, capped - bal)
    if pay_net > 0:
        await db.add_balance(wid, pay_net, note="jackpot payout")

    win_bet = float(winner.get("bet") or 0)
    profit = pay_net - win_bet
    await db.record_game_result(wid, True, profit)
    for p in players:
        pid = int(p["user_id"])
        if pid == wid:
            continue
        lost = float(p.get("bet") or 0)
        await db.record_game_result(pid, False, -lost)

    from modules.game_log import post_solo_game_log

    try:
        member = channel.guild.get_member(wid) if channel.guild else None
        await post_solo_game_log(
            user_id=wid,
            game_id="jackpot",
            bet=win_bet,
            won=True,
            payout=pay_net,
            user=member,
            client=bot,
            guild_id=channel.guild.id if channel.guild else None,
        )
    except Exception:
        log.exception("Jackpot game log failed")

    wmsg_id = winner.get("message_id")
    round_data["winner_id"] = wid
    round_data["winner_message_id"] = wmsg_id
    round_data["status"] = "finished"
    preserve = []
    if round_data.get("message_id"):
        preserve.append(int(round_data["message_id"]))
    if wmsg_id:
        preserve.append(int(wmsg_id))
    round_data["preserve_message_ids"] = preserve

    winner_name = winner.get("username") or "Winner"
    w_pct = player_chance(win_bet, pool) * 100.0
    from Games.jackpot import format_chance

    body = (
        f"## 🎰 Jackpot Winner\n"
        f"**{winner_name}** won **{pay_net:,.2f} pts** "
        f"(pool {pool:,.2f} − {he_pct:g}% fee)\n"
        f"Chance: **{format_chance(w_pct)}**  •  Bet: **{win_bet:,.2f} pts**"
    )
    gif_buf.seek(0)
    result_layout = jackpot_winner_layout(body=body, spin_gif=True, timeout=None)
    result_msg = await channel.send(
        files=[discord.File(gif_buf, SPIN_ATTACHMENT)],
        view=result_layout,
    )
    round_data["menu_message_id"] = result_msg.id
    preserve.append(result_msg.id)
    round_data["preserve_message_ids"] = preserve
    set_round(channel.id, round_data)

    await purge_channel_messages(channel, round_data)
    set_round(channel.id, None)
    rd_new = new_round(channel.id)
    set_round(channel.id, rd_new)
    await refresh_lobby_message(bot, channel, rd_new)


async def purge_channel_messages(channel: discord.TextChannel, round_data: dict) -> None:
    """Delete user messages; keep bot, admin/cashier, winner join message."""
    from modules.database import check_permission

    preserve = set(int(x) for x in (round_data.get("preserve_message_ids") or []))
    winner_mid = round_data.get("winner_message_id")
    if winner_mid:
        preserve.add(int(winner_mid))

    def _keep(msg: discord.Message) -> bool:
        if msg.id in preserve:
            return True
        if msg.author.bot:
            return True
        if check_permission(str(msg.author.id), "admin"):
            return True
        if check_permission(str(msg.author.id), "cashier"):
            return True
        return False

    try:
        async for msg in channel.history(limit=200):
            if _keep(msg):
                continue
            try:
                await msg.delete()
            except Exception:
                pass
    except Exception:
        log.exception("Jackpot purge failed ch=%s", channel.id)


async def on_jackpot_channel_message(message: discord.Message) -> None:
    """Auto-delete chat in jackpot room (except staff / preserved)."""
    if message.author.bot or not message.guild:
        return
    if not is_jackpot_channel(message.channel.id):
        return

    from modules.database import check_permission

    if check_permission(str(message.author.id), "admin"):
        return
    if check_permission(str(message.author.id), "cashier"):
        return

    rd = get_round(message.channel.id)
    preserve: set[int] = set()
    if rd:
        for x in rd.get("preserve_message_ids") or []:
            preserve.add(int(x))
        wmid = rd.get("winner_message_id")
        if wmid and int(rd.get("winner_id") or 0) == message.author.id:
            preserve.add(int(wmid))

    if message.id in preserve:
        return

    try:
        await message.delete()
    except Exception:
        pass


def _err_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xE74C3C)


def _ok_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {msg}", color=0x2ECC71)
