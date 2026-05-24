"""Live exchange rate from /panel → server/exchange_rates."""

from __future__ import annotations

import config
from modules.database import get_data


def get_coin_usd_rate() -> float:
    """USD value of 1 coin (panel base rate)."""
    rates = get_data("server/exchange_rates") or {}
    try:
        rate = float(rates.get("coin_usd_rate", 0) or 0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate > 0:
        return rate
    if config.POINTS_PER_USD > 0:
        return 1.0 / float(config.POINTS_PER_USD)
    return 0.01


def get_coins_per_usd() -> float:
    rate = get_coin_usd_rate()
    return 1.0 / rate if rate > 0 else float(config.POINTS_PER_USD or 100)


def coins_to_usd(coins: float) -> float:
    return float(coins) * get_coin_usd_rate()


def usd_to_coins(usd: float) -> float:
    rate = get_coin_usd_rate()
    return float(usd) / rate if rate > 0 else float(usd) * float(config.POINTS_PER_USD or 100)
