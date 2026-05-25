"""HiLo — animated GIF (BJ cards), Components V2 buttons."""

from __future__ import annotations

import io
import json
import random

import discord
from discord.ext import commands

from database import db
from Games.hilo import (
    HOUSE_EDGE as HILO_HOUSE_EDGE,
    calc_hilo_odds,
    hilo_guess,
    new_hilo_state,
)
from modules import balance_cap
from modules import flip_utils as utils
from modules import image_gen

HILO_GIF = "hilo.gif"
GAME_TIMEOUT = 120

_hilo_msg_to_user: dict[str, int] = {}


def _gif_file(buf: io.BytesIO) -> discord.File:
    """Fresh attachment each edit (BytesIO is consumed on send)."""
    return discord.File(io.BytesIO(buf.getvalue()), filename=HILO_GIF)


async def _edit_hilo_message(
    target: discord.Message | discord.Interaction,
    buf: io.BytesIO,
    view: discord.ui.LayoutView | None,
) -> None:
    payload = dict(
        content=None,
        embed=None,
        attachments=[_gif_file(buf)],
        view=view,
    )
    if isinstance(target, discord.Interaction):
        if target.response.is_done():
            await target.edit_original_response(**payload)
        else:
            await target.response.edit_message(**payload)
    else:
        await target.edit(**payload)


def hilo_card_display(card: str) -> str:
    return image_gen.hilo_card_display(card)


# ── Buttons (exported for games_play_v2) ───────────────────────────────────────

class _HiLoHigherBtn(discord.ui.Button):
    def __init__(self, message_id: str, label: str, *, disabled: bool = False):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.success,
            emoji="📈",
            disabled=disabled,
            custom_id=f"hilo_h_{message_id}",
        )
        self._message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await _hilo_pick(interaction, "higher")


class _HiLoLowerBtn(discord.ui.Button):
    def __init__(self, message_id: str, label: str, *, disabled: bool = False):
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            emoji="📉",
            disabled=disabled,
            custom_id=f"hilo_l_{message_id}",
        )
        self._message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await _hilo_pick(interaction, "lower")


class _HiLoCashOutBtn(discord.ui.Button):
    def __init__(self, message_id: str, *, disabled: bool = False):
        super().__init__(
            label="Cash Out",
            style=discord.ButtonStyle.secondary,
            emoji="💰",
            disabled=disabled,
            custom_id=f"hilo_c_{message_id}",
        )
        self._message_id = message_id

    async def callback(self, interaction: discord.Interaction):
        await _hilo_cashout_interaction(interaction)


def hilo_action_buttons(message_id: str, state: dict) -> list[discord.ui.Item]:
    """Higher / Lower / Cash Out row for V2 layout."""
    if state.get("phase") != "playing":
        return []

    deck = state["deck"]
    card_idx = state["card_idx"]
    remaining = deck[card_idx + 1 :] if card_idx + 1 < len(deck) else []
    odds = calc_hilo_odds(deck[card_idx], remaining)

    _RV = {
        "A": 14, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
        "7": 7, "8": 8, "9": 9, "0": 10, "J": 11, "Q": 12, "K": 13,
    }
    cv = _RV.get(deck[card_idx][0], 0)
    same_count = odds["same_count"]
    total = odds["total"]
    same_pct = same_count / total if total > 0 else 0
    same_mult = round((1 - HILO_HOUSE_EDGE) / same_pct, 2) if same_pct > 0 else 0.0

    if cv == 14:
        h_label = f"≥ Higher  {same_mult:.2f}x" if same_mult > 0 else "≥ Higher"
        l_label = f"Lower  {odds['lower_mult']:.2f}x" if odds["lower_mult"] > 0 else "Lower  —"
        h_disabled = False
        l_disabled = odds["lower_mult"] == 0
    elif cv == 2:
        h_label = f"Higher  {odds['higher_mult']:.2f}x" if odds["higher_mult"] > 0 else "Higher  —"
        l_label = f"≤ Lower  {same_mult:.2f}x" if same_mult > 0 else "≤ Lower"
        h_disabled = odds["higher_mult"] == 0
        l_disabled = False
    else:
        h_label = f"Higher  {odds['higher_mult']:.2f}x" if odds["higher_mult"] > 0 else "Higher  —"
        l_label = f"Lower  {odds['lower_mult']:.2f}x" if odds["lower_mult"] > 0 else "Lower  —"
        h_disabled = odds["higher_mult"] == 0
        l_disabled = odds["lower_mult"] == 0

    can_cashout = state.get("round", 0) > 0
    items: list[discord.ui.Item] = [
        _HiLoHigherBtn(message_id, h_label, disabled=h_disabled),
        _HiLoLowerBtn(message_id, l_label, disabled=l_disabled),
        _HiLoCashOutBtn(message_id, disabled=not can_cashout),
    ]
    return items


class _HiLoView(discord.ui.LayoutView):
    """Components V2: GIF + Higher / Lower / Cash Out."""

    def __init__(self, user_id: int, message_id: str, state: dict):
        super().__init__(timeout=GAME_TIMEOUT)
        self.user_id = user_id
        self.message_id = message_id
        self.state = state

        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{HILO_GIF}")
        container.add_item(gallery)

        buttons = hilo_action_buttons(message_id, state)
        if buttons:
            row = discord.ui.ActionRow()
            for btn in buttons[:5]:
                row.add_item(btn)
            container.add_item(row)
        self.add_item(container)

    async def on_timeout(self):
        uid = self.user_id
        sess = await db.get_game_session(uid)
        if not sess or sess["game"] != "hilo":
            return
        bet = float(sess["bet"])
        await db.add_balance(uid, bet, note="hilo timeout refund")
        await db.clear_game_session(uid)
        _hilo_msg_to_user.pop(self.message_id, None)


class _HiLoResultView(discord.ui.LayoutView):
    def __init__(self, user_id: int, bet: float):
        super().__init__(timeout=GAME_TIMEOUT)
        self.user_id = user_id
        container = discord.ui.Container(accent_colour=discord.Colour.blurple())
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=f"attachment://{HILO_GIF}")
        container.add_item(gallery)
        if user_id and bet > 0:
            row = discord.ui.ActionRow()
            rb = discord.ui.Button(label="Re-bet", style=discord.ButtonStyle.secondary, emoji="🔄")
            rb.callback = self._rebet(bet)
            row.add_item(rb)
            x2 = discord.ui.Button(label="2× Bet", style=discord.ButtonStyle.primary, emoji="⬆️")
            x2.callback = self._rebet(bet * 2)
            row.add_item(x2)
            container.add_item(row)
        self.add_item(container)

    def _rebet(self, amount: float):
        async def _cb(interaction: discord.Interaction):
            if interaction.user.id != self.user_id:
                return await interaction.response.send_message(
                    embed=utils.error_embed("Not your game."), ephemeral=True,
                )
            await start_hilo_interaction(interaction, amount)
        return _cb


async def _guard_owner(interaction: discord.Interaction) -> int | None:
    uid = _hilo_msg_to_user.get(str(interaction.message.id))
    if not uid:
        await interaction.response.send_message(
            embed=utils.error_embed("Game not found."), ephemeral=True,
        )
        return None
    if interaction.user.id != uid:
        await interaction.response.send_message(
            embed=utils.error_embed("Not your game."), ephemeral=True,
        )
        return None
    return uid


async def _session_active(uid: int) -> dict | None:
    from cogs.games import _ensure_session_active
    return await _ensure_session_active(uid, "hilo")


async def _render_play(state: dict, username: str, bet: float, *, status: str = "", result: str = "", net: float = 0.0):
    card = state["deck"][state["card_idx"]]
    return await image_gen.render_hilo_gif(
        card,
        multiplier=float(state.get("multiplier", 1.0)),
        bet=bet,
        username=username,
        status=status,
        result=result,
        net_change=net,
    )


async def start_hilo_command(ctx: commands.Context, amount: float) -> None:
    from cogs.games import _check_game
    await db.ensure_user(ctx.author.id, ctx.author.name)
    if not await _check_game(ctx, "hilo", amount):
        return

    floats = [random.random(), random.random(), random.random()]
    state = new_hilo_state(int(amount), floats, str(ctx.author.id))
    await db.set_game_session(ctx.author.id, "hilo", amount, json.dumps(state))
    await db.add_balance(ctx.author.id, -amount, note="hilo bet")

    gif = await _render_play(state, ctx.author.display_name, amount)
    mid = "pending"
    msg = await ctx.send(
        file=_gif_file(gif),
        view=_HiLoView(ctx.author.id, mid, state),
    )
    mid = str(msg.id)
    _hilo_msg_to_user[mid] = ctx.author.id
    state["message_id"] = mid
    await db.set_game_session(ctx.author.id, "hilo", amount, json.dumps(state))
    await _edit_hilo_message(msg, gif, _HiLoView(ctx.author.id, mid, state))


async def start_hilo_interaction(interaction: discord.Interaction, amount: float) -> None:
    from cogs.games import _check_game_interaction
    uid = interaction.user.id
    if not await _check_game_interaction(interaction, uid, "hilo", amount):
        return

    floats = [random.random(), random.random(), random.random()]
    state = new_hilo_state(int(amount), floats, str(uid))
    await db.set_game_session(uid, "hilo", amount, json.dumps(state))
    await db.add_balance(uid, -amount, note="hilo bet")

    gif = await _render_play(state, interaction.user.display_name, amount)
    mid = str(interaction.message.id)
    _hilo_msg_to_user[mid] = uid
    await interaction.response.edit_message(
        content=None,
        embed=None,
        attachments=[_gif_file(gif)],
        view=_HiLoView(uid, mid, state),
    )


async def _hilo_pick(interaction: discord.Interaction, choice: str) -> None:
    uid = await _guard_owner(interaction)
    if uid is None:
        return

    sess = await _session_active(uid)
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active Hi-Lo game."), ephemeral=True,
        )

    state = json.loads(sess["state"])
    if state.get("phase") != "playing":
        return await interaction.response.send_message(
            embed=utils.error_embed("Game already finished."), ephemeral=True,
        )

    bet = float(sess["bet"])
    deck = state["deck"]
    idx = state["card_idx"]
    prev_card = deck[idx]
    next_card = deck[idx + 1] if idx + 1 < len(deck) else None

    await interaction.response.defer()

    from modules import flip_balance_cap as bc
    from modules.game_rig import rig_hilo_before_guess

    if await bc.should_rig_outcome(uid, "hilo", bet):
        rig_hilo_before_guess(state, choice)

    hilo_guess(state, choice)

    result = state.get("last_result")
    mid = str(interaction.message.id)

    if result == "lose":
        from cogs.games import _earn_rakeback, _record
        await db.clear_game_session(uid)
        await db.add_wager(uid, bet)
        await _earn_rakeback(uid, bet, interaction.user if isinstance(interaction.user, discord.Member) else None)
        await _record(uid, False, bet, 0.0)
        _hilo_msg_to_user.pop(mid, None)
        hist = state.get("history") or []
        revealed = hist[-1]["next"] if hist else next_card or prev_card
        gif = await image_gen.render_hilo_gif(
            revealed,
            prev_card=prev_card,
            reveal_card=revealed,
            animate_reveal=bool(next_card),
            multiplier=float(state.get("multiplier", 1.0)),
            bet=bet,
            username=interaction.user.display_name,
            status="LOSE",
            result="lose",
            net_change=-bet,
        )
        await _edit_hilo_message(interaction, gif, _HiLoResultView(uid, bet))
        return

    if result in ("win", "push"):
        await db.set_game_session(uid, "hilo", bet, json.dumps(state))
        revealed = state["deck"][state["card_idx"]]
        status = "WIN" if result == "win" else "PUSH"
        gif = await image_gen.render_hilo_gif(
            revealed,
            prev_card=prev_card,
            reveal_card=revealed,
            animate_reveal=bool(next_card),
            multiplier=float(state.get("multiplier", 1.0)),
            bet=bet,
            username=interaction.user.display_name,
            status=status,
            result=result,
        )
        await _edit_hilo_message(interaction, gif, _HiLoView(uid, mid, state))
        return

    await db.set_game_session(uid, "hilo", bet, json.dumps(state))
    card = state["deck"][state["card_idx"]]
    gif = await image_gen.render_hilo_gif(
        card,
        prev_card=prev_card,
        reveal_card=next_card,
        animate_reveal=bool(next_card),
        multiplier=float(state.get("multiplier", 1.0)),
        bet=bet,
        username=interaction.user.display_name,
    )
    await _edit_hilo_message(interaction, gif, _HiLoView(uid, mid, state))


async def _hilo_cashout_interaction(interaction: discord.Interaction) -> None:
    uid = await _guard_owner(interaction)
    if uid is None:
        return
    await hilo_cashout_user(uid, interaction=interaction)


async def hilo_cashout_user(
    uid: int,
    *,
    interaction: discord.Interaction | None = None,
    ctx: commands.Context | None = None,
) -> None:
    from cogs.games import _earn_rakeback, _record

    sess = await db.get_game_session(uid)
    if not sess or sess["game"] != "hilo":
        msg = utils.error_embed("No active Hi-Lo game.")
        if interaction:
            return await interaction.response.send_message(embed=msg, ephemeral=True)
        if ctx:
            return await ctx.send(embed=msg)
        return

    state = json.loads(sess["state"])
    if state.get("phase") != "playing":
        err = utils.error_embed("Hi-Lo game already ended.")
        if interaction:
            if interaction.response.is_done():
                return await interaction.followup.send(embed=err, ephemeral=True)
            return await interaction.response.send_message(embed=err, ephemeral=True)
        if ctx:
            return await ctx.send(embed=err)
        return

    if state.get("round", 0) < 1:
        err = utils.error_embed("Win at least one round before cashing out.")
        if interaction:
            if interaction.response.is_done():
                return await interaction.followup.send(embed=err, ephemeral=True)
            return await interaction.response.send_message(embed=err, ephemeral=True)
        if ctx:
            return await ctx.send(embed=err)
        return

    bet = float(sess["bet"])
    mult = float(state.get("multiplier", 1.0))
    user = await db.get_user(uid)
    bal = int(float((user or {}).get("balance", 0)))
    payout = balance_cap.cap_hilo_cashout_payout(uid, "real", bal, int(bet), mult)
    if payout <= 0:
        state["phase"] = "done"
        state["last_result"] = "lose"
        await db.clear_game_session(uid)
        await db.add_wager(uid, bet)
        await _earn_rakeback(uid, bet)
        await _record(uid, False, bet, 0.0)
        gif = await image_gen.render_hilo_gif(
            state["deck"][state["card_idx"]],
            multiplier=mult,
            bet=bet,
            status="LOSE",
            result="lose",
            net_change=-bet,
        )
        view = _HiLoResultView(uid, bet)
        if interaction:
            await _edit_hilo_message(interaction, gif, view)
        elif ctx:
            msg_id = _hilo_message_for_user(uid)
            if msg_id:
                ch = ctx.channel
                if ch:
                    try:
                        msg = await ch.fetch_message(int(msg_id))
                        await _edit_hilo_message(msg, gif, view)
                    except Exception:
                        await ctx.send(file=_gif_file(gif), view=view)
                else:
                    await ctx.send(file=_gif_file(gif), view=view)
            else:
                await ctx.send(file=_gif_file(gif), view=view)
        return

    await db.add_balance(uid, payout, note="hilo cashout")
    await db.add_wager(uid, bet)
    member = None
    if interaction and isinstance(interaction.user, discord.Member):
        member = interaction.user
    elif ctx:
        member = ctx.author if isinstance(ctx.author, discord.Member) else None
    await _earn_rakeback(uid, bet, member)
    await _record(uid, True, bet, float(payout))
    await db.clear_game_session(uid)

    net = float(payout) - bet
    gif = await image_gen.render_hilo_gif(
        state["deck"][state["card_idx"]],
        multiplier=mult,
        bet=bet,
        username=(interaction.user.display_name if interaction else ctx.author.display_name) if (interaction or ctx) else "",
        status="CASH OUT",
        result="cashout",
        net_change=net,
    )
    view = _HiLoResultView(uid, bet)
    if interaction:
        await _edit_hilo_message(interaction, gif, view)
        _hilo_msg_to_user.pop(str(interaction.message.id), None)
    elif ctx:
        msg_id = _hilo_message_for_user(uid)
        if msg_id and ctx.channel:
            try:
                msg = await ctx.channel.fetch_message(int(msg_id))
                await _edit_hilo_message(msg, gif, view)
                _hilo_msg_to_user.pop(msg_id, None)
            except Exception:
                await ctx.send(file=_gif_file(gif), view=view)
        else:
            await ctx.send(file=_gif_file(gif), view=view)


def _hilo_message_for_user(uid: int) -> str | None:
    for mid, owner in _hilo_msg_to_user.items():
        if owner == uid:
            return mid
    return None
