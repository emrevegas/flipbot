"""All casino games as prefix commands.

Games: coinflip, dice, roulette, mines, hilo, blackjack, limbo, slots, crash
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
from modules import image_gen, utils, balance_cap as bc


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
        await ctx.send(embed=_err(
            f"You already have an active **{existing['game']}** game. "
            f"Finish or cash out first."
        ))
        return False

    return True


async def _payout(user_id: int | str, game_id: str, bet: float, gross_payout: float) -> float:
    """Deduct bet, apply house edge / balance cap, credit payout. Returns net payout."""
    game_cfg = await db.get_game_config(game_id)
    house_edge = float(game_cfg["house_edge"]) if game_cfg else 0.02
    # deduct bet
    await db.add_balance(user_id, -bet, note=f"{game_id} bet")
    # apply house edge
    net = gross_payout * (1 - house_edge)
    # cap
    user = await db.get_user(user_id)
    if user:
        current_bal = float(user["balance"]) - bet  # already deducted above but not committed yet
    current_bal_after = float((await db.get_user(user_id) or {}).get("balance", 0))
    capped = await bc.apply_balance_cap(user_id, current_bal_after + net)
    net = max(0.0, capped - current_bal_after)

    if net > 0:
        await db.add_balance(user_id, net, note=f"{game_id} payout")
    await db.add_wager(user_id, bet)

    import config as _cfg
    # Rakeback
    tier = utils.get_rakeback_tier(
        float((await db.get_user(user_id) or {}).get("total_wagered", 0))
    )
    rb = bet * tier["rate"]
    await db.add_rakeback(user_id, rb)

    return net


async def _record(user_id: int | str, won: bool, bet: float, net: float):
    profit = net - bet if won else -bet
    await db.record_game_result(user_id, won, profit)


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
            won, gross, outcome = False, amount, "TIE"  # tie returns bet
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
            # ensure no jackpot
            reels = [random.choice(self.SLOT_SYMBOLS) for _ in range(3)]
            while len(set(reels)) == 1:
                reels = [random.choice(self.SLOT_SYMBOLS) for _ in range(3)]
        else:
            # weighted toward wins
            if random.random() < 0.30:  # 30% jackpot chance
                sym = random.choice(self.SLOT_SYMBOLS)
                reels = [sym, sym, sym]
            elif random.random() < 0.45:
                sym = random.choice(self.SLOT_SYMBOLS)
                reels = [sym, sym, random.choice(self.SLOT_SYMBOLS)]
                random.shuffle(reels)
            else:
                reels = [random.choice(self.SLOT_SYMBOLS) for _ in range(3)]

        # calculate payout
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

        import asyncio as _aio
        loop = _aio.get_event_loop()
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

        # generate crash point
        if rigged:
            crash_point = round(random.uniform(1.0, 1.8), 2)
        else:
            r = random.random()
            # house edge built in: crash point = 0.99 / (1 - r), capped
            crash_point = round(min(0.99 / max(1 - r, 0.01), 1000.0), 2)

        # did auto cashout trigger before crash?
        if auto_cashout and auto_cashout <= crash_point:
            multi = auto_cashout
            won = True
            outcome = f"CASHED OUT @ {multi:.2f}x"
        elif not auto_cashout and crash_point > 1.0:
            # no auto: random manual cashout between 1.01 and crash
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
        """Start a blackjack game. .blackjack 100 — then .hit / .stand / .double"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "blackjack", amount):
            return

        deck = self._new_deck()
        random.shuffle(deck)
        player = [deck.pop(), deck.pop()]
        dealer = [deck.pop(), deck.pop()]

        state = {
            "bet": amount,
            "player": player,
            "dealer": dealer,
            "deck": deck,
            "doubled": False,
        }
        await db.set_game_session(ctx.author.id, "blackjack", amount, json.dumps(state))
        await db.add_balance(ctx.author.id, -amount, note="blackjack bet")

        embed = self._bj_embed(player, [dealer[0], "?"], amount, in_progress=True)
        await ctx.send(embed=embed)

        if self._hand_value(player) == 21:
            await self._bj_finish(ctx, "natural_blackjack")

    @commands.command(name="hit")
    async def bj_hit(self, ctx: commands.Context):
        """Hit in blackjack."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "blackjack":
            return await ctx.send(embed=_err("No active blackjack game. Start with `.blackjack <amount>`."))
        state = json.loads(sess["state"])
        deck = state["deck"]
        state["player"].append(deck.pop())
        state["deck"] = deck
        await db.set_game_session(ctx.author.id, "blackjack", sess["bet"], json.dumps(state))

        pv = self._hand_value(state["player"])
        if pv > 21:
            await self._bj_finish(ctx, "bust", state=state)
        elif pv == 21:
            await self._bj_finish(ctx, "stand", state=state)
        else:
            embed = self._bj_embed(state["player"], [state["dealer"][0], "?"], sess["bet"], in_progress=True)
            await ctx.send(embed=embed)

    @commands.command(name="stand")
    async def bj_stand(self, ctx: commands.Context):
        """Stand in blackjack."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "blackjack":
            return await ctx.send(embed=_err("No active blackjack game."))
        state = json.loads(sess["state"])
        await self._bj_finish(ctx, "stand", state=state)

    @commands.command(name="double")
    async def bj_double(self, ctx: commands.Context):
        """Double down in blackjack."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "blackjack":
            return await ctx.send(embed=_err("No active blackjack game."))
        state = json.loads(sess["state"])
        user = await db.get_user(ctx.author.id)
        if float(user["balance"]) < sess["bet"]:
            return await ctx.send(embed=_err("Insufficient balance to double."))
        await db.add_balance(ctx.author.id, -sess["bet"], note="blackjack double")
        state["doubled"] = True
        state["player"].append(state["deck"].pop())
        await self._bj_finish(ctx, "stand", state=state)

    async def _bj_finish(self, ctx: commands.Context, reason: str, state: dict | None = None):
        sess = await db.get_game_session(ctx.author.id)
        if not state:
            state = json.loads(sess["state"])
        original_bet = float(sess["bet"])
        total_bet = original_bet * (2 if state.get("doubled") else 1)

        # dealer plays
        if reason not in ("bust", "natural_blackjack"):
            while self._hand_value(state["dealer"]) < 17:
                state["dealer"].append(state["deck"].pop())

        pv = self._hand_value(state["player"])
        dv = self._hand_value(state["dealer"])

        if reason == "natural_blackjack":
            outcome, gross = "BLACKJACK", total_bet * 2.5
            won = True
        elif reason == "bust" or pv > 21:
            outcome, gross = "BUST", 0
            won = False
        elif dv > 21 or pv > dv:
            outcome, gross = "WIN", total_bet * 2
            won = True
        elif pv == dv:
            outcome, gross = "PUSH", total_bet
            won = False
        else:
            outcome, gross = "LOSS", 0
            won = False

        game_cfg = await db.get_game_config("blackjack")
        he = float(game_cfg["house_edge"]) if game_cfg else 0.02
        net = gross * (1 - he) if gross > 0 else 0
        if net > 0:
            net_capped = await bc.apply_balance_cap(
                ctx.author.id,
                float((await db.get_user(ctx.author.id) or {}).get("balance", 0)) + net,
            )
            net = max(0.0, net_capped - float((await db.get_user(ctx.author.id) or {}).get("balance", 0)))
            await db.add_balance(ctx.author.id, net, note="blackjack payout")
        await db.add_wager(ctx.author.id, total_bet)
        tier = utils.get_rakeback_tier(float((await db.get_user(ctx.author.id) or {}).get("total_wagered", 0)))
        await db.add_rakeback(ctx.author.id, total_bet * tier["rate"])
        await _record(ctx.author.id, won, total_bet, net)
        await db.clear_game_session(ctx.author.id)

        embed = self._bj_embed(state["player"], state["dealer"], total_bet, result=outcome, net=net)
        await ctx.send(embed=embed)

    def _bj_embed(self, player, dealer, bet, *, in_progress=False, result=None, net=None):
        COLORS = {
            "WIN": 0x2ECC71, "BLACKJACK": 0xF1C40F, "PUSH": 0x5865F2,
            "LOSS": 0xE74C3C, "BUST": 0xE74C3C,
        }
        color = COLORS.get(result, 0x5865F2)
        embed = discord.Embed(title=f"🃏 Blackjack {'— ' + result if result else ''}", color=color)
        pv = self._hand_value(player)
        dv = self._hand_value(dealer) if "?" not in dealer else "?"
        embed.add_field(name=f"Your Hand ({pv})", value=" ".join(str(c) for c in player), inline=False)
        embed.add_field(name=f"Dealer Hand ({dv})", value=" ".join(str(c) for c in dealer), inline=False)
        embed.add_field(name="Bet", value=f"`{utils.fmt_pts(bet)} pts`", inline=True)
        if result and net is not None:
            embed.add_field(name="Payout", value=f"`{utils.fmt_pts(net)} pts`", inline=True)
        if in_progress:
            embed.set_footer(text="Use .hit / .stand / .double")
        return embed

    def _new_deck(self) -> list[str]:
        suits = ["♠", "♥", "♦", "♣"]
        ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
        return [f"{r}{s}" for s in suits for r in ranks] * 2

    def _hand_value(self, hand: list[str]) -> int:
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
                total += int(rank)
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
        embed.add_field(name="Multiplier", value=f"`1.00x`", inline=True)
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
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "hilo":
            return await ctx.send(embed=_err("No active Hi-Lo game. Start with `.hilo <amount>`."))
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
            # synthesize card
            next_card = f"{['A','2','3','4','5','6','7','8','9','10','J','Q','K'][next_rank-1]}♠"

        correct = (guess == "higher" and next_rank > current_rank) or \
                  (guess == "lower" and next_rank < current_rank)
        tie = next_rank == current_rank

        if tie:
            state["current"] = next_card
            state["deck"] = state.get("deck", [])
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
            state["deck"] = state.get("deck", [])
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
            tier = utils.get_rakeback_tier(float((await db.get_user(ctx.author.id) or {}).get("total_wagered", 0)))
            await db.add_rakeback(ctx.author.id, sess["bet"] * tier["rate"])
            await _record(ctx.author.id, False, sess["bet"], 0)
            embed = discord.Embed(title="🃏 Hi-Lo — WRONG!", color=0xE74C3C)
            embed.add_field(name="New Card", value=f"`{next_card}`", inline=True)
            embed.add_field(name="Lost", value=f"`{utils.fmt_pts(sess['bet'])} pts`", inline=True)
            await ctx.send(embed=embed)

    @commands.command(name="cashout")
    async def hilo_cashout(self, ctx: commands.Context):
        """Cash out Hi-Lo winnings."""
        sess = await db.get_game_session(ctx.author.id)
        if not sess:
            return await ctx.send(embed=_err("No active game to cash out."))

        if sess["game"] == "hilo":
            state = json.loads(sess["state"])
            gross = sess["bet"] * state["multiplier"]
            net = await _payout(ctx.author.id, "hilo", 0, gross)  # bet already deducted
            # we need to add gross directly since bet was already deducted
            game_cfg = await db.get_game_config("hilo")
            he = float(game_cfg["house_edge"]) if game_cfg else 0.02
            actual_net = gross * (1 - he)
            await db.add_balance(ctx.author.id, actual_net, note="hilo cashout")
            await db.add_wager(ctx.author.id, sess["bet"])
            tier = utils.get_rakeback_tier(float((await db.get_user(ctx.author.id) or {}).get("total_wagered", 0)))
            await db.add_rakeback(ctx.author.id, sess["bet"] * tier["rate"])
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
        """Start a mines game. .mines 100 3 — then .pick A1, .cashout"""
        await db.ensure_user(ctx.author.id, ctx.author.name)
        if not await _check_game(ctx, "mines", amount):
            return
        if not 1 <= mine_count <= 20:
            return await ctx.send(embed=_err("Mine count must be between 1 and 20."))

        grid_size = 5
        total = grid_size * grid_size
        mine_positions = random.sample(range(total), mine_count)
        state = {
            "bet": amount,
            "grid_size": grid_size,
            "mines": mine_positions,
            "revealed": [],
            "multiplier": 1.0,
            "mine_count": mine_count,
        }
        await db.set_game_session(ctx.author.id, "mines", amount, json.dumps(state))
        await db.add_balance(ctx.author.id, -amount, note="mines bet")

        loop = asyncio.get_event_loop()
        img_buf = await loop.run_in_executor(
            None, image_gen.render_mines_grid,
            grid_size, set(mine_positions), set(), amount, 1.0,
        )
        await ctx.send(
            content=f"💣 **Mines** started! Use `.pick A1` to reveal cells, `.cashout` to cash out.",
            file=discord.File(img_buf, "mines.png"),
        )

    @commands.command(name="pick")
    async def mines_pick(self, ctx: commands.Context, cell: str):
        """Pick a cell in mines. .pick A1"""
        sess = await db.get_game_session(ctx.author.id)
        if not sess or sess["game"] != "mines":
            return await ctx.send(embed=_err("No active mines game."))
        state = json.loads(sess["state"])

        cell = cell.upper().strip()
        if len(cell) < 2:
            return await ctx.send(embed=_err("Invalid cell. Use format like A1, B3, etc."))

        row_char = cell[0]
        col_str = cell[1:]
        if not row_char.isalpha() or not col_str.isdigit():
            return await ctx.send(embed=_err("Invalid cell format. Example: A1, B3"))

        gs = state["grid_size"]
        row = ord(row_char) - ord('A')
        col = int(col_str) - 1

        if row < 0 or row >= gs or col < 0 or col >= gs:
            return await ctx.send(embed=_err(f"Cell out of bounds. Valid rows: A-{chr(64+gs)}, cols: 1-{gs}"))

        idx = row * gs + col
        if idx in state["revealed"]:
            return await ctx.send(embed=_err("You already revealed that cell!"))

        state["revealed"].append(idx)
        mine_set = set(state["mines"])
        rigged = await bc.should_rig_outcome(ctx.author.id, "mines", sess["bet"])

        hit_mine = idx in mine_set
        if rigged and not hit_mine and len(state["revealed"]) >= 3:
            # Force a mine hit after some safe picks
            if random.random() < 0.4:
                hit_mine = True
                mine_set.add(idx)
                state["mines"] = list(mine_set)

        if hit_mine:
            await db.clear_game_session(ctx.author.id)
            await db.add_wager(ctx.author.id, sess["bet"])
            tier = utils.get_rakeback_tier(float((await db.get_user(ctx.author.id) or {}).get("total_wagered", 0)))
            await db.add_rakeback(ctx.author.id, sess["bet"] * tier["rate"])
            await _record(ctx.author.id, False, sess["bet"], 0)
            loop = asyncio.get_event_loop()
            img_buf = await loop.run_in_executor(
                None, image_gen.render_mines_grid,
                gs, mine_set, set(state["revealed"]), sess["bet"], state["multiplier"], True,
            )
            await ctx.send(
                content=f"💥 **MINE!** {ctx.author.mention} You lost **{utils.fmt_pts(sess['bet'])} pts**!",
                file=discord.File(img_buf, "mines.png"),
            )
        else:
            safe_cells = gs * gs - state["mine_count"]
            picks = len(state["revealed"])
            mult = round(1.0 + (picks / max(safe_cells, 1)) * (state["mine_count"] / 5), 2)
            state["multiplier"] = mult
            await db.set_game_session(ctx.author.id, "mines", sess["bet"], json.dumps(state))
            loop = asyncio.get_event_loop()
            img_buf = await loop.run_in_executor(
                None, image_gen.render_mines_grid,
                gs, mine_set, set(state["revealed"]), sess["bet"], mult,
            )
            await ctx.send(
                content=f"✅ Safe! Multiplier: **{mult:.2f}x** | Potential: **{utils.fmt_pts(sess['bet'] * mult)} pts** | `.cashout` to collect",
                file=discord.File(img_buf, "mines.png"),
            )

    async def _mines_cashout(self, ctx: commands.Context, sess: dict):
        state = json.loads(sess["state"])
        if not state["revealed"]:
            await db.clear_game_session(ctx.author.id)
            await db.add_balance(ctx.author.id, sess["bet"], note="mines cancelled")
            return await ctx.send(embed=discord.Embed(description="No cells revealed — bet refunded.", color=0x5865F2))

        game_cfg = await db.get_game_config("mines")
        he = float(game_cfg["house_edge"]) if game_cfg else 0.02
        gross = sess["bet"] * state["multiplier"]
        net = gross * (1 - he)
        net_capped_bal = await bc.apply_balance_cap(
            ctx.author.id,
            float((await db.get_user(ctx.author.id) or {}).get("balance", 0)) + net,
        )
        net = max(0.0, net_capped_bal - float((await db.get_user(ctx.author.id) or {}).get("balance", 0)))
        await db.add_balance(ctx.author.id, net, note="mines cashout")
        await db.add_wager(ctx.author.id, sess["bet"])
        tier = utils.get_rakeback_tier(float((await db.get_user(ctx.author.id) or {}).get("total_wagered", 0)))
        await db.add_rakeback(ctx.author.id, sess["bet"] * tier["rate"])
        await _record(ctx.author.id, True, sess["bet"], net)
        await db.clear_game_session(ctx.author.id)

        await ctx.send(embed=discord.Embed(
            title="💰 Mines — Cashed Out!",
            description=f"Multiplier: **{state['multiplier']:.2f}x** | Payout: **{utils.fmt_pts(net)} pts**",
            color=0x2ECC71,
        ))


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
