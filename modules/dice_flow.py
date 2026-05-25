"""Dice — vs bot GIF, PvP challenge (HTW-style)."""

from __future__ import annotations

import random

import discord
from discord.ext import commands

import config
from database import db
from modules import image_gen
from modules.game_media_v2 import gif_media_layout, gif_result_layout
from modules.pvp_challenge import PVP_CHALLENGE_TIMEOUT, PvpChallengeView
from modules import flip_utils as utils
from modules import flip_balance_cap as bc

DICE_GIF = "dice.gif"


def parse_dice_args(ctx: commands.Context) -> tuple[discord.Member | None, float | None]:
    """`.dice <bet>` or `.dice @user <bet>`."""
    parts = ctx.message.content.split()
    tokens = parts[1:] if len(parts) > 1 else []
    opponent = ctx.message.mentions[0] if ctx.message.mentions else None
    bet = None
    for tok in reversed(tokens):
        if tok.startswith("<@"):
            continue
        try:
            bet = float(tok.replace(",", ""))
        except ValueError:
            continue
    return opponent, bet


def dice_roll_pair(*, rig_vs_bot: bool = False) -> tuple[int, int, str]:
    """(left_roll, right_roll, left outcome: WIN|LOSE|PUSH)."""
    if rig_vs_bot:
        from modules.game_rig import dice_roll_rigged
        return dice_roll_rigged()
    left = random.randint(1, 6)
    right = random.randint(1, 6)
    if left > right:
        return left, right, "WIN"
    if left < right:
        return left, right, "LOSE"
    return left, right, "PUSH"


async def _payout(user_id, game_id, bet, gross):
    from cogs.games import _payout as gp
    return await gp(user_id, game_id, bet, gross)


async def _record(user_id, won, bet, net):
    from cogs.games import _record as gr
    await gr(user_id, won, bet, net)


async def _run_dice_animation(
    target,
    *,
    mode: str,
    left_name: str,
    right_name: str,
    left_roll: int,
    right_roll: int,
    bet: float,
    left_payout: float,
    left_lost: float,
    right_payout: float,
    right_lost: float,
    is_push: bool = False,
    message: discord.Message | None = None,
    user_id: int | None = None,
) -> discord.Message | None:
    gif = await image_gen.render_dice_gif(
        mode=mode,
        left_name=left_name,
        right_name=right_name,
        left_roll=left_roll,
        right_roll=right_roll,
        bet=bet,
        left_payout=left_payout,
        left_lost=left_lost,
        right_payout=right_payout,
        right_lost=right_lost,
        is_push=is_push,
    )
    if user_id and bet > 0 and mode == "bot":
        layout = gif_result_layout(
            DICE_GIF,
            user_id=user_id,
            bet=bet,
            rebet_cb=_dice_rebet_from_interaction,
        )
    else:
        layout = gif_media_layout(DICE_GIF)
    file = discord.File(gif, DICE_GIF)
    if message is not None:
        await message.edit(content=None, embed=None, attachments=[file], view=layout)
        return message
    if isinstance(target, discord.Message):
        await target.edit(content=None, embed=None, attachments=[file], view=layout)
        return target
    if isinstance(target, commands.Context):
        return await target.send(file=file, view=layout)
    return await target.send(file=file, view=layout)


async def settle_dice_pvp(
    challenger_id: int,
    opponent_id: int,
    bet: float,
    left_roll: int,
    right_roll: int,
) -> tuple[int | None, float]:
    """Returns (winner_id, winner payout credited)."""
    from cogs.games import _earn_rakeback

    game_cfg = await db.get_game_config("dice")
    he = float(game_cfg["house_edge"]) if game_cfg else 0.02
    winner_payout = 0.0

    if left_roll > right_roll:
        winner_id = challenger_id
    elif left_roll < right_roll:
        winner_id = opponent_id
    else:
        winner_id = None

    if winner_id is None:
        await db.add_balance(challenger_id, bet, note="dice pvp push refund")
        await db.add_balance(opponent_id, bet, note="dice pvp push refund")
        pass
    else:
        pool = bet * 2
        payout = pool * (1 - he)
        wuser = await db.get_user(winner_id)
        cur = float((wuser or {}).get("balance", 0))
        capped = await bc.apply_balance_cap(winner_id, cur + payout)
        payout = max(0.0, capped - cur)
        winner_payout = payout
        if payout > 0:
            await db.add_balance(winner_id, payout, note="dice pvp win")
    await db.add_wager(challenger_id, bet)
    await db.add_wager(opponent_id, bet)
    await _earn_rakeback(challenger_id, bet)
    await _earn_rakeback(opponent_id, bet)

    win_display = bet * 2 * (1 - he)
    if winner_id == challenger_id:
        await _record(challenger_id, True, bet, win_display)
        await _record(opponent_id, False, bet, 0)
    elif winner_id == opponent_id:
        await _record(challenger_id, False, bet, 0)
        await _record(opponent_id, True, bet, win_display)
    else:
        await _record(challenger_id, False, bet, bet)
        await _record(opponent_id, False, bet, bet)

    return winner_id, winner_payout


class DiceChallengeView(PvpChallengeView):
    def __init__(self, challenger_id: int, opponent_id: int, bet: float):
        super().__init__(challenger_id, opponent_id, game_name="Dice")
        self.bet = bet

    async def handle_accept(self, interaction: discord.Interaction):
        from cogs.games import _err

        if interaction.user.id != self.opponent_id:
            return await interaction.response.send_message(
                "Only the challenged player can accept.", ephemeral=True,
            )

        await db.ensure_user(self.challenger_id, "challenger")
        await db.ensure_user(self.opponent_id, "opponent")

        if not await db.get_game_config("dice"):
            return await interaction.response.send_message(
                embed=_err("Dice is disabled."), ephemeral=True,
            )

        for uid in (self.challenger_id, self.opponent_id):
            user = await db.get_user(uid)
            if not user or float(user["balance"]) < self.bet:
                return await interaction.response.send_message(
                    embed=_err("One of you no longer has enough balance."),
                    ephemeral=True,
                )

        self._mark_done()
        await interaction.response.defer()

        await db.add_balance(self.challenger_id, -self.bet, note="dice pvp bet")
        await db.add_balance(self.opponent_id, -self.bet, note="dice pvp bet")

        left_roll, right_roll, _ = dice_roll_pair()
        winner_id, win_pay = await settle_dice_pvp(
            self.challenger_id, self.opponent_id, self.bet, left_roll, right_roll,
        )

        challenger = interaction.guild.get_member(self.challenger_id) if interaction.guild else None
        opponent = interaction.user if isinstance(interaction.user, discord.Member) else None
        left_name = challenger.display_name if challenger else str(self.challenger_id)
        right_name = opponent.display_name if opponent else str(self.opponent_id)

        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass

        is_push = winner_id is None
        if is_push:
            lp, ll, rp, rl = self.bet, 0.0, self.bet, 0.0
        elif winner_id == self.challenger_id:
            lp, ll, rp, rl = win_pay, 0.0, 0.0, self.bet
        else:
            lp, ll, rp, rl = 0.0, self.bet, win_pay, 0.0

        await _run_dice_animation(
            interaction.channel,
            mode="pvp",
            left_name=left_name,
            right_name=right_name,
            left_roll=left_roll,
            right_roll=right_roll,
            bet=self.bet,
            left_payout=lp,
            left_lost=ll,
            right_payout=rp,
            right_lost=rl,
            is_push=is_push,
            message=interaction.message,
        )


async def _dice_rebet_from_interaction(
    interaction: discord.Interaction,
    user_id: int,
    bet: float,
) -> None:
    from cogs.games import _check_game_interaction

    if not await _check_game_interaction(interaction, user_id, "dice", bet):
        return
    await db.ensure_user(user_id, interaction.user.name)
    await interaction.response.defer()

    rigged = await bc.should_rig_outcome(user_id, "dice", bet)
    left_roll, right_roll, outcome = dice_roll_pair(rig_vs_bot=rigged)

    if outcome == "WIN":
        gross, won = bet * 2, True
    elif outcome == "PUSH":
        gross, won = bet, False
    else:
        gross, won = 0, False

    payout_credited = await _payout(user_id, "dice", bet, gross)
    await _record(user_id, won, bet, payout_credited)

    house_name = getattr(config, "BOT_DISPLAY_NAME", "VegasBet")
    if outcome == "WIN":
        lp, ll, rp, rl, push = payout_credited, 0.0, 0.0, bet, False
    elif outcome == "PUSH":
        lp, ll, rp, rl, push = bet, 0.0, bet, 0.0, True
    else:
        lp, ll, rp, rl, push = 0.0, bet, bet, 0.0, False

    await _run_dice_animation(
        interaction.message,
        mode="bot",
        left_name=interaction.user.display_name,
        right_name=house_name,
        left_roll=left_roll,
        right_roll=right_roll,
        bet=bet,
        left_payout=lp,
        left_lost=ll,
        right_payout=rp,
        right_lost=rl,
        is_push=push,
        message=interaction.message,
        user_id=user_id,
    )


async def start_dice_bot_game(ctx: commands.Context, bet: float) -> None:
    rigged = await bc.should_rig_outcome(ctx.author.id, "dice", bet)
    left_roll, right_roll, outcome = dice_roll_pair(rig_vs_bot=rigged)

    if outcome == "WIN":
        gross, won = bet * 2, True
    elif outcome == "PUSH":
        gross, won = bet, False
    else:
        gross, won = 0, False

    payout_credited = await _payout(ctx.author.id, "dice", bet, gross)
    await _record(ctx.author.id, won, bet, payout_credited)

    house_name = getattr(config, "BOT_DISPLAY_NAME", "VegasBet")
    if outcome == "WIN":
        lp, ll, rp, rl, push = payout_credited, 0.0, 0.0, bet, False
    elif outcome == "PUSH":
        lp, ll, rp, rl, push = bet, 0.0, bet, 0.0, True
    else:
        lp, ll, rp, rl, push = 0.0, bet, bet, 0.0, False

    await _run_dice_animation(
        ctx,
        mode="bot",
        left_name=ctx.author.display_name,
        right_name=house_name,
        left_roll=left_roll,
        right_roll=right_roll,
        bet=bet,
        left_payout=lp,
        left_lost=ll,
        right_payout=rp,
        right_lost=rl,
        is_push=push,
        user_id=ctx.author.id,
    )


async def start_dice_pvp(ctx: commands.Context, opponent: discord.Member, bet: float) -> None:
    embed = discord.Embed(
        title="🎲 Dice Challenge",
        description=(
            f"{ctx.author.mention} challenges {opponent.mention} to **Dice**!\n\n"
            f"**Bet:** {utils.fmt_pts(bet)} pts\n"
            f"Highest roll wins • tie = push\n\n"
            f"{opponent.mention} — press **Accept** within **{PVP_CHALLENGE_TIMEOUT} seconds**.\n"
            f"Either player can press **Decline & Cancel** to withdraw."
        ),
        color=0x3498DB,
    )
    view = DiceChallengeView(ctx.author.id, opponent.id, bet)
    msg = await ctx.send(embed=embed, view=view)
    view.attach_message(msg)
