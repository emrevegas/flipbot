"""Components V2 layouts for the games hub (menu + pre-play setup)."""

from __future__ import annotations

from typing import Optional, Sequence

import discord
from discord import ui

from modules.translator import t
from modules.ui_v2 import (
    ACCENT_BRAND,
    MAX_TEXT,
    add_action_row,
    add_controls_to_container,
    build_layout,
    new_container,
    panel_markdown,
)
from modules.utils import get_user_lang


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def embed_to_panel_text(embed: discord.Embed) -> str:
    """Convert a hub embed to markdown for TextDisplay."""
    parts: list[str] = []
    if embed.title:
        parts.append(f"## {embed.title}")
    if embed.description:
        parts.append(embed.description)
    for field in embed.fields:
        name = (field.name or "").strip()
        value = (field.value or "").strip()
        if name and name != "\u200b":
            parts.append(f"**{name}**\n{value}")
        elif value:
            parts.append(value)
    if embed.footer and embed.footer.text:
        parts.append(f"-# {embed.footer.text}")
    return _clip("\n\n".join(parts))


def _chunk_controls(controls: Sequence[ui.Item], *, max_per_row: int = 5) -> list[list[ui.Item]]:
    rows: list[list[ui.Item]] = []
    current: list[ui.Item] = []
    for ctrl in controls:
        current.append(ctrl)
        if len(current) >= max_per_row:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    return rows


def collect_active_game_controls(message_id: str, game: str) -> list[ui.Item]:
    """Extract hub controls from legacy ActiveGameView.__init__ logic."""
    from cogs.games import (
        BetSelect,
        CaseFavButton,
        CaseSingleSelect,
        CaseTypeToggleButton,
        CoinFlipChoiceButton,
        CrystalsPlayButton,
        GameSelectDropdown,
        GameSession,
        LimboMultiplierSelect,
        MineCountSelect,
        OpenCaseButton,
        OpenCountSelect,
        PlayButton,
        SlotLineModeSelect,
        StartBlackjackButton,
        StartHiLoButton,
        StartLimboButton,
        StartMinesButton,
        StartSlotButton,
        StartTowersButton,
        TowersModeSelect,
        _BjPPToggleButton,
        _BjT3ToggleButton,
        _get_cases_data,
        _get_user_favorites,
    )

    controls: list[ui.Item] = [
        GameSelectDropdown(message_id),
        BetSelect(message_id),
    ]
    if game == "coinflip":
        controls.append(CoinFlipChoiceButton(message_id, "Hot"))
        controls.append(CoinFlipChoiceButton(message_id, "Cold"))
    elif game == "mines":
        controls.append(MineCountSelect(message_id))
        controls.append(StartMinesButton(message_id))
    elif game == "crystals":
        controls.append(CrystalsPlayButton(message_id))
    elif game == "towers":
        controls.append(TowersModeSelect(message_id))
        controls.append(StartTowersButton(message_id))
    elif game == "limbo":
        controls.append(LimboMultiplierSelect(message_id))
        controls.append(StartLimboButton(message_id))
    elif game == "case_battle":
        from cogs.games import (
            CaseBattleBotToggleButton,
            CaseBattleCaseSelect,
            CaseBattleCountSelect,
            CaseBattleModeSelect,
            StartCaseBattleButton,
        )

        session = GameSession.get_session(message_id)
        controls = [GameSelectDropdown(message_id)]
        controls.append(CaseBattleCaseSelect(message_id))
        if session and session.get("case_battle_case_id"):
            controls.append(CaseBattleCountSelect(message_id))
        controls.append(CaseBattleModeSelect(message_id))
        controls.append(CaseBattleBotToggleButton(message_id))
        if session and session.get("case_battle_case_id"):
            controls.append(StartCaseBattleButton(message_id))
        return controls
    elif game == "case_opening":
        session = GameSession.get_session(message_id)
        owner_id = int(session.get("owner", 0)) if session else 0
        data = _get_cases_data()
        all_cases = data.get("cases", {})
        view_mode = (session.get("case_opening_view_mode", "house") if session else "house")
        case_id = session.get("case_opening_case_id") if session else None
        favs = _get_user_favorites(owner_id)
        controls.append(CaseSingleSelect(message_id, view_mode))
        if case_id and case_id in all_cases:
            controls.append(OpenCountSelect(message_id))
            controls.append(CaseTypeToggleButton(message_id, view_mode))
            controls.append(CaseFavButton(message_id, case_id, case_id in favs))
            controls.append(OpenCaseButton(message_id))
        else:
            controls.append(CaseTypeToggleButton(message_id, view_mode))
    elif game == "slot":
        controls.append(SlotLineModeSelect(message_id))
        controls.append(StartSlotButton(message_id))
    elif game == "blackjack":
        controls.append(_BjPPToggleButton(message_id))
        controls.append(_BjT3ToggleButton(message_id))
        controls.append(StartBlackjackButton(message_id))
    elif game == "hilo":
        controls.append(StartHiLoButton(message_id))
    else:
        controls.append(PlayButton(message_id, game))
    return controls


def build_game_menu_layout(
    message_id: str,
    user: discord.abc.User,
    lang: Optional[str] = None,
) -> ui.LayoutView:
    """Main games hub — title, description, game select only."""
    from cogs.games import GameSelectDropdown

    if lang is None:
        lang = get_user_lang(user.id)
    body = t("games.menu_description", lang=lang)
    c = new_container(accent=ACCENT_BRAND)
    header = panel_markdown(
        title=t("games.menu_title", lang=lang),
        body=body,
        footer=t("games.footer", lang=lang),
        emoji="🎰",
    )
    avatar = getattr(user, "display_avatar", None)
    url = avatar.url if avatar else None
    if url:
        c.add_item(ui.Section(ui.TextDisplay(header), accessory=ui.Thumbnail(media=url)))
    else:
        c.add_item(ui.TextDisplay(header))
    c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
    add_action_row(c, GameSelectDropdown(message_id))
    return build_layout(c, timeout=None)


def build_active_game_hub_layout(
    message_id: str,
    user: discord.Member,
    session: dict,
    game: str,
) -> ui.LayoutView:
    """Active game setup hub — embed body as markdown + all setup controls."""
    from cogs.games import create_game_embed

    embed = create_game_embed(user, session)
    body = embed_to_panel_text(embed)
    accent = int(embed.color.value) if embed.color else ACCENT_BRAND
    c = new_container(accent=accent)
    if embed.thumbnail and embed.thumbnail.url:
        c.add_item(
            ui.Section(ui.TextDisplay(body), accessory=ui.Thumbnail(media=embed.thumbnail.url))
        )
    else:
        c.add_item(ui.TextDisplay(body))
    controls = collect_active_game_controls(message_id, game)
    if controls:
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        add_controls_to_container(c, controls)
    return build_layout(c, timeout=None)


def hub_layout_for_session(
    message_id: str,
    user: discord.Member,
    session: Optional[dict] = None,
) -> ui.LayoutView:
    """Menu if no game selected, else active game hub."""
    from cogs.games import GameSession

    if session is None:
        session = GameSession.get_session(message_id) or {}
    game = session.get("game") or "none"
    if not game or game == "none":
        return build_game_menu_layout(message_id, user)
    return build_active_game_hub_layout(message_id, user, session, game)
