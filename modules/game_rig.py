"""
House rigging — outcomes decided before UI; player never sees 'rigged' messaging.

Configured via server/games rigged_chance and /promodos bulk rolls.
Crystals: never rigged. PvP: never rigged.
"""
from __future__ import annotations

import random
from typing import Any

from Games.hilo import RANK_VALUE, card_value
from Games.mines import MinesGame
from modules.database import get_data

PROMODOS_KEY = "server/promodos"
GAMES_KEY = "server/games"

# game_id -> (label, emoji) for /promodos display
RIGGED_PROMO_GAMES: tuple[tuple[str, str, str], ...] = (
    ("mines", "Mines", "💣"),
    ("towers", "Towers", "🗼"),
    ("blackjack", "Blackjack", "🃏"),
    ("hilo", "HiLo", "🎴"),
    ("roulette", "Roulette", "🎰"),
    ("dice", "Dice", "🎲"),
    ("coinflip", "Coinflip", "🪙"),
    ("htw", "Hot Towel War", "🎡"),
    ("limbo", "Limbo", "🚀"),
    ("slide", "Slide", "🎢"),
    ("horse_race", "Horse Race", "🏇"),
    ("market_predict", "Market Predict", "📈"),
    ("slot", "Slots", "🎰"),
    ("case_opening", "Case Opening", "📦"),
)

NO_RIG_GAMES = frozenset({"crystals", "crystal", "case_battle", "live_blackjack", "jackpot"})

PROMODOS_LOW_RANGE = (3.0, 8.0)
PROMODOS_HIGH_RANGE = (22.0, 35.0)
PROMODOS_ACTIVE_DEFAULT = (20.0, 25.0)
PROMODOS_INACTIVE_DEFAULT = (5.0, 10.0)
PROMODOS_ACTIVE_PER_GAME = {
    "mines": (5.0, 9.0),
    "towers": (8.0, 15.0),
}


def get_rigged_chance(game_id: str) -> float:
    from modules.balance_cap import get_game_rigged_chance
    return get_game_rigged_chance(game_id)


def roll_rigged(game_id: str) -> bool:
    if game_id in NO_RIG_GAMES:
        return False
    chance = get_rigged_chance(game_id)
    return chance > 0 and random.uniform(0, 100) < chance


async def should_rig_outcome(
    user_id: int | str,
    game_id: str,
    bet: float,
    *,
    pvp: bool = False,
) -> bool:
    """True => force a natural-looking loss before showing the result."""
    if pvp or game_id in NO_RIG_GAMES:
        return False
    return roll_rigged(game_id)


def roll_promodos_percentages(mode: str = "inactive") -> dict[str, float]:
    """
    mode: inactive | active | low | mid | high
    Writes rigged_chance into server/games for all RIGGED_PROMO_GAMES keys.
    """
    from modules.database import set_data

    games_data = get_data(GAMES_KEY) or {}
    if not isinstance(games_data, dict):
        games_data = {}

    rolled: dict[str, float] = {}
    now = int(__import__("time").time())

    for key, _label, _emoji in RIGGED_PROMO_GAMES:
        entry = games_data.get(key)
        if not isinstance(entry, dict):
            entry = {}
            games_data[key] = entry
        if mode == "low":
            low, high = PROMODOS_LOW_RANGE
        elif mode == "mid":
            low = (PROMODOS_LOW_RANGE[0] + PROMODOS_HIGH_RANGE[0]) / 2.0
            high = (PROMODOS_LOW_RANGE[1] + PROMODOS_HIGH_RANGE[1]) / 2.0
        elif mode == "high":
            low, high = PROMODOS_HIGH_RANGE
        elif mode == "active":
            low, high = PROMODOS_ACTIVE_PER_GAME.get(key, PROMODOS_ACTIVE_DEFAULT)
        else:
            low, high = PROMODOS_INACTIVE_DEFAULT
        pct = round(random.uniform(low, high), 2)
        entry["rigged_chance"] = pct
        entry["last_modified"] = now
        rolled[key] = pct

    set_data(GAMES_KEY, games_data)
    return rolled


def snapshot_rigged_percentages() -> dict[str, float]:
    out: dict[str, float] = {}
    for key, _label, _emoji in RIGGED_PROMO_GAMES:
        out[key] = round(get_rigged_chance(key), 2)
    return out


# ── HiLo ──────────────────────────────────────────────────────────────────────


def _hilo_would_win(current: str, nxt: str, choice: str) -> bool:
    cur_val = card_value(current)
    nxt_val = card_value(nxt)
    if nxt_val > cur_val:
        actual = "higher"
    elif nxt_val < cur_val:
        actual = "lower"
    else:
        actual = "same"
    if actual == "same" and cur_val in (14, 2):
        return (cur_val == 14 and choice == "higher") or (cur_val == 2 and choice == "lower")
    if actual == "same":
        return False
    return actual == choice


def rig_hilo_before_guess(state: dict, choice: str) -> None:
    """Swap next deck card so the visible reveal is a loss (choice looks wrong)."""
    deck = state.get("deck") or []
    idx = int(state.get("card_idx", 0))
    if idx + 1 >= len(deck):
        return
    current = deck[idx]
    losing_idxs = [
        i for i in range(idx + 1, len(deck))
        if not _hilo_would_win(current, deck[i], choice)
    ]
    if not losing_idxs:
        return
    swap_i = random.choice(losing_idxs)
    nxt = idx + 1
    deck[nxt], deck[swap_i] = deck[swap_i], deck[nxt]
    state["deck"] = deck


# ── Blackjack rig templates ─────────────────────────────────────────────────────

_BJ_SUITS = ["♠", "♥", "♦", "♣"]


def _bj_build_pool(exclude: list[str]) -> list[str]:
    ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
    pool = [f"{r}{s}" for s in _BJ_SUITS for r in ranks] * 2
    for card in exclude:
        if card in pool:
            pool.remove(card)
    random.shuffle(pool)
    return pool


BJ_RIG_TEMPLATES: list[dict[str, Any]] = [
    {
        "player": ["10♠", "3♥"],
        "dealer": ["8♦", "10♣"],
        "hit_card": "K♠",
        "dealer_extra": [],
    },
    {
        "player": ["9♣", "4♦"],
        "dealer": ["9♥", "9♠"],
        "hit_card": "Q♥",
        "dealer_extra": [],
    },
    {
        "player": ["7♠", "6♥"],
        "dealer": ["10♦", "8♣"],
        "hit_card": "9♦",
        "dealer_extra": [],
    },
    {
        "player": ["K♣", "5♠"],
        "dealer": ["7♥", "10♦"],
        "hit_card": "J♥",
        "dealer_extra": ["3♣"],
    },
]


def build_rigged_blackjack_state(bet: float, username: str) -> dict:
    """Pre-deal rigged layout: player ~13, dealer beats on stand; hit busts."""
    tpl = random.choice(BJ_RIG_TEMPLATES)
    player = list(tpl["player"])
    dealer = list(tpl["dealer"])
    reserved = player + dealer + [tpl["hit_card"]] + list(tpl.get("dealer_extra") or [])
    deck = _bj_build_pool(reserved)
    deck.insert(0, tpl["hit_card"])
    for c in reversed(tpl.get("dealer_extra") or []):
        deck.insert(0, c)
    state = {
        "bet": bet,
        "player": player,
        "dealer": dealer,
        "deck": deck,
        "doubled": False,
        "username": username,
        "rigged": True,
        "rig_hit_card": tpl["hit_card"],
    }
    return state


# ── Mines / Towers ────────────────────────────────────────────────────────────


def mines_multiplier(mine_count: int, gems_revealed: int, house_edge: float) -> float:
    """Vegas-style nCr multiplier."""
    he = max(0.0, min(0.99, float(house_edge)))
    if isinstance(house_edge, (int, float)) and house_edge > 1:
        he = float(house_edge) / 100.0
    return MinesGame.calc_multiplier(int(mine_count), int(gems_revealed), he)


def rig_mines_safe_to_bomb(state: dict, picked_idx: int) -> bool:
    """
    User clicked a safe cell; move a mine from elsewhere onto picked_idx.
    Returns True if rig applied (picked cell is now a mine).
    """
    mines = [int(x) for x in state.get("mines") or []]
    if picked_idx in mines:
        return True
    revealed = set(int(x) for x in state.get("revealed") or [])
    other_mines = [m for m in mines if m != picked_idx and m not in revealed]
    if not other_mines:
        mines.append(picked_idx)
        state["mines"] = mines
        return True
    donor = random.choice(other_mines)
    mines = [picked_idx if m == donor else m for m in mines]
    state["mines"] = mines
    return True


def rig_towers_gem_to_bomb(state: dict, floor: int, col: int) -> None:
    """Turn chosen gem on this floor into bomb; move bomb to another column on same floor."""
    grid = state.get("grid") or []
    if floor < 0 or floor >= len(grid):
        return
    row = list(grid[floor])
    if col < 0 or col >= len(row) or row[col] != "gem":
        return
    bomb_cols = [i for i, c in enumerate(row) if c == "bomb" and i != col]
    if not bomb_cols:
        row[col] = "bomb"
    else:
        bc = random.choice(bomb_cols)
        row[col], row[bc] = "bomb", "gem"
    grid[floor] = row
    state["grid"] = grid


# ── Cases ─────────────────────────────────────────────────────────────────────


def rig_case_winners(items: list[dict], count: int) -> list[dict]:
    """Low-value pulls only — looks like a normal open."""
    if not items or count < 1:
        return []
    ranked = sorted(items, key=lambda x: int(x.get("value", 0) or 0))
    pool = ranked[: max(1, (len(ranked) + 2) // 3)]
    return [dict(random.choice(pool)) for _ in range(count)]


# ── HTW / Dice bot rig ────────────────────────────────────────────────────────


def htw_spin_rigged() -> tuple[int, int, str]:
    """House spin strictly higher than player."""
    left_spin = random.randint(0, 35)
    right_spin = random.randint(left_spin + 1, 36)
    return left_spin, right_spin, "LOSE"


def dice_roll_rigged() -> tuple[int, int, str]:
    """House roll beats player."""
    left_roll = random.randint(1, 5)
    right_roll = random.randint(left_roll + 1, 6)
    return left_roll, right_roll, "LOSE"


def dice_roll_favored() -> tuple[int, int, str]:
    """Player roll beats house."""
    right_roll = random.randint(1, 5)
    left_roll = random.randint(right_roll + 1, 6)
    return left_roll, right_roll, "WIN"


def htw_spin_favored() -> tuple[int, int, str]:
    """Player spin strictly higher than house."""
    right_spin = random.randint(0, 35)
    left_spin = random.randint(right_spin + 1, 36)
    return left_spin, right_spin, "WIN"


def rig_hilo_favor_win(state: dict, choice: str) -> None:
    """Swap next deck card so the visible reveal is a win."""
    deck = state.get("deck") or []
    idx = int(state.get("card_idx", 0))
    if idx + 1 >= len(deck):
        return
    current = deck[idx]
    winning_idxs = [
        i for i in range(idx + 1, len(deck))
        if _hilo_would_win(current, deck[i], choice)
    ]
    if not winning_idxs:
        return
    swap_i = random.choice(winning_idxs)
    nxt = idx + 1
    deck[nxt], deck[swap_i] = deck[swap_i], deck[nxt]
    state["deck"] = deck


def rig_mines_bomb_to_safe(state: dict, picked_idx: int) -> bool:
    """User hit a mine; move that mine onto an unrevealed safe cell."""
    mines = [int(x) for x in state.get("mines") or []]
    if picked_idx not in mines:
        return False
    revealed = set(int(x) for x in state.get("revealed") or [])
    grid_cells = 25
    safe = [
        i for i in range(grid_cells)
        if i not in mines and i not in revealed and i != picked_idx
    ]
    if not safe:
        mines = [m for m in mines if m != picked_idx]
        state["mines"] = mines
        return True
    donor = random.choice(safe)
    mines = [donor if m == picked_idx else m for m in mines]
    state["mines"] = mines
    return True


def rig_towers_bomb_to_gem(state: dict, floor: int, col: int) -> None:
    """Turn chosen bomb into gem; move bomb to another column on same floor."""
    grid = state.get("grid") or []
    if floor < 0 or floor >= len(grid):
        return
    row = list(grid[floor])
    if col < 0 or col >= len(row) or row[col] != "bomb":
        return
    gem_cols = [i for i, c in enumerate(row) if c == "gem" and i != col]
    if not gem_cols:
        row[col] = "gem"
    else:
        gc = random.choice(gem_cols)
        row[col], row[gc] = "gem", "bomb"
    grid[floor] = row
    state["grid"] = grid


def rig_case_best_winners(items: list[dict], count: int) -> list[dict]:
    """High-value pulls only."""
    if not items or count < 1:
        return []
    ranked = sorted(items, key=lambda x: int(x.get("value", 0) or 0), reverse=True)
    pool = ranked[: max(1, (len(ranked) + 2) // 3)]
    return [dict(random.choice(pool)) for _ in range(count)]
