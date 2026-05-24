"""All casino games as prefix commands.

Games: coinflip, dice, roulette, mines, hilo, blackjack, limbo, slots, crash, chickenroad
"""
from __future__ import annotations

import asyncio
import io
import json
import math
import random
import time
from typing import Optional

import discord
from discord.ext import commands

import config
from database import db
from modules import image_gen, flip_utils as utils, flip_balance_cap as bc
from modules.database import get_data

GAME_TIMEOUT = 120  # seconds for interactive games


async def _earn_rakeback(
    user_id: int | str,
    wager_amount: float,
    member: discord.Member | None = None,
) -> None:
    if wager_amount <= 0:
        return
    user = await db.get_user(user_id)
    total_wagered = float((user or {}).get("total_wagered", 0))
    tier = utils.get_rakeback_tier(total_wagered, member)
    rate = float(tier.get("rate", 0))
    if rate > 0:
        await db.add_rakeback(user_id, wager_amount * rate)


def _parse_btn_emoji(s: str):
    """Unicode or <:name:id> custom emoji for discord.ui.Button."""
    if not s:
        return "❓"
    s = str(s).strip()
    if s.startswith("<") and ":" in s:
        try:
            return discord.PartialEmoji.from_str(s)
        except Exception:
            return "❓"
    return s


def _get_mines_settings() -> dict:
    """Mines house edge + emojis from panel (server/games.mines)."""
    games_data = get_data("server/games") or {}
    mines_data = games_data.get("mines", {}) if isinstance(games_data, dict) else {}
    if not isinstance(mines_data, dict):
        mines_data = {}

    house_edge_percent = mines_data.get("house_edge", 15.0)
    try:
        house_edge_percent = float(house_edge_percent)
    except (TypeError, ValueError):
        house_edge_percent = 15.0
    house_edge_percent = max(0.0, min(99.99, house_edge_percent))

    emojis = mines_data.get("emojis", {}) if isinstance(mines_data.get("emojis"), dict) else {}
    game_emoji = str(mines_data.get("emoji", "💣") or "💣")
    hidden_emoji = str(emojis.get("hidden", "❓") or "❓")
    gem_emoji = str(emojis.get("gem", "💎") or "💎")
    mine_emoji = str(emojis.get("mine", "💣") or "💣")

    rigged_chance = mines_data.get("rigged_chance", 5.0)
    try:
        rigged_chance = float(rigged_chance)
    except (TypeError, ValueError):
        rigged_chance = 5.0
    rigged_chance = max(0.0, min(100.0, rigged_chance))

    return {
        "house_edge_percent": house_edge_percent,
        "house_edge_decimal": house_edge_percent / 100.0,
        "game": game_emoji,
        "hidden": hidden_emoji,
        "gem": gem_emoji,
        "mine": mine_emoji,
        "rigged_chance": rigged_chance,
    }


# ── shared helpers ─────────────────────────────────────────────────────────────

def _err(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xE74C3C)


def _ok(msg: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {msg}", color=0x2ECC71)


async def _check_game(ctx: commands.Context, game_id: str, bet: float) -> bool:
    """Validate user status and bet. Returns True if OK to play."""
    uid = ctx.author.id
    if await db.is_banned(uid):
        await ctx.send(embed=_err("You are banned from using this bot."))
        return False
    if await db.is_muted(uid):
        await ctx.send(embed=_err("You are muted from games."))
        return False

    game_cfg = await db.get_game_config(game_id)
    if not game_cfg or not game_cfg["enabled"]:
        await ctx.send(embed=_err(f"Game **{game_id}** is currently disabled."))
        return False

    if bet < game_cfg["min_bet"]:
        await ctx.send(embed=_err(f"Minimum bet is **{utils.fmt_pts(game_cfg['min_bet'])} pts**."))
        return False
    if bet > game_cfg["max_bet"]:
        await ctx.send(embed=_err(f"Maximum bet is **{utils.fmt_pts(game_cfg['max_bet'])} pts**."))
        return False

    user = await db.ensure_user(uid, ctx.author.name)
    if float(user["balance"]) < bet:
        await ctx.send(embed=_err(f"Insufficient balance. You have **{utils.fmt_pts(user['balance'])} pts**."))
        return False

    existing = await db.get_game_session(uid)
    if existing:
        if _session_expired(existing):
            await _resolve_expired_session(uid, existing)
        else:
            await ctx.send(embed=_err(
                f"You already have an active **{existing['game']}** game. "
                f"Finish or cash out first."
            ))
            return False

    return True


def _session_expired(sess: dict) -> bool:
    started = int(sess.get("started_at") or 0)
    return started > 0 and (time.time() - started) >= GAME_TIMEOUT


async def _refund_game(user_id: int | str, bet: float, game_id: str, note: str = "timeout refund"):
    """Refund bet and clear session on timeout."""
    await db.add_balance(user_id, bet, note=note)
    await db.clear_game_session(user_id)


async def _bj_auto_stand(user_id: int, msg: discord.Message | None = None) -> None:
    """Finish an active BJ session as if the player stood (timeout)."""
    sess = await db.get_game_session(user_id)
    if not sess or sess["game"] != "blackjack":
        return
    state = json.loads(sess["state"])
    total_bet = float(sess["bet"]) * (2 if state.get("doubled") else 1)
    username = state.get("username", "Player")

    while Games._hand_value_static(state["dealer"]) < 17:
        state["dealer"].append(state["deck"].pop())

    pv = Games._hand_value_static(state["player"])
    dv = Games._hand_value_static(state["dealer"])

    if pv > 21:
        outcome, gross, won = "BUST", 0.0, False
    elif dv > 21 or pv > dv:
        outcome, gross, won = "WIN", total_bet * 2, True
    elif pv == dv:
        outcome, gross, won = "PUSH", total_bet, False
    else:
        outcome, gross, won = "LOSS", 0.0, False

    game_cfg = await db.get_game_config("blackjack")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    net = gross * (1 - he) if gross > 0 else 0.0

    if net > 0:
        bal = float((await db.get_user(user_id) or {}).get("balance", 0))
        net_capped = await bc.apply_balance_cap(user_id, bal + net)
        net = max(0.0, net_capped - bal)
        await db.add_balance(user_id, net, note="blackjack timeout payout")

    await db.add_wager(user_id, total_bet)
    await _earn_rakeback(user_id, total_bet)
    await _record(user_id, won, total_bet, net)
    await db.clear_game_session(user_id)
    for mid, uid in list(_bj_msg_to_user.items()):
        if uid == user_id:
            _bj_msg_to_user.pop(mid, None)
    _bj_user_msg.pop(int(user_id), None)

    if msg:
        net_change = (net - total_bet) if won else (-total_bet if outcome != "PUSH" else 0.0)
        gif_buf = await image_gen.render_bj_gif(
            state["player"], state["dealer"],
            reveal_dealer=True, result_text=outcome,
            net_change=net_change, bet=total_bet, username=username,
        )
        try:
            await msg.edit(
                attachments=[discord.File(gif_buf, "blackjack.gif")],
                view=_BJResultView(user_id, total_bet),
            )
        except Exception:
            pass


async def _resolve_expired_session(user_id: int | str, sess: dict) -> None:
    """Resolve a timed-out session (refund or BJ auto-stand)."""
    if sess["game"] == "blackjack":
        await _bj_auto_stand(int(user_id))
    else:
        await _refund_game(user_id, float(sess["bet"]), sess["game"])


async def _ensure_session_active(user_id: int | str, game_id: str) -> dict | None:
    """Return session if active and not expired; refund + clear if timed out."""
    sess = await db.get_game_session(user_id)
    if not sess or sess["game"] != game_id:
        return None
    if _session_expired(sess):
        await _resolve_expired_session(user_id, sess)
        return None
    return sess


async def _check_game_interaction(
    interaction: discord.Interaction, user_id: int, game_id: str, bet: float,
) -> bool:
    """Validate balance/config/session for re-bet from button interactions."""
    existing = await db.get_game_session(user_id)
    if existing:
        if _session_expired(existing):
            await _resolve_expired_session(user_id, existing)
        else:
            await interaction.response.send_message(
                embed=_err("You already have an active game. Finish it first."), ephemeral=True,
            )
            return False
    user = await db.get_user(user_id)
    if not user or float(user["balance"]) < bet:
        await interaction.response.send_message(embed=_err("Insufficient balance."), ephemeral=True)
        return False
    game_cfg = await db.get_game_config(game_id)
    if not game_cfg or not game_cfg["enabled"]:
        await interaction.response.send_message(
            embed=_err(f"**{game_id}** is currently disabled."), ephemeral=True,
        )
        return False
    if not (game_cfg["min_bet"] <= bet <= game_cfg["max_bet"]):
        await interaction.response.send_message(
            embed=_err(
                f"Bet must be between {utils.fmt_pts(game_cfg['min_bet'])} "
                f"and {utils.fmt_pts(game_cfg['max_bet'])} pts."
            ),
            ephemeral=True,
        )
        return False
    return True


async def _payout(user_id: int | str, game_id: str, bet: float, gross_payout: float) -> float:
    """Deduct bet, apply house edge / balance cap, credit payout. Returns net payout."""
    game_cfg = await db.get_game_config(game_id)
    house_edge = float(game_cfg["house_edge"]) if game_cfg else 0.02
    await db.add_balance(user_id, -bet, note=f"{game_id} bet")
    net = gross_payout * (1 - house_edge)
    current_bal_after = float((await db.get_user(user_id) or {}).get("balance", 0))
    capped = await bc.apply_balance_cap(user_id, current_bal_after + net)
    net = max(0.0, capped - current_bal_after)

    if net > 0:
        await db.add_balance(user_id, net, note=f"{game_id} payout")
    await db.add_wager(user_id, bet)

    await _earn_rakeback(user_id, bet)

    return net


async def _record(user_id: int | str, won: bool, bet: float, net: float):
    profit = net - bet if won else -bet
    await db.record_game_result(user_id, won, profit)


# ─────────────────────────────────────────────────────────────────────────────
# MINES — button-based 4×5 grid
# ─────────────────────────────────────────────────────────────────────────────

_mines_msg_to_user: dict[str, int] = {}  # message_id -> user_id


class _MinesCell(discord.ui.Button):
    def __init__(self, r: int, c: int, message_id: str, *, hidden_emoji):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            emoji=hidden_emoji,
            row=r, custom_id=f"mc_{r}{c}_{message_id}",
        )
        self.r = r
        self.c = c

    async def callback(self, interaction: discord.Interaction):
        user_id = _mines_msg_to_user.get(str(interaction.message.id))
        if not user_id:
            return await interaction.response.send_message(
                embed=_err("Game session not found. It may have expired."), ephemeral=True
            )
        if interaction.user.id != user_id:
            return await interaction.response.send_message(
                embed=_err("This is not your game."), ephemeral=True
            )
        await _mines_do_pick(interaction, user_id, self.r, self.c)


class _MinesCashoutBtn(discord.ui.Button):
    def __init__(self, message_id: str, label: str, disabled: bool):
        super().__init__(
            style=discord.ButtonStyle.success,
            label=label,
            row=4,
            disabled=disabled,
            custom_id=f"mco_{message_id}",
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = _mines_msg_to_user.get(str(interaction.message.id))
        if not user_id:
            return await interaction.response.send_message(
                embed=_err("Game session not found. It may have expired."), ephemeral=True
            )
        if interaction.user.id != user_id:
            return await interaction.response.send_message(
                embed=_err("This is not your game."), ephemeral=True
            )
        await _mines_do_cashout(interaction, user_id)


class MinesGridView(discord.ui.View):
    def __init__(self, state: dict, message_id: str, user_id: int = 0, game_over: bool = False):
        super().__init__(timeout=None if game_over else GAME_TIMEOUT)
        self.user_id = user_id
        self._state = state
        self._message_id = message_id
        self._game_over = game_over
        mine_set = set(state["mines"])
        revealed = set(state["revealed"])
        ms = _get_mines_settings()
        gem_emoji = _parse_btn_emoji(ms["gem"])
        mine_emoji = _parse_btn_emoji(ms["mine"])
        hidden_emoji = _parse_btn_emoji(ms["hidden"])

        for r in range(4):
            for c in range(5):
                idx = r * 5 + c
                if idx in revealed:
                    is_mine = idx in mine_set
                    btn = discord.ui.Button(
                        style=discord.ButtonStyle.danger if is_mine else discord.ButtonStyle.success,
                        emoji=mine_emoji if is_mine else gem_emoji,
                        row=r, disabled=True,
                        custom_id=f"mr_{r}{c}_{message_id}",
                    )
                elif game_over:
                    is_mine = idx in mine_set
                    btn = discord.ui.Button(
                        style=discord.ButtonStyle.danger if is_mine else discord.ButtonStyle.secondary,
                        emoji=mine_emoji if is_mine else gem_emoji,
                        row=r, disabled=True,
                        custom_id=f"mo_{r}{c}_{message_id}",
                    )
                else:
                    btn = _MinesCell(r, c, message_id, hidden_emoji=hidden_emoji)
                self.add_item(btn)

        mult = state["multiplier"]
        pot = float(state["bet"]) * mult
        cashout_disabled = len(revealed) == 0 or game_over
        if not cashout_disabled:
            cashout_label = f"Cash Out  {mult:.2f}x  ·  {utils.fmt_pts(pot)} pts"
        else:
            cashout_label = "Cash Out"
        self.add_item(_MinesCashoutBtn(message_id, cashout_label, cashout_disabled))

    async def on_timeout(self):
        if self._game_over or not self.user_id:
            return
        sess = await db.get_game_session(self.user_id)
        if not sess or sess["game"] != "mines":
            return
        bet = float(sess["bet"])
        await _refund_game(self.user_id, bet, "mines", note="mines timeout refund")
        if self._message_id:
            _mines_msg_to_user.pop(self._message_id, None)
        if self.message:
            embed = discord.Embed(
                title="⏱ Mines — Timed Out",
                description=f"Bet **{utils.fmt_pts(bet)} pts** refunded.",
                color=0xF39C12,
            )
            try:
                await self.message.edit(
                    embed=embed,
                    view=MinesGridView(self._state, self._message_id, self.user_id, game_over=True),
                )
            except Exception:
                pass


async def _mines_do_pick(interaction: discord.Interaction, user_id: int, r: int, c: int):
    sess = await _ensure_session_active(user_id, "mines")
    if not sess:
        return await interaction.response.send_message(
            embed=_err("No active mines game (may have timed out)."), ephemeral=True
        )
    state = json.loads(sess["state"])

    idx = r * 5 + c
    if idx in state["revealed"]:
        return await interaction.response.send_message(
            embed=_err("Cell already revealed!"), ephemeral=True
        )

    mine_set = set(state["mines"])
    rigged = await bc.should_rig_outcome(user_id, "mines", float(sess["bet"]))

    hit_mine = idx in mine_set
    state["revealed"].append(idx)
    if rigged and not hit_mine and len(state["revealed"]) >= 3:
        if random.random() < 0.4:
            hit_mine = True
            mine_set.add(idx)
            state["mines"] = list(mine_set)

    msg_id = str(interaction.message.id)
    bet = float(sess["bet"])

    if hit_mine:
        await db.clear_game_session(user_id)
        await db.add_wager(user_id, bet)
        await _earn_rakeback(user_id, bet)
        await _record(user_id, False, bet, 0)
        _mines_msg_to_user.pop(msg_id, None)

        ms = _get_mines_settings()
        view = MinesGridView(state, msg_id, user_id, game_over=True)
        embed = discord.Embed(title=f"💥 {ms['game']} — BOOM!", color=0xE74C3C)
        embed.add_field(name="Bet", value=f"`{utils.fmt_pts(bet)} pts`", inline=True)
        embed.add_field(name="Result", value=f"{ms['mine']} Mine hit!", inline=True)
        embed.add_field(name="Lost", value=f"`{utils.fmt_pts(bet)} pts`", inline=True)
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        safe_cells = 20 - state["mine_count"]
        picks = len(state["revealed"])
        mult = round(1.0 + (picks / max(safe_cells, 1)) * (state["mine_count"] / 5), 2)
        state["multiplier"] = mult
        await db.set_game_session(user_id, "mines", bet, json.dumps(state))

        ms = _get_mines_settings()
        view = MinesGridView(state, msg_id, user_id)
        embed = discord.Embed(title=str(ms["game"]), color=0x5865F2)
        embed.add_field(name="Bet", value=f"`{utils.fmt_pts(bet)} pts`", inline=True)
        embed.add_field(name="Mines", value=str(state["mine_count"]), inline=True)
        embed.add_field(name="Multiplier", value=f"`{mult:.2f}x`", inline=True)
        embed.add_field(name="Potential", value=f"`{utils.fmt_pts(bet * mult)} pts`", inline=True)
        embed.set_footer(text="Click cells to reveal. Cash out to collect winnings.")
        await interaction.response.edit_message(embed=embed, view=view)


async def _mines_do_cashout(interaction: discord.Interaction, user_id: int):
    sess = await _ensure_session_active(user_id, "mines")
    if not sess:
        return await interaction.response.send_message(
            embed=_err("No active mines game (may have timed out)."), ephemeral=True
        )
    state = json.loads(sess["state"])
    msg_id = str(interaction.message.id)
    bet = float(sess["bet"])

    if not state["revealed"]:
        await db.clear_game_session(user_id)
        await db.add_balance(user_id, bet, note="mines cancelled")
        _mines_msg_to_user.pop(msg_id, None)
        view = MinesGridView(state, msg_id, user_id, game_over=True)
        return await interaction.response.edit_message(
            embed=discord.Embed(description="No cells revealed — bet refunded.", color=0x5865F2),
            view=view,
        )

    game_cfg = await db.get_game_config("mines")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    gross = bet * state["multiplier"]
    net = gross * (1 - he)

    user = await db.get_user(user_id)
    current_bal = float((user or {}).get("balance", 0))
    net_capped_bal = await bc.apply_balance_cap(user_id, current_bal + net)
    net = max(0.0, net_capped_bal - current_bal)

    await db.add_balance(user_id, net, note="mines cashout")
    await db.add_wager(user_id, bet)
    await _earn_rakeback(user_id, bet)
    await _record(user_id, True, bet, net)
    await db.clear_game_session(user_id)
    _mines_msg_to_user.pop(msg_id, None)

    view = MinesGridView(state, msg_id, user_id, game_over=True)
    embed = discord.Embed(title="💰 Mines — Cashed Out!", color=0x2ECC71)
    embed.add_field(name="Multiplier", value=f"`{state['multiplier']:.2f}x`", inline=True)
    embed.add_field(name="Payout", value=f"`{utils.fmt_pts(net)} pts`", inline=True)
    await interaction.response.edit_message(embed=embed, view=view)


# ─────────────────────────────────────────────────────────────────────────────
# BLACKJACK — GIF animation + button UI
# ─────────────────────────────────────────────────────────────────────────────

_bj_msg_to_user: dict[str, int] = {}  # message_id -> user_id
_bj_user_msg: dict[int, discord.Message] = {}   # user_id -> latest game message


def _cache_bj_msg(user_id: int, msg: discord.Message | None) -> None:
    if msg is not None:
        _bj_user_msg[int(user_id)] = msg


def _make_bj_deck() -> list[str]:
    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    deck  = [f"{r}{s}" for s in suits for r in ranks] * 2
    random.shuffle(deck)
    return deck


async def _bj_start_from_interaction(interaction: discord.Interaction, user_id: int, bet: float):
    """Start (or re-start) a BJ game from a button interaction, editing the current message."""
    if not await _check_game_interaction(interaction, user_id, "blackjack", bet):
        return

    user = await db.get_user(user_id)
    deck   = _make_bj_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]
    state  = {
        "bet": bet, "player": player, "dealer": dealer,
        "deck": deck, "doubled": False,
        "username": interaction.user.display_name,
    }
    await db.set_game_session(user_id, "blackjack", bet, json.dumps(state))
    await db.add_balance(user_id, -bet, note="blackjack re-bet")

    pv       = Games._hand_value_static(player)
    gif_buf  = await image_gen.render_bj_gif(
        player, [dealer[0], "?"],
        bet=bet, username=interaction.user.display_name,
    )
    msg_id   = str(interaction.message.id)
    can_double = float(user["balance"]) - bet >= bet
    view     = _BJView(user_id, msg_id, can_double=can_double)
    _bj_msg_to_user[msg_id] = user_id

    await interaction.response.edit_message(
        attachments=[discord.File(gif_buf, "blackjack.gif")],
        view=view,
    )
    _cache_bj_msg(user_id, interaction.message)
    if pv == 21:
        await asyncio.sleep(0.6)
        msg = interaction.message
        await _bj_finish_interaction_free(
            None, msg, state, "natural_blackjack", user_id,
            username=interaction.user.display_name,
        )


class _BJResultView(discord.ui.LayoutView):
    """Components V2: result GIF + Re-bet / 2× Bet buttons."""

    def __init__(self, user_id: int = 0, bet: float = 0.0):
        super().__init__(timeout=GAME_TIMEOUT)
        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        gallery   = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://blackjack.gif")
        container.add_item(gallery)

        if user_id and bet > 0:
            row = discord.ui.ActionRow()
            rb  = discord.ui.Button(label="Re-bet", style=discord.ButtonStyle.secondary, emoji="🔄")
            rb.callback = self._make_cb(user_id, bet)
            row.add_item(rb)
            x2  = discord.ui.Button(label="2× Bet", style=discord.ButtonStyle.primary, emoji="⬆️")
            x2.callback = self._make_cb(user_id, bet * 2)
            row.add_item(x2)
            container.add_item(row)

        self.add_item(container)

    def _make_cb(self, user_id: int, bet: float):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != user_id:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Not your game."), ephemeral=True,
                )
            await _bj_start_from_interaction(interaction, user_id, bet)
        return _cb


class _BJView(discord.ui.LayoutView):
    """Components V2 LayoutView: Container → MediaGallery (image) + ActionRow (buttons)."""

    def __init__(self, user_id: int, message_id: str = "", can_double: bool = True):
        super().__init__(timeout=GAME_TIMEOUT)
        self.user_id    = user_id
        self.message_id = message_id

        # ── Container wrapping image + buttons ──────────────────────────────
        container = discord.ui.Container(
            accent_colour=discord.Colour.blurple(),
        )

        # Image gallery — references the attached file by name
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://blackjack.gif")
        container.add_item(gallery)

        # Action row with Hit / Stand / Double Down
        row = discord.ui.ActionRow()

        hit_btn = discord.ui.Button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
        hit_btn.callback = self._on_hit
        row.add_item(hit_btn)

        stand_btn = discord.ui.Button(label="Stand", style=discord.ButtonStyle.secondary, emoji="🛑")
        stand_btn.callback = self._on_stand
        row.add_item(stand_btn)

        double_btn = discord.ui.Button(
            label="Double Down", style=discord.ButtonStyle.success,
            emoji="⬆️", disabled=not can_double,
        )
        double_btn.callback = self._on_double
        row.add_item(double_btn)

        container.add_item(row)
        self.add_item(container)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=utils.error_embed("Not your game."), ephemeral=True
            )
            return False
        return True

    async def _on_hit(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await _bj_do_hit(interaction)

    async def _on_stand(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await _bj_do_stand(interaction)

    async def _on_double(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await _bj_do_double(interaction)

    async def on_timeout(self):
        msg = _bj_user_msg.get(self.user_id)
        await _bj_auto_stand(self.user_id, msg)


async def _bj_do_hit(interaction: discord.Interaction):
    user_id = _bj_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True
        )

    sess = await _ensure_session_active(user_id, "blackjack")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active blackjack game (may have timed out)."), ephemeral=True
        )

    state = json.loads(sess["state"])
    prev_count = len(state["player"])           # cards BEFORE new card
    state["player"].append(state["deck"].pop())
    await db.set_game_session(user_id, "blackjack", sess["bet"], json.dumps(state))

    pv = Games._hand_value_static(state["player"])
    username = state.get("username", str(interaction.user.display_name))

    if pv >= 21:
        await _bj_finish_from_interaction(
            interaction, user_id, state,
            "stand" if pv == 21 else "bust",
        )
    else:
        user_data = await db.get_user(user_id)
        can_double = False  # can only double on initial 2 cards
        gif_buf = await image_gen.render_bj_gif(
            state["player"], [state["dealer"][0], "?"],
            animate_from=prev_count,            # only animate the new card
            bet=float(sess["bet"]), username=username,
        )
        view = _BJView(user_id, str(interaction.message.id), can_double=can_double)
        await interaction.response.edit_message(
            attachments=[discord.File(gif_buf, "blackjack.gif")],
            view=view,
        )
        _cache_bj_msg(user_id, interaction.message)


async def _bj_do_stand(interaction: discord.Interaction):
    user_id = _bj_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True
        )

    sess = await _ensure_session_active(user_id, "blackjack")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active blackjack game (may have timed out)."), ephemeral=True
        )

    state = json.loads(sess["state"])
    await _bj_finish_from_interaction(interaction, user_id, state, "stand")


async def _bj_do_double(interaction: discord.Interaction):
    user_id = _bj_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True
        )

    sess = await _ensure_session_active(user_id, "blackjack")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active blackjack game (may have timed out)."), ephemeral=True
        )

    user_data = await db.get_user(user_id)
    if float((user_data or {}).get("balance", 0)) < float(sess["bet"]):
        return await interaction.response.send_message(
            embed=utils.error_embed("Insufficient balance to double down."), ephemeral=True
        )

    await db.add_balance(user_id, -float(sess["bet"]), note="blackjack double")
    state = json.loads(sess["state"])
    state["doubled"] = True
    state["player"].append(state["deck"].pop())
    await db.set_game_session(user_id, "blackjack", sess["bet"], json.dumps(state))
    await _bj_finish_from_interaction(interaction, user_id, state, "stand")


async def _bj_finish_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    state: dict,
    reason: str,
):
    sess = await db.get_game_session(user_id)
    if not sess:
        return
    total_bet = float(sess["bet"]) * (2 if state.get("doubled") else 1)
    username = state.get("username", str(interaction.user.display_name))

    if reason not in ("bust", "natural_blackjack"):
        while Games._hand_value_static(state["dealer"]) < 17:
            state["dealer"].append(state["deck"].pop())

    pv = Games._hand_value_static(state["player"])
    dv = Games._hand_value_static(state["dealer"])

    if reason == "natural_blackjack":
        outcome, gross, won = "BLACKJACK", total_bet * 2.5, True
    elif reason == "bust" or pv > 21:
        outcome, gross, won = "BUST", 0.0, False
    elif dv > 21 or pv > dv:
        outcome, gross, won = "WIN", total_bet * 2, True
    elif pv == dv:
        outcome, gross, won = "PUSH", total_bet, False
    else:
        outcome, gross, won = "LOSS", 0.0, False

    game_cfg = await db.get_game_config("blackjack")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    net = gross * (1 - he) if gross > 0 else 0.0

    if net > 0:
        bal = float((await db.get_user(user_id) or {}).get("balance", 0))
        net_capped = await bc.apply_balance_cap(user_id, bal + net)
        net = max(0.0, net_capped - bal)
        await db.add_balance(user_id, net, note="blackjack payout")

    await db.add_wager(user_id, total_bet)
    await _earn_rakeback(user_id, total_bet)
    await _record(user_id, won, total_bet, net)
    await db.clear_game_session(user_id)
    _bj_msg_to_user.pop(str(interaction.message.id), None)
    _bj_user_msg.pop(int(user_id), None)

    net_change = (net - total_bet) if won else (-total_bet if outcome != "PUSH" else 0.0)
    gif_buf = await image_gen.render_bj_gif(
        state["player"], state["dealer"],
        reveal_dealer=True, result_text=outcome,
        net_change=net_change, bet=total_bet, username=username,
    )

    try:
        await interaction.response.edit_message(
            attachments=[discord.File(gif_buf, "blackjack.gif")],
            view=_BJResultView(user_id, total_bet),
        )
    except Exception:
        try:
            gif_buf.seek(0)
            await interaction.followup.send(file=discord.File(gif_buf, "blackjack.gif"))
        except Exception:
            pass


async def _bj_finish_interaction_free(
    ctx: "commands.Context | None",
    msg: discord.Message,
    state: dict,
    reason: str,
    user_id: int,
    username: str = "",
):
    """Finish a BJ game without an active interaction — used for natural blackjack on start."""
    sess = await db.get_game_session(user_id)
    if not sess:
        return
    total_bet = float(sess["bet"]) * (2 if state.get("doubled") else 1)
    if not username:
        username = state.get("username", (ctx.author.display_name if ctx else "Player"))

    if reason == "natural_blackjack":
        outcome, gross, won = "BLACKJACK", total_bet * 2.5, True
    else:
        outcome, gross, won = "LOSS", 0.0, False

    game_cfg = await db.get_game_config("blackjack")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    net = gross * (1 - he) if gross > 0 else 0.0

    if net > 0:
        bal = float((await db.get_user(user_id) or {}).get("balance", 0))
        net_capped = await bc.apply_balance_cap(user_id, bal + net)
        net = max(0.0, net_capped - bal)
        await db.add_balance(user_id, net, note="blackjack payout")

    await db.add_wager(user_id, total_bet)
    await _earn_rakeback(user_id, total_bet)
    await _record(user_id, won, total_bet, net)
    await db.clear_game_session(user_id)
    _bj_msg_to_user.pop(str(msg.id), None)
    _bj_user_msg.pop(int(user_id), None)

    net_change = (net - total_bet) if won else 0.0
    gif_buf = await image_gen.render_bj_gif(
        state["player"], state["dealer"],
        reveal_dealer=True, result_text=outcome,
        net_change=net_change, bet=total_bet, username=username,
    )
    try:
        await msg.edit(attachments=[discord.File(gif_buf, "blackjack.gif")], view=_BJResultView(user_id, total_bet))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────

class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Coin Flip ─────────────────────────────────────────────────────────────

    @commands.command(name="coinflip", aliases=["cf", "flip"])
    async def coinflip(self, ctx: commands.Context, amount: float, choice: str = ""):
        """Flip a coin. .coinflip 100 [hot/cold]"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "coinflip", amount):
            return

        sides = ["HOT", "COLD"]
        if choice.upper() in sides:
            player_side = choice.upper()
        else:
            player_side = random.choice(sides)

        rigged = await bc.should_rig_outcome(ctx.author.id, "coinflip", amount)
        if rigged:
            result = "COLD" if player_side == "HOT" else "HOT"
        else:
            result = random.choice(sides)

        won = result == player_side
        gross = amount * 2 if won else 0
        net = await _payout(ctx.author.id, "coinflip", amount, gross)
        await _record(ctx.author.id, won, amount, net)

        outcome = "WIN" if won else "LOSS"
        img_buf = await image_gen.render_game_result_card(
            "Coin Flip", outcome, amount, net,
            details={"Your pick": player_side, "Result": result},
        )
        await ctx.send(
            content=f"{'🏆' if won else '💔'} **{outcome}!** {ctx.author.mention}",
            file=discord.File(img_buf, "coinflip.png"),
        )

    # ── Dice ──────────────────────────────────────────────────────────────────

    @commands.command(name="dice", aliases=["roll"])
    async def dice(self, ctx: commands.Context, amount: float):
        """Roll dice vs house (highest wins). .dice 100"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "dice", amount):
            return

        player_roll = random.randint(1, 6)
        rigged = await bc.should_rig_outcome(ctx.author.id, "dice", amount)
        if rigged:
            house_roll = random.randint(max(player_roll, 1), 6)
        else:
            house_roll = random.randint(1, 6)

        if player_roll > house_roll:
            won, gross, outcome = True, amount * 2, "WIN"
        elif player_roll == house_roll:
            won, gross, outcome = False, amount, "TIE"
        else:
            won, gross, outcome = False, 0, "LOSS"

        net = await _payout(ctx.author.id, "dice", amount, gross)
        await _record(ctx.author.id, won, amount, net)

        img_buf = await image_gen.render_game_result_card(
            "Dice", outcome, amount, net,
            details={"Your roll": f"🎲 {player_roll}", "House roll": f"🎲 {house_roll}"},
        )
        await ctx.send(
            content=f"{'🏆' if won else ('⚖️' if outcome == 'TIE' else '💔')} **{outcome}!** {ctx.author.mention}",
            file=discord.File(img_buf, "dice.png"),
        )

    # ── Roulette ──────────────────────────────────────────────────────────────

    @commands.command(name="roulette", aliases=["rl"])
    async def roulette(self, ctx: commands.Context, amount: float):
        """Roulette vs house (highest number wins). .roulette 100"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "roulette", amount):
            return

        player_num = random.randint(0, 36)
        rigged = await bc.should_rig_outcome(ctx.author.id, "roulette", amount)
        if rigged:
            house_num = random.randint(max(player_num, 0), 36)
        else:
            house_num = random.randint(0, 36)

        if player_num > house_num:
            won, gross, outcome = True, amount * 2, "WIN"
        elif player_num == house_num:
            won, gross, outcome = False, amount, "TIE"
        else:
            won, gross, outcome = False, 0, "LOSS"

        net = await _payout(ctx.author.id, "roulette", amount, gross)
        await _record(ctx.author.id, won, amount, net)

        img_buf = await image_gen.render_game_result_card(
            "Roulette", outcome, amount, net,
            details={"Your number": player_num, "House number": house_num},
        )
        await ctx.send(
            content=f"{'🏆' if won else ('⚖️' if outcome == 'TIE' else '💔')} **{outcome}!** {ctx.author.mention}",
            file=discord.File(img_buf, "roulette.png"),
        )

    # ── Limbo ─────────────────────────────────────────────────────────────────

    @commands.command(name="limbo")
    async def limbo(self, ctx: commands.Context, amount: float, target: float = 2.0):
        """Limbo — crash below target to win. .limbo 100 2.5"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "limbo", amount):
            return
        if target < 1.01 or target > 1000:
            return await ctx.send(embed=_err("Target must be between 1.01 and 1000."))

        rigged = await bc.should_rig_outcome(ctx.author.id, "limbo", amount)
        if rigged:
            crash = round(random.uniform(1.0, max(1.01, target - 0.01)), 2)
        else:
            crash = round(random.uniform(1.0, target * 2), 2)
            crash = max(1.0, crash)

        won = crash >= target
        gross = amount * target if won else 0
        outcome = "WIN" if won else "LOSS"

        net = await _payout(ctx.author.id, "limbo", amount, gross)
        await _record(ctx.author.id, won, amount, net)

        img_buf = await image_gen.render_game_result_card(
            "Limbo", outcome, amount, net,
            details={"Target": f"{target:.2f}x", "Crash point": f"{crash:.2f}x"},
        )
        await ctx.send(
            content=f"{'🏆' if won else '💔'} **{outcome}!** {ctx.author.mention}",
            file=discord.File(img_buf, "limbo.png"),
        )

    # ── Slots ─────────────────────────────────────────────────────────────────

    SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎", "7️⃣"]
    SLOT_PAYOUTS = {
        "7️⃣": 10.0,
        "💎": 7.0,
        "⭐": 5.0,
        "🍇": 4.0,
        "🍊": 3.0,
        "🍋": 2.5,
        "🍒": 2.0,
    }

    @commands.command(name="slots", aliases=["slot"])
    async def slots(self, ctx: commands.Context, amount: float):
        """Spin the slot machine. .slots 100"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "slots", amount):
            return

        rigged = await bc.should_rig_outcome(ctx.author.id, "slots", amount)

        if rigged:
            reels = [random.choice(self.SLOT_SYMBOLS) for _ in range(3)]
            while len(set(reels)) == 1:
                reels = [random.choice(self.SLOT_SYMBOLS) for _ in range(3)]
        else:
            if random.random() < 0.30:
                sym = random.choice(self.SLOT_SYMBOLS)
                reels = [sym, sym, sym]
            elif random.random() < 0.45:
                sym = random.choice(self.SLOT_SYMBOLS)
                reels = [sym, sym, random.choice(self.SLOT_SYMBOLS)]
                random.shuffle(reels)
            else:
                reels = [random.choice(self.SLOT_SYMBOLS) for _ in range(3)]

        if len(set(reels)) == 1:
            multi = self.SLOT_PAYOUTS.get(reels[0], 2.0)
            gross = amount * multi
        elif len(set(reels)) == 2:
            gross = amount * 1.5
        else:
            gross = 0

        won = gross > 0
        net = await _payout(ctx.author.id, "slots", amount, gross)
        await _record(ctx.author.id, won, amount, net)

        loop = asyncio.get_event_loop()
        img_buf = await loop.run_in_executor(
            None, image_gen.render_slots_card, reels, amount, net
        )
        outcome = "WIN" if won else "LOSS"
        await ctx.send(
            content=f"{'🏆' if won else '💔'} **{outcome}!** {ctx.author.mention}",
            file=discord.File(img_buf, "slots.png"),
        )

    # ── Crash ─────────────────────────────────────────────────────────────────

    @commands.command(name="crash")
    async def crash(self, ctx: commands.Context, amount: float, auto_cashout: float = 0):
        """Crash game. .crash 100 [auto_cashout_multiplier]"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "crash", amount):
            return
        if auto_cashout and auto_cashout < 1.01:
            return await ctx.send(embed=_err("Auto cashout must be >= 1.01."))

        rigged = await bc.should_rig_outcome(ctx.author.id, "crash", amount)

        if rigged:
            crash_point = round(random.uniform(1.0, 1.8), 2)
        else:
            r = random.random()
            crash_point = round(min(0.99 / max(1 - r, 0.01), 1000.0), 2)

        if auto_cashout and auto_cashout <= crash_point:
            multi = auto_cashout
            won = True
            outcome = f"CASHED OUT @ {multi:.2f}x"
        elif not auto_cashout and crash_point > 1.0:
            multi = round(random.uniform(1.0, crash_point), 2)
            won = True
            outcome = f"CASHED OUT @ {multi:.2f}x"
        else:
            multi = crash_point
            won = False
            outcome = f"CRASHED @ {crash_point:.2f}x"

        gross = amount * multi if won else 0
        net = await _payout(ctx.author.id, "crash", amount, gross)
        await _record(ctx.author.id, won, amount, net)

        img_buf = await image_gen.render_game_result_card(
            "Crash", "WIN" if won else "CRASH", amount, net,
            details={"Crash point": f"{crash_point:.2f}x", "Outcome": outcome},
        )
        await ctx.send(
            content=f"{'🏆' if won else '💔'} **{outcome}!** {ctx.author.mention}",
            file=discord.File(img_buf, "crash.png"),
        )

    # ── Blackjack ─────────────────────────────────────────────────────────────

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx: commands.Context, amount: float):
        """Start a blackjack game. .blackjack 100"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "blackjack", amount):
            return

        deck = self._new_deck()
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]

        state = {
            "bet": amount, "player": player, "dealer": dealer,
            "deck": deck, "doubled": False,
            "username": ctx.author.display_name,
        }
        await db.set_game_session(ctx.author.id, "blackjack", amount, json.dumps(state))
        await db.add_balance(ctx.author.id, -amount, note="blackjack bet")

        user_data = await db.get_user(ctx.author.id)
        can_double = float((user_data or {}).get("balance", 0)) >= amount
        pv = self._hand_value(player)

        gif_buf = await image_gen.render_bj_gif(
            player, [dealer[0], "?"],
            bet=amount, username=ctx.author.display_name,
        )
        view = _BJView(ctx.author.id, "pending", can_double=can_double)
        msg = await ctx.send(file=discord.File(gif_buf, "blackjack.gif"), view=view)
        _bj_msg_to_user[str(msg.id)] = ctx.author.id
        _cache_bj_msg(ctx.author.id, msg)
        view.message_id = str(msg.id)

        if pv == 21:
            await asyncio.sleep(0.6)
            await _bj_finish_interaction_free(ctx, msg, state, "natural_blackjack", ctx.author.id)

    @commands.command(name="hit")
    async def bj_hit(self, ctx: commands.Context):
        """Hit in blackjack (prefix fallback)."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "blackjack":
            return await ctx.send(embed=_err("No active blackjack game. Start with `.blackjack <amount>`."))
        state = json.loads(sess["state"])
        state["player"].append(state["deck"].pop())
        await db.set_game_session(ctx.author.id, "blackjack", sess["bet"], json.dumps(state))
        pv = self._hand_value(state["player"])
        if pv > 21:
            await self._bj_finish(ctx, "bust", state=state)
        elif pv == 21:
            await self._bj_finish(ctx, "stand", state=state)
        else:
            gif_buf = await image_gen.render_bj_gif(
                state["player"], [state["dealer"][0], "?"],
                bet=float(sess["bet"]),
                username=state.get("username", ctx.author.display_name),
            )
            await ctx.send(file=discord.File(gif_buf, "blackjack.gif"))

    @commands.command(name="stand")
    async def bj_stand(self, ctx: commands.Context):
        """Stand in blackjack (prefix fallback)."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "blackjack":
            return await ctx.send(embed=_err("No active blackjack game."))
        await self._bj_finish(ctx, "stand")

    @commands.command(name="double")
    async def bj_double(self, ctx: commands.Context):
        """Double down in blackjack (prefix fallback)."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "blackjack":
            return await ctx.send(embed=_err("No active blackjack game."))
        state = json.loads(sess["state"])
        user = await db.get_user(ctx.author.id)
        if float(user["balance"]) < float(sess["bet"]):
            return await ctx.send(embed=_err("Insufficient balance to double."))
        await db.add_balance(ctx.author.id, -float(sess["bet"]), note="blackjack double")
        state["doubled"] = True
        state["player"].append(state["deck"].pop())
        await self._bj_finish(ctx, "stand", state=state)

    async def _bj_finish(self, ctx: commands.Context, reason: str, state: dict | None = None):
        """Prefix-command BJ finish (sends a new message with result GIF)."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess:
            return
        if not state:
            state = json.loads(sess["state"])
        total_bet = float(sess["bet"]) * (2 if state.get("doubled") else 1)
        username = state.get("username", ctx.author.display_name)

        if reason not in ("bust", "natural_blackjack"):
            while self._hand_value(state["dealer"]) < 17:
                state["dealer"].append(state["deck"].pop())

        pv = self._hand_value(state["player"])
        dv = self._hand_value(state["dealer"])

        if reason == "natural_blackjack":
            outcome, gross, won = "BLACKJACK", total_bet * 2.5, True
        elif reason == "bust" or pv > 21:
            outcome, gross, won = "BUST", 0.0, False
        elif dv > 21 or pv > dv:
            outcome, gross, won = "WIN", total_bet * 2, True
        elif pv == dv:
            outcome, gross, won = "PUSH", total_bet, False
        else:
            outcome, gross, won = "LOSS", 0.0, False

        game_cfg = await db.get_game_config("blackjack")
        he = float(game_cfg["house_edge"]) if game_cfg else 0.02
        net = gross * (1 - he) if gross > 0 else 0.0
        if net > 0:
            bal = float((await db.get_user(ctx.author.id) or {}).get("balance", 0))
            net_capped = await bc.apply_balance_cap(ctx.author.id, bal + net)
            net = max(0.0, net_capped - bal)
            await db.add_balance(ctx.author.id, net, note="blackjack payout")
        await db.add_wager(ctx.author.id, total_bet)
        await _earn_rakeback(ctx.author.id, total_bet, ctx.author)
        await _record(ctx.author.id, won, total_bet, net)
        await db.clear_game_session(ctx.author.id)
        for mid, uid in list(_bj_msg_to_user.items()):
            if uid == ctx.author.id:
                _bj_msg_to_user.pop(mid, None)

        net_change = (net - total_bet) if won else (-total_bet if outcome != "PUSH" else 0.0)
        gif_buf = await image_gen.render_bj_gif(
            state["player"], state["dealer"],
            reveal_dealer=True, result_text=outcome,
            net_change=net_change, bet=total_bet, username=username,
        )
        await ctx.send(file=discord.File(gif_buf, "blackjack.gif"))

    def _new_deck(self) -> list[str]:
        suits = ["♠", "♥", "♦", "♣"]
        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        return [f"{r}{s}" for s in suits for r in ranks] * 2

    def _hand_value(self, hand: list[str]) -> int:
        return Games._hand_value_static(hand)

    @staticmethod
    def _hand_value_static(hand: list[str]) -> int:
        total, aces = 0, 0
        for card in hand:
            if card == "?":
                continue
            rank = card[:-1] if len(card) > 1 else card
            if rank in ("J", "Q", "K"):
                total += 10
            elif rank == "A":
                total += 11
                aces += 1
            else:
                try:
                    total += int(rank)
                except ValueError:
                    pass
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    # ── Hi-Lo ─────────────────────────────────────────────────────────────────

    @commands.command(name="hilo", aliases=["hl"])
    async def hilo(self, ctx: commands.Context, amount: float):
        """Start a Hi-Lo card game. .hilo 100 — then .higher / .lower / .cashout"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "hilo", amount):
            return

        deck = self._new_deck()
        random.shuffle(deck)
        current = deck.pop()
        state = {
            "bet": amount,
            "current": current,
            "deck": deck,
            "multiplier": 1.0,
            "streak": 0,
        }
        await db.set_game_session(ctx.author.id, "hilo", amount, json.dumps(state))
        await db.add_balance(ctx.author.id, -amount, note="hilo bet")

        embed = discord.Embed(title="🃏 Hi-Lo", color=0x5865F2)
        embed.add_field(name="Current Card", value=f"`{current}`", inline=True)
        embed.add_field(name="Multiplier", value="`1.00x`", inline=True)
        embed.set_footer(text="Use .higher / .lower to predict, or .cashout to take winnings")
        await ctx.send(embed=embed)

    @commands.command(name="higher")
    async def hilo_higher(self, ctx: commands.Context):
        """Predict higher in Hi-Lo."""
        await self._hilo_guess(ctx, "higher")

    @commands.command(name="lower")
    async def hilo_lower(self, ctx: commands.Context):
        """Predict lower in Hi-Lo."""
        await self._hilo_guess(ctx, "lower")

    async def _hilo_guess(self, ctx: commands.Context, guess: str):
        sess = await _ensure_session_active(ctx.author.id, "hilo")
        if not sess:
            return await ctx.send(embed=_err("No active Hi-Lo game (may have timed out — bet refunded)."))
        state = json.loads(sess["state"])

        current_rank = self._card_rank(state["current"])
        next_card = state["deck"].pop() if state["deck"] else self._new_deck()[random.randint(0, 51)]
        next_rank = self._card_rank(next_card)

        rigged = await bc.should_rig_outcome(ctx.author.id, "hilo", sess["bet"])

        if rigged:
            if guess == "higher":
                next_rank = max(1, current_rank - 1)
            else:
                next_rank = min(13, current_rank + 1)
            next_card = f"{['A','2','3','4','5','6','7','8','9','10','J','Q','K'][next_rank-1]}♠"

        correct = (guess == "higher" and next_rank > current_rank) or \
                  (guess == "lower" and next_rank < current_rank)
        tie = next_rank == current_rank

        if tie:
            state["current"] = next_card
            await db.set_game_session(ctx.author.id, "hilo", sess["bet"], json.dumps(state))
            embed = discord.Embed(title="🃏 Hi-Lo — TIE", color=0xF1C40F)
            embed.add_field(name="New Card", value=f"`{next_card}`", inline=True)
            embed.add_field(name="Multiplier", value=f"`{state['multiplier']:.2f}x`", inline=True)
            embed.set_footer(text="Tie — same card. Continue guessing!")
            return await ctx.send(embed=embed)

        if correct:
            state["multiplier"] = round(state["multiplier"] * 1.5, 2)
            state["streak"] = state.get("streak", 0) + 1
            state["current"] = next_card
            await db.set_game_session(ctx.author.id, "hilo", sess["bet"], json.dumps(state))
            embed = discord.Embed(title="🃏 Hi-Lo — Correct!", color=0x2ECC71)
            embed.add_field(name="New Card", value=f"`{next_card}`", inline=True)
            embed.add_field(name="Multiplier", value=f"`{state['multiplier']:.2f}x`", inline=True)
            embed.add_field(name="Potential Win", value=f"`{utils.fmt_pts(sess['bet'] * state['multiplier'])} pts`", inline=True)
            embed.set_footer(text=".higher / .lower / .cashout")
            await ctx.send(embed=embed)
        else:
            await db.clear_game_session(ctx.author.id)
            await db.add_wager(ctx.author.id, sess["bet"])
            await _earn_rakeback(ctx.author.id, sess["bet"], ctx.author)
            await _record(ctx.author.id, False, sess["bet"], 0)
            embed = discord.Embed(title="🃏 Hi-Lo — WRONG!", color=0xE74C3C)
            embed.add_field(name="New Card", value=f"`{next_card}`", inline=True)
            embed.add_field(name="Lost", value=f"`{utils.fmt_pts(sess['bet'])} pts`", inline=True)
            await ctx.send(embed=embed)

    @commands.command(name="cashout")
    async def hilo_cashout(self, ctx: commands.Context):
        """Cash out Hi-Lo or Mines winnings."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess:
            return await ctx.send(embed=_err("No active game to cash out."))
        if _session_expired(sess):
            await _resolve_expired_session(ctx.author.id, sess)
            return await ctx.send(embed=_err(f"Game timed out — bet refunded."))

        if sess["game"] == "hilo":
            state = json.loads(sess["state"])
            gross = sess["bet"] * state["multiplier"]
            game_cfg = await db.get_game_config("hilo")
            he = float(game_cfg["house_edge"]) if game_cfg else 0.02
            actual_net = gross * (1 - he)
            user = await db.get_user(ctx.author.id)
            current_bal = float((user or {}).get("balance", 0))
            net_capped_bal = await bc.apply_balance_cap(ctx.author.id, current_bal + actual_net)
            actual_net = max(0.0, net_capped_bal - current_bal)
            await db.add_balance(ctx.author.id, actual_net, note="hilo cashout")
            await db.add_wager(ctx.author.id, sess["bet"])
            await _earn_rakeback(ctx.author.id, sess["bet"], ctx.author)
            await _record(ctx.author.id, True, sess["bet"], actual_net)
            await db.clear_game_session(ctx.author.id)
            embed = discord.Embed(title="🃏 Hi-Lo — Cashed Out!", color=0x2ECC71)
            embed.add_field(name="Multiplier", value=f"`{state['multiplier']:.2f}x`", inline=True)
            embed.add_field(name="Payout", value=f"`{utils.fmt_pts(actual_net)} pts`", inline=True)
            await ctx.send(embed=embed)

        elif sess["game"] == "mines":
            await self._mines_cashout(ctx, sess)
        else:
            await ctx.send(embed=_err(f"No cashout available for {sess['game']}."))

    def _card_rank(self, card: str) -> int:
        rank_map = {"A": 1, "J": 11, "Q": 12, "K": 13}
        rank_str = card[:-1] if len(card) > 1 else card
        return rank_map.get(rank_str, int(rank_str) if rank_str.isdigit() else 1)

    # ── Mines ─────────────────────────────────────────────────────────────────

    @commands.command(name="mines")
    async def mines(self, ctx: commands.Context, amount: float, mine_count: int = 3):
        """Start a mines game. .mines 100 3 — click grid buttons to reveal, cashout button to collect."""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "mines", amount):
            return
        if not 1 <= mine_count <= 19:
            return await ctx.send(embed=_err("Mine count must be between 1 and 19."))

        total = 20  # 4 rows × 5 cols
        mine_positions = random.sample(range(total), mine_count)
        state = {
            "bet": amount,
            "mines": mine_positions,
            "revealed": [],
            "multiplier": 1.0,
            "mine_count": mine_count,
        }
        await db.set_game_session(ctx.author.id, "mines", amount, json.dumps(state))
        await db.add_balance(ctx.author.id, -amount, note="mines bet")

        ms = _get_mines_settings()
        view = MinesGridView(state, "pending", ctx.author.id)
        embed = discord.Embed(title=str(ms["game"]), color=0x5865F2)
        embed.add_field(name="Bet", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        embed.add_field(name="Mines", value=str(mine_count), inline=True)
        embed.add_field(name="Multiplier", value="`1.00x`", inline=True)
        embed.add_field(name="Potential", value=f"`{utils.fmt_pts(amount)} pts`", inline=True)
        embed.set_footer(text="Click cells to reveal. Cash out to collect winnings.")

        msg = await ctx.send(embed=embed, view=view)
        _mines_msg_to_user[str(msg.id)] = ctx.author.id
        # Re-render with correct message_id so custom_ids are unique per game
        view2 = MinesGridView(state, str(msg.id), ctx.author.id)
        await msg.edit(view=view2)

    # ── Crystals ───────────────────────────────────────────────────────────────

    @commands.command(name="crystals", aliases=["crystal", "cr"])
    async def crystals(self, ctx: commands.Context, amount: str = ""):
        """Reveal 5 crystals and match for prizes.  .crystals <bet>"""
        if not amount:
            return await ctx.send(embed=_err("Usage: `.crystals <bet>`"))
        try:
            bet = float(amount.replace(",", ""))
        except ValueError:
            return await ctx.send(embed=_err("Invalid bet amount."))

        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "crystals", bet):
            return

        await db.add_balance(ctx.author.id, -bet, note="crystals bet")
        gif, _ = await _crystals_play(bet, ctx.author.display_name, ctx.author.id)
        await ctx.send(
            file=discord.File(gif, "crystals.gif"),
            view=_CrystalsResultView(ctx.author.id, bet),
        )

    # ── Towers ─────────────────────────────────────────────────────────────────

    @commands.command(name="towers", aliases=["tw"])
    async def towers(self, ctx: commands.Context, amount: str = "", mode: str = "easy"):
        """Start a Towers game.  .towers <bet> [easy|normal|hard]"""
        if not amount:
            return await ctx.send(embed=_err("Usage: `.towers <bet> [easy|normal|hard]`"))

        try:
            bet = float(amount.replace(",", ""))
        except ValueError:
            return await ctx.send(embed=_err("Invalid bet amount."))

        mode = mode.lower()
        if mode not in ("easy", "normal", "hard"):
            return await ctx.send(embed=_err("Mode must be **easy**, **normal**, or **hard**.  `.towers <bet> [easy|normal|hard]`"))

        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "towers", bet):
            return

        # Generate the 10×4 grid
        bombs_per_floor = image_gen.TOWERS_BOMBS[mode]
        grid: list[list[str]] = []
        for _ in range(10):
            cells = ["bomb"] * bombs_per_floor + ["gem"] * (4 - bombs_per_floor)
            random.shuffle(cells)
            grid.append(cells)

        state = {
            "mode":     mode,
            "floor":    0,
            "grid":     grid,
            "picks":    [None] * 10,
            "username": ctx.author.display_name,
        }
        await db.set_game_session(ctx.author.id, "towers", bet, json.dumps(state))
        await db.add_balance(ctx.author.id, -bet, note="towers bet")

        gif = await image_gen.render_towers_gif(
            grid, state["picks"], 0, mode, bet, ctx.author.display_name,
        )
        view = _TowersView(ctx.author.id, "", mode, can_cashout=False)
        msg  = await ctx.send(
            file=discord.File(gif, "towers.gif"),
            view=view,
        )
        _tw_msg_to_user[str(msg.id)] = ctx.author.id
        _cache_tw_msg(ctx.author.id, msg)
        # Rebuild view with correct message_id so button callbacks resolve correctly
        view2 = _TowersView(ctx.author.id, str(msg.id), mode, can_cashout=False)
        await msg.edit(view=view2)

    # ── Chicken Road ───────────────────────────────────────────────────────────

    @commands.command(name="chickenroad", aliases=["chicken", "chkn", "crroad"])
    async def chickenroad(self, ctx: commands.Context, amount: str = "", mode: str = "easy"):
        """Cross the road — cash out anytime.  .chickenroad <bet> [easy|normal|hard]"""
        if not amount:
            return await ctx.send(embed=_err("Usage: `.chickenroad <bet> [easy|normal|hard]`"))

        try:
            bet = float(amount.replace(",", ""))
        except ValueError:
            return await ctx.send(embed=_err("Invalid bet amount."))

        mode = mode.lower()
        if mode not in ("easy", "normal", "hard"):
            return await ctx.send(embed=_err(
                "Mode must be **easy**, **normal**, or **hard**.  `.chickenroad <bet> [easy|normal|hard]`"
            ))

        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "chicken_road", bet):
            return

        num = image_gen.chicken_road_num_steps(mode)
        prob = image_gen.CHICKEN_CRASH_PROB[mode]
        lanes = ["crash" if random.random() < prob else "safe" for _ in range(num)]

        state = {
            "mode": mode,
            "step": 0,
            "lanes": lanes,
            "username": ctx.author.display_name,
        }
        await db.set_game_session(ctx.author.id, "chicken_road", bet, json.dumps(state))
        await db.add_balance(ctx.author.id, -bet, note="chicken road bet")

        gif = await image_gen.render_chicken_road_gif(
            0, mode, bet, ctx.author.display_name,
        )
        view = _ChickenRoadView(ctx.author.id, "", mode, can_cashout=False)
        msg = await ctx.send(
            file=discord.File(gif, "chickenroad.gif"),
            view=view,
        )
        _cr_msg_to_user[str(msg.id)] = ctx.author.id
        _cache_cr_msg(ctx.author.id, msg)
        view2 = _ChickenRoadView(ctx.author.id, str(msg.id), mode, can_cashout=False)
        await msg.edit(view=view2)

    async def _mines_cashout(self, ctx: commands.Context, sess: dict):
        """Prefix fallback cashout for mines."""
        state = json.loads(sess["state"])
        user_id = ctx.author.id
        bet = float(sess["bet"])

        if not state["revealed"]:
            await db.clear_game_session(user_id)
            await db.add_balance(user_id, bet, note="mines cancelled")
            # Remove any stale message mapping
            for mid, uid in list(_mines_msg_to_user.items()):
                if uid == user_id:
                    _mines_msg_to_user.pop(mid, None)
            return await ctx.send(
                embed=discord.Embed(description="No cells revealed — bet refunded.", color=0x5865F2)
            )

        game_cfg = await db.get_game_config("mines")
        he = float(game_cfg["house_edge"]) if game_cfg else 0.02
        gross = bet * state["multiplier"]
        net = gross * (1 - he)
        user = await db.get_user(user_id)
        current_bal = float((user or {}).get("balance", 0))
        net_capped_bal = await bc.apply_balance_cap(user_id, current_bal + net)
        net = max(0.0, net_capped_bal - current_bal)
        await db.add_balance(user_id, net, note="mines cashout")
        await db.add_wager(user_id, bet)
        await _earn_rakeback(user_id, bet, interaction.user if isinstance(interaction.user, discord.Member) else None)
        await _record(user_id, True, bet, net)
        await db.clear_game_session(user_id)
        for mid, uid in list(_mines_msg_to_user.items()):
            if uid == user_id:
                _mines_msg_to_user.pop(mid, None)

        await ctx.send(embed=discord.Embed(
            title="💰 Mines — Cashed Out!",
            description=f"Multiplier: **{state['multiplier']:.2f}x** | Payout: **{utils.fmt_pts(net)} pts**",
            color=0x2ECC71,
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))


# ─────────────────────────────────────────────────────────────────────────────
# CRYSTALS — 5-crystal reveal matcher, image-based GIF
# ─────────────────────────────────────────────────────────────────────────────


async def _crystals_play(bet: float, username: str, user_id: int) -> tuple[io.BytesIO, float]:
    """Run crystals round: deduct bet, compute outcome, return reveal GIF + net_change."""
    crystals = random.choices(image_gen.CRYSTAL_TYPES, k=5)
    combo    = image_gen.crystals_get_combo(crystals)
    mult     = image_gen.CRYSTALS_MULTS[combo]

    game_cfg = await db.get_game_config("crystals")
    he       = float(game_cfg["house_edge"]) if game_cfg else 0.02
    gross    = bet * mult
    net      = gross * (1 - he) if gross > 0 else 0.0

    won = mult >= 1.0
    if net > 0:
        user    = await db.get_user(user_id)
        cur_bal = float((user or {}).get("balance", 0))
        net = max(0.0, (await bc.apply_balance_cap(user_id, cur_bal + net)) - cur_bal)
        await db.add_balance(user_id, net, note="crystals payout")

    await db.add_wager(user_id, bet)
    await _earn_rakeback(user_id, bet, interaction.user if isinstance(interaction.user, discord.Member) else None)
    await _record(user_id, won, bet, net if won else 0.0)

    net_change = (net - bet) if won else -bet
    gif = await image_gen.render_crystals_gif(
        crystals, combo, mult, bet, username, net_change, reveal_count=5,
    )
    return gif, net_change


async def _crystals_start_from_interaction(
    interaction: discord.Interaction, user_id: int, bet: float,
):
    if not await _check_game_interaction(interaction, user_id, "crystals", bet):
        return

    await db.add_balance(user_id, -bet, note="crystals re-bet")
    gif, _ = await _crystals_play(bet, interaction.user.display_name, user_id)
    await interaction.response.edit_message(
        attachments=[discord.File(gif, "crystals.gif")],
        view=_CrystalsResultView(user_id, bet),
    )


class _CrystalsResultView(discord.ui.LayoutView):
    def __init__(self, user_id: int = 0, bet: float = 0.0):
        super().__init__(timeout=GAME_TIMEOUT)
        c = discord.ui.Container(accent_colour=discord.Colour.purple())
        g = discord.ui.MediaGallery()
        g.add_item(media="attachment://crystals.gif")
        c.add_item(g)

        if user_id and bet > 0:
            row = discord.ui.ActionRow()
            rb  = discord.ui.Button(label="Re-bet", style=discord.ButtonStyle.secondary, emoji="🔄")
            rb.callback = self._make_cb(user_id, bet)
            row.add_item(rb)
            x2  = discord.ui.Button(label="2× Bet", style=discord.ButtonStyle.primary, emoji="⬆️")
            x2.callback = self._make_cb(user_id, bet * 2)
            row.add_item(x2)
            c.add_item(row)

        self.add_item(c)

    def _make_cb(self, user_id: int, bet: float):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != user_id:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Not your game."), ephemeral=True,
                )
            await _crystals_start_from_interaction(interaction, user_id, bet)
        return _cb


# ─────────────────────────────────────────────────────────────────────────────
# TOWERS — image-based, button-driven, 10-floor climb
# ─────────────────────────────────────────────────────────────────────────────

_tw_msg_to_user: dict[str, int] = {}   # message_id → user_id
_tw_user_msg: dict[int, discord.Message] = {}


def _cache_tw_msg(user_id: int, msg: discord.Message | None) -> None:
    if msg is not None:
        _tw_user_msg[int(user_id)] = msg


async def _towers_start_from_interaction(
    interaction: discord.Interaction, user_id: int, bet: float, mode: str,
):
    if not await _check_game_interaction(interaction, user_id, "towers", bet):
        return

    bombs_per_floor = image_gen.TOWERS_BOMBS[mode]
    grid: list[list[str]] = []
    for _ in range(10):
        cells = ["bomb"] * bombs_per_floor + ["gem"] * (4 - bombs_per_floor)
        random.shuffle(cells)
        grid.append(cells)

    state = {
        "mode": mode, "floor": 0, "grid": grid,
        "picks": [None] * 10, "username": interaction.user.display_name,
    }
    await db.set_game_session(user_id, "towers", bet, json.dumps(state))
    await db.add_balance(user_id, -bet, note="towers re-bet")

    gif  = await image_gen.render_towers_gif(grid, state["picks"], 0, mode, bet, interaction.user.display_name)
    view = _TowersView(user_id, str(interaction.message.id), mode, can_cashout=False)
    _tw_msg_to_user[str(interaction.message.id)] = user_id
    await interaction.response.edit_message(
        attachments=[discord.File(gif, "towers.gif")],
        view=view,
    )
    _cache_tw_msg(user_id, interaction.message)


class _TowersResultView(discord.ui.LayoutView):
    """Final view — GIF + Re-bet / 2× Bet buttons."""

    def __init__(self, user_id: int = 0, bet: float = 0.0, mode: str = "easy"):
        super().__init__(timeout=GAME_TIMEOUT)
        c = discord.ui.Container(accent_colour=discord.Colour.gold())
        g = discord.ui.MediaGallery()
        g.add_item(media="attachment://towers.gif")
        c.add_item(g)

        if user_id and bet > 0:
            row = discord.ui.ActionRow()
            rb  = discord.ui.Button(label="Re-bet", style=discord.ButtonStyle.secondary, emoji="🔄")
            rb.callback = self._make_cb(user_id, bet, mode)
            row.add_item(rb)
            x2  = discord.ui.Button(label="2× Bet", style=discord.ButtonStyle.primary, emoji="⬆️")
            x2.callback = self._make_cb(user_id, bet * 2, mode)
            row.add_item(x2)
            c.add_item(row)

        self.add_item(c)

    def _make_cb(self, user_id: int, bet: float, mode: str):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != user_id:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Not your game."), ephemeral=True,
                )
            await _towers_start_from_interaction(interaction, user_id, bet, mode)
        return _cb


class _TowersView(discord.ui.LayoutView):
    """Active game view: image + 4 column buttons + cashout."""

    def __init__(self, user_id: int, message_id: str, mode: str = "easy", can_cashout: bool = False):
        super().__init__(timeout=GAME_TIMEOUT)
        self.user_id    = user_id
        self.message_id = message_id
        self.mode       = mode

        container = discord.ui.Container(accent_colour=discord.Colour.gold())
        gallery   = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://towers.gif")
        container.add_item(gallery)

        # Column pick buttons
        col_row = discord.ui.ActionRow()
        for col in range(4):
            btn          = discord.ui.Button(label=str(col + 1), style=discord.ButtonStyle.primary)
            btn.callback = self._make_pick(col)
            col_row.add_item(btn)
        container.add_item(col_row)

        # Cashout button (separate row)
        co_row   = discord.ui.ActionRow()
        co_btn   = discord.ui.Button(
            label="Cash Out", style=discord.ButtonStyle.success,
            emoji="💰", disabled=not can_cashout,
        )
        co_btn.callback = self._on_cashout
        co_row.add_item(co_btn)
        container.add_item(co_row)

        self.add_item(container)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=utils.error_embed("Not your game."), ephemeral=True,
            )
            return False
        return True

    def _make_pick(self, col: int):
        async def _cb(interaction: discord.Interaction):
            if await self._guard(interaction):
                await _towers_do_pick(interaction, col)
        return _cb

    async def _on_cashout(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await _towers_do_cashout(interaction)

    async def on_timeout(self):
        sess = await db.get_game_session(self.user_id)
        if not sess or sess["game"] != "towers":
            return
        state = json.loads(sess["state"])
        bet   = float(sess["bet"])
        mode  = state.get("mode", self.mode)
        username = state.get("username", "")
        await _refund_game(self.user_id, bet, "towers", note="towers timeout refund")
        msg = _tw_user_msg.get(self.user_id)
        if msg:
            _tw_msg_to_user.pop(str(msg.id), None)
            _tw_user_msg.pop(self.user_id, None)
            gif = await image_gen.render_towers_gif(
                state["grid"], state["picks"], state["floor"], mode, bet, username,
            )
            try:
                await msg.edit(
                    attachments=[discord.File(gif, "towers.gif")],
                    view=_TowersResultView(self.user_id, bet, mode),
                )
            except Exception:
                pass


async def _towers_do_pick(interaction: discord.Interaction, col: int):
    user_id = _tw_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True,
        )

    sess = await _ensure_session_active(user_id, "towers")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active towers game (may have timed out)."), ephemeral=True,
        )

    state     = json.loads(sess["state"])
    floor     = state["floor"]
    grid      = state["grid"]
    picks     = state["picks"]
    mode      = state["mode"]
    bet       = float(sess["bet"])
    username  = state.get("username", str(interaction.user.display_name))
    cell_type = grid[floor][col]
    picks[floor] = col

    if cell_type == "bomb":
        await db.clear_game_session(user_id)
        _tw_msg_to_user.pop(str(interaction.message.id), None)

        await db.add_wager(user_id, bet)
        await _earn_rakeback(user_id, bet)
        await _record(user_id, False, bet, 0.0)

        gif = await image_gen.render_towers_gif(
            grid, picks, floor, mode, bet, username,
            just_revealed_floor=floor, result="BOOM", net_change=-bet,
        )
        await interaction.response.edit_message(
            attachments=[discord.File(gif, "towers.gif")],
            view=_TowersResultView(user_id, bet, mode),
        )
        _tw_user_msg.pop(int(user_id), None)

    else:
        # Gem found
        new_floor = floor + 1
        state["floor"] = new_floor
        state["picks"] = picks
        mults = image_gen.TOWERS_MULTS[mode]

        if new_floor >= 10:
            # Reached the top — auto-cashout
            await db.clear_game_session(user_id)
            _tw_msg_to_user.pop(str(interaction.message.id), None)

            game_cfg = await db.get_game_config("towers")
            he   = float(game_cfg["house_edge"]) if game_cfg else 0.02
            gross = bet * mults[9]
            net   = gross * (1 - he)
            user  = await db.get_user(user_id)
            cur_bal = float((user or {}).get("balance", 0))
            net = max(0.0, (await bc.apply_balance_cap(user_id, cur_bal + net)) - cur_bal)
            await db.add_balance(user_id, net, note="towers top-floor win")
            await db.add_wager(user_id, bet)
            await _earn_rakeback(user_id, bet, interaction.user if isinstance(interaction.user, discord.Member) else None)
            await _record(user_id, True, bet, net)

            gif = await image_gen.render_towers_gif(
                grid, picks, 10, mode, bet, username,
                just_revealed_floor=floor, result="CASHOUT",
                net_change=net - bet,
            )
            await interaction.response.edit_message(
                attachments=[discord.File(gif, "towers.gif")],
                view=_TowersResultView(user_id, bet, mode),
            )
            _tw_user_msg.pop(int(user_id), None)

        else:
            await db.set_game_session(user_id, "towers", bet, json.dumps(state))
            gif = await image_gen.render_towers_gif(
                grid, picks, new_floor, mode, bet, username,
                just_revealed_floor=floor,
            )
            view = _TowersView(user_id, str(interaction.message.id), mode, can_cashout=True)
            await interaction.response.edit_message(
                attachments=[discord.File(gif, "towers.gif")],
                view=view,
            )
            _cache_tw_msg(user_id, interaction.message)


async def _towers_do_cashout(interaction: discord.Interaction):
    user_id = _tw_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True,
        )

    sess = await _ensure_session_active(user_id, "towers")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active towers game (may have timed out)."), ephemeral=True,
        )

    state    = json.loads(sess["state"])
    floor    = state["floor"]
    mode     = state["mode"]
    bet      = float(sess["bet"])
    username = state.get("username", str(interaction.user.display_name))
    mults    = image_gen.TOWERS_MULTS[mode]

    if floor == 0:
        await db.clear_game_session(user_id)
        _tw_msg_to_user.pop(str(interaction.message.id), None)
        await db.add_balance(user_id, bet, note="towers refund (no floors cleared)")
        return await interaction.response.send_message(
            embed=_ok("No floors cleared — bet refunded."), ephemeral=True,
        )

    game_cfg = await db.get_game_config("towers")
    he    = float(game_cfg["house_edge"]) if game_cfg else 0.02
    gross = bet * mults[floor - 1]
    net   = gross * (1 - he)
    user  = await db.get_user(user_id)
    cur_bal = float((user or {}).get("balance", 0))
    net = max(0.0, (await bc.apply_balance_cap(user_id, cur_bal + net)) - cur_bal)
    await db.add_balance(user_id, net, note="towers cashout")
    await db.add_wager(user_id, bet)
    await _earn_rakeback(user_id, bet)
    await _record(user_id, True, bet, net)
    await db.clear_game_session(user_id)
    _tw_msg_to_user.pop(str(interaction.message.id), None)

    gif = await image_gen.render_towers_gif(
        state["grid"], state["picks"], floor, mode, bet, username,
        result="CASHOUT", net_change=net - bet,
    )
    await interaction.response.edit_message(
        attachments=[discord.File(gif, "towers.gif")],
        view=_TowersResultView(user_id, bet, mode),
    )
    _tw_user_msg.pop(int(user_id), None)
    _tw_user_msg.pop(int(user_id), None)


# ─────────────────────────────────────────────────────────────────────────────
# CHICKEN ROAD — cross lanes, rising multiplier, car crash GIF
# ─────────────────────────────────────────────────────────────────────────────

_cr_msg_to_user: dict[str, int] = {}
_cr_user_msg: dict[int, discord.Message] = {}


def _cache_cr_msg(user_id: int, msg: discord.Message | None) -> None:
    if msg is not None:
        _cr_user_msg[int(user_id)] = msg


async def _chicken_start_from_interaction(
    interaction: discord.Interaction, user_id: int, bet: float, mode: str,
):
    if not await _check_game_interaction(interaction, user_id, "chicken_road", bet):
        return

    num = image_gen.chicken_road_num_steps(mode)
    prob = image_gen.CHICKEN_CRASH_PROB[mode]
    lanes = ["crash" if random.random() < prob else "safe" for _ in range(num)]

    state = {
        "mode": mode,
        "step": 0,
        "lanes": lanes,
        "username": interaction.user.display_name,
    }
    await db.set_game_session(user_id, "chicken_road", bet, json.dumps(state))
    await db.add_balance(user_id, -bet, note="chicken road re-bet")

    gif = await image_gen.render_chicken_road_gif(0, mode, bet, interaction.user.display_name)
    view = _ChickenRoadView(user_id, str(interaction.message.id), mode, can_cashout=False)
    _cr_msg_to_user[str(interaction.message.id)] = user_id
    await interaction.response.edit_message(
        attachments=[discord.File(gif, "chickenroad.gif")],
        view=view,
    )
    _cache_cr_msg(user_id, interaction.message)


class _ChickenRoadResultView(discord.ui.LayoutView):
    def __init__(self, user_id: int = 0, bet: float = 0.0, mode: str = "easy"):
        super().__init__(timeout=GAME_TIMEOUT)
        c = discord.ui.Container(accent_colour=discord.Colour.orange())
        g = discord.ui.MediaGallery()
        g.add_item(media="attachment://chickenroad.gif")
        c.add_item(g)

        if user_id and bet > 0:
            row = discord.ui.ActionRow()
            rb = discord.ui.Button(label="Re-bet", style=discord.ButtonStyle.secondary, emoji="🔄")
            rb.callback = self._make_cb(user_id, bet, mode)
            row.add_item(rb)
            x2 = discord.ui.Button(label="2× Bet", style=discord.ButtonStyle.primary, emoji="⬆️")
            x2.callback = self._make_cb(user_id, bet * 2, mode)
            row.add_item(x2)
            c.add_item(row)

        self.add_item(c)

    def _make_cb(self, user_id: int, bet: float, mode: str):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != user_id:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Not your game."), ephemeral=True,
                )
            await _chicken_start_from_interaction(interaction, user_id, bet, mode)
        return _cb


class _ChickenRoadView(discord.ui.LayoutView):
    """Cross + Cash Out buttons."""

    def __init__(self, user_id: int, message_id: str, mode: str = "easy", can_cashout: bool = False):
        super().__init__(timeout=GAME_TIMEOUT)
        self.user_id = user_id
        self.message_id = message_id
        self.mode = mode

        container = discord.ui.Container(accent_colour=discord.Colour.orange())
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media="attachment://chickenroad.gif")
        container.add_item(gallery)

        row = discord.ui.ActionRow()
        cross = discord.ui.Button(
            label="Cross", style=discord.ButtonStyle.primary, emoji="🐔",
        )
        cross.callback = self._on_cross
        row.add_item(cross)

        cash = discord.ui.Button(
            label="Cash Out", style=discord.ButtonStyle.success, emoji="💰",
            disabled=not can_cashout,
        )
        cash.callback = self._on_cashout
        row.add_item(cash)
        container.add_item(row)

        self.add_item(container)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=utils.error_embed("Not your game."), ephemeral=True,
            )
            return False
        return True

    async def _on_cross(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await _chicken_do_cross(interaction)

    async def _on_cashout(self, interaction: discord.Interaction):
        if await self._guard(interaction):
            await _chicken_do_cashout(interaction)

    async def on_timeout(self):
        sess = await db.get_game_session(self.user_id)
        if not sess or sess["game"] != "chicken_road":
            return
        state = json.loads(sess["state"])
        bet = float(sess["bet"])
        mode = state.get("mode", self.mode)
        username = state.get("username", "")
        await _refund_game(self.user_id, bet, "chicken_road", note="chicken road timeout refund")
        msg = _cr_user_msg.get(self.user_id)
        if msg:
            _cr_msg_to_user.pop(str(msg.id), None)
            _cr_user_msg.pop(self.user_id, None)
            step = int(state.get("step", 0))
            gif = await image_gen.render_chicken_road_gif(step, mode, bet, username)
            try:
                await msg.edit(
                    attachments=[discord.File(gif, "chickenroad.gif")],
                    view=_ChickenRoadResultView(self.user_id, bet, mode),
                )
            except Exception:
                pass


async def _chicken_do_cross(interaction: discord.Interaction):
    user_id = _cr_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True,
        )

    sess = await _ensure_session_active(user_id, "chicken_road")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active Chicken Road game (may have timed out)."), ephemeral=True,
        )

    state = json.loads(sess["state"])
    step = int(state["step"])
    lanes = state["lanes"]
    mode = state["mode"]
    bet = float(sess["bet"])
    username = state.get("username", str(interaction.user.display_name))
    num = len(lanes)
    mults = image_gen.CHICKEN_MULTS[mode]

    if step >= num:
        return await interaction.response.send_message(
            embed=utils.error_embed("You already finished the road."), ephemeral=True,
        )

    outcome = lanes[step]
    if await bc.should_rig_outcome(user_id, "chicken_road", bet):
        outcome = "crash"
        lanes[step] = "crash"
        state["lanes"] = lanes

    if outcome == "crash":
        await db.clear_game_session(user_id)
        _cr_msg_to_user.pop(str(interaction.message.id), None)

        await db.add_wager(user_id, bet)
        await _earn_rakeback(
            user_id, bet,
            interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        await _record(user_id, False, bet, 0.0)

        gif = await image_gen.render_chicken_road_gif(
            step, mode, bet, username,
            cross_lane=step, cross_result="crash",
            result="CRASH", net_change=-bet,
        )
        await interaction.response.edit_message(
            attachments=[discord.File(gif, "chickenroad.gif")],
            view=_ChickenRoadResultView(user_id, bet, mode),
        )
        _cr_user_msg.pop(int(user_id), None)
        return

    new_step = step + 1
    state["step"] = new_step

    if new_step >= num:
        await db.clear_game_session(user_id)
        _cr_msg_to_user.pop(str(interaction.message.id), None)

        game_cfg = await db.get_game_config("chicken_road")
        he = float(game_cfg["house_edge"]) if game_cfg else 0.02
        gross = bet * mults[-1]
        net = gross * (1 - he)
        user = await db.get_user(user_id)
        cur_bal = float((user or {}).get("balance", 0))
        net = max(0.0, (await bc.apply_balance_cap(user_id, cur_bal + net)) - cur_bal)
        await db.add_balance(user_id, net, note="chicken road finish win")
        await db.add_wager(user_id, bet)
        await _earn_rakeback(
            user_id, bet,
            interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        await _record(user_id, True, bet, net)

        gif = await image_gen.render_chicken_road_gif(
            new_step, mode, bet, username,
            cross_lane=step, cross_result="safe",
            result="WIN", net_change=net - bet,
        )
        await interaction.response.edit_message(
            attachments=[discord.File(gif, "chickenroad.gif")],
            view=_ChickenRoadResultView(user_id, bet, mode),
        )
        _cr_user_msg.pop(int(user_id), None)
        return

    await db.set_game_session(user_id, "chicken_road", bet, json.dumps(state))
    gif = await image_gen.render_chicken_road_gif(
        new_step, mode, bet, username,
        cross_lane=step, cross_result="safe",
    )
    view = _ChickenRoadView(user_id, str(interaction.message.id), mode, can_cashout=True)
    await interaction.response.edit_message(
        attachments=[discord.File(gif, "chickenroad.gif")],
        view=view,
    )
    _cache_cr_msg(user_id, interaction.message)


async def _chicken_do_cashout(interaction: discord.Interaction):
    user_id = _cr_msg_to_user.get(str(interaction.message.id))
    if not user_id:
        return await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True,
        )

    sess = await _ensure_session_active(user_id, "chicken_road")
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active Chicken Road game (may have timed out)."), ephemeral=True,
        )

    state = json.loads(sess["state"])
    step = int(state["step"])
    mode = state["mode"]
    bet = float(sess["bet"])
    username = state.get("username", str(interaction.user.display_name))
    mults = image_gen.CHICKEN_MULTS[mode]

    if step == 0:
        await db.clear_game_session(user_id)
        _cr_msg_to_user.pop(str(interaction.message.id), None)
        await db.add_balance(user_id, bet, note="chicken road refund (no lanes crossed)")
        return await interaction.response.send_message(
            embed=_ok("No lanes crossed — bet refunded."), ephemeral=True,
        )

    game_cfg = await db.get_game_config("chicken_road")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    gross = bet * mults[step - 1]
    net = gross * (1 - he)
    user = await db.get_user(user_id)
    cur_bal = float((user or {}).get("balance", 0))
    net = max(0.0, (await bc.apply_balance_cap(user_id, cur_bal + net)) - cur_bal)
    await db.add_balance(user_id, net, note="chicken road cashout")
    await db.add_wager(user_id, bet)
    await _earn_rakeback(
        user_id, bet,
        interaction.user if isinstance(interaction.user, discord.Member) else None,
    )
    await _record(user_id, True, bet, net)
    await db.clear_game_session(user_id)
    _cr_msg_to_user.pop(str(interaction.message.id), None)

    gif = await image_gen.render_chicken_road_gif(
        step, mode, bet, username,
        result="CASHOUT", net_change=net - bet,
    )
    await interaction.response.edit_message(
        attachments=[discord.File(gif, "chickenroad.gif")],
        view=_ChickenRoadResultView(user_id, bet, mode),
    )
    _cr_user_msg.pop(int(user_id), None)
