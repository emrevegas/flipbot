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

ORB_VALUES = [2, 2, 3, 3, 4, 5, 5, 8, 10, 12, 15, 20, 25, 50, 100, 250, 500]

_WEIGHT_TOTAL = sum(s["weight"] for s in SYMBOLS)
_ID_TO_SYM = {s["id"]: s for s in SYMBOLS}
_DEFAULT_EMOJI_MAP: dict[str, str] = {s["id"]: s["emoji"] for s in SYMBOLS}

PAYOUT_BY_COUNT: dict[int, float] = {
    8: 0.40, 9: 0.55, 10: 0.75, 11: 1.00, 12: 1.50, 13: 2.00, 14: 2.50,
    15: 4.00, 16: 5.00, 17: 6.50, 18: 8.00, 19: 10.0, 20: 14.0, 21: 18.0,
    22: 22.0, 23: 28.0, 24: 35.0, 25: 45.0, 26: 55.0, 27: 70.0, 28: 85.0,
    29: 100.0, 30: 125.0,
}


def get_gates_emojis() -> dict[str, str]:
    """Symbol id → emoji token (custom Discord or unicode)."""
    try:
        from modules.database import get_data

        games_data = get_data("server/games") or {}
        gates_data = games_data.get("gates", {}) if isinstance(games_data, dict) else {}
        if not isinstance(gates_data, dict):
            gates_data = {}
        emojis = gates_data.get("emojis", {})
        if not isinstance(emojis, dict):
            emojis = {}
        out = {}
        for s in SYMBOLS:
            custom = emojis.get(s["id"])
            out[s["id"]] = str(custom) if custom else s["emoji"]
        return out
    except Exception:
        return dict(_DEFAULT_EMOJI_MAP)


def apply_emoji_map(grid: list[list[dict]], emoji_map: dict[str, str]) -> list[list[dict]]:
    """Return grid copy with display emoji applied."""
    out: list[list[dict]] = []
    for row in grid:
        nr = []
        for sym in row:
            d = dict(sym)
            d["emoji"] = emoji_map.get(sym["id"], sym.get("emoji", "?"))
            nr.append(d)
        out.append(nr)
    return out


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


def _rng_from_pf(pf_fl: Optional[list]) -> random.Random:
    if pf_fl:
        return random.Random(int(pf_fl[0] * (2 ** 32)))
    return random.Random()


def roll_orb_mults(rng: Optional[random.Random] = None) -> list[int]:
    """Gates-style multiplier orbs — values sum on win."""
    r = rng or random.Random()
    count = r.randint(1, 4)
    return [r.choice(ORB_VALUES) for _ in range(count)]


def orb_total(orb_mults: list[int]) -> int:
    return sum(orb_mults) if orb_mults else 1


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


def build_win_grid(
    match_count: int = 10,
    symbol_id: str = "ruby",
    pf_fl: Optional[list] = None,
) -> list[list[dict]]:
    """Force a scatter win grid (cap-safe high wins)."""
    sym = _ID_TO_SYM.get(symbol_id, SYMBOLS[0])
    floats = _make_floats(pf_fl)
    rng = _rng_from_pf(pf_fl)
    match_count = max(8, min(GRID_SIZE, int(match_count)))
    all_pos = [(r, c) for r in range(ROWS) for c in range(COLS)]
    win_pos = set(rng.sample(all_pos, match_count))
    filler_ids = [s["id"] for s in SYMBOLS if s["id"] not in (symbol_id, "scatter")]
    idx = 0
    grid: list[list[dict]] = []
    for r in range(ROWS):
        row: list[dict] = []
        for c in range(COLS):
            if (r, c) in win_pos:
                row.append(dict(sym))
            else:
                fid = filler_ids[idx % len(filler_ids)]
                row.append(dict(_ID_TO_SYM[fid]))
                idx += 1
        grid.append(row)
    return grid


def evaluate_grid(
    grid: list[list[dict]],
    *,
    pf_fl: Optional[list] = None,
) -> tuple[list[dict], list[int]]:
    """Returns (wins, orb_mults)."""
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
    symbol_mult = 0.0
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
        symbol_mult += mult

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
        symbol_mult += sc_mult

    wins.sort(key=lambda w: w["mult"], reverse=True)

    orb_mults: list[int] = []
    if symbol_mult > 0:
        orb_mults = roll_orb_mults(_rng_from_pf(pf_fl))

    return wins, orb_mults


def gross_payout(bet: int, wins: list[dict], orb_mults: list[int]) -> int:
    symbol_mult = sum(w["mult"] for w in wins)
    if symbol_mult <= 0:
        return 0
    total = symbol_mult * orb_total(orb_mults)
    return int(bet * total)


def spin_round(
    bet: int,
    pf_fl: Optional[list] = None,
) -> tuple[list[list[dict]], list[dict], int, list[int]]:
    grid = spin_grid(pf_fl)
    wins, orb_mults = evaluate_grid(grid, pf_fl=pf_fl)
    total_payout = gross_payout(int(bet), wins, orb_mults)
    return grid, wins, total_payout, orb_mults
