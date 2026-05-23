"""Shared utilities."""
from __future__ import annotations

import discord
import config

# ── Rakeback tier cache ────────────────────────────────────────────────────────
# Loaded from DB at startup and refreshed whenever tiers are modified via /panel.
# Sync callers (games, rakeback cog, etc.) read from this cache.

_tier_cache: list[dict] = [
    {"name": "Bronze",   "min_wagered": 0,       "rate": 0.03},
    {"name": "Silver",   "min_wagered": 5_000,   "rate": 0.05},
    {"name": "Gold",     "min_wagered": 25_000,  "rate": 0.08},
    {"name": "Platinum", "min_wagered": 100_000, "rate": 0.12},
    {"name": "Diamond",  "min_wagered": 500_000, "rate": 0.18},
]


async def refresh_tier_cache() -> None:
    """Reload tier cache from DB. Call after any tier add/edit/delete."""
    from database import db as _db
    rows = await _db.get_rakeback_tiers()
    if rows:
        global _tier_cache
        _tier_cache = sorted(rows, key=lambda r: r["min_wagered"])


def get_rakeback_tier(total_wagered: float) -> dict:
    best = _tier_cache[0]
    for tier in _tier_cache:
        if total_wagered >= tier["min_wagered"]:
            best = tier
    return best


def get_next_rakeback_tier(total_wagered: float) -> dict | None:
    for tier in _tier_cache:
        if total_wagered < tier["min_wagered"]:
            return tier
    return None


def get_all_tiers() -> list[dict]:
    return list(_tier_cache)


# ── General helpers ────────────────────────────────────────────────────────────

def pts_to_usd(pts: float) -> float:
    return pts / config.POINTS_PER_USD


def fmt_pts(n: float) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return f"{n:.2f}"


def is_owner(user_id: int) -> bool:
    return user_id in config.OWNER_IDS


def is_admin(ctx) -> bool:
    if is_owner(ctx.author.id):
        return True
    if hasattr(ctx, "guild") and ctx.guild:
        return ctx.author.guild_permissions.administrator
    return False


def error_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {msg}", color=0xE74C3C)


def success_embed(msg: str) -> discord.Embed:
    return discord.Embed(description=f"✅ {msg}", color=0x2ECC71)


def info_embed(title: str, msg: str) -> discord.Embed:
    return discord.Embed(title=title, description=msg, color=0x5865F2)
