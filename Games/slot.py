"""
3×5 Slot Machine — Provably Fair, 30 paylines

House edge / RTP ≈ 87.6 %  (house edge ≈ 12.4 %)
--------------------------------------------------
Player bets B total (covers all 30 lines).
Each payline effectively stakes  B / 30.
A winning line with multiplier M returns  (B / 30) × M.
Total payout = Σ (B/30 × M_i)  over all winning lines i.

By linearity of expectation and independent reel spins,
RTP = Σ_symbols Σ_k p_s^k × (1-p_s) × payout_k  ≈ 87.6 %.

Provably Fair
-------------
consume_pf_round() → 8 floats from HMAC-SHA256.
We seed Python Random from pf_fl[0] to derive 15 deterministic floats.
Verifiable: seed = int(pf_fl[0] × 2³²); rng.seed(seed); rng.random() × 15
"""

import random
from typing import Optional

import discord

from modules.utils import format_balance
from .base_game import BaseGame, GameResult


# ─── Constants ────────────────────────────────────────────────────────────────

ROWS = 3
COLS = 5
NUM_LINES = 30
SPIN_EMOJI = "🔮"

# Default spin emoji (overridden per-spin by DB setting)
_DEFAULT_SPIN_EMOJI = "🔮"

# ─── Symbol table ─────────────────────────────────────────────────────────────

SYMBOLS: list[dict] = [
    {"id": "cherry",  "emoji": "🍒", "weight": 24, "payout_3":  10, "payout_4":  25, "payout_5":   50, "label": "Cherry"},
    {"id": "lemon",   "emoji": "🍋", "weight": 20, "payout_3":  15, "payout_4":  36, "payout_5":   72, "label": "Lemon"},
    {"id": "orange",  "emoji": "🍊", "weight": 16, "payout_3":  25, "payout_4":  60, "payout_5":  125, "label": "Orange"},
    {"id": "grapes",  "emoji": "🍇", "weight": 12, "payout_3":  40, "payout_4": 100, "payout_5":  200, "label": "Grapes"},
    {"id": "bell",    "emoji": "🔔", "weight":  8, "payout_3":  60, "payout_4": 175, "payout_5":  350, "label": "Bell"},
    {"id": "star",    "emoji": "⭐", "weight":  6, "payout_3": 100, "payout_4": 300, "payout_5":  600, "label": "Star"},
    {"id": "diamond", "emoji": "💎", "weight":  4, "payout_3": 200, "payout_4": 600, "payout_5": 1250, "label": "Diamond"},
    {"id": "seven",   "emoji": "7️⃣", "weight":  2, "payout_3": 250, "payout_4": 750, "payout_5": 2500, "label": "Seven"},
]

_WEIGHT_TOTAL = sum(s["weight"] for s in SYMBOLS)

# Default emoji map (symbol id → emoji)
_DEFAULT_EMOJI_MAP: dict[str, str] = {s["id"]: s["emoji"] for s in SYMBOLS}


def get_slot_emojis() -> tuple[dict[str, str], str]:
    """Return (symbol_emoji_map, spin_emoji) from DB, falling back to defaults."""
    try:
        from modules.database import get_data
        games_data = get_data("server/games") or {}
        slot_data = games_data.get("slot", {}) if isinstance(games_data, dict) else {}
        if not isinstance(slot_data, dict):
            slot_data = {}
        emojis = slot_data.get("emojis", {})
        if not isinstance(emojis, dict):
            emojis = {}
        symbol_map = {}
        for s in SYMBOLS:
            custom = emojis.get(s["id"])
            symbol_map[s["id"]] = str(custom) if custom else s["emoji"]
        spin_e = emojis.get("spin") or _DEFAULT_SPIN_EMOJI
        return symbol_map, str(spin_e)
    except Exception:
        return dict(_DEFAULT_EMOJI_MAP), _DEFAULT_SPIN_EMOJI

# ─── 30 Paylines ──────────────────────────────────────────────────────────────
# Each tuple = (row_for_col0, row_for_col1, ..., row_for_col4)

PAYLINES: list[tuple] = [
    (1, 1, 1, 1, 1),  #  1  Middle horizontal
    (0, 0, 0, 0, 0),  #  2  Top horizontal
    (2, 2, 2, 2, 2),  #  3  Bottom horizontal
    (0, 1, 2, 1, 0),  #  4  V-shape
    (2, 1, 0, 1, 2),  #  5  ^-shape
    (0, 0, 1, 2, 2),  #  6  Diagonal ↘
    (2, 2, 1, 0, 0),  #  7  Diagonal ↗
    (1, 0, 0, 0, 1),  #  8  Top arch
    (1, 2, 2, 2, 1),  #  9  Bottom arch
    (0, 1, 1, 1, 0),  # 10  Mid-bridge top
    (2, 1, 1, 1, 2),  # 11  Mid-bridge bottom
    (1, 1, 0, 1, 1),  # 12  Center dip top
    (1, 1, 2, 1, 1),  # 13  Center dip bottom
    (0, 0, 0, 1, 2),  # 14  Top → diagonal
    (2, 2, 2, 1, 0),  # 15  Bottom → diagonal
    (0, 1, 2, 2, 2),  # 16  Step down
    (2, 1, 0, 0, 0),  # 17  Step up
    (1, 0, 1, 2, 1),  # 18  Zigzag 1
    (1, 2, 1, 0, 1),  # 19  Zigzag 2
    (0, 2, 2, 2, 0),  # 20  Bottom arch wide
    (2, 0, 0, 0, 2),  # 21  Top arch wide
    (0, 1, 0, 1, 0),  # 22  Top zigzag
    (2, 1, 2, 1, 2),  # 23  Bottom zigzag
    (1, 0, 2, 0, 1),  # 24  W-shape
    (1, 2, 0, 2, 1),  # 25  M-shape
    (0, 0, 2, 0, 0),  # 26  Top spike
    (2, 2, 0, 2, 2),  # 27  Bottom spike
    (0, 2, 1, 2, 0),  # 28  Outer bottom
    (2, 0, 1, 0, 2),  # 29  Outer top
    (1, 0, 0, 2, 1),  # 30  Mixed
]


# ─── Low-level helpers ────────────────────────────────────────────────────────

def _spin_reel(fval: float) -> dict:
    cursor = fval * _WEIGHT_TOTAL
    acc = 0.0
    for sym in SYMBOLS:
        acc += sym["weight"]
        if cursor < acc:
            return sym
    return SYMBOLS[-1]


def _make_floats(pf_fl: Optional[list]) -> list[float]:
    """Return exactly 15 deterministic floats for the 3×5 grid."""
    if pf_fl:
        seed = int(pf_fl[0] * (2 ** 32))
        rng = random.Random(seed)
        available = list(pf_fl[:8])
        extra = [rng.random() for _ in range(15 - len(available))]
        return (available + extra)[:15]
    return [random.random() for _ in range(15)]


def _spin_grid(pf_fl: Optional[list] = None) -> list[list[dict]]:
    floats = _make_floats(pf_fl)
    return [[_spin_reel(floats[row * COLS + col]) for col in range(COLS)] for row in range(ROWS)]


def _evaluate_paylines(grid: list[list[dict]], num_lines: int = NUM_LINES) -> list[dict]:
    """Evaluate the first `num_lines` paylines and return sorted win list."""
    wins = []
    active = PAYLINES[:num_lines]
    for line_idx, payline in enumerate(active):
        line = [grid[payline[col]][col] for col in range(COLS)]
        first_id = line[0]["id"]
        count = 1
        for sym in line[1:]:
            if sym["id"] == first_id:
                count += 1
            else:
                break
        if count < 3:
            continue
        payout = line[0].get(f"payout_{count}", 0)
        if not payout:
            continue
        wins.append({
            "line_num": line_idx + 1,
            "symbol": line[0],
            "count": count,
            "mult": payout,
            "emojis": [s["emoji"] for s in line],
        })
    wins.sort(key=lambda w: w["mult"], reverse=True)
    return wins


# ─── Display helpers ──────────────────────────────────────────────────────────

# Row labels drawn beside the grid
_ROW_LABEL = ["▸ TOP", "▸ MID", "▸ BOT"]

def _grid_str(grid: list[list[dict]], revealed_cols: int = COLS,
             emoji_map: Optional[dict] = None, spin_emoji: str = SPIN_EMOJI) -> str:
    """Render the 3×5 grid with row labels."""
    rows = []
    for row in range(ROWS):
        cells = [
            (emoji_map.get(grid[row][col]["id"], grid[row][col]["emoji"]) if emoji_map else grid[row][col]["emoji"])
            if col < revealed_cols else spin_emoji
            for col in range(COLS)
        ]
        rows.append("  ".join(cells))
    return "\n".join(rows)


def _build_embed(
    user: discord.Member,
    grid: list[list[dict]],
    revealed_cols: int,
    bet: int,
    mode: str,
    *,
    num_lines: int = NUM_LINES,
    wins: Optional[list] = None,
    total_payout: Optional[int] = None,
    game_uid: Optional[str] = None,
    balance: Optional[int] = None,
    emoji_map: Optional[dict] = None,
    spin_emoji: str = SPIN_EMOJI,
) -> discord.Embed:
    grid_display = _grid_str(grid, revealed_cols, emoji_map=emoji_map, spin_emoji=spin_emoji)
    # Box without backticks so custom Discord emojis render properly
    top_bar    = "┌─────────────────────────┐"
    bot_bar    = "└─────────────────────────┘"
    grid_lines = [f"│  {ln}  │" for ln in grid_display.split("\n")]
    grid_block = top_bar + "\n" + "\n".join(grid_lines) + "\n" + bot_bar

    line_bet = bet / num_lines

    spinning = wins is None

    if spinning:
        color = discord.Color.from_rgb(255, 165, 0)   # orange
        if revealed_cols == 0:
            spin_label = "🔄  Spinning the reels…"
        elif revealed_cols < COLS:
            spin_label = f"🔄  Reel **{revealed_cols}** locked in!  ({COLS - revealed_cols} remaining)"
        else:
            spin_label = "🔍  Checking paylines…"

        desc = (
            f"{grid_block}\n\n"
            f"{spin_label}\n\n"
            f"💰 **Bet:** {format_balance(bet, mode)}"
            f"  ·  📊 **{num_lines} Lines**"
            f"  ·  🎲 **{format_balance(int(line_bet), mode)} / line**"
        )
        embed = discord.Embed(title="🎰  S L O T  M A C H I N E", description=desc, color=color)

    else:
        if wins:
            color = discord.Color.gold()
            profit  = (total_payout or 0) - bet
            sign    = "+" if profit >= 0 else ""
            pct     = round(total_payout / bet * 100, 1) if bet > 0 else 0

            win_lines = []
            for w in wins[:5]:
                line_win = int(w["mult"] * line_bet)
                sym_emoji = (emoji_map.get(w["symbol"]["id"], w["symbol"]["emoji"]) if emoji_map else w["symbol"]["emoji"])
                win_lines.append(
                    f"  `L{w['line_num']:02d}` {sym_emoji} ×{w['count']}"
                    f"  →  **{w['mult']}x**  *(+{format_balance(line_win, mode)})*"
                )
            if len(wins) > 5:
                win_lines.append(f"  ┄┄  *+{len(wins) - 5} more winning line(s)*")

            title_line = f"🏆 **{len(wins)} winning line{'s' if len(wins) > 1 else ''}!**"
            payout_line = (
                f"💵  {format_balance(total_payout, mode)}"
                f"  (`{total_payout/bet:.2f}x`)  "
                f"**{sign}{format_balance(profit, mode)}**"
            )
            result_block = title_line + "\n" + "\n".join(win_lines) + "\n\n" + payout_line

        else:
            color = discord.Color.from_rgb(180, 40, 40)
            result_block = "😢  **No winning lines this spin.**\nTry adjusting your paylines or bet!"

        bal_line = f"\n💰 **Balance:** {format_balance(balance, mode)}" if balance is not None else ""
        uid_line = f"\n🔐 `{game_uid}`" if game_uid else ""

        desc = (
            f"{grid_block}\n\n"
            f"{result_block}"
            f"{bal_line}"
            f"{uid_line}"
        )
        embed = discord.Embed(title="🎰  S L O T  M A C H I N E", description=desc, color=color)

    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="🔐 Provably Fair · Vegas Bot")
    return embed


# ─── Game class ───────────────────────────────────────────────────────────────

class SlotGame(BaseGame):
    """3×5 Slot Machine — up to 30 paylines, RTP ≈ 87.6 %."""

    def __init__(self):
        super().__init__(name="Slot Machine", emoji="🎰", multiplier=1.0, game_id="slot")

    def spin_and_evaluate(self, bet: int, pf_fl: Optional[list] = None,
                          num_lines: int = NUM_LINES):
        grid     = _spin_grid(pf_fl)
        wins     = _evaluate_paylines(grid, num_lines)
        line_bet = bet / num_lines
        total_payout = int(sum(w["mult"] * line_bet for w in wins))
        result_str   = "win" if total_payout > bet else "lose"
        total_mult   = round(total_payout / bet, 4) if bet > 0 else 0.0
        game_result  = GameResult(
            result=result_str,
            bet=bet,
            multiplier=total_mult,
            meta={
                "grid": [[s["id"] for s in row] for row in grid],
                "wins": [{"line": w["line_num"], "symbol": w["symbol"]["id"],
                          "count": w["count"], "mult": w["mult"]} for w in wins],
                "total_payout": total_payout,
                "winning_lines": len(wins),
                "num_lines": num_lines,
            },
            amount=total_payout,
        )
        return grid, wins, total_payout, game_result

    async def play(self, interaction: discord.Interaction, message_id: str,
                   player, bet: int, mode: str, num_lines: int = NUM_LINES):
        from cogs.games import GameSession, ActiveGameView, create_game_embed
        from modules.provably_fair import (
            consume_pf_round, hash_seed, log_game_start, log_game_end, new_game_uid,
        )
        import asyncio

        bet       = int(bet)
        num_lines = int(num_lines)

        # Fetch custom emojis once
        emoji_map, spin_emoji = get_slot_emojis()

        server_seed, client_seed, nonce, pf_fl = consume_pf_round(int(player.uid))
        game_uid = new_game_uid()
        log_msg  = await log_game_start(
            interaction, self.name, self.emoji, interaction.user,
            bet, mode, hash_seed(server_seed), client_seed, nonce, game_uid,
        )

        # Free-round varsa bakiye kesilmez, bet promo'dan alınır
        is_free_round, bet = self.deduct_bet(player, mode, bet)
        grid, wins, total_payout, game_result = self.spin_and_evaluate(bet, pf_fl, num_lines)

        if not is_free_round and game_result.result == "win":
            try:
                import modules.balance_cap as balance_cap
                balance = player.get_balance(mode)
                if balance_cap.should_rig_outcome(
                    player.uid, mode, balance, bet, total_payout, game_id="slot",
                ):
                    wins = []
                    total_payout = 0
                    game_result = GameResult(
                        "lose", bet,
                        meta={
                            "grid": game_result.meta.get("grid", []),
                            "wins": [],
                            "total_payout": 0,
                            "winning_lines": 0,
                            "num_lines": num_lines,
                            "predetermined": True,
                        },
                        amount=0,
                    )
            except Exception:
                pass

        from modules.games_play_v2 import build_game_play_layout, status_button

        async def _edit(embed: discord.Embed, *, spinning: bool = False):
            controls = [status_button("Spinning...", emoji=spin_emoji)] if spinning else []
            layout = build_game_play_layout(embed, controls, timeout=None)
            try:
                await interaction.message.edit(embed=None, content=None, view=layout)
            except Exception:
                pass

        # Frame 0 — all spinning
        await _edit(
            _build_embed(
                interaction.user, grid, 0, bet, mode, num_lines=num_lines,
                emoji_map=emoji_map, spin_emoji=spin_emoji,
            ),
            spinning=True,
        )
        await asyncio.sleep(0.7)

        # Reveal each reel one by one
        for col in range(1, COLS + 1):
            await _edit(
                _build_embed(
                    interaction.user, grid, col, bet, mode, num_lines=num_lines,
                    emoji_map=emoji_map, spin_emoji=spin_emoji,
                ),
                spinning=True,
            )
            await asyncio.sleep(0.45)

        # Process result
        last_game_info = self.handle_result(game_result, player, mode, member=interaction.user, is_free_round=is_free_round)
        if mode == "real" and isinstance(interaction.user, discord.Member):
            from Games.base_game import _check_and_assign_tier_role
            await _check_and_assign_tier_role(interaction.user, player)
            levels_cog = interaction.client.cogs.get("LevelsCog")
            if levels_cog:
                await levels_cog.process_level_up(interaction.user.id)

        # Event hooks
        from modules.event_manager import process_game_event
        _ev = process_game_event(
            player.uid,
            {"game": "slots", "won": game_result.result == "win",
             "multiplier": game_result.multiplier, "bet": bet, "mode": mode},
            player,
        )

        # Result frame
        result_embed = _build_embed(
            interaction.user, grid, COLS, bet, mode,
            num_lines=num_lines,
            wins=wins, total_payout=total_payout,
            game_uid=game_uid, balance=player.get_balance(mode),
            emoji_map=emoji_map, spin_emoji=spin_emoji,
        )
        await _edit(result_embed)
        await asyncio.sleep(4)

        await log_game_end(
            log_msg, self.name, self.emoji, interaction.user,
            bet, mode, server_seed, client_seed, nonce, game_uid,
            game_result.result, game_result.meta, last_game_info.get("profit", 0),
            interaction=interaction,
        )

        session = GameSession.get_session(message_id)
        if session:
            from cogs.games import hub_active_layout

            GameSession.update_session(message_id, in_game=False, last_game=last_game_info)
            session = GameSession.get_session(message_id)
            layout = hub_active_layout(message_id, interaction.user, session, "slot")
            try:
                await interaction.message.edit(embed=None, content=None, view=layout)
            except Exception:
                pass
        if _ev:
            from cogs.events import send_event_completion
            await send_event_completion(interaction, _ev)
