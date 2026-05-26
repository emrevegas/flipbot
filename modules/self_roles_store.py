"""Per-guild self-role menu configuration."""

from __future__ import annotations

from typing import Any

from modules.database import get_server_data, set_server_data

MAX_ROLES = 25

_STYLE_MAP = {
    "primary": "primary",
    "secondary": "secondary",
    "success": "success",
    "danger": "danger",
    "blurple": "primary",
    "grey": "secondary",
    "gray": "secondary",
    "green": "success",
    "red": "danger",
}


def get_config(guild_id: int | str) -> dict[str, Any]:
    data = get_server_data(str(guild_id)) or {}
    sr = data.get("self_roles")
    if not isinstance(sr, dict):
        sr = {}
    sr.setdefault("title", "Self Roles")
    sr.setdefault(
        "description",
        "Use the buttons below to add or remove roles from yourself.",
    )
    sr.setdefault("roles", [])
    sr.setdefault("channel_id", None)
    sr.setdefault("message_id", None)
    if not isinstance(sr.get("roles"), list):
        sr["roles"] = []
    return sr


def save_config(guild_id: int | str, cfg: dict[str, Any]) -> None:
    data = get_server_data(str(guild_id)) or {}
    data["self_roles"] = cfg
    set_server_data(str(guild_id), data)


def normalize_style(raw: str) -> str:
    return _STYLE_MAP.get((raw or "secondary").strip().lower(), "secondary")


def add_role(
    guild_id: int | str,
    role_id: int,
    *,
    label: str = "",
    emoji: str = "",
    style: str = "secondary",
    row: int = 0,
) -> tuple[bool, str]:
    cfg = get_config(guild_id)
    roles: list = cfg["roles"]
    rid = str(int(role_id))
    if any(str(r.get("role_id")) == rid for r in roles):
        return False, "That role is already on the menu."
    if len(roles) >= MAX_ROLES:
        return False, f"Maximum **{MAX_ROLES}** roles per menu (Discord limit)."
    roles.append({
        "role_id": rid,
        "label": (label or "")[:80],
        "emoji": (emoji or "").strip(),
        "style": normalize_style(style),
        "row": max(0, min(4, int(row))),
    })
    cfg["roles"] = roles
    save_config(guild_id, cfg)
    return True, ""


def remove_role(guild_id: int | str, role_id: int) -> bool:
    cfg = get_config(guild_id)
    rid = str(int(role_id))
    roles = [r for r in cfg["roles"] if str(r.get("role_id")) != rid]
    if len(roles) == len(cfg["roles"]):
        return False
    cfg["roles"] = roles
    save_config(guild_id, cfg)
    return True


def set_panel_text(guild_id: int | str, *, title: str | None = None, description: str | None = None) -> None:
    cfg = get_config(guild_id)
    if title is not None:
        cfg["title"] = title[:256]
    if description is not None:
        cfg["description"] = description[:4000]
    save_config(guild_id, cfg)


def set_channel_message(guild_id: int | str, channel_id: int | None, message_id: int | None) -> None:
    cfg = get_config(guild_id)
    cfg["channel_id"] = int(channel_id) if channel_id else None
    cfg["message_id"] = int(message_id) if message_id else None
    save_config(guild_id, cfg)


def format_roles_list(guild, cfg: dict) -> str:
    roles = cfg.get("roles") or []
    if not roles:
        return "*(no roles configured)*"
    lines = []
    for r in roles:
        rid = int(r.get("role_id", 0))
        role = guild.get_role(rid) if guild else None
        mention = role.mention if role else f"<@&{rid}>"
        label = r.get("label") or (role.name if role else str(rid))
        lines.append(f"{mention} — **{label}** (row {int(r.get('row', 0))})")
    return "\n".join(lines[:25])
