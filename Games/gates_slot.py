"""
Gates of Olympus style — 6×5 scatter pays (8+ anywhere), provably fair.

Separate from Games/slot.py (classic 3×5 paylines). Do not modify the legacy slot.
"""

from __future__ import annotations

import random
from typing import Optional

ROWS = 5
COLS = 6
GRID_SIZE = ROWS * COLS

SYMBOLS: list[dict] = [
    {"id": "ruby",     "emoji": "🔴", "weight": 22, "label": "Ruby"},
    {"id": "emerald",  "emoji": "🟢", "weight": 20, "label": "Emerald"},
    {"id": "sapphire", "emoji": "🔵", "weight": 18, "label": "Sapphire"},
    {"id": "amethyst", "emoji": "🟣", "weight": 16, "label": "Amethyst"},
    {"id": "topaz",    "emoji": "🟡", "weight": 14, "label": "Topaz"},
    {"id": "goblet",   "emoji": "🏆", "weight":  8, "label": "Goblet"},
    {"id": "ring",     "emoji": "💍", "weight":  6, "label": "Ring"},
    {"id": "crown",    "emoji": "👑", "weight":  4, "label": "Crown"},
    {"id": "scatter",  "emoji": "⚡", "weight":  2, "label": "Zeus"},
]

_WEIGHT_TOTAL = sum(s["weight"] for s in SYMBOLS)
_ID_TO_SYM = {s["id"]: s for s in SYMBOLS}

# Total-bet multiplier by matching symbol count (anywhere on grid)
PAYOUT_BY_COUNT: dict[int, float] = {
    8: 0.40,
    9: 0.55,
    10: 0.75,
    11: 1.00,
    12: 1.50,
    13: 2.00,
    14: 2.50,
    15: 4.00,
    16: 5.00,
    17: 6.50,
    18: 8.00,
    19: 10.0,
    20: 14.0,
    21: 18.0,
    22: 22.0,
    23: 28.0,
    24: 35.0,
    25: 45.0,
    26: 55.0,
    27: 70.0,
    28: 85.0,
    29: 100.0,
    30: 125.0,
}


def _mult_for_count(count: int) -> float:
    if count < 8:
        return 0.0
    return PAYOUT_BY_COUNT.get(count, PAYOUT_BY_COUNT[30])


def _pick_symbol(fval: float) -> dict:
    cursor = fval * _WEIGHT_TOTAL
    acc = 0.0
    for sym in SYMBOLS:
        acc += sym["weight"]
        if cursor < acc:
            return sym
    return SYMBOLS[-1]


def _make_floats(pf_fl: Optional[list]) -> list[float]:
    if pf_fl:
        seed = int(pf_fl[0] * (2 ** 32))
        rng = random.Random(seed)
        available = list(pf_fl[:8])
        extra = [rng.random() for _ in range(GRID_SIZE - len(available))]
        return (available + extra)[:GRID_SIZE]
    return [random.random() for _ in range(GRID_SIZE)]


def spin_grid(pf_fl: Optional[list] = None) -> list[list[dict]]:
    floats = _make_floats(pf_fl)
    grid: list[list[dict]] = []
    idx = 0
    for _row in range(ROWS):
        row: list[dict] = []
        for _col in range(COLS):
            row.append(_pick_symbol(floats[idx]))
            idx += 1
        grid.append(row)
    return grid


def evaluate_grid(grid: list[list[dict]]) -> tuple[list[dict], int, float]:
    """
    Returns (wins, total_payout_int, orb_multiplier).
    Each win: {symbol_id, count, mult, positions: [(r,c),...]}
    """
    counts: dict[str, int] = {}
    positions: dict[str, list[tuple[int, int]]] = {}
    scatter_count = 0

    for r in range(ROWS):
        for c in range(COLS):
            sid = grid[r][c]["id"]
            if sid == "scatter":
                scatter_count += 1
                continue
            counts[sid] = counts.get(sid, 0) + 1
            positions.setdefault(sid, []).append((r, c))

    wins: list[dict] = []
    total_mult = 0.0
    for sid, cnt in counts.items():
        mult = _mult_for_count(cnt)
        if mult <= 0:
            continue
        sym = _ID_TO_SYM[sid]
        wins.append({
            "symbol_id": sid,
            "symbol": sym,
            "count": cnt,
            "mult": mult,
            "positions": positions[sid],
        })
        total_mult += mult

    # Scatter bonus (pays on 4+ Zeus anywhere)
    if scatter_count >= 4:
        sc_mult = {4: 3.0, 5: 5.0, 6: 10.0}.get(scatter_count, 15.0 + scatter_count)
        wins.append({
            "symbol_id": "scatter",
            "symbol": _ID_TO_SYM["scatter"],
            "count": scatter_count,
            "mult": sc_mult,
            "positions": [
                (r, c)
                for r in range(ROWS)
                for c in range(COLS)
                if grid[r][c]["id"] == "scatter"
            ],
        })
        total_mult += sc_mult

    wins.sort(key=lambda w: w["mult"], reverse=True)

    orb_mult = 1.0
    if total_mult > 0:
        orb_mult = random.choice([1.0, 1.0, 2.0, 2.0, 3.0, 5.0])

    return wins, 0, orb_mult


def spin_round(
    bet: int,
    pf_fl: Optional[list] = None,
) -> tuple[list[list[dict]], list[dict], int, float]:
    grid = spin_grid(pf_fl)
    wins, _, orb_mult = evaluate_grid(grid)
    gross_mult = sum(w["mult"] for w in wins) * orb_mult
    total_payout = int(bet * gross_mult) if gross_mult > 0 else 0
    return grid, wins, total_payout, orb_mult
