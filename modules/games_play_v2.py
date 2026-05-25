"""Components V2 layouts for in-game play screens (mines, towers, hilo, etc.)."""

from __future__ import annotations

from typing import Optional, Sequence

import discord
from discord import ui

from modules.games_hub_v2 import embed_to_panel_text
from modules.ui_v2 import ACCENT_BRAND, add_controls_to_container, build_layout, new_container


def build_game_play_layout(
    embed: discord.Embed,
    controls: Sequence[ui.Item],
    *,
    timeout: Optional[float] = 600,
) -> ui.LayoutView:
    """Game board / result screen — text in container, controls in ActionRows."""
    body = embed_to_panel_text(embed)
    accent = int(embed.color.value) if embed.color else ACCENT_BRAND
    c = new_container(accent=accent)
    thumb = embed.thumbnail.url if embed.thumbnail else None
    if thumb:
        c.add_item(ui.Section(ui.TextDisplay(body), accessory=ui.Thumbnail(media=thumb)))
    else:
        c.add_item(ui.TextDisplay(body))
    if controls:
        c.add_item(ui.Separator(spacing=discord.SeparatorSpacing.small))
        add_controls_to_container(c, controls)
    return build_layout(c, timeout=timeout)


def mines_grid_items(state, *, game_over: bool = False) -> list[ui.Item]:
    """20 grid cells + cashout — same logic as MinesGridView."""
    from cogs.games import (
        MinesCashoutButton,
        MinesCell,
        MinesGame,
        _get_mines_settings,
    )

    settings = _get_mines_settings()
    mult = MinesGame.calc_multiplier(state.mine_count, state.diamonds, settings["house_edge_decimal"])
    earnings = int(state.bet * mult)
    items: list[ui.Item] = []

    for r in range(4):
        for c in range(5):
            cell = (r, c)
            if cell in state.revealed:
                is_mine = state.board[r][c] == 1
                items.append(
                    discord.ui.Button(
                        style=discord.ButtonStyle.danger if is_mine else discord.ButtonStyle.success,
                        emoji=settings["mine"] if is_mine else settings["gem"],
                        disabled=True,
                        custom_id=f"mr_{r}{c}_{state.message_id}",
                    )
                )
            elif game_over:
                is_mine = state.board[r][c] == 1
                items.append(
                    discord.ui.Button(
                        style=discord.ButtonStyle.danger if is_mine else discord.ButtonStyle.secondary,
                        emoji=settings["mine"] if is_mine else settings["gem"],
                        disabled=True,
                        custom_id=f"mo_{r}{c}_{state.message_id}",
                    )
                )
            else:
                items.append(MinesCell(state.message_id, r, c))

    cashout_disabled = state.diamonds == 0 or game_over
    items.append(
        MinesCashoutButton(state.message_id, mult, earnings, state.mode, cashout_disabled)
    )
    return items


def towers_floor_items(
    state,
    *,
    game_over: bool = False,
    cashed_out: bool = False,
) -> list[ui.Item]:
    from cogs.games import TowersCashoutButton, TowersColButton, TowersGame, _get_towers_settings

    settings = _get_towers_settings()
    cols = TowersGame.COLS[state.tower_mode]
    mults = TowersGame.MULTIPLIERS[state.tower_mode]
    items: list[ui.Item] = []

    for c in range(cols):
        if game_over:
            if state.current_floor < TowersGame.FLOORS:
                bomb_here = state.bomb_positions[state.current_floor]
            else:
                bomb_here = -1
            if cashed_out:
                btn_style = discord.ButtonStyle.success
                btn_emoji = settings["gem"]
            else:
                btn_style = discord.ButtonStyle.danger if c == bomb_here else discord.ButtonStyle.secondary
                btn_emoji = settings["bomb"] if c == bomb_here else None
            items.append(
                discord.ui.Button(
                    label=str(c + 1),
                    style=btn_style,
                    emoji=btn_emoji,
                    disabled=True,
                    custom_id=f"tgo_{c}_{state.message_id}",
                )
            )
        else:
            items.append(TowersColButton(state.message_id, c))

    cashout_disabled = state.current_floor == 0 or game_over
    mult = mults[state.current_floor - 1] if state.current_floor > 0 else 1.0
    earnings = int(state.bet * mult)
    items.append(
        TowersCashoutButton(state.message_id, mult, earnings, state.mode, cashout_disabled)
    )
    return items


def hilo_play_items(message_id: str, state: dict) -> list[ui.Item]:
    from modules.hilo_flow import hilo_action_buttons
    return hilo_action_buttons(message_id, state)


def coinflip_result_items(msg_id: str, house_flip: str | None = None) -> list[ui.Item]:
    BS = discord.ButtonStyle
    hot_style, cold_style = BS.secondary, BS.secondary
    hot_dis, cold_dis = True, True
    if house_flip == "Hot":
        hot_style, hot_dis = BS.success, False
        cold_style, cold_dis = BS.danger, True
    elif house_flip == "Cold":
        cold_style, cold_dis = BS.success, False
        hot_style, hot_dis = BS.danger, True
    return [
        discord.ui.Button(
            label="Hot", style=hot_style, disabled=hot_dis, emoji="🔥",
            custom_id=f"cf_h_{msg_id}",
        ),
        discord.ui.Button(
            label="Cold", style=cold_style, disabled=cold_dis, emoji="❄️",
            custom_id=f"cf_c_{msg_id}",
        ),
    ]


def dice_anim_items(
    msg_id: str,
    *,
    p_label: str = "🎲",
    h_label: str = "🎲",
    p_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    h_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    p_dis: bool = True,
    h_dis: bool = True,
) -> list[ui.Item]:
    return [
        discord.ui.Button(label="YOU", style=discord.ButtonStyle.secondary, disabled=True),
        discord.ui.Button(label="HOUSE", style=discord.ButtonStyle.secondary, disabled=True),
        discord.ui.Button(
            label=p_label, style=p_style, disabled=p_dis, custom_id=f"dp_{msg_id}"
        ),
        discord.ui.Button(
            label=h_label, style=h_style, disabled=h_dis, custom_id=f"dh_{msg_id}"
        ),
    ]


def crystals_reveal_items() -> list[ui.Item]:
    return [
        discord.ui.Button(
            label="Revealing…",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            emoji="💠",
        )
    ]


def status_button(label: str, *, emoji: str | None = None) -> ui.Button:
    """Single disabled placeholder button for loading/spin states."""
    return discord.ui.Button(
        label=label[:80],
        emoji=emoji,
        style=discord.ButtonStyle.secondary,
        disabled=True,
    )


def case_battle_duel_items(
    player_item: dict,
    bot_item: dict,
    *,
    revealed: bool = True,
) -> list[ui.Button]:
    """YOU vs BOT item row for case battle reveal."""
    if not revealed:
        return [status_button("Opening cases...", emoji="⚔️")]

    p_val = int(player_item.get("value", 0))
    b_val = int(bot_item.get("value", 0))
    p_win = p_val > b_val
    b_win = b_val > p_val
    tie = p_val == b_val

    def _item_btn(side: str, item: dict, won: bool) -> ui.Button:
        em = _parse_case_btn_emoji(str(item.get("emoji", "❓")))
        name = str(item.get("name", "?"))[:12]
        val = int(item.get("value", 0))
        if tie:
            style = discord.ButtonStyle.secondary
        elif won:
            style = discord.ButtonStyle.success
        else:
            style = discord.ButtonStyle.danger
        return discord.ui.Button(
            label=f"{side}: {name} ({val:,})"[:80],
            emoji=em,
            style=style,
            disabled=True,
        )

    return [
        _item_btn("YOU", player_item, p_win and not tie),
        discord.ui.Button(label="VS", style=discord.ButtonStyle.secondary, disabled=True),
        _item_btn("BOT", bot_item, b_win and not tie),
    ]


def _parse_case_btn_emoji(s: str):
    if not s:
        return "❓"
    if s.startswith("<") and ":" in s:
        try:
            return discord.PartialEmoji.from_str(s)
        except Exception:
            return "❓"
    return s


def case_reel_items(
    pool: list,
    winners: list,
    batch_start: int,
    *,
    reveal: bool,
    uid: str,
) -> list[ui.Item]:
    """Case-opening reel grid as buttons (1–5 winners per batch)."""
    import random

    count = len(winners)

    def _rand_emoji() -> str:
        return random.choice(pool).get("emoji", "❓") if pool else "❓"

    items: list[ui.Item] = []
    for row_idx, winner in enumerate(winners):
        w_emoji = winner.get("emoji", "❓")
        if count == 1:
            for col in range(5):
                is_win = (col == 2) and reveal
                e = _parse_case_btn_emoji(w_emoji if is_win else _rand_emoji())
                items.append(
                    discord.ui.Button(
                        style=discord.ButtonStyle.success if is_win else discord.ButtonStyle.secondary,
                        emoji=e,
                        disabled=not is_win,
                        custom_id=f"reel_{uid}_{row_idx}_{col}",
                    )
                )
        else:
            items.append(
                discord.ui.Button(
                    style=discord.ButtonStyle.secondary,
                    label=str(batch_start + row_idx + 1),
                    disabled=True,
                    custom_id=f"reel_{uid}_{row_idx}_0",
                )
            )
            for col in range(1, 5):
                is_win = (col == 2) and reveal
                e = _parse_case_btn_emoji(w_emoji if is_win else _rand_emoji())
                items.append(
                    discord.ui.Button(
                        style=discord.ButtonStyle.success if is_win else discord.ButtonStyle.secondary,
                        emoji=e,
                        disabled=not is_win,
                        custom_id=f"reel_{uid}_{row_idx}_{col}",
                    )
                )
    return items


async def message_edit_play(
    message: discord.Message,
    embed: discord.Embed,
    controls: Sequence[ui.Item] | None = None,
    *,
    timeout: float | None = None,
) -> None:
    """Edit a channel message to a V2 play layout."""
    layout = build_game_play_layout(embed, controls or [], timeout=timeout)
    await message.edit(embed=None, content=None, view=layout)


def roulette_anim_items(
    msg_id: str,
    *,
    p_label: str = "🍡",
    h_label: str = "🍡",
    p_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    h_style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    p_dis: bool = True,
    h_dis: bool = True,
    r2_0: str = "⏳",
    r2_1: str = "⏳",
    r2_2: str = "⏳",
) -> list[ui.Item]:
    BS = discord.ButtonStyle
    return [
        discord.ui.Button(label="YOU", style=BS.secondary, disabled=True),
        discord.ui.Button(label="HTW", style=BS.secondary, disabled=True),
        discord.ui.Button(label="HOUSE", style=BS.secondary, disabled=True),
        discord.ui.Button(
            label=p_label, style=p_style, disabled=p_dis, custom_id=f"rp_{msg_id}"
        ),
        discord.ui.Button(label="VS", style=BS.secondary, disabled=True),
        discord.ui.Button(
            label=h_label, style=h_style, disabled=h_dis, custom_id=f"rh_{msg_id}"
        ),
        discord.ui.Button(label=r2_0, style=BS.secondary, disabled=True),
        discord.ui.Button(label=r2_1, style=BS.secondary, disabled=True),
        discord.ui.Button(label=r2_2, style=BS.secondary, disabled=True),
    ]
