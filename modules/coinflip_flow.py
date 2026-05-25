"""Hot/Cold coin flip — vs bot, PvP challenge, V2 GIF in same message."""

from __future__ import annotations

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


def parse_cf_args(ctx: commands.Context) -> tuple[discord.Member | None, float | None, str | None]:
    parts = ctx.message.content.split()
    tokens = parts[1:] if len(parts) > 1 else []
    opponent = ctx.message.mentions[0] if ctx.message.mentions else None
    bet = None
    choice = None
    for tok in tokens:
        if tok.startswith("<@"):
            continue
        side = parse_side(tok)
        if side:
            choice = side
            continue
        try:
            bet = float(tok.replace(",", ""))
        except ValueError:
            continue
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
    wuser = await db.get_user(winner_id)
    cur = float((wuser or {}).get("balance", 0))
    capped = await bc.apply_balance_cap(winner_id, cur + payout)
    payout = max(0.0, capped - cur)
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


async def _run_cf_bot_round(
    user_id: int,
    display_name: str,
    bet: float,
    choice: str | None,
    *,
    user: discord.abc.User | None = None,
    client: discord.Client | None = None,
    guild_id: int | None = None,
):
    player_side = parse_side(choice) if choice else None
    if not player_side:
        player_side = random.choice(SIDES)
    hot_e, cold_e = get_coinflip_emojis()
    rigged = await bc.should_rig_outcome(user_id, "coinflip", bet)
    if rigged:
        result = "COLD" if player_side == "HOT" else "HOT"
    else:
        result = random.choice(SIDES)

    won = result == player_side
    gross = bet * 2 if won else 0
    net = await _payout(user_id, "coinflip", bet, gross)
    await _record(
        user_id, won, bet, net,
        game_id="coinflip",
        user=user,
        client=client,
        guild_id=guild_id,
    )
    lp, ll = (net, 0.0) if won else (0.0, bet)

    gif = await image_gen.render_coinflip_gif(
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
    )
    return gif, player_side


async def _cf_rebet_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    bet: float,
    choice: str | None,
) -> None:
    from cogs.games import _check_game_interaction

    if not await _check_game_interaction(interaction, user_id, "coinflip", bet):
        return
    await db.ensure_user(user_id, interaction.user.name)
    await interaction.response.defer()
    gif, side = await _run_cf_bot_round(
        user_id,
        interaction.user.display_name,
        bet,
        choice,
        user=interaction.user,
        client=interaction.client,
        guild_id=interaction.guild.id if interaction.guild else None,
    )
    await interaction.message.edit(
        content=None,
        embed=None,
        attachments=[discord.File(gif, COINFLIP_GIF)],
        view=gif_result_layout(
            COINFLIP_GIF,
            user_id=user_id,
            bet=bet,
            rebet_cb=lambda i, u, b: _cf_rebet_from_interaction(i, u, b, side),
        ),
    )


async def start_cf_bot_game(ctx: commands.Context, bet: float, choice: str | None) -> None:
    gif, side = await _run_cf_bot_round(
        ctx.author.id,
        ctx.author.display_name,
        bet,
        choice,
        user=ctx.author,
        client=ctx.bot,
        guild_id=ctx.guild.id if ctx.guild else None,
    )
    await ctx.send(
        file=discord.File(gif, COINFLIP_GIF),
        view=gif_result_layout(
            COINFLIP_GIF,
            user_id=ctx.author.id,
            bet=bet,
            rebet_cb=lambda i, u, b: _cf_rebet_from_interaction(i, u, b, side),
        ),
    )


async def start_cf_pvp(ctx: commands.Context, opponent: discord.Member, bet: float, choice: str) -> None:
    view = CoinflipChallengeLayout(ctx.author, opponent, bet, choice)
    msg = await ctx.send(view=view)
    view.attach_message(msg)
