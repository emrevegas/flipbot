"""Horse race — 6 lanes, per-round odds (one favorite ~1.10x, one longshot up to 20x)."""

from __future__ import annotations

import random

NUM_HORSES = 6
MAX_ODDS = 20.0
MIN_ODDS = 1.10
FAVORITE_MAX = 1.35
LONGSHOT_MIN = 10.0
MID_MIN = 2.0
MID_MAX = 8.5


def roll_race_odds(
    *,
    favorite_min: float = MIN_ODDS,
    favorite_max: float = FAVORITE_MAX,
    longshot_min: float = LONGSHOT_MIN,
    longshot_max: float = MAX_ODDS,
    mid_min: float = MID_MIN,
    mid_max: float = MID_MAX,
) -> tuple[float, ...]:
    """
    Each race: exactly one low favorite (~1.10x), one high longshot (10x–20x),
    four medium lanes between them — never all horses at 10x+.
    """
    fav_lo = max(1.05, float(favorite_min))
    fav_hi = max(fav_lo, float(favorite_max))
    ls_lo = max(fav_hi + 0.5, float(longshot_min))
    ls_hi = max(ls_lo, float(longshot_max))
    m_lo = max(fav_hi + 0.15, float(mid_min))
    m_hi = min(ls_lo - 0.25, max(m_lo, float(mid_max)))

    indices = list(range(NUM_HORSES))
    random.shuffle(indices)
    fav_idx = indices[0]
    long_idx = indices[1]
    mid_indices = indices[2:]

    odds: list[float] = [0.0] * NUM_HORSES
    odds[fav_idx] = round(random.uniform(fav_lo, fav_hi), 2)
    odds[long_idx] = round(random.uniform(ls_lo, ls_hi), 2)

    # Spread mids so they don't cluster at the top of the range
    mid_vals = sorted(random.uniform(m_lo, m_hi) for _ in mid_indices)
    for lane, val in zip(mid_indices, mid_vals):
        odds[lane] = round(val, 2)

    return tuple(odds)


def normalize_odds(raw: list | tuple | None) -> tuple[float, ...]:
    """Legacy / admin defaults — gameplay uses roll_race_odds()."""
    if isinstance(raw, (list, tuple)) and len(raw) >= NUM_HORSES:
        out = []
        for i in range(NUM_HORSES):
            try:
                out.append(min(MAX_ODDS, max(1.05, float(raw[i]))))
            except (TypeError, ValueError):
                out.append(3.0 + i * 0.5)
        return tuple(out)
    return roll_race_odds()


def win_chances(odds: tuple[float, ...]) -> tuple[float, ...]:
    weights = [1.0 / max(o, 1.01) for o in odds]
    total = sum(weights) or 1.0
    return tuple(100.0 * w / total for w in weights)


def bet_tiers(min_bet: float, max_bet: float, steps: int = 25) -> list[int]:
    lo = max(1, int(min_bet))
    hi = max(lo, int(max_bet))
    if steps <= 1 or hi <= lo:
        return [lo]
    tiers: list[int] = []
    for i in range(steps):
        t = i / (steps - 1)
        tiers.append(int(round(lo + (hi - lo) * t)))
    seen: set[int] = set()
    out: list[int] = []
    for v in tiers:
        v = max(lo, min(hi, v))
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out or [lo]


def pick_winner_index(
    odds: tuple[float, ...],
    *,
    rig_lose: bool = False,
    player_picks: list[int] | None = None,
) -> int:
    """Win chance ∝ 1/odds. Rig: unpicked horse wins; if all picked, lowest odds wins."""
    picks = set(int(p) for p in (player_picks or []) if 0 <= int(p) < NUM_HORSES)
    weights = [1.0 / max(o, 1.01) for o in odds]

    if rig_lose and picks:
        if len(picks) >= NUM_HORSES:
            return min(range(NUM_HORSES), key=lambda i: odds[i])
        losers = [i for i in range(NUM_HORSES) if i not in picks]
        if losers:
            lw = [weights[i] for i in losers]
            return random.choices(losers, weights=lw, k=1)[0]

    return random.choices(range(NUM_HORSES), weights=weights, k=1)[0]


def gross_payout(bet: float, winner: int, odds: tuple[float, ...]) -> float:
    if winner < 0 or winner >= NUM_HORSES:
        return 0.0
    return bet * odds[winner]
