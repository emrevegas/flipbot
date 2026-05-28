"""Hot/Cold coin flip — vs bot (progressive streak), PvP challenge, V2 GIF."""

from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING

import discord
from discord import ui

from database import db
from modules import image_gen
from modules.database import get_data, set_data
from modules.game_media_v2 import challenge_text_layout, gif_media_layout, gif_result_layout
from modules.pvp_challenge import PVP_CHALLENGE_TIMEOUT
from modules import flip_utils as utils
from modules import flip_balance_cap as bc

if TYPE_CHECKING:
    from discord.ext import commands

COINFLIP_GIF = "coinflip.gif"
SIDES = ("HOT", "COLD")
PROG_GAME = "coinflip_prog"
MAX_PROG_STREAK = 20
_cf_prog_msg_to_user: dict[str, int] = {}


def get_coinflip_emojis() -> tuple[str, str]:
    games = get_data("server/games") or {}
    cf = games.get("coinflip") if isinstance(games.get("coinflip"), dict) else {}
    hot = str(cf.get("hot_emoji") or "🔥")
    cold = str(cf.get("cold_emoji") or "❄️")
    return hot, cold


def save_coinflip_emojis(hot: str, cold: str) -> None:
    from cogs.admin_panel import _ensure_coinflip_game_entry

    games = _ensure_coinflip_game_entry(get_data("server/games") or {})
    cf = games["coinflip"]
    cf["hot_emoji"] = hot.strip() or "🔥"
    cf["cold_emoji"] = cold.strip() or "❄️"
    cf["last_modified"] = int(__import__("time").time())
    games["coinflip"] = cf
    set_data("server/games", games)


def parse_side(raw: str) -> str | None:
    u = raw.strip().upper()
    if u in ("HOT", "H", "FIRE", "HEADS", "HEAD"):
        return "HOT"
    if u in ("COLD", "C", "ICE", "TAILS", "TAIL"):
        return "COLD"
    return None


def get_progressive_step_mult() -> float:
    games = get_data("server/games") or {}
    cf = games.get("coinflip") if isinstance(games.get("coinflip"), dict) else {}
    try:
        return float(cf.get("progressive_mult", 1.92))
    except (TypeError, ValueError):
        return 1.92


def progressive_gross(bet: float, streak: int) -> float:
    if streak <= 0:
        return 0.0
    return bet * (get_progressive_step_mult() ** streak)


async def progressive_cashout_net(bet: float, streak: int) -> float:
    gross = progressive_gross(bet, streak)
    if gross <= 0:
        return 0.0
    game_cfg = await db.get_game_config("coinflip")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    return gross * (1 - he)


def _side_label(side: str, hot_e: str, cold_e: str) -> str:
    return f"{hot_e} Hot" if side == "HOT" else f"{cold_e} Cold"


async def parse_cf_args(ctx: commands.Context) -> tuple[discord.Member | None, float | None, str | None]:
    from modules.bet_parse import parse_bet_token

    parts = ctx.message.content.split()
    tokens = parts[1:] if len(parts) > 1 else []
    opponent = ctx.message.mentions[0] if ctx.message.mentions else None
    choice = None
    bet = None
    user = await db.get_user(ctx.author.id)
    balance = float((user or {}).get("balance", 0))
    for tok in tokens:
        if tok.startswith("<@"):
            continue
        side = parse_side(tok)
        if side:
            choice = side
            continue
        b = parse_bet_token(tok, balance)
        if b is not None:
            bet = b
    return opponent, bet, choice


async def _edit_gif_message(message: discord.Message, gif_buf, *, view=None) -> None:
    await message.edit(
        content=None,
        embed=None,
        attachments=[discord.File(gif_buf, COINFLIP_GIF)],
        view=view or gif_media_layout(COINFLIP_GIF),
    )


async def _payout(user_id, game_id, bet, gross):
    from cogs.games import _payout as gp
    return await gp(user_id, game_id, bet, gross)


async def _record(user_id, won, bet, net, **kwargs):
    from cogs.games import _record as gr
    await gr(user_id, won, bet, net, **kwargs)


async def settle_coinflip_pvp(
    challenger_id: int,
    opponent_id: int,
    bet: float,
    left_side: str,
    result: str,
    *,
    guild: discord.Guild | None = None,
    client: discord.Client | None = None,
) -> tuple[int, float, float]:
    game_cfg = await db.get_game_config("coinflip")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    left_wins = result == left_side
    winner_id = challenger_id if left_wins else opponent_id
    pool = bet * 2
    payout = pool * (1 - he)
    if payout > 0:
        await db.add_balance(winner_id, payout, note="coinflip pvp win")
    await db.add_wager(challenger_id, bet)
    await db.add_wager(opponent_id, bet)
    from cogs.games import _earn_rakeback

    await _earn_rakeback(challenger_id, bet)
    await _earn_rakeback(opponent_id, bet)
    if left_wins:
        await _record(challenger_id, True, bet, payout, game_id="coinflip", skip_log=True)
        await _record(opponent_id, False, bet, 0, game_id="coinflip", skip_log=True)
    else:
        await _record(challenger_id, False, bet, 0, game_id="coinflip", skip_log=True)
        await _record(opponent_id, True, bet, payout, game_id="coinflip", skip_log=True)

    if guild and client:
        from modules.game_log import post_pvp_game_log

        pa = guild.get_member(challenger_id)
        pb = guild.get_member(opponent_id)
        if pa and pb:
            winner_member = guild.get_member(winner_id)
            await post_pvp_game_log(
                player_a=pa,
                player_b=pb,
                game_id="coinflip",
                winner=winner_member,
                payout=payout,
                bet=bet,
                client=client,
                guild_id=guild.id,
            )
    return winner_id, payout, bet


class CoinflipChallengeLayout(ui.LayoutView):
    """V2 challenge — Accept / Decline & Cancel, 30s timeout, same-message GIF on accept."""

    def __init__(
        self,
        challenger: discord.Member,
        opponent: discord.Member,
        bet: float,
        challenger_side: str,
    ):
        super().__init__(timeout=PVP_CHALLENGE_TIMEOUT)
        self.challenger_id = challenger.id
        self.opponent_id = opponent.id
        self.bet = bet
        self.challenger_side = challenger_side
        self.opponent_side = "COLD" if challenger_side == "HOT" else "HOT"
        self._done = False
        self._message: discord.Message | None = None

        opp_side = self.opponent_side
        body = (
            f"## 🪙 Coin Flip Challenge\n"
            f"{challenger.mention} vs {opponent.mention}\n\n"
            f"**Bet:** {utils.fmt_pts(bet)} pts\n"
            f"**{challenger.display_name}:** {challenger_side}\n"
            f"**{opponent.display_name}:** {opp_side}\n\n"
            f"{opponent.mention} — **Accept** within **{PVP_CHALLENGE_TIMEOUT}s**.\n"
            f"Either player: **Decline & Cancel**."
        )
        container = ui.Container()
        container.add_item(ui.TextDisplay(body))
        container.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        row = ui.ActionRow()
        accept = ui.Button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
        accept.callback = self._on_accept
        decline = ui.Button(label="Decline & Cancel", style=discord.ButtonStyle.danger, emoji="❌")
        decline.callback = self._on_decline
        row.add_item(accept)
        row.add_item(decline)
        container.add_item(row)
        self.add_item(container)

    def attach_message(self, message: discord.Message) -> None:
        self._message = message

    def _mark_done(self) -> None:
        self._done = True
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.challenger_id, self.opponent_id):
            await interaction.response.send_message("This challenge is not for you.", ephemeral=True)
            return False
        if self._done:
            await interaction.response.send_message("This challenge is no longer active.", ephemeral=True)
            return False
        return True

    async def _on_decline(self, interaction: discord.Interaction):
        uid = interaction.user.id
        if uid == self.challenger_id:
            text = f"❌ {interaction.user.display_name} cancelled the **Coin Flip** challenge."
        elif uid == self.opponent_id:
            text = f"❌ {interaction.user.display_name} declined the **Coin Flip** challenge."
        else:
            return
        self._mark_done()
        body = f"## 🪙 Coin Flip\n{text}"
        await interaction.response.edit_message(
            content=None,
            embed=None,
            view=challenge_text_layout(body, [], timeout=None),
        )

    async def _on_accept(self, interaction: discord.Interaction):
        if interaction.user.id != self.opponent_id:
            return await interaction.response.send_message(
                "Only the challenged player can accept.", ephemeral=True,
            )
        await db.ensure_user(self.challenger_id, "challenger")
        await db.ensure_user(self.opponent_id, "opponent")
        if not await db.get_game_config("coinflip"):
            return await interaction.response.send_message(
                embed=utils.error_embed("Coin Flip is disabled."), ephemeral=True,
            )
        for uid in (self.challenger_id, self.opponent_id):
            user = await db.get_user(uid)
            if not user or float(user["balance"]) < self.bet:
                return await interaction.response.send_message(
                    embed=utils.error_embed("One of you no longer has enough balance."),
                    ephemeral=True,
                )

        self._mark_done()
        await interaction.response.defer()
        await db.add_balance(self.challenger_id, -self.bet, note="coinflip pvp bet")
        await db.add_balance(self.opponent_id, -self.bet, note="coinflip pvp bet")

        hot_e, cold_e = get_coinflip_emojis()
        result = random.choice(SIDES)
        winner_id, win_pay, lost_bet = await settle_coinflip_pvp(
            self.challenger_id,
            self.opponent_id,
            self.bet,
            self.challenger_side,
            result,
            guild=interaction.guild,
            client=interaction.client,
        )

        guild = interaction.guild
        challenger = guild.get_member(self.challenger_id) if guild else None
        opponent = interaction.user if isinstance(interaction.user, discord.Member) else None
        left_name = challenger.display_name if challenger else str(self.challenger_id)
        right_name = opponent.display_name if opponent else str(self.opponent_id)

        left_won = winner_id == self.challenger_id
        if left_won:
            lp, ll, rp, rl = win_pay, 0.0, 0.0, lost_bet
        else:
            lp, ll, rp, rl = 0.0, lost_bet, win_pay, 0.0

        gif = await image_gen.render_coinflip_gif(
            mode="pvp",
            left_name=left_name,
            right_name=right_name,
            left_side=self.challenger_side,
            right_side=self.opponent_side,
            result=result,
            hot_emoji=hot_e,
            cold_emoji=cold_e,
            bet=self.bet,
            left_payout=lp,
            left_lost=ll,
            right_payout=rp,
            right_lost=rl,
        )
        await _edit_gif_message(interaction.message, gif)

    async def on_timeout(self) -> None:
        if self._done:
            return
        self._done = True
        msg = self._message
        if not msg:
            return
        try:
            body = (
                f"## ⏱️ Coin Flip — Expired\n"
                f"Challenge was not accepted within **{PVP_CHALLENGE_TIMEOUT} seconds**."
            )
            await msg.edit(view=challenge_text_layout(body, [], timeout=None))
        except Exception:
            pass


async def _prog_flip_outcome(
    user_id: int,
    bet: float,
    player_side: str,
    *,
    streak_before: int,
) -> tuple[str, bool]:
    """Return (result_side, won) for one progressive flip."""
    prospective = progressive_gross(bet, streak_before + 1)
    rigged = await bc.should_rig_outcome(
        user_id, "coinflip", bet, gross=prospective,
    )
    if rigged:
        result = "COLD" if player_side == "HOT" else "HOT"
    else:
        result = random.choice(SIDES)
    return result, result == player_side


async def _prog_render_gif(
    display_name: str,
    bet: float,
    player_side: str,
    result: str,
    *,
    won: bool,
    streak: int = 0,
    history: list[str] | None = None,
    cashout_net: float = 0.0,
) -> bytes:
    hot_e, cold_e = get_coinflip_emojis()
    step = get_progressive_step_mult()
    if won and cashout_net > 0:
        lp, ll = cashout_net, 0.0
    elif won:
        lp, ll = 0.0, 0.0
    else:
        lp, ll = 0.0, bet
    return await image_gen.render_coinflip_gif(
        mode="bot",
        left_name=display_name,
        right_name="Flip",
        left_side=player_side,
        right_side=result,
        result=result,
        hot_emoji=hot_e,
        cold_emoji=cold_e,
        bet=bet,
        left_payout=lp,
        left_lost=ll,
        progressive=True,
        streak=streak,
        step_mult=step,
        flip_won=won,
        history_results=list(history or []),
    )


async def _prog_settle_loss(
    user_id: int,
    bet: float,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
) -> None:
    from cogs.games import _earn_rakeback

    await db.clear_game_session(user_id)
    await db.add_wager(user_id, bet)
    await _earn_rakeback(user_id, bet, user if isinstance(user, discord.Member) else None)
    await _record(
        user_id, False, bet, 0.0,
        game_id="coinflip",
        user=user,
        client=client,
        guild_id=guild_id,
    )


async def _prog_cashout(
    user_id: int,
    bet: float,
    streak: int,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
) -> tuple[bool, float]:
    """Credit progressive cashout. Returns (paid, net)."""
    from cogs.games import _earn_rakeback
    import modules.balance_cap as balance_cap

    if streak < 1:
        return False, 0.0

    user_row = await db.get_user(user_id)
    bal = int(float((user_row or {}).get("balance", 0)))
    net = await progressive_cashout_net(bet, streak)
    payout_int = int(net)
    if balance_cap.should_force_cap_loss(user_id, "real", bal, payout_int, game_id="coinflip"):
        await _prog_settle_loss(
            user_id, bet, user=user, client=client, guild_id=guild_id,
        )
        return False, 0.0

    if net > 0:
        await db.add_balance(user_id, net, note="coinflip prog cashout")
    await db.add_wager(user_id, bet)
    await _earn_rakeback(user_id, bet, user if isinstance(user, discord.Member) else None)
    await _record(
        user_id, True, bet, net,
        game_id="coinflip",
        user=user,
        client=client,
        guild_id=guild_id,
    )
    await db.clear_game_session(user_id)
    return True, net


async def progressive_expire_session(user_id: int, sess: dict) -> None:
    """Session timeout from games cog — auto-cashout if streak else refund."""
    state = json.loads(sess.get("state") or "{}")
    streak = int(state.get("streak") or 0)
    bet = float(sess["bet"])
    if streak > 0:
        await _prog_cashout(user_id, bet, streak)
    else:
        await db.add_balance(user_id, bet, note="coinflip prog timeout refund")
        await db.clear_game_session(user_id)


async def progressive_timeout_cashout(
    user_id: int,
    message: discord.Message | None,
) -> None:
    from cogs.games import _ensure_session_active
    from modules.coinflip_progressive_v2 import build_progressive_done_layout

    sess = await _ensure_session_active(user_id, PROG_GAME)
    if not sess:
        return
    state = json.loads(sess["state"])
    streak = int(state.get("streak") or 0)
    bet = float(sess["bet"])
    username = state.get("username") or "Player"
    hot_e = state.get("hot_emoji") or get_coinflip_emojis()[0]
    cold_e = state.get("cold_emoji") or get_coinflip_emojis()[1]

    if streak > 0:
        paid, net = await _prog_cashout(user_id, bet, streak)
        if message:
            pick = state.get("last_pick", "HOT")
            result = state.get("last_result", pick)
            hist = list(state.get("history") or [])
            gif = await _prog_render_gif(
                username, bet, pick, result,
                won=True, streak=streak, history=hist,
                cashout_net=net if paid else 0.0,
            )
            body = (
                f"⏱️ Timed out — auto **cash out** "
                f"**{utils.fmt_pts(net)} pts**." if paid else
                "⏱️ Timed out — cash out blocked (balance cap)."
            )
            try:
                await message.edit(
                    content=None,
                    embed=None,
                    attachments=[discord.File(gif, COINFLIP_GIF)],
                    view=build_progressive_done_layout(title="Coin Flip", body=body),
                )
            except Exception:
                pass
    else:
        await db.add_balance(user_id, bet, note="coinflip prog timeout refund")
        await db.clear_game_session(user_id)
        if message:
            try:
                await message.edit(
                    view=build_progressive_done_layout(
                        title="Coin Flip — Expired",
                        body="⏱️ No result in time — bet refunded.",
                        accent=0xE67E22,
                    ),
                )
            except Exception:
                pass

    if message:
        _cf_prog_msg_to_user.pop(str(message.id), None)


async def _prog_cashout_interaction(interaction: discord.Interaction) -> None:
    from cogs.games import _ensure_session_active

    uid = interaction.user.id
    sess = await _ensure_session_active(uid, PROG_GAME)
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active coin flip."), ephemeral=True,
        )
    state = json.loads(sess["state"])
    streak = int(state.get("streak") or 0)
    if streak < 1:
        return await interaction.response.send_message(
            embed=utils.error_embed("Win a flip before cashing out."), ephemeral=True,
        )

    bet = float(sess["bet"])
    await interaction.response.defer()
    paid, net = await _prog_cashout(
        uid, bet, streak,
        user=interaction.user,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
    )
    pick = state.get("last_pick", "HOT")
    result = state.get("last_result", pick)
    hist = list(state.get("history") or [])
    gif = await _prog_render_gif(
        interaction.user.display_name, bet, pick, result,
        won=paid,
        streak=streak,
        history=hist,
        cashout_net=net if paid else 0.0,
    )
    _cf_prog_msg_to_user.pop(str(interaction.message.id), None)
    if paid:
        body = f"💰 Cashed out **{utils.fmt_pts(net)} pts** (streak **{streak}**)."
    else:
        body = "Cash out failed — balance cap applied."
    await interaction.message.edit(
        content=None,
        embed=None,
        attachments=[discord.File(gif, COINFLIP_GIF)],
        view=gif_result_layout(
            COINFLIP_GIF,
            user_id=uid,
            bet=bet,
            rebet_cb=lambda i, u, b: _cf_rebet_from_interaction(
                i, u, b, pick,
            ),
        ),
    )


async def _prog_continue_interaction(
    interaction: discord.Interaction,
    player_side: str,
) -> None:
    from cogs.games import _ensure_session_active
    uid = interaction.user.id
    if interaction.message and str(interaction.message.id) in _cf_prog_msg_to_user:
        if _cf_prog_msg_to_user[str(interaction.message.id)] != uid:
            return await interaction.response.send_message(
                embed=utils.error_embed("Not your game."), ephemeral=True,
            )

    sess = await _ensure_session_active(uid, PROG_GAME)
    if not sess:
        return await interaction.response.send_message(
            embed=utils.error_embed("No active coin flip."), ephemeral=True,
        )

    state = json.loads(sess["state"])
    streak = int(state.get("streak") or 0)
    if streak >= MAX_PROG_STREAK:
        return await interaction.response.send_message(
            embed=utils.error_embed(
                f"Max streak (**{MAX_PROG_STREAK}**) — cash out first.",
            ),
            ephemeral=True,
        )

    bet = float(sess["bet"])
    hot_e, cold_e = get_coinflip_emojis()
    await interaction.response.defer()

    result, won = await _prog_flip_outcome(uid, bet, player_side, streak_before=streak)
    if not won:
        await _prog_settle_loss(
            uid, bet,
            user=interaction.user,
            client=interaction.client,
            guild_id=interaction.guild.id if interaction.guild else None,
        )
        hist = list(state.get("history") or [])
        gif = await _prog_render_gif(
            interaction.user.display_name, bet, player_side, result,
            won=False, streak=streak, history=hist,
        )
        _cf_prog_msg_to_user.pop(str(interaction.message.id), None)
        await interaction.message.edit(
            content=None,
            embed=None,
            attachments=[discord.File(gif, COINFLIP_GIF)],
            view=gif_result_layout(
                COINFLIP_GIF,
                user_id=uid,
                bet=bet,
                rebet_cb=lambda i, u, b: _cf_rebet_from_interaction(
                    i, u, b, player_side,
                ),
            ),
        )
        return

    streak += 1
    state["streak"] = streak
    state["last_pick"] = player_side
    state["last_result"] = result
    state["username"] = interaction.user.display_name
    state["hot_emoji"] = hot_e
    state["cold_emoji"] = cold_e
    await db.set_game_session(uid, PROG_GAME, bet, json.dumps(state))

    hist = list(state.get("history") or [])
    if result not in hist:
        hist.append(result)
    state["history"] = hist
    gif = await _prog_render_gif(
        interaction.user.display_name, bet, player_side, result,
        won=True, streak=streak, history=hist,
    )
    layout = await _prog_build_win_layout(uid, bet, streak, player_side, result)
    layout.attach_message(interaction.message)
    await interaction.message.edit(
        content=None,
        embed=None,
        attachments=[discord.File(gif, COINFLIP_GIF)],
        view=layout,
    )


async def _prog_build_win_layout(
    user_id: int,
    bet: float,
    streak: int,
    player_side: str,
    result: str,
):
    from modules.coinflip_progressive_v2 import build_progressive_win_layout

    hot_e, cold_e = get_coinflip_emojis()
    net = await progressive_cashout_net(bet, streak)
    return build_progressive_win_layout(
        user_id=user_id,
        cashout_net=int(net),
        hot_emoji=hot_e,
        cold_emoji=cold_e,
        on_cashout=_prog_cashout_interaction,
        on_flip=_prog_continue_interaction,
    )


async def _prog_first_flip(
    user_id: int,
    display_name: str,
    bet: float,
    player_side: str,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
) -> tuple[bytes, bool, str, str]:
    """First flip after bet deducted. Returns gif, won, pick, result."""
    result, won = await _prog_flip_outcome(user_id, bet, player_side, streak_before=0)
    hot_e, cold_e = get_coinflip_emojis()

    if not won:
        await _prog_settle_loss(
            user_id, bet, user=user, client=client, guild_id=guild_id,
        )
        gif = await _prog_render_gif(
            display_name, bet, player_side, result,
            won=False, streak=0, history=[],
        )
        return gif, False, player_side, result

    state = {
        "streak": 1,
        "username": display_name,
        "hot_emoji": hot_e,
        "cold_emoji": cold_e,
        "last_pick": player_side,
        "last_result": result,
        "history": [result],
    }
    await db.set_game_session(user_id, PROG_GAME, bet, json.dumps(state))
    gif = await _prog_render_gif(
        display_name, bet, player_side, result,
        won=True, streak=1, history=[result],
    )
    return gif, True, player_side, result


async def _cf_rebet_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    bet: float,
    choice: str | None,
) -> None:
    from cogs.games import _check_game_interaction

    player_side = parse_side(choice) if choice else None
    if not player_side:
        return await interaction.response.send_message(
            embed=utils.error_embed("Pick **hot** or **cold** to re-bet."), ephemeral=True,
        )
    if not await _check_game_interaction(interaction, user_id, "coinflip", bet):
        return
    await db.ensure_user(user_id, interaction.user.name)
    await interaction.response.defer()
    await db.add_balance(user_id, -bet, note="coinflip prog bet")
    hot_e, cold_e = get_coinflip_emojis()
    await db.set_game_session(
        user_id,
        PROG_GAME,
        bet,
        json.dumps({
            "streak": 0,
            "username": interaction.user.display_name,
            "hot_emoji": hot_e,
            "cold_emoji": cold_e,
        }),
    )
    gif, won, side, result = await _prog_first_flip(
        user_id,
        interaction.user.display_name,
        bet,
        player_side,
        user=interaction.user,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
    )
    if won:
        view = await _prog_build_win_layout(user_id, bet, 1, side, result)
    else:
        view = gif_result_layout(
            COINFLIP_GIF,
            user_id=user_id,
            bet=bet,
            rebet_cb=lambda i, u, b: _cf_rebet_from_interaction(i, u, b, side),
        )
    msg = await interaction.message.edit(
        content=None,
        embed=None,
        attachments=[discord.File(gif, COINFLIP_GIF)],
        view=view,
    )
    if won and isinstance(msg, discord.Message):
        view.attach_message(msg)
        _cf_prog_msg_to_user[str(msg.id)] = user_id


async def start_cf_bot_game(ctx: commands.Context, bet: float, choice: str | None) -> None:
    player_side = parse_side(choice) if choice else None
    if not player_side:
        return await ctx.send(embed=utils.error_embed(
            "Pick **hot** or **cold**: `.cf <bet> hot` or `.cf <bet> cold`",
        ))

    await db.add_balance(ctx.author.id, -bet, note="coinflip prog bet")
    hot_e, cold_e = get_coinflip_emojis()
    await db.set_game_session(
        ctx.author.id,
        PROG_GAME,
        bet,
        json.dumps({
            "streak": 0,
            "username": ctx.author.display_name,
            "hot_emoji": hot_e,
            "cold_emoji": cold_e,
        }),
    )

    gif, won, side, result = await _prog_first_flip(
        ctx.author.id,
        ctx.author.display_name,
        bet,
        player_side,
        user=ctx.author,
        client=ctx.bot,
        guild_id=ctx.guild.id if ctx.guild else None,
    )

    if won:
        view = await _prog_build_win_layout(ctx.author.id, bet, 1, side, result)
    else:
        view = gif_result_layout(
            COINFLIP_GIF,
            user_id=ctx.author.id,
            bet=bet,
            rebet_cb=lambda i, u, b: _cf_rebet_from_interaction(i, u, b, side),
        )

    msg = await ctx.send(file=discord.File(gif, COINFLIP_GIF), view=view)
    if won:
        view.attach_message(msg)
        _cf_prog_msg_to_user[str(msg.id)] = ctx.author.id


async def start_cf_pvp(ctx: commands.Context, opponent: discord.Member, bet: float, choice: str) -> None:
    view = CoinflipChallengeLayout(ctx.author, opponent, bet, choice)
    msg = await ctx.send(view=view)
    view.attach_message(msg)
