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


def _member_role_ids(member: discord.Member | None) -> set[str]:
    if member is None:
        return set()
    return {str(r.id) for r in member.roles}


def resolve_tier(
    total_wagered: float,
    member: discord.Member | None = None,
) -> dict[str, Any]:
    """Best qualifying panel tier for this player."""
    tiers = _tier_list()
    if not tiers:
        return {
            "name": "Default",
            "role_id": None,
            "min_wagered": 0.0,
            "percentage": 3.0,
            "rate": 0.03,
        }

    role_ids = _member_role_ids(member)
    best: dict | None = None

    for tier in tiers:
        if total_wagered < tier["min_wagered"]:
            continue
        if member is not None and tier["role_id"] and tier["role_id"] not in role_ids:
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
    member: discord.Member | None = None,
) -> dict[str, Any] | None:
    """Next tier the player can still unlock (by wager and/or role)."""
    tiers = _tier_list()
    if not tiers:
        return None

    current = resolve_tier(total_wagered, member)
    role_ids = _member_role_ids(member)

    for tier in tiers:
        if tier["percentage"] <= current.get("percentage", 0) and total_wagered >= tier["min_wagered"]:
            if member is None or tier["role_id"] in role_ids:
                continue
        if total_wagered < tier["min_wagered"]:
            return {
                "name": tier["role_name"],
                "min_wagered": tier["min_wagered"],
                "percentage": tier["percentage"],
                "rate": tier["rate"],
                "needs_role": member is not None and tier["role_id"] not in role_ids,
            }
        if member is not None and tier["role_id"] not in role_ids:
            return {
                "name": tier["role_name"],
                "min_wagered": tier["min_wagered"],
                "percentage": tier["percentage"],
                "rate": tier["rate"],
                "needs_role": True,
            }
    return None


def all_tiers_display() -> list[dict]:
    return _tier_list()
