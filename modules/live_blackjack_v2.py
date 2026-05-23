"""Components V2 layouts for multi-table live blackjack."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import ui

if TYPE_CHECKING:
    from discord.ext import commands

from modules.live_blackjack_tables import (
    PHASE_COUNTDOWN,
    PHASE_PLAYING,
    PHASE_SETTLING,
    PHASE_WAITING,
    SEAT_COUNT,
    count_user_seats,
    get_game_settings,
    get_settings,
    get_user_saved_bet,
)
from modules.utils import format_balance
from Games import live_blackjack as lbj
from modules import ui_v2


def _nice_bet_amount(n: float) -> int:
    import math

    if n <= 0:
        return 0
    if n < 10:
        return int(round(n))
    exp = math.floor(math.log10(n))
    step = 5 * (10 ** (exp - 1))
    return int(round(n / step) * step)


def _bet_limits() -> tuple[int, int]:
    """Same min/max source as hub BetSelect, capped by live_blackjack game settings."""
    from modules.database import get_server_data

    server = get_server_data()
    gs = get_game_settings()
    mn = int(server.get("minBet", gs["min_bet"]) or gs["min_bet"])
    mx = int(server.get("maxBet", gs["max_bet"]) or gs["max_bet"])
    mn = max(mn, int(gs["min_bet"]))
    mx = min(mx, int(gs["max_bet"]))
    if mn > mx:
        mx = mn
    return mn, mx


def _bet_options() -> list[int]:
    """15 evenly spaced presets between min/max (hub BetSelect pattern)."""
    mn, mx = _bet_limits()
    if mn >= mx:
        return [mn]
    steps = 14
    presets: list[int] = []
    seen: set[int] = set()
    for i in range(15):
        raw = mn + (mx - mn) * i / steps
        amount = max(mn, min(mx, _nice_bet_amount(raw)))
        if amount not in seen:
            seen.add(amount)
            presets.append(amount)
    return presets or [mn]


def _side_bet_amount(main_bet: int) -> int:
    return max(1, int(main_bet) * 20 // 100) if main_bet > 0 else 0


def _coin_select_emoji() -> str | discord.PartialEmoji | None:
    """Coin emoji for bet select options (not in label text)."""
    from modules.database import get_data

    raw = (get_data("server/server") or {}).get("coin_emoji") or ""
    if isinstance(raw, str) and raw.startswith("<") and ":" in raw:
        try:
            return discord.PartialEmoji.from_str(raw)
        except Exception:
            return None
    return raw or None


def _bet_option_label(amount: int) -> str:
    """Numeric label only — Discord does not render custom emojis in select labels."""
    return f"{int(amount):,}"


class LiveBjSitButton(ui.Button):
    def __init__(self, table_id: str, seat_idx: int, *, disabled: bool = False):
        super().__init__(
            label=f"Seat {seat_idx + 1}",
            style=discord.ButtonStyle.success,
            emoji="🪑",
            custom_id=f"lbj_sit:{table_id}:{seat_idx}",
            disabled=disabled,
        )
        self.table_id = table_id
        self.seat_idx = seat_idx


class LiveBjLeaveButton(ui.Button):
    def __init__(self, table_id: str, seat_idx: int, *, disabled: bool = False):
        super().__init__(
            label=f"Leave {seat_idx + 1}",
            style=discord.ButtonStyle.secondary,
            emoji="🚪",
            custom_id=f"lbj_leave:{table_id}:{seat_idx}",
            disabled=disabled,
        )
        self.table_id = table_id
        self.seat_idx = seat_idx


class LiveBjConfirmBetButton(ui.Button):
    def __init__(self, table_id: str, *, disabled: bool = False):
        super().__init__(
            label="Confirm Bet",
            style=discord.ButtonStyle.primary,
            emoji="✅",
            custom_id=f"lbj_bet_ok:{table_id}",
            disabled=disabled,
        )
        self.table_id = table_id


class LiveBjBetSelect(ui.Select):
    def __init__(
        self,
        table_id: str,
        *,
        disabled: bool = False,
        current_bet: int | None = None,
        select_gen: int = 0,
    ):
        coin_emoji = _coin_select_emoji()
        mn, mx = _bet_limits()
        options = [
            discord.SelectOption(
                label="✏️ Custom Bet",
                value="custom_bet",
                emoji=coin_emoji,
            ),
        ]
        for b in _bet_options()[:24]:
            options.append(
                discord.SelectOption(
                    label=_bet_option_label(b),
                    value=f"bet_{b}",
                    emoji=coin_emoji,
                )
            )
        if current_bet and current_bet > 0:
            ph = f"💰 Bet: {format_balance(current_bet, 'real')} ({mn:,}–{mx:,})"
        else:
            ph = f"💰 Select bet ({mn:,}–{mx:,})"
        super().__init__(
            placeholder=ph[:100],
            options=options[:25],
            custom_id=f"lbj_bet_sel:{table_id}:{int(select_gen)}",
            disabled=disabled,
            min_values=1,
            max_values=1,
        )
        self.table_id = table_id


class LiveBjSidePPButton(ui.Button):
    def __init__(
        self,
        table_id: str,
        *,
        disabled: bool = False,
        active: bool = False,
        amount: int = 0,
    ):
        label = f"PP ✓ {amount:,}" if active and amount else "Perfect Pairs"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success if active else discord.ButtonStyle.secondary,
            emoji="🃏",
            custom_id=f"lbj_pp:{table_id}",
            disabled=disabled,
        )
        self.table_id = table_id


class LiveBjSide21Button(ui.Button):
    def __init__(
        self,
        table_id: str,
        *,
        disabled: bool = False,
        active: bool = False,
        amount: int = 0,
    ):
        label = f"21+3 ✓ {amount:,}" if active and amount else "21+3"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.success if active else discord.ButtonStyle.secondary,
            emoji="🎴",
            custom_id=f"lbj_213:{table_id}",
            disabled=disabled,
        )
        self.table_id = table_id


class LiveBjActionButton(ui.Button):
    def __init__(self, table_id: str, action: str, label: str, style: discord.ButtonStyle):
        super().__init__(
            label=label,
            style=style,
            custom_id=f"lbj_act:{table_id}:{action}",
        )
        self.table_id = table_id
        self.action = action


class LiveBjBalanceButton(ui.Button):
    def __init__(self, table_id: str, *, disabled: bool = False):
        super().__init__(
            label="Balance",
            style=discord.ButtonStyle.secondary,
            emoji="💰",
            custom_id=f"lbj_bal:{table_id}",
            disabled=disabled,
        )
        self.table_id = table_id


def _format_betting_summary(table: dict) -> str:
    """One line per player: mention + main / side bets (aggregated across seats)."""
    by_user: dict[int, dict] = {}
    order: list[int] = []
    for seat in table.get("seats") or []:
        uid = seat.get("user_id")
        if uid is None:
            continue
        uid = int(uid)
        if uid not in by_user:
            by_user[uid] = {
                "main": 0,
                "pp": 0,
                "t3": 0,
                "confirmed": True,
            }
            order.append(uid)
        row = by_user[uid]
        confirmed = bool(seat.get("bet_confirmed"))
        if confirmed:
            row["main"] += int(seat.get("bet") or 0)
            row["pp"] += int(seat.get("side_pp") or 0)
            row["t3"] += int(seat.get("side_21_3") or 0)
        else:
            row["main"] += int(seat.get("pending_bet") or 0)
            row["pp"] += int(seat.get("pending_side_pp") or 0)
            row["t3"] += int(seat.get("pending_side_21_3") or 0)
            row["confirmed"] = False

    lines: list[str] = []
    for uid in order:
        row = by_user[uid]
        main, pp, t3 = row["main"], row["pp"], row["t3"]
        if main <= 0 and pp <= 0 and t3 <= 0:
            continue
        tag = "✅ " if row["confirmed"] else ""
        parts = [f"Main **{format_balance(main, 'real')}**"]
        parts.append(
            f"PP **{format_balance(pp, 'real')}** ✓" if pp else "PP —"
        )
        parts.append(
            f"21+3 **{format_balance(t3, 'real')}** ✓" if t3 else "21+3 —"
        )
        total = main + pp + t3
        lines.append(
            f"{tag}<@{uid}>: {' · '.join(parts)} → **{format_balance(total, 'real')}**"
        )
    return "\n".join(lines)


def build_table_layout(
    table: dict,
    viewer_id: int | None = None,
    bot: "commands.Bot | discord.Client | None" = None,
) -> ui.LayoutView:
    """3 containers (seats) + bet container + action container."""
    emoji_map = lbj.get_emoji_map(bot)
    phase = table.get("phase", PHASE_WAITING)
    gs = get_game_settings()
    uid = int(viewer_id) if viewer_id else None
    my_seats = count_user_seats(table, uid) if uid else 0
    rnd = table.get("round") or {}
    rnd_phase = rnd.get("phase") or ""
    dealer_anim = rnd_phase == "dealer_anim"
    settling = phase == PHASE_SETTLING or bool(table.get("round_results"))
    playing = phase == PHASE_PLAYING and not settling and not dealer_anim
    betting_open = phase in (PHASE_WAITING, PHASE_COUNTDOWN) and not playing and not settling

    header_accent = 0x1ABC9C
    if settling:
        nets = [int(r.get("net") or 0) for r in table.get("round_results") or []]
        if nets and all(n > 0 for n in nets):
            header_accent = 0x2ECC71
        elif nets and all(n < 0 for n in nets):
            header_accent = 0xE74C3C
        else:
            header_accent = 0xF39C12

    phase_label = phase.replace("_", " ").title()
    if dealer_anim:
        phase_label = "Dealer Playing"
    elif settling:
        phase_label = "Results"

    header_lines = [
        f"### 🃏 Live Blackjack — `{table.get('id', '?')}`",
        f"**Phase:** {phase_label}",
    ]
    if phase == PHASE_COUNTDOWN and table.get("countdown_announce"):
        header_lines.append(f"⏱ **{table['countdown_announce']}**")
    flash = table.get("status_flash")
    if flash:
        header_lines.append(str(flash))
    header_lines.append("")
    hide_hole = (playing and rnd_phase == "playing") or dealer_anim
    header_lines.append(
        f"**Dealer:** {lbj.format_dealer_line(table, emoji_map, hide_hole=hide_hole)}"
    )
    turn = None
    if playing and table.get("round"):
        from Games.live_blackjack import _current_turn

        turn = _current_turn(table)
        if turn:
            sidx, _ = turn
            header_lines.append(f"▶ Turn: Seat **{sidx + 1}**")

    c_header = ui_v2.panel_container(
        title="Live Table",
        body="\n".join(header_lines),
        accent=header_accent,
        emoji="🃏",
    )
    containers = [c_header]

    # Player turns: only active seat. Dealer/results: all occupied seats + house.
    show_all_seats = settling or dealer_anim
    turn_seat_idx = turn[0] if turn else None
    active_turns = playing and rnd_phase == "playing" and not show_all_seats

    for i in range(SEAT_COUNT):
        seat = table["seats"][i]
        seat_uid = seat.get("user_id")
        is_empty = seat_uid is None

        if active_turns:
            if turn_seat_idx is None or i != turn_seat_idx or is_empty:
                continue
        elif show_all_seats and is_empty:
            continue

        body = lbj.format_seat_line(table, i, emoji_map)
        is_mine = not is_empty and uid is not None and int(seat_uid) == uid

        controls: list[ui.Item] = []
        if betting_open:
            if is_empty and (uid is None or my_seats < 2):
                controls.append(LiveBjSitButton(table["id"], i))
            elif not is_empty:
                controls.append(LiveBjLeaveButton(table["id"], i))

        seat_result = lbj.result_for_seat(table, i)
        seat_accent = lbj.result_accent(seat_result) if seat_result else (
            0x9B59B6 if is_mine else 0x5865F2
        )
        c_seat = ui_v2.panel_container(
            title=f"Seat {i + 1}",
            body=body,
            accent=seat_accent,
        )
        if controls:
            ui_v2.add_controls_to_container(c_seat, controls)
        containers.append(c_seat)

    if betting_open:
        c_bet = ui_v2.new_container(accent=0xF39C12)
        ui_v2.add_text(c_bet, "### 💰 Betting")
        ui_v2.add_text(
            c_bet,
            "> Bahis select’ten miktar seç (kaydedilir). **PP** / **21+3** isteğe bağlı, sonra **Confirm**.",
        )
        summary = _format_betting_summary(table)
        if summary:
            ui_v2.add_text(c_bet, summary)
        current_bet: int | None = None
        pp_amt = t3_amt = 0
        pp_on = t3_on = False
        if uid:
            current_bet = get_user_saved_bet(uid)
            for s in table["seats"]:
                if int(s.get("user_id") or 0) == uid:
                    pb = int(s.get("pending_bet") or 0)
                    if pb > 0:
                        current_bet = pb
                    pp_amt += int(s.get("pending_side_pp") or 0)
                    t3_amt += int(s.get("pending_side_21_3") or 0)
            pp_on = pp_amt > 0
            t3_on = t3_amt > 0
        bet_controls: list[ui.Item] = [
            LiveBjBalanceButton(table["id"]),
            LiveBjBetSelect(
                table["id"],
                current_bet=current_bet,
                select_gen=int(table.get("bet_select_gen", 0)),
            ),
            LiveBjConfirmBetButton(table["id"]),
            LiveBjSidePPButton(
                table["id"],
                active=pp_on,
                amount=pp_amt,
            ),
            LiveBjSide21Button(
                table["id"],
                active=t3_on,
                amount=t3_amt,
            ),
        ]
        ui_v2.add_controls_to_container(c_bet, bet_controls)
        containers.append(c_bet)

    if settling and table.get("round_results"):
        c_res = ui_v2.new_container(accent=header_accent)
        ui_v2.add_text(c_res, "### 📊 Round Results")
        lines: list[str] = []
        for r in table.get("round_results") or []:
            sidx = int(r.get("seat_idx", 0)) + 1
            uid = int(r.get("user_id") or 0)
            lines.append(
                f"**Seat {sidx}** <@{uid}> — {lbj.format_result_badge(r)}"
            )
        ui_v2.add_text(c_res, "\n".join(lines) if lines else "—")
        containers.append(c_res)

    if dealer_anim:
        c_dealer = ui_v2.new_container(accent=0xE67E22)
        ui_v2.add_text(
            c_dealer,
            "### 🎰 Dealer Turn\n> Revealing hole card and drawing…",
        )
        containers.append(c_dealer)

    if playing and rnd_phase == "playing":
        from Games.blackjack import can_double, can_insurance, can_split
        from modules.live_blackjack_tables import TURN_TIMEOUT_SECONDS

        c_act = ui_v2.new_container(accent=0xE74C3C)
        if turn:
            sidx, hand = turn
            turn_uid = int(table["seats"][sidx].get("user_id") or 0)
            is_my_turn = uid is not None and turn_uid == uid
            ui_v2.add_text(
                c_act,
                f"### Turn — Seat **{sidx + 1}** (<@{turn_uid}>)\n"
                + (
                    f"> **Your turn** — {TURN_TIMEOUT_SECONDS}s timeout → auto stand"
                    if is_my_turn
                    else f"> Waiting for <@{turn_uid}> — only they can act."
                ),
            )
            pseudo = {
                "deck": rnd["deck"],
                "dealer": rnd["dealer"],
                "hands": [hand],
                "cur": 0,
                "phase": "playing",
                "insurance_bet": hand.get("insurance_bet", 0),
            }
            acts: list[ui.Item] = [
                LiveBjActionButton(
                    table["id"], "hit", "Hit", discord.ButtonStyle.primary
                ),
                LiveBjActionButton(
                    table["id"], "stand", "Stand", discord.ButtonStyle.danger
                ),
            ]
            if can_double(pseudo):
                acts.append(
                    LiveBjActionButton(
                        table["id"], "double", "Double", discord.ButtonStyle.success
                    )
                )
            if can_insurance(pseudo):
                acts.append(
                    LiveBjActionButton(
                        table["id"],
                        "insurance",
                        "Insurance",
                        discord.ButtonStyle.secondary,
                    )
                )
            if can_split(pseudo):
                acts.append(
                    LiveBjActionButton(
                        table["id"], "split", "Split", discord.ButtonStyle.secondary
                    )
                )
            ui_v2.add_controls_to_container(c_act, acts)
        containers.append(c_act)

    return ui_v2.build_layout(*containers, timeout=None)
