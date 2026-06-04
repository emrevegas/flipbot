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

# Per-cell multiplier spawn chance (~18% of spins get at least one orb on grid)
ORB_ANY_SPIN_CHANCE = 0.18
ORB_TWO_CHANCE = 0.22
ORB_THREE_CHANCE = 0.06

_WEIGHT_TOTAL = sum(s["weight"] for s in SYMBOLS)
_ID_TO_SYM = {s["id"]: s for s in SYMBOLS}
_DEFAULT_EMOJI_MAP: dict[str, str] = {s["id"]: s["emoji"] for s in SYMBOLS}
_ALL_POS = [(r, c) for r in range(ROWS) for c in range(COLS)]

PAYOUT_BY_COUNT: dict[int, float] = {
    8: 0.25, 9: 0.35, 10: 0.50, 11: 0.65, 12: 0.85, 13: 1.05, 14: 1.30,
    15: 1.75, 16: 2.20, 17: 2.75, 18: 3.40, 19: 4.20, 20: 5.50, 21: 7.00,
    22: 8.50, 23: 10.5, 24: 13.0, 25: 16.0, 26: 20.0, 27: 25.0, 28: 30.0,
    29: 38.0, 30: 48.0,
}


def get_gates_emojis() -> dict[str, str]:
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


def _rng_from_pf(pf_fl: Optional[list], salt: int = 0) -> random.Random:
    if pf_fl:
        base = int(pf_fl[0] * (2 ** 32))
        return random.Random(base ^ (salt * 0x9E3779B1))
    return random.Random()


def _pick_orb_value(rng: random.Random, *, cap_safe: bool = False) -> int:
    """2x most common; 100x+ extremely rare. cap_safe = force-win path."""
    if cap_safe:
        return 2 if rng.random() < 0.85 else 3
    roll = rng.random() * 10_000
    if roll < 5_400:
        return 2
    if roll < 7_200:
        return 3
    if roll < 8_200:
        return 4
    if roll < 8_950:
        return 5
    if roll < 9_400:
        return 8
    if roll < 9_700:
        return 10
    if roll < 9_850:
        return 15
    if roll < 9_930:
        return 20
    if roll < 9_970:
        return 25
    if roll < 9_988:
        return 50
    if roll < 9_996:
        return 100
    if roll < 9_999:
        return 250
    return 500


def roll_grid_orbs(
    rng: Optional[random.Random] = None,
    *,
    cap_safe: bool = False,
) -> dict[tuple[int, int], int]:
    """
    Multiplier orbs land on grid cells (Gates style).
    Most spins: 0 orbs. When present, usually one 2x.
    """
    r = rng or random.Random()
    if cap_safe:
        if r.random() > 0.35:
            return {}
        pos = r.choice(_ALL_POS)
        return {pos: _pick_orb_value(r, cap_safe=True)}

    if r.random() > ORB_ANY_SPIN_CHANCE:
        return {}

    rr = r.random()
    if rr < ORB_THREE_CHANCE:
        count = 3
    elif rr < ORB_THREE_CHANCE + ORB_TWO_CHANCE:
        count = 2
    else:
        count = 1

    positions = r.sample(_ALL_POS, count)
    return {pos: _pick_orb_value(r) for pos in positions}


def orb_mult_for_payout(grid_orbs: dict[tuple[int, int], int]) -> int:
    """Sum of on-grid multipliers applied to a win (Gates rules)."""
    if not grid_orbs:
        return 1
    return max(1, sum(grid_orbs.values()))


def grid_orbs_to_list(grid_orbs: dict[tuple[int, int], int]) -> list[tuple[int, int, int]]:
    return [(r, c, m) for (r, c), m in sorted(grid_orbs.items())]


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
    *,
    grid_orbs: dict[tuple[int, int], int] | None = None,
) -> tuple[list[list[dict]], dict[tuple[int, int], int]]:
    sym = _ID_TO_SYM.get(symbol_id, SYMBOLS[0])
    rng = _rng_from_pf(pf_fl, salt=1)
    match_count = max(8, min(GRID_SIZE, int(match_count)))
    win_pos = set(rng.sample(_ALL_POS, match_count))
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

    orbs = dict(grid_orbs) if grid_orbs is not None else roll_grid_orbs(
        _rng_from_pf(pf_fl, salt=2), cap_safe=True,
    )
    return grid, orbs


def evaluate_grid(
    grid: list[list[dict]],
    grid_orbs: dict[tuple[int, int], int] | None = None,
) -> list[dict]:
    """Returns win list. Orbs do not create wins — they multiply existing wins."""
    grid_orbs = grid_orbs or {}
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
    for sid, cnt in counts.items():
        mult = _mult_for_count(cnt)
        if mult <= 0:
            continue
        wins.append({
            "symbol_id": sid,
            "symbol": _ID_TO_SYM[sid],
            "count": cnt,
            "mult": mult,
            "positions": positions[sid],
        })

    if scatter_count >= 4:
        sc_mult = {4: 2.0, 5: 3.5, 6: 6.0}.get(scatter_count, 8.0 + scatter_count * 0.5)
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

    wins.sort(key=lambda w: w["mult"], reverse=True)
    return wins


def gross_payout(
    bet: int,
    wins: list[dict],
    grid_orbs: dict[tuple[int, int], int] | None = None,
) -> int:
    symbol_mult = sum(w["mult"] for w in wins)
    if symbol_mult <= 0:
        return 0
    base = int(bet * symbol_mult)
    orb_x = orb_mult_for_payout(grid_orbs or {})
    return int(base * orb_x) if orb_x > 1 else base


def spin_round(
    bet: int,
    pf_fl: Optional[list] = None,
) -> tuple[list[list[dict]], dict[tuple[int, int], int], list[dict], int]:
    grid = spin_grid(pf_fl)
    grid_orbs = roll_grid_orbs(_rng_from_pf(pf_fl, salt=2))
    wins = evaluate_grid(grid, grid_orbs)
    total_payout = gross_payout(int(bet), wins, grid_orbs)
    return grid, grid_orbs, wins, total_payout
