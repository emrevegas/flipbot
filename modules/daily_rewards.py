"""Daily reward config, status check, and claim cooldown."""

from __future__ import annotations

import time
from typing import Any

import discord

from modules.database import get_data, get_user_data, set_data, set_user_data
from modules.promo import (
    check_status_requirement,
    get_member_custom_status,
    normalize_status_keywords,
    resolve_member_for_status,
)

SETTINGS_KEY = "server/daily_rewards"
DEFAULT_COOLDOWN_SEC = 24 * 3600


def get_config() -> dict[str, Any]:
    data = get_data(SETTINGS_KEY)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("default_amount", 0)
    data.setdefault("booster_amount", 0)
    data.setdefault("role_rewards", {})
    data.setdefault("status_contains", "")
    data.setdefault("cooldown_hours", 24)
    data.setdefault("enabled", True)
    if not isinstance(data.get("role_rewards"), dict):
        data["role_rewards"] = {}
    return data


def save_config(data: dict[str, Any]) -> None:
    set_data(SETTINGS_KEY, data)


def cooldown_seconds(cfg: dict | None = None) -> int:
    cfg = cfg or get_config()
    hours = max(1, int(cfg.get("cooldown_hours", 24) or 24))
    return hours * 3600


def is_server_booster(member: discord.Member) -> bool:
    return getattr(member, "premium_since", None) is not None


def compute_reward(member: discord.Member, cfg: dict | None = None) -> tuple[int, str]:
    """
    Pick a single reward: max of default, booster (if boosting), and best matching role tier.
    Returns (amount, label for display).
    """
    cfg = cfg or get_config()
    candidates: list[tuple[int, str]] = []

    default_amt = int(cfg.get("default_amount", 0) or 0)
    if default_amt > 0:
        candidates.append((default_amt, "Daily"))

    booster_amt = int(cfg.get("booster_amount", 0) or 0)
    if booster_amt > 0 and is_server_booster(member):
        candidates.append((booster_amt, "Server Booster"))

    role_rewards: dict = cfg.get("role_rewards") or {}
    for role in member.roles:
        rid = str(role.id)
        if rid in role_rewards:
            amt = int(role_rewards[rid] or 0)
            if amt > 0:
                candidates.append((amt, role.name))

    if not candidates:
        return 0, ""
    amount, label = max(candidates, key=lambda x: x[0])
    return amount, label


def seconds_until_claim(user_id: int, cfg: dict | None = None) -> int:
    cfg = cfg or get_config()
    cd = cooldown_seconds(cfg)
    row = get_user_data(int(user_id), "daily_claim") or {}
    last = int(row.get("last_claim", 0) or 0)
    if last <= 0:
        return 0
    remain = last + cd - int(time.time())
    return max(0, remain)


def can_claim(user_id: int, cfg: dict | None = None) -> tuple[bool, int]:
    remain = seconds_until_claim(user_id, cfg)
    return remain <= 0, remain


def record_claim(user_id: int, amount: int, label: str) -> None:
    set_user_data(
        int(user_id),
        "daily_claim",
        {
            "last_claim": int(time.time()),
            "last_amount": int(amount),
            "last_label": label,
        },
    )


def format_config_summary(cfg: dict | None = None) -> str:
    cfg = cfg or get_config()
    lines = [
        f"**Default:** {int(cfg.get('default_amount', 0)):,} pts",
        f"**Booster:** {int(cfg.get('booster_amount', 0)):,} pts",
        f"**Cooldown:** {int(cfg.get('cooldown_hours', 24))}h",
        f"**Enabled:** {'Yes' if cfg.get('enabled', True) else 'No'}",
    ]
    status = (cfg.get("status_contains") or "").strip()
    lines.append(
        f"**Status req:** {status if status else '*(off)*'}"
    )
    role_rewards: dict = cfg.get("role_rewards") or {}
    if role_rewards:
        role_lines = [
            f"<@&{rid}>: **{int(amt):,}** pts"
            for rid, amt in sorted(role_rewards.items(), key=lambda x: -int(x[1] or 0))
        ]
        lines.append("**Role tiers:**\n" + "\n".join(role_lines[:12]))
    else:
        lines.append("**Role tiers:** *(none)*")
    return "\n".join(lines)


def set_default_amount(amount: int) -> None:
    cfg = get_config()
    cfg["default_amount"] = max(0, int(amount))
    save_config(cfg)


def set_booster_amount(amount: int) -> None:
    cfg = get_config()
    cfg["booster_amount"] = max(0, int(amount))
    save_config(cfg)


def set_role_amount(role_id: int, amount: int) -> None:
    cfg = get_config()
    rewards = dict(cfg.get("role_rewards") or {})
    rid = str(int(role_id))
    if amount <= 0:
        rewards.pop(rid, None)
    else:
        rewards[rid] = int(amount)
    cfg["role_rewards"] = rewards
    save_config(cfg)


def set_status_requirement(keywords: str) -> None:
    cfg = get_config()
    cfg["status_contains"] = normalize_status_keywords(keywords)
    save_config(cfg)


async def check_daily_requirements(
    member: discord.Member | None,
    guild: discord.Guild | None,
    user_id: int,
) -> tuple[bool, str]:
    from modules.server_tag import check_server_tag

    ok_tag, tag_err = await check_server_tag(member, guild, user_id)
    if not ok_tag:
        return False, tag_err

    cfg = get_config()
    req = (cfg.get("status_contains") or "").strip()
    if not req:
        return True, ""
    member = resolve_member_for_status(guild, user_id, member)
    return check_status_requirement(member, req)


def check_daily_status(
    member: discord.Member | None,
    guild: discord.Guild | None,
    user_id: int,
) -> tuple[bool, str]:
    """Sync status-only check (prefer check_daily_requirements in cogs)."""
    cfg = get_config()
    req = (cfg.get("status_contains") or "").strip()
    if not req:
        return True, ""
    member = resolve_member_for_status(guild, user_id, member)
    return check_status_requirement(member, req)
