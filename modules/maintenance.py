"""Global maintenance mode — blocks user interactions while admins can still manage."""

from __future__ import annotations

import discord

from modules.database import get_data, set_data, is_super_admin

DEFAULT_MESSAGE = (
    "🔧 **Maintenance mode is active**\n\n"
    "The bot is temporarily unavailable for updates. Please try again shortly."
)


def _root_server() -> dict:
    data = get_data("server/server") or {}
    return data if isinstance(data, dict) else {}


def is_maintenance_enabled() -> bool:
    return bool(_root_server().get("maintenance_enabled"))


def get_maintenance_message() -> str:
    msg = _root_server().get("maintenance_message")
    if msg and str(msg).strip():
        return str(msg).strip()
    return DEFAULT_MESSAGE


def set_maintenance_enabled(enabled: bool) -> None:
    set_data("server/server", {"maintenance_enabled": bool(enabled)})


def set_maintenance_message(message: str | None) -> None:
    if message and str(message).strip():
        set_data("server/server", {"maintenance_message": str(message).strip()[:500]})
    else:
        root = _root_server()
        root.pop("maintenance_message", None)
        set_data("server/server", root, merge=False)


def can_bypass_maintenance(user_id: int) -> bool:
    """Super admin, bot owner, and server admins with full admin permission."""
    if is_super_admin(user_id):
        return True
    admins = get_data("server/admins") or {}
    perms = admins.get(str(user_id), [])
    if isinstance(perms, list) and "admin" in perms:
        return True
    return False


def should_block_user(user_id: int) -> bool:
    return is_maintenance_enabled() and not can_bypass_maintenance(user_id)


async def send_maintenance_notice(interaction: discord.Interaction) -> None:
    """Ephemeral notice for blocked interactions."""
    from modules.utils import get_user_lang
    from modules.translator import t

    lang = get_user_lang(interaction.user.id)
    text = get_maintenance_message()
    custom = _root_server().get("maintenance_message")
    if custom and str(custom).strip():
        body = text
    else:
        body = t("maintenance.default_message", lang=lang)

    try:
        from modules.ui_v2 import warning_panel, send_ephemeral

        view = warning_panel(
            t("maintenance.title", lang=lang),
            body,
            footer=t("maintenance.footer", lang=lang),
        )
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await send_ephemeral(interaction, view)
    except Exception:
        if interaction.response.is_done():
            await interaction.followup.send(body, ephemeral=True)
        else:
            await interaction.response.send_message(body, ephemeral=True)
