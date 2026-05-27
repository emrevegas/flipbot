"""Rakeback tiers from /panel → server/rakeback_settings."""

from __future__ import annotations

from typing import Any

import discord

from modules.database import get_data


def get_settings() -> dict:
    data = get_data("server/rakeback_settings") or {}
    return data if isinstance(data, dict) else {}


def get_min_withdrawal() -> int:
    return int(get_settings().get("min_withdrawal", 100) or 100)


def format_rakeback_pct(pct: float) -> str:
    """Format rakeback percentage for display (e.g. 2.5%, 10%, 0.75%)."""
    pct = float(pct)
    if abs(pct - round(pct)) < 1e-9:
        return f"{int(round(pct))}%"
    text = f"{pct:.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def tier_pct_label(tier: dict) -> str:
    """Human-readable % from a tier dict (percentage or rate field)."""
    if tier.get("percentage") is not None:
        return format_rakeback_pct(tier["percentage"])
    return format_rakeback_pct(float(tier.get("rate", 0)) * 100)


def _tier_list() -> list[dict]:
    tiers = get_settings().get("tiers", [])
    if not isinstance(tiers, list):
        return []
    out: list[dict] = []
    for t in tiers:
        if not isinstance(t, dict):
            continue
        try:
            pct = float(t.get("percentage", 0) or 0)
            min_w = float(t.get("min_wagered", 0) or 0)
        except (TypeError, ValueError):
            continue
        out.append({
            "role_id": str(t.get("role_id", "")),
            "role_name": str(t.get("role_name") or "Tier"),
            "percentage": pct,
            "min_wagered": min_w,
            "rate": pct / 100.0,
        })
    return sorted(out, key=lambda x: (x["min_wagered"], x["percentage"]))


def resolve_tier(
    total_wagered: float,
    member: discord.Member | None = None,  # noqa: ARG001 — kept for call-site compat
) -> dict[str, Any]:
    """Best qualifying panel tier for this player (wager only; roles are assigned separately)."""
    tiers = _tier_list()
    if not tiers:
        return {
            "name": "Default",
            "role_id": None,
            "min_wagered": 0.0,
            "percentage": 3.0,
            "rate": 0.03,
        }

    best: dict | None = None

    for tier in tiers:
        if total_wagered < tier["min_wagered"]:
            continue
        if best is None or tier["percentage"] > best["percentage"]:
            best = tier

    if best is None:
        return {
            "name": "None",
            "role_id": None,
            "min_wagered": 0.0,
            "percentage": 0.0,
            "rate": 0.0,
        }

    return {
        "name": best["role_name"],
        "role_id": best["role_id"],
        "min_wagered": best["min_wagered"],
        "percentage": best["percentage"],
        "rate": best["rate"],
    }


def next_tier_goal(
    total_wagered: float,
    member: discord.Member | None = None,  # noqa: ARG001
) -> dict[str, Any] | None:
    """Next tier the player can unlock by wagering more."""
    tiers = _tier_list()
    if not tiers:
        return None

    current = resolve_tier(total_wagered, member)

    for tier in tiers:
        if tier["percentage"] <= current.get("percentage", 0) and total_wagered >= tier["min_wagered"]:
            continue
        if total_wagered < tier["min_wagered"]:
            return {
                "name": tier["role_name"],
                "min_wagered": tier["min_wagered"],
                "percentage": tier["percentage"],
                "rate": tier["rate"],
            }
    return None


def all_tiers_display() -> list[dict]:
    return _tier_list()
