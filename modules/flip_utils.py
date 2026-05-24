"""Shared utilities — rakeback tiers from /panel, USD via exchange rate."""

from __future__ import annotations

import discord

import config
from modules import rakeback_engine


async def refresh_tier_cache() -> None:
    """Tiers are read live from panel settings."""
    return


def get_rakeback_tier(
    total_wagered: float,
    member: discord.Member | None = None,
) -> dict:
    return rakeback_engine.resolve_tier(total_wagered, member)


def get_next_rakeback_tier(
    total_wagered: float,
    member: discord.Member | None = None,
) -> dict | None:
    nxt = rakeback_engine.next_tier_goal(total_wagered, member)
    if not nxt:
        return None
    return {"name": nxt["name"], "min_wagered": nxt["min_wagered"], "rate": nxt["rate"]}


def get_all_tiers() -> list[dict]:
    tiers = rakeback_engine.all_tiers_display()
    if tiers:
        return [
            {
                "name": t["role_name"],
                "min_wagered": t["min_wagered"],
                "rate": t["rate"],
                "percentage": t["percentage"],
                "role_id": t["role_id"],
            }
            for t in tiers
        ]
    return [{"name": "Default", "min_wagered": 0, "rate": 0.03, "percentage": 3.0, "role_id": None}]


def pts_to_usd(pts: float) -> float:
    from modules.economy import coins_to_usd
    return coins_to_usd(pts)


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
