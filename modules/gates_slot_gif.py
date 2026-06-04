"""Gates of Olympus style GIF — multiplier orbs rendered on grid cells."""

from __future__ import annotations

import io
import math
import random

import aiohttp
from PIL import Image, ImageDraw

from Games.gates_slot import COLS, ROWS, SYMBOLS
from modules.image_gen import _font, _fmt, _load_emoji_rgba

GATES_GIF = "gates.gif"
FINAL_HOLD_MS = 10_000

W, H = 720, 620
HDR_H = 50
FOOTER_H = 36
METER_H = 72
METER_GAP = 18
PAD = 12
GAP = 6

BG_TOP = (28, 18, 52)
BG_BOT = (12, 8, 28)
PANEL = (22, 16, 42)
MUTED = (140, 130, 170)
WHITE = (250, 246, 255)
GOLD = (255, 200, 60)
PURPLE = (160, 100, 255)
GREEN = (72, 220, 130)
RED = (240, 90, 90)
CYAN = (100, 210, 255)

_SYM_GLOW: dict[str, tuple[int, int, int]] = {
    "ruby": (255, 80, 100),
    "emerald": (60, 220, 130),
    "sapphire": (70, 140, 255),
    "amethyst": (180, 100, 255),
    "topaz": (255, 190, 60),
    "goblet": (255, 210, 80),
    "ring": (255, 160, 220),
    "crown": (255, 220, 100),
    "scatter": (120, 200, 255),
}


def _ease_out_cubic(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def _layout() -> dict:
    meter_y0 = H - FOOTER_H - METER_H
    grid_bottom = meter_y0 - METER_GAP
    grid_top = HDR_H + 14
    avail_h = grid_bottom - grid_top
    avail_w = W - 2 * PAD - 40
    cell = min(
        (avail_w - (COLS - 1) * GAP) // COLS,
        (avail_h - (ROWS - 1) * GAP) // ROWS,
    )
    cell = max(64, cell)
    grid_w = COLS * cell + (COLS - 1) * GAP
    grid_h = ROWS * cell + (ROWS - 1) * GAP
    grid_x0 = (W - grid_w) // 2
    grid_y0 = grid_top + max(0, (avail_h - grid_h) // 2)
    meter_w = min(420, grid_w + 32)
    meter_x0 = (W - meter_w) // 2
    return {
        "cell": cell,
        "grid_x0": grid_x0,
        "grid_y0": grid_y0,
        "grid_w": grid_w,
        "grid_h": grid_h,
        "meter_x0": meter_x0,
        "meter_y0": meter_y0,
        "meter_w": meter_w,
    }


def _make_static_bg(layout: dict) -> Image.Image:
    img = Image.new("RGB", (W, H), BG_BOT)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / max(H - 1, 1)
        r = int(BG_TOP[0] * (1 - ratio) + BG_BOT[0] * ratio)
        g = int(BG_TOP[1] * (1 - ratio) + BG_BOT[1] * ratio)
        b = int(BG_TOP[2] * (1 - ratio) + BG_BOT[2] * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    gx0 = layout["grid_x0"]
    gy0 = layout["grid_y0"]
    gw = layout["grid_w"]
    gh = layout["grid_h"]
    my0 = layout["meter_y0"]
    for px in (gx0 - 22, gx0 + gw + 6):
        draw.rounded_rectangle(
            [px, gy0 - 8, px + 12, my0 - METER_GAP + 4],
            radius=6,
            fill=(38, 28, 62),
            outline=(80, 60, 120),
            width=2,
        )
    draw.rounded_rectangle(
        [gx0 - 8, gy0 - 8, gx0 + gw + 8, gy0 + gh + 8],
        radius=14,
        fill=PANEL,
        outline=(90, 70, 130),
        width=2,
    )
    return img


async def render_gates_gif(
    *,
    username: str,
    bet: float,
    balance: float,
    grid: list[list[dict]],
    wins: list[dict],
    payout: float,
    won: bool,
    grid_orbs: dict[tuple[int, int], int] | None = None,
    emoji_map: dict[str, str] | None = None,
) -> io.BytesIO:
    grid_orbs = dict(grid_orbs or {})
    id_to_sym = {s["id"]: s for s in SYMBOLS}
    pool_ids = [s["id"] for s in SYMBOLS]
    layout = _layout()
    cell = layout["cell"]
    em_size = max(36, cell - 16)
    orb_total = sum(grid_orbs.values()) if grid_orbs else 0

    font_title = _font(16, bold=True)
    font_name = _font(12, bold=True)
    font_bet = _font(11, bold=True)
    font_lbl = _font(10, bold=True)
    font_amt = _font(22, bold=True)
    font_pts = _font(12, bold=True)
    font_sub = _font(11, bold=True)
    font_orb = _font(11, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    def _cell_xy(col: int, row: int) -> tuple[int, int]:
        return (
            layout["grid_x0"] + col * (cell + GAP),
            layout["grid_y0"] + row * (cell + GAP),
        )

    def _tok(sym: dict) -> str:
        sid = sym.get("id", "")
        if emoji_map and sid in emoji_map:
            return emoji_map[sid]
        return sym.get("emoji", "?")

    win_positions: set[tuple[int, int]] = set()
    for w in wins:
        for pos in w.get("positions", []):
            win_positions.add(tuple(pos))

    tokens: set[str] = set()
    for row in grid:
        for sym in row:
            tokens.add(_tok(sym))
    for _ in range(6):
        sid = random.choice(pool_ids)
        tokens.add(emoji_map.get(sid, id_to_sym[sid]["emoji"]) if emoji_map else id_to_sym[sid]["emoji"])

    emoji_cache: dict[str, Image.Image] = {}
    async with aiohttp.ClientSession() as session:
        for tok in tokens:
            if tok and tok not in emoji_cache:
                emoji_cache[tok] = await _load_emoji_rgba(str(tok), em_size, session, transparent_bg=True)

    static_bg = _make_static_bg(layout)

    def _draw_orb_badge(layer: Image.Image, mult: int, *, pulse: float = 0.0) -> None:
        ld = ImageDraw.Draw(layer)
        sz = layer.size[0]
        badge = int(26 * (1.0 + 0.08 * pulse))
        bx = sz - badge - 4
        by = sz - badge - 4
        ld.ellipse(
            [bx, by, bx + badge, by + badge],
            fill=(255, 215, 70),
            outline=(255, 245, 200),
            width=2,
        )
        lbl = f"x{mult}"
        lw = _tw(ld, lbl, font_orb)
        ld.text((bx + (badge - lw) / 2, by + badge / 2 - 7), lbl, font=font_orb, fill=(45, 28, 8))

    def _paste_cell(
        base: Image.Image,
        col: int,
        row: int,
        sym: dict,
        *,
        dim: float = 1.0,
        glow: float = 0.0,
        scale: float = 1.0,
        show_orb: bool = True,
        orb_pulse: float = 0.0,
    ) -> None:
        x, y = _cell_xy(col, row)
        sz = int(cell * scale)
        ox = x + (cell - sz) // 2
        oy = y + (cell - sz) // 2

        layer = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        sid = sym.get("id", "")
        glow_col = _SYM_GLOW.get(sid, PURPLE)
        fill = (30, 22, 50)
        border = (70, 58, 100)
        orb_here = grid_orbs.get((row, col))
        if orb_here and show_orb:
            border = GOLD
            fill = (42, 32, 58)
        if glow > 0:
            border = glow_col
        ld.rounded_rectangle([1, 1, sz - 2, sz - 2], radius=10, fill=fill, outline=border, width=2)

        em = emoji_cache.get(_tok(sym))
        if em is None:
            em = list(emoji_cache.values())[0]
        em = em.copy()
        esz = sz - 12
        em = em.resize((esz, esz), Image.LANCZOS)
        layer.paste(em, ((sz - em.width) // 2, (sz - em.height) // 2), em)

        if orb_here and show_orb:
            _draw_orb_badge(layer, orb_here, pulse=orb_pulse)

        if dim < 1.0:
            r, g, b, a = layer.split()
            a = a.point(lambda p: int(p * dim) if p else 0)
            layer = Image.merge("RGBA", (r, g, b, a))

        base.paste(layer, (ox, oy), layer)

    def _draw_header(draw: ImageDraw.ImageDraw) -> None:
        title = "GATES OF OLYMPUS"
        tw = _tw(draw, title, font_title)
        draw.text(((W - tw) / 2, 8), title, font=font_title, fill=GOLD)
        draw.text((PAD, HDR_H - 18), username[:18], font=font_name, fill=WHITE)
        bet_txt = f"Bet {_fmt(bet)}"
        bw = _tw(draw, bet_txt, font_bet)
        draw.text((W - PAD - bw, HDR_H - 18), bet_txt, font=font_bet, fill=CYAN)

    def _draw_meter(draw: ImageDraw.ImageDraw, *, show_result: bool) -> None:
        x1, y1 = layout["meter_x0"], layout["meter_y0"]
        x2 = x1 + layout["meter_w"]
        outline = GREEN if show_result and won else (RED if show_result else (60, 50, 90))
        draw.rounded_rectangle([x1, y1, x2, y1 + METER_H], radius=10, fill=(26, 18, 44), outline=outline, width=2)
        if not show_result:
            hint = "SPINNING..."
            hw = _tw(draw, hint, font_lbl)
            draw.text(((W - hw) / 2, y1 + (METER_H - 10) // 2), hint, font=font_lbl, fill=MUTED)
            return

        if won and payout > 0:
            core, col = f"+{_fmt(payout)}", GREEN
        else:
            core, col = f"-{_fmt(bet)}", RED
        pts = "pts"
        aw = _tw(draw, core, font_amt)
        pw = _tw(draw, pts, font_pts)
        ax = (W - aw - pw - 6) / 2
        draw.text((ax, y1 + 6), core, font=font_amt, fill=col)
        draw.text((ax + aw + 6, y1 + 14), pts, font=font_pts, fill=col)

        if show_result and won and orb_total > 1:
            mx = f"Mult x{orb_total}"
            mw = _tw(draw, mx, font_lbl)
            draw.text((x2 - mw - 12, y1 + 8), mx, font=font_lbl, fill=GOLD)

        draw.line([(x1 + 10, y1 + 40), (x2 - 10, y1 + 40)], fill=(55, 45, 80))
        bal = f"{_fmt(balance)} pts"
        draw.text((x1 + 12, y1 + 48), "Balance", font=font_lbl, fill=MUTED)
        bvw = _tw(draw, bal, font_sub)
        draw.text((x2 - 12 - bvw, y1 + 48), bal, font=font_sub, fill=WHITE)

    def _make_frame(
        revealed_cols: int,
        *,
        spin_col: int | None = None,
        spin_t: float = 0.0,
        show_result: bool = False,
        glow_phase: float = 0.0,
    ) -> Image.Image:
        img = static_bg.copy().convert("RGBA")
        draw = ImageDraw.Draw(img)
        _draw_header(draw)

        for col in range(COLS):
            locked = col < revealed_cols
            spinning = spin_col is not None and col == spin_col and not locked
            for row in range(ROWS):
                sym = grid[row][col]
                if spinning:
                    flick = id_to_sym[random.choice(pool_ids)]
                    bounce = 0.94 + 0.06 * abs(math.sin(spin_t * math.pi * 3))
                    _paste_cell(img, col, row, flick, dim=0.7, scale=bounce, show_orb=False)
                elif locked:
                    glow = 0.0
                    if show_result and (row, col) in win_positions:
                        glow = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(glow_phase * math.pi * 2))
                    orb_pulse = glow_phase if (row, col) in grid_orbs else 0.0
                    _paste_cell(
                        img, col, row, sym,
                        glow=glow,
                        scale=1.0 + 0.05 * glow,
                        show_orb=True,
                        orb_pulse=orb_pulse,
                    )
                else:
                    _paste_cell(img, col, row, sym, dim=0.2, show_orb=False)

        draw = ImageDraw.Draw(img)
        _draw_meter(draw, show_result=show_result)
        return img.convert("P", palette=Image.ADAPTIVE, colors=256)

    frames: list[Image.Image] = []
    durations: list[int] = []

    spin_steps = 4
    for col in range(COLS):
        for step in range(spin_steps):
            t = (step + 1) / spin_steps
            frames.append(_make_frame(col, spin_col=col, spin_t=_ease_out_cubic(t)))
            durations.append(45)
        frames.append(_make_frame(col + 1))
        durations.append(55)

    result_frames = 4 if wins else 2
    for i in range(result_frames):
        phase = i / max(result_frames - 1, 1)
        frames.append(_make_frame(COLS, show_result=True, glow_phase=phase))
        durations.append(90)

    final = _make_frame(COLS, show_result=True, glow_phase=1.0)
    frames.append(final)
    durations.append(FINAL_HOLD_MS)

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        optimize=True,
        disposal=2,
    )
    buf.seek(0)
    return buf
