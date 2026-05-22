"""Shared utilities."""
from __future__ import annotations

import discord
import config


def pts_to_usd(pts: float) -> float:
    return pts / config.POINTS_PER_USD


def fmt_pts(n: float) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return f"{n:.2f}"


def get_rakeback_tier(total_wagered: float) -> dict:
    best = config.RAKEBACK_TIERS[0]
    for tier in config.RAKEBACK_TIERS:
        if total_wagered >= tier["min_wagered"]:
            best = tier
    return best


def get_next_rakeback_tier(total_wagered: float) -> dict | None:
    for tier in config.RAKEBACK_TIERS:
        if total_wagered < tier["min_wagered"]:
            return tier
    return None


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
