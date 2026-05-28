"""Slide — horizontal multiplier strip; pointer picks the payout cell."""

from __future__ import annotations

import random
from typing import Sequence

# (multiplier, weight) — Recommendation 1 (~97.25% RTP before house_edge)
SLIDE_SEGMENTS: Sequence[tuple[float, float]] = (
    (0.0, 0.38),
    (0.5, 0.15),
    (1.0, 0.21),
    (1.5, 0.11),
    (2.0, 0.08),
    (3.0, 0.04),
    (5.0, 0.02),
    (10.0, 0.008),
    (25.0, 0.0015),
    (50.0, 0.0005),
)

_CUMULATIVE: list[tuple[float, float]] = []
_acc = 0.0
for mult, w in SLIDE_SEGMENTS:
    _acc += w
    _CUMULATIVE.append((mult, _acc))


def multiplier_from_float(f: float) -> float:
    """Map uniform [0,1) to segment multiplier."""
    f = max(0.0, min(0.999999, float(f)))
    for mult, edge in _CUMULATIVE:
        if f < edge:
            return mult
    return SLIDE_SEGMENTS[-1][0]


def roll_multiplier() -> float:
    return multiplier_from_float(random.random())


def pick_rigged_multiplier() -> float:
    """Low outcome when house rig is active."""
    r = random.random()
    if r < 0.78:
        return 0.0
    if r < 0.96:
        return 0.5
    return 1.0


def pick_favored_multiplier() -> float:
    """High outcome when rigged_chance < 0 (guaranteed player win)."""
    r = random.random()
    if r < 0.55:
        return 3.0
    if r < 0.85:
        return 5.0
    if r < 0.97:
        return 10.0
    return 25.0


def random_strip_cell() -> float:
    return roll_multiplier()


def gross_payout(bet: float, multiplier: float) -> float:
    return max(0.0, bet * multiplier)
