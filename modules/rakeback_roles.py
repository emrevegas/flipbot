"""Assign /panel rakeback tier Discord roles from wager thresholds."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import discord

from modules.database import get_data
from modules.rakeback_engine import all_tiers_display

_FLIP_DB = Path(__file__).resolve().parents[1] / "database" / "flipbot.db"


def _is_tracking_exempt(user_id: int | str) -> bool:
    admins = get_data("server/admins") or {}
    permissions = admins.get(str(user_id), [])
    if isinstance(permissions, str):
        permissions = [permissions]
    if not isinstance(permissions, list):
        return False
    return "admin" in {str(p).lower() for p in permissions}


def get_flip_total_wagered(user_id: int | str) -> float:
    """Total wagered from flipbot.db (prefix games / .rakeback)."""
    try:
        with sqlite3.connect(_FLIP_DB) as conn:
            row = conn.execute(
                "SELECT total_wagered FROM users WHERE user_id=?",
                (str(user_id),),
            ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def best_tier_for_wager(total_wagered: float) -> dict | None:
    tiers = all_tiers_display()
    qualified = [
        t for t in tiers
        if total_wagered >= float(t["min_wagered"]) and t.get("role_id")
    ]
    if not qualified:
        return None
    return max(qualified, key=lambda t: (float(t["min_wagered"]), float(t["percentage"])))


async def sync_rakeback_tier_roles(
    member: discord.Member,
    total_wagered: float,
) -> None:
    """Keep exactly one tier role — highest tier the user has wagered into."""
    if _is_tracking_exempt(member.id):
        return

    tiers = all_tiers_display()
    role_ids = {int(t["role_id"]) for t in tiers if t.get("role_id")}
    if not role_ids:
        return

    current = [r for r in member.roles if r.id in role_ids]
    best = best_tier_for_wager(total_wagered)

    if best is None:
        for role in current:
            try:
                await member.remove_roles(role, reason="Below rakeback tier minimum")
            except (discord.Forbidden, discord.HTTPException):
                pass
        return

    best_role_id = int(best["role_id"])
    if len(current) == 1 and current[0].id == best_role_id:
        return

    for role in current:
        if role.id != best_role_id:
            try:
                await member.remove_roles(role, reason="Rakeback tier update")
            except (discord.Forbidden, discord.HTTPException):
                pass

    if not any(r.id == best_role_id for r in member.roles):
        discord_role = member.guild.get_role(best_role_id)
        if discord_role:
            try:
                await member.add_roles(discord_role, reason="Rakeback tier earned")
            except (discord.Forbidden, discord.HTTPException):
                pass
