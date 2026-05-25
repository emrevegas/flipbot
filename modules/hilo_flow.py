"""HiLo — animated GIF (BJ cards), Components V2 buttons."""

from __future__ import annotations

import json
import random
from typing import Awaitable, Callable

import discord
from discord.ext import commands

from database import db
from Games.hilo import (
    HOUSE_EDGE as HILO_HOUSE_EDGE,
    calc_hilo_odds,
    hilo_guess,
    new_hilo_state,
)
from modules import flip_balance_cap as bc
from modules import flip_utils as utils
from modules import image_gen

HILO_GIF = "hilo.gif"
GAME_TIMEOUT = 120

_hilo_msg_to_user: dict[str, int] = {}


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
    def __init__(self, message_id: str):
        super().__init__(
            label="Cash Out",
            style=discord.ButtonStyle.secondary,
            emoji="💰",
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

    items: list[discord.ui.Item] = [
        _HiLoHigherBtn(message_id, h_label, disabled=h_disabled),
        _HiLoLowerBtn(message_id, l_label, disabled=l_disabled),
    ]
    if state.get("round", 0) > 0:
        items.append(_HiLoCashOutBtn(message_id))
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
    msg = await ctx.send(
        file=discord.File(gif, HILO_GIF),
        view=_HiLoView(ctx.author.id, "pending", state),
    )
    mid = str(msg.id)
    _hilo_msg_to_user[mid] = ctx.author.id
    state["message_id"] = mid
    await db.set_game_session(ctx.author.id, "hilo", amount, json.dumps(state))
    await msg.edit(view=_HiLoView(ctx.author.id, mid, state))


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
    await interaction.response.edit_message(
        attachments=[discord.File(gif, HILO_GIF)],
        view=_HiLoView(uid, str(interaction.message.id), state),
    )
    mid = str(interaction.message.id)
    _hilo_msg_to_user[mid] = uid


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

    if next_card:
        anim = await image_gen.render_hilo_gif(
            prev_card,
            prev_card=prev_card,
            reveal_card=next_card,
            animate_reveal=True,
            multiplier=float(state.get("multiplier", 1.0)),
            bet=bet,
            username=interaction.user.display_name,
        )
        try:
            await interaction.message.edit(
                attachments=[discord.File(anim, HILO_GIF)],
                view=None,
            )
        except Exception:
            pass

    hilo_guess(state, choice)

    user = await db.get_user(uid)
    bal = int(float((user or {}).get("balance", 0)))
    bc.apply_hilo_step_bias(state, uid, "real", bal)

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
            multiplier=float(state.get("multiplier", 1.0)),
            bet=bet,
            username=interaction.user.display_name,
            status="LOSE",
            result="lose",
            net_change=-bet,
        )
        await interaction.message.edit(
            attachments=[discord.File(gif, HILO_GIF)],
            view=_HiLoResultView(uid, bet),
        )
        return

    if result in ("win", "push"):
        await db.set_game_session(uid, "hilo", bet, json.dumps(state))
        hist = state.get("history") or []
        revealed = state["deck"][state["card_idx"]]
        status = "WIN" if result == "win" else "PUSH"
        gif = await image_gen.render_hilo_gif(
            revealed,
            prev_card=prev_card,
            reveal_card=revealed,
            multiplier=float(state.get("multiplier", 1.0)),
            bet=bet,
            username=interaction.user.display_name,
            status=status,
            result=result,
        )
        await interaction.message.edit(
            attachments=[discord.File(gif, HILO_GIF)],
            view=_HiLoView(uid, mid, state),
        )
        return

    await db.set_game_session(uid, "hilo", bet, json.dumps(state))
    gif = await _render_play(state, interaction.user.display_name, bet)
    await interaction.message.edit(
        attachments=[discord.File(gif, HILO_GIF)],
        view=_HiLoView(uid, mid, state),
    )


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
    if state.get("round", 0) < 1:
        err = utils.error_embed("Win at least one round before cashing out.")
        if interaction:
            return await interaction.response.send_message(embed=err, ephemeral=True)
        if ctx:
            return await ctx.send(embed=err)
        return

    bet = float(sess["bet"])
    mult = float(state.get("multiplier", 1.0))
    user = await db.get_user(uid)
    bal = int(float((user or {}).get("balance", 0)))
    payout = bc.cap_hilo_cashout_payout(uid, "real", bal, int(bet), mult)
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
        file = discord.File(gif, HILO_GIF)
        view = _HiLoResultView(uid, bet)
        if interaction:
            await interaction.response.edit_message(attachments=[file], view=view)
        elif ctx:
            await ctx.send(file=file, view=view)
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
    file = discord.File(gif, HILO_GIF)
    view = _HiLoResultView(uid, bet)
    if interaction:
        await interaction.response.edit_message(attachments=[file], view=view)
        _hilo_msg_to_user.pop(str(interaction.message.id), None)
    elif ctx:
        await ctx.send(file=file, view=view)
