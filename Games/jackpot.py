"""Jackpot — weighted winner by bet share; 2% house edge on total pool."""

from __future__ import annotations

import random
from typing import Sequence


def player_chance(bet: float, pool: float) -> float:
    if pool <= 0 or bet <= 0:
        return 0.0
    return bet / pool


def format_chance(pct: float) -> str:
    if pct >= 99.95:
        return "99.9%"
    if pct < 0.05:
        return "<0.1%"
    return f"{pct:.1f}%"


def pick_winner_index(players: Sequence[dict]) -> int:
    """players: list of dicts with 'bet' (float). Returns index into players."""
    weights = [max(0.0, float(p.get("bet") or 0)) for p in players]
    total = sum(weights)
    if total <= 0:
        return 0
    r = random.random() * total
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if r <= acc:
            return i
    return len(players) - 1


def winner_payout(pool: float, house_edge: float = 0.02) -> float:
    """Total pot minus house edge (e.g. pool 100 → 98)."""
    pool = max(0.0, float(pool))
    he = max(0.0, min(0.5, float(house_edge)))
    return pool * (1.0 - he)
