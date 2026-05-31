"""License plan pricing (balance shop)."""

from __future__ import annotations

import os

PLAN_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}

PLAN_LABELS = {
    "daily": "Daily (1 day)",
    "weekly": "Weekly (7 days)",
    "monthly": "Monthly (30 days)",
}


def plan_price(plan: str) -> float:
    plan = plan.lower().strip()
    env_key = f"BALANCE_PRICE_{plan.upper()}"
    raw = os.getenv(env_key, "").strip()
    defaults = {"daily": 5.0, "weekly": 25.0, "monthly": 80.0}
    if not raw:
        return defaults.get(plan, 0.0)
    try:
        return float(raw)
    except ValueError:
        return defaults.get(plan, 0.0)
