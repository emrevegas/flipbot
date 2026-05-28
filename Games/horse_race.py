"""Horse race — 6 lanes, per-round odds (max 20x), weighted winner."""

from __future__ import annotations

import random

NUM_HORSES = 6
MAX_ODDS = 20.0
MIN_ODDS = 1.5


def roll_race_odds(
    *,
    min_mult: float = MIN_ODDS,
    max_mult: float = MAX_ODDS,
) -> tuple[float, ...]:
    """Fresh multipliers each race (1.5x–20x)."""
    lo = max(1.1, float(min_mult))
    hi = max(lo, float(max_mult))
    return tuple(round(random.uniform(lo, hi), 2) for _ in range(NUM_HORSES))


def normalize_odds(raw: list | tuple | None) -> tuple[float, ...]:
    """Legacy / admin defaults — gameplay uses roll_race_odds()."""
    if isinstance(raw, (list, tuple)) and len(raw) >= NUM_HORSES:
        out = []
        for i in range(NUM_HORSES):
            try:
                out.append(min(MAX_ODDS, max(1.1, float(raw[i]))))
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
