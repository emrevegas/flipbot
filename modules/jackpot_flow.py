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
    LOBBY_IDLE_REFRESH_SEC,
    MAX_PLAYERS,
    MIN_PLAYERS,
    can_cancel,
    get_channel_id,
    get_round,
    is_jackpot_channel,
    new_round,
    player_in_round,
    pool_total,
    resolve_jackpot_channel,
    set_round,
)

log = logging.getLogger("flipbot.jackpot")

_channel_locks: dict[int, asyncio.Lock] = {}
_room_tasks: dict[int, asyncio.Task] = {}
JP_FEEDBACK_DELETE_SEC = 8.0

# Lobby edit only on roster/pool/status changes — never on countdown ticks alone.
JP_UI_MIN_REFRESH_WAIT_SEC = 10
JP_UI_PLAYER_CHANGE_MIN_SEC = 3


def _lock(channel_id: int) -> asyncio.Lock:
    if channel_id not in _channel_locks:
        _channel_locks[channel_id] = asyncio.Lock()
    return _channel_locks[channel_id]


def is_jackpot_staff(member: discord.abc.User) -> bool:
    """Panel admin/cashier, super admin, or Discord server administrator."""
    from modules.database import check_permission, is_super_admin

    if is_super_admin(member.id):
        return True
    if isinstance(member, discord.Member):
        if member.guild_permissions.administrator:
            return True
    # check_permission returns False when the user HAS the permission
    if not check_permission(str(member.id), "admin"):
        return True
    if not check_permission(str(member.id), "cashier"):
        return True
    return False


def static_avatar_url(user: discord.abc.User) -> str:
    """Return avatar URL usable by our image fetcher.

    Some discord.py versions don't expose `discord.AvatarFormat`, so we pass `format="png"`
    as a plain string and fall back to the original `.url`.
    """
    av = user.display_avatar
    try:
        # `Asset.replace()` exists on discord.py; format may accept a string.
        return str(av.replace(format="png", size=128))
    except Exception:
        return str(av.url)


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


def _pause_countdown_to_waiting(round_data: dict) -> dict:
    """Not enough players — stop timer (no stacked +15s extensions)."""
    round_data["status"] = "waiting"
    round_data["countdown_ends"] = 0
    round_data["countdown_secs"] = COUNTDOWN_BASE_SEC
    return round_data


def _lobby_ui_snapshot(round_data: dict) -> list:
    """Lobby fingerprint — excludes countdown seconds (time alone must not trigger edits)."""
    players = round_data.get("players") or []
    status = round_data.get("status") or "waiting"
    pool = round(pool_total(players), 2)
    player_key = [
        [int(p.get("user_id", 0)), round(float(p.get("bet") or 0), 2)]
        for p in sorted(players, key=lambda x: int(x.get("user_id", 0)))
    ]
    return [status, player_key, pool]


def _min_refresh_interval(*, player_change: bool) -> float:
    if player_change:
        return float(JP_UI_PLAYER_CHANGE_MIN_SEC)
    return float(JP_UI_MIN_REFRESH_WAIT_SEC)


async def _delete_message_after(message: discord.Message, seconds: float) -> None:
    await asyncio.sleep(seconds)
    try:
        await message.delete()
    except Exception:
        pass


async def send_jp_feedback(
    channel: discord.abc.Messageable,
    text: str,
    *,
    ok: bool = False,
    delete_after: float = JP_FEEDBACK_DELETE_SEC,
) -> discord.Message | None:
    embed = _ok_embed(text) if ok else _err_embed(text)
    try:
        msg = await channel.send(embed=embed, delete_after=delete_after)
        return msg
    except Exception:
        log.exception("Jackpot feedback send failed")
        return None


async def _cleanup_check_game_reply(ctx: commands.Context) -> None:
    """Remove transient error embed from _check_game (no delete_after there)."""
    await asyncio.sleep(0.35)
    lobby_id = int((get_round(ctx.channel.id) or {}).get("message_id") or 0)
    try:
        async for msg in ctx.channel.history(limit=12):
            if msg.author.id != ctx.bot.user.id:
                continue
            if lobby_id and msg.id == lobby_id:
                continue
            if not msg.embeds:
                continue
            desc = (msg.embeds[0].description or "")
            if desc.startswith("❌"):
                asyncio.create_task(_delete_message_after(msg, JP_FEEDBACK_DELETE_SEC))
                break
    except Exception:
        pass


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
    *,
    player_change: bool = False,
) -> discord.Message | None:
    """Edit or create the main jackpot V2 message."""
    players = round_data.get("players") or []
    pool = pool_total(players)
    rem = _countdown_remaining(round_data) if round_data.get("status") == "countdown" else None

    snap = _lobby_ui_snapshot(round_data)
    if snap == round_data.get("ui_snapshot") and not round_data.get("ui_dirty"):
        return None

    now_ts = time.time()
    last_ts = float(round_data.get("ui_last_refresh") or 0)
    min_interval = _min_refresh_interval(player_change=player_change)
    if min_interval > 0 and (now_ts - last_ts) < min_interval:
        round_data["ui_dirty"] = True
        set_round(channel.id, round_data)
        return None

    status_line = ""
    if round_data.get("status") == "countdown":
        status_line = f"Pool {pool:,.0f} pts  •  {len(players)} players  •  countdown"

    lobby_buf = await image_gen.render_jackpot_lobby_png(
        players,
        pool=pool,
        countdown_secs=None,
        status_line=status_line,
    )
    lobby_buf.seek(0)

    n = len(players)
    if n < MIN_PLAYERS:
        footer = f"Waiting for players — **{n}/{MIN_PLAYERS}** minimum. Join with `.jp <bet>`"
    elif round_data.get("status") == "countdown":
        footer = f"**{n}** players  •  round starting soon…"
    else:
        footer = f"**{n}** players in pool."

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

    try:
        if message:
            await message.edit(content=None, embed=None, attachments=files, view=layout)
        else:
            message = await channel.send(files=files, view=layout)
            round_data["message_id"] = message.id
    except Exception:
        log.exception("Jackpot lobby refresh failed ch=%s", channel.id)
        return None

    round_data["ui_last_refresh"] = now_ts
    round_data["ui_snapshot"] = snap
    round_data["ui_dirty"] = False
    set_round(channel.id, round_data)
    return message


def ensure_jackpot_room_loop(bot: discord.Client, channel_id: int) -> None:
    """Background tick — idle lobby refresh, countdown, spin trigger."""
    if channel_id in _room_tasks and not _room_tasks[channel_id].done():
        return

    async def _runner() -> None:
        try:
            while True:
                await asyncio.sleep(1)
                if not is_jackpot_channel(channel_id):
                    break
                ch = await resolve_jackpot_channel(bot, channel_id)
                if not ch:
                    continue

                rd = get_round(channel_id)
                if not rd:
                    rd = new_round(channel_id)
                    set_round(channel_id, rd)

                status = rd.get("status")

                if status == "waiting":
                    if rd.get("ui_dirty"):
                        async with _lock(channel_id):
                            rd = get_round(channel_id)
                            if rd and rd.get("status") == "waiting":
                                await refresh_lobby_message(bot, ch, rd)
                    continue

                if status == "countdown":
                    rem = _countdown_remaining(rd)
                    if rem > 0:
                        if rd.get("ui_dirty"):
                            async with _lock(channel_id):
                                rd = get_round(channel_id)
                                if rd and rd.get("status") == "countdown":
                                    await refresh_lobby_message(bot, ch, rd)
                        continue

                    async with _lock(channel_id):
                        rd = get_round(channel_id)
                        if not rd or rd.get("status") != "countdown":
                            continue
                        players = rd.get("players") or []
                        if len(players) < MIN_PLAYERS:
                            rd = _pause_countdown_to_waiting(rd)
                            rd["ui_dirty"] = True
                            set_round(channel_id, rd)
                            await refresh_lobby_message(bot, ch, rd)
                            continue
                        await _run_spin(bot, ch, rd)
                    continue

        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Jackpot room loop error ch=%s", channel_id)
        finally:
            _room_tasks.pop(channel_id, None)

    _room_tasks[channel_id] = asyncio.create_task(_runner())


async def bootstrap_jackpot_room(bot: discord.Client) -> None:
    """Post lobby menu and start auto-refresh loop for configured channel."""
    ch_id = get_channel_id()
    if not ch_id:
        return
    ch = await resolve_jackpot_channel(bot, ch_id)
    if not ch:
        log.warning("Jackpot channel %s not found", ch_id)
        return

    rd = get_round(ch_id)
    if not rd:
        rd = new_round(ch_id)
        set_round(ch_id, rd)

    if rd.get("status") in ("waiting", "countdown"):
        await refresh_lobby_message(bot, ch, rd)

    ensure_jackpot_room_loop(bot, ch_id)
    log.info("Jackpot room bootstrapped in #%s", ch.name)


async def join_jackpot(
    ctx: commands.Context,
    bet: float,
    *,
    join_message: discord.Message | None = None,
) -> None:
    """Add player to current round in jackpot channel."""
    if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
        await send_jp_feedback(ctx.channel, "Jackpot only works in a server text channel.")
        return

    if not is_jackpot_channel(ctx.channel.id):
        ch_id = get_channel_id()
        target = f"<#{ch_id}>" if ch_id else "**(not set)**"
        await send_jp_feedback(
            ctx.channel,
            f"This is not the Jackpot room. Use {target} (set in **Panel → Games → Jackpot**).",
        )
        return

    from cogs.games import _check_game

    if not await _check_game(ctx, "jackpot", bet):
        await _cleanup_check_game_reply(ctx)
        return

    uid = ctx.author.id
    async with _lock(ctx.channel.id):
        rd = get_round(ctx.channel.id)
        if not rd:
            rd = new_round(ctx.channel.id)
        status = rd.get("status")
        if status in ("spinning", "finished"):
            await send_jp_feedback(ctx.channel, "A round is already running. Wait for the next lobby.")
            return
        if player_in_round(rd, uid):
            await send_jp_feedback(ctx.channel, "You are already in this jackpot round.")
            return
        if len(rd.get("players") or []) >= MAX_PLAYERS:
            await send_jp_feedback(ctx.channel, f"Round is full (max {MAX_PLAYERS} players).")
            return

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

        rd["ui_dirty"] = True
        set_round(ctx.channel.id, rd)
        await refresh_lobby_message(ctx.bot, ctx.channel, rd, player_change=True)
        ensure_jackpot_room_loop(ctx.bot, ctx.channel.id)

    try:
        mid = get_round(ctx.channel.id)
        bot_msg = int(mid.get("message_id") or 0) if mid else 0
        if join_message and join_message.id != bot_msg:
            await join_message.delete()
    except Exception:
        pass


async def cancel_jackpot(ctx: commands.Context) -> None:
    if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
        await send_jp_feedback(ctx.channel, "Jackpot only works in a server text channel.")
        return
    if not is_jackpot_channel(ctx.channel.id):
        ch_id = get_channel_id()
        target = f"<#{ch_id}>" if ch_id else "**(not set)**"
        await send_jp_feedback(ctx.channel, f"This is not the Jackpot room. Use {target}.")
        return

    uid = ctx.author.id
    async with _lock(ctx.channel.id):
        rd = get_round(ctx.channel.id)
        if not rd or not can_cancel(rd):
            await send_jp_feedback(ctx.channel, "No cancellable jackpot round (game may have already started).")
            return
        players = rd.get("players") or []
        mine = [p for p in players if int(p.get("user_id", 0)) == uid]
        if not mine:
            await send_jp_feedback(ctx.channel, "You are not in this jackpot round.")
            return

        for p in mine:
            await db.add_balance(uid, float(p.get("bet") or 0), note="jackpot cancel refund")
        players = [p for p in players if int(p.get("user_id", 0)) != uid]
        rd["players"] = players

        if not players:
            rd = _pause_countdown_to_waiting(rd)
            rd["ui_dirty"] = True
            set_round(ctx.channel.id, rd)
            await refresh_lobby_message(ctx.bot, ctx.channel, rd, player_change=True)
            try:
                await ctx.message.delete()
            except Exception:
                pass
            await send_jp_feedback(ctx.channel, "Jackpot round cancelled. Your bet was refunded.", ok=True)
            return

        if len(players) < MIN_PLAYERS:
            rd = _pause_countdown_to_waiting(rd)
        rd["ui_dirty"] = True
        set_round(ctx.channel.id, rd)
        await refresh_lobby_message(ctx.bot, ctx.channel, rd, player_change=True)
        ensure_jackpot_room_loop(ctx.bot, ctx.channel.id)

    try:
        await ctx.message.delete()
    except Exception:
        pass
    await send_jp_feedback(ctx.channel, "You left the jackpot. Bet refunded.", ok=True)


async def _run_spin(
    bot: discord.Client,
    channel: discord.TextChannel,
    round_data: dict,
) -> None:
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

    capped = await bc.apply_balance_cap(wid, bal + payout, game_id="jackpot")
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
    ensure_jackpot_room_loop(bot, channel.id)


async def purge_channel_messages(channel: discord.TextChannel, round_data: dict) -> None:
    """Delete user messages; keep staff, winner join msg, lobby/result bot posts."""
    preserve = set(int(x) for x in (round_data.get("preserve_message_ids") or []))
    winner_mid = round_data.get("winner_message_id")
    if winner_mid:
        preserve.add(int(winner_mid))
    lobby_id = int(round_data.get("message_id") or 0)
    if lobby_id:
        preserve.add(lobby_id)
    menu_id = int(round_data.get("menu_message_id") or 0)
    if menu_id:
        preserve.add(menu_id)

    def _keep(msg: discord.Message) -> bool:
        if msg.id in preserve:
            return True
        if msg.author.bot:
            return False
        if is_jackpot_staff(msg.author):
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

    if is_jackpot_staff(message.author):
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
