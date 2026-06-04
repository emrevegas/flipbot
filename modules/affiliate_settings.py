"""Affiliate wager commission — configurable via `.set affiliate wager`."""

from __future__ import annotations

from modules.database import get_data, set_data

_SETTINGS_KEY = "server/affiliate_settings"

_DEFAULT_RATE = 0.005  # 0.5%
_DEFAULT_MIN_DEPOSIT = 100.0


def get_affiliate_settings() -> dict:
    raw = get_data(_SETTINGS_KEY) or {}
    rate = float(raw.get("wager_rate", _DEFAULT_RATE))
    if rate < 0:
        rate = 0.0
    min_dep = float(raw.get("wager_min_deposit", _DEFAULT_MIN_DEPOSIT))
    if min_dep < 0:
        min_dep = 0.0
    enabled = raw.get("wager_enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.lower() not in ("0", "false", "off", "no")
    return {
        "wager_rate": rate,
        "wager_min_deposit": min_dep,
        "wager_enabled": bool(enabled),
    }


def save_affiliate_settings(patch: dict) -> dict:
    cur = get_affiliate_settings()
    cur.update(patch)
    set_data(_SETTINGS_KEY, cur)
    return cur


def parse_wager_rate_input(raw: str) -> float:
    """Accept `0.5` (percent) or `0.005` (fraction). Values > 0.05 treated as percent."""
    val = float(str(raw).strip().replace(",", "."))
    if val < 0:
        raise ValueError("Rate cannot be negative.")
    if val > 100:
        raise ValueError("Rate too high (max 100%).")
    if val > 0.05:
        return val / 100.0
    return val


def wager_commission_enabled() -> bool:
    cfg = get_affiliate_settings()
    return bool(cfg["wager_enabled"]) and float(cfg["wager_rate"]) > 0


def format_settings_summary() -> str:
    import config

    cfg = get_affiliate_settings()
    pct = cfg["wager_rate"] * 100
    dep_pct = int(config.AFFILIATE_NET_RATE * 100)
    if not cfg["wager_enabled"] or cfg["wager_rate"] <= 0:
        status = "**disabled**"
    else:
        status = f"**{pct:g}%** of referred wagers (live)"
    return (
        f"Wager commission: {status}\n"
        f"Min lifetime deposit (referred user): **{cfg['wager_min_deposit']:g}** coins\n"
        f"*(Daily net deposit commission remains **{dep_pct}%** at 00:00 UTC)*"
    )
