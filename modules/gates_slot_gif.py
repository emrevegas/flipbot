"""Gates of Olympus style GIF — soft column cascade, glow wins, orb multiplier."""

from __future__ import annotations

import io
import math
import random

import aiohttp
from PIL import Image, ImageDraw

from Games.gates_slot import COLS, ROWS, SYMBOLS
from modules.image_gen import _font, _fmt, _load_emoji_rgba

GATES_GIF = "gates.gif"

W, H = 740, 560
HDR_H = 58
INFO_H = 44
PAD = 14

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

CELL_W, CELL_H = 98, 78
GAP = 8

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


def _ease_in_out_sine(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return -(math.cos(math.pi * t) - 1) / 2


async def render_gates_gif(
    *,
    username: str,
    bet: float,
    balance: float,
    grid: list[list[dict]],
    wins: list[dict],
    payout: float,
    won: bool,
    orb_mult: float = 1.0,
) -> io.BytesIO:
    id_to_sym = {s["id"]: s for s in SYMBOLS}
    pool_ids = [s["id"] for s in SYMBOLS]

    grid_w = COLS * CELL_W + (COLS - 1) * GAP
    grid_h = ROWS * CELL_H + (ROWS - 1) * GAP
    grid_x0 = (W - grid_w) // 2
    grid_y0 = HDR_H + 18
    footer_top = H - INFO_H
    meter_h = 86
    meter_y0 = footer_top - meter_h - 10
    meter_w = min(400, grid_w + 48)
    meter_x0 = (W - meter_w) // 2

    font_title = _font(17, bold=True)
    font_name = _font(13, bold=True)
    font_bet = _font(12, bold=True)
    font_lbl = _font(11, bold=True)
    font_amt = _font(24, bold=True)
    font_pts = _font(13, bold=True)
    font_sub = _font(12, bold=True)
    font_orb = _font(20, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    def _cell_xy(col: int, row: int) -> tuple[int, int]:
        return grid_x0 + col * (CELL_W + GAP), grid_y0 + row * (CELL_H + GAP)

    win_positions: set[tuple[int, int]] = set()
    for w in wins:
        for pos in w.get("positions", []):
            win_positions.add(tuple(pos))

    tokens: set[str] = set()
    for row in grid:
        for sym in row:
            tokens.add(sym.get("emoji", "?"))
    for _ in range(8):
        tokens.add(id_to_sym[random.choice(pool_ids)]["emoji"])

    emoji_cache: dict[str, Image.Image] = {}
    async with aiohttp.ClientSession() as session:
        for tok in tokens:
            if tok and tok not in emoji_cache:
                emoji_cache[tok] = await _load_emoji_rgba(str(tok), 52, session, transparent_bg=True)

    def _draw_bg(t_phase: float = 0.0) -> Image.Image:
        img = Image.new("RGB", (W, H), BG_BOT)
        draw = ImageDraw.Draw(img)
        pulse = 0.5 + 0.5 * math.sin(t_phase * math.pi * 2)
        for y in range(H):
            ratio = y / max(H - 1, 1)
            r = int(BG_TOP[0] * (1 - ratio) + BG_BOT[0] * ratio + pulse * 6)
            g = int(BG_TOP[1] * (1 - ratio) + BG_BOT[1] * ratio + pulse * 3)
            b = int(BG_TOP[2] * (1 - ratio) + BG_BOT[2] * ratio + pulse * 8)
            draw.line([(0, y), (W, y)], fill=(r, g, b))

        for px in (grid_x0 - 28, grid_x0 + grid_w + 12):
            draw.rounded_rectangle(
                [px, grid_y0 - 12, px + 16, footer_top - 8],
                radius=8,
                fill=(38, 28, 62),
                outline=(80, 60, 120),
                width=2,
            )
        draw.rounded_rectangle(
            [grid_x0 - 10, grid_y0 - 10, grid_x0 + grid_w + 10, grid_y0 + grid_h + 10],
            radius=16,
            fill=PANEL,
            outline=(90, 70, 130),
            width=2,
        )
        return img

    def _paste_cell(
        base: Image.Image,
        col: int,
        row: int,
        sym: dict,
        *,
        dim: float = 1.0,
        glow: float = 0.0,
        scale: float = 1.0,
    ) -> None:
        x, y = _cell_xy(col, row)
        cw, ch = int(CELL_W * scale), int(CELL_H * scale)
        ox = x + (CELL_W - cw) // 2
        oy = y + (CELL_H - ch) // 2

        layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        sid = sym.get("id", "")
        glow_col = _SYM_GLOW.get(sid, PURPLE)
        fill = (30, 22, 50)
        border = (70, 58, 100)
        if glow > 0:
            fill = tuple(min(255, int(c * (1 + glow * 0.35))) for c in fill)
            border = glow_col
        ld.rounded_rectangle([1, 1, cw - 2, ch - 2], radius=10, fill=fill, outline=border, width=2)

        if glow > 0.2:
            glow_layer = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow_layer)
            alpha = int(80 + 120 * glow)
            gd.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=12, outline=(*glow_col, alpha), width=4)
            layer = Image.alpha_composite(layer, glow_layer)

        em = emoji_cache.get(sym.get("emoji", "?"))
        if em is None:
            em = list(emoji_cache.values())[0]
        em = em.copy()
        esz = int(min(cw, ch) - 14)
        em = em.resize((esz, esz), Image.LANCZOS)
        ex = (cw - em.width) // 2
        ey = (ch - em.height) // 2
        layer.paste(em, (ex, ey), em)

        if dim < 1.0:
            r, g, b, a = layer.split()
            a = a.point(lambda p: int(p * dim) if p else 0)
            layer = Image.merge("RGBA", (r, g, b, a))

        base.paste(layer, (ox, oy), layer)

    def _draw_header(draw: ImageDraw.ImageDraw) -> None:
        title = "GATES OF OLYMPUS"
        tw = _tw(draw, title, font_title)
        draw.text(((W - tw) / 2, 10), title, font=font_title, fill=GOLD)
        sub = "8+ scatter pays anywhere"
        sw = _tw(draw, sub, font_lbl)
        draw.text(((W - sw) / 2, 32), sub, font=font_lbl, fill=MUTED)
        draw.text((PAD, HDR_H - 22), username[:18], font=font_name, fill=WHITE)
        bet_txt = f"Bet {_fmt(bet)}"
        bw = _tw(draw, bet_txt, font_bet)
        draw.text((W - PAD - bw, HDR_H - 22), bet_txt, font=font_bet, fill=CYAN)

    def _draw_meter(draw: ImageDraw.ImageDraw, *, show_result: bool, orb_show: float = 0.0) -> None:
        x1, y1 = meter_x0, meter_y0
        x2, y2 = x1 + meter_w, y1 + meter_h
        outline = GREEN if show_result and won else (RED if show_result else (60, 50, 90))
        draw.rounded_rectangle([x1, y1, x2, y2], radius=12, fill=(26, 18, 44), outline=outline, width=2)
        if not show_result:
            hint = "SPINNING..."
            hw = _tw(draw, hint, font_lbl)
            draw.text(((W - hw) / 2, y1 + (meter_h - 12) // 2), hint, font=font_lbl, fill=MUTED)
            return

        if won and payout > 0:
            core = f"+{_fmt(payout)}"
            col = GREEN
        else:
            core = f"-{_fmt(bet)}"
            col = RED
        pts = "pts"
        aw = _tw(draw, core, font_amt)
        pw = _tw(draw, pts, font_pts)
        ax = (W - aw - pw - 6) / 2
        draw.text((ax, y1 + 8), core, font=font_amt, fill=col)
        draw.text((ax + aw + 6, y1 + 16), pts, font=font_pts, fill=col)

        if orb_mult > 1.0 and orb_show > 0:
            orb_txt = f"x{int(orb_mult) if orb_mult == int(orb_mult) else orb_mult:g}"
            ow = _tw(draw, orb_txt, font_orb)
            draw.text((x2 - ow - 14, y1 + 10), orb_txt, font=font_orb, fill=GOLD)

        draw.line([(x1 + 12, y1 + 44), (x2 - 12, y1 + 44)], fill=(55, 45, 80))
        bal = f"{_fmt(balance)} pts"
        draw.text((x1 + 14, y1 + 52), "Balance", font=font_lbl, fill=MUTED)
        bvw = _tw(draw, bal, font_sub)
        draw.text((x2 - 14 - bvw, y1 + 52), bal, font=font_sub, fill=WHITE)

    def _draw_particles(overlay: Image.Image, phase: float) -> None:
        if not win_positions:
            return
        od = ImageDraw.Draw(overlay)
        rng = random.Random(42)
        for _ in range(18):
            cx = rng.randint(grid_x0, grid_x0 + grid_w)
            cy = rng.randint(grid_y0, grid_y0 + grid_h)
            drift = phase * 40 + rng.random() * 20
            py = cy - drift
            px = cx + math.sin(phase * 6 + rng.random()) * 8
            sz = 2 + rng.randint(0, 2)
            od.ellipse([px - sz, py - sz, px + sz, py + sz], fill=(255, 220, 120, 120))

    def _make_frame(
        revealed_cols: int,
        *,
        spin_col: int | None = None,
        spin_t: float = 0.0,
        show_result: bool = False,
        glow_phase: float = 0.0,
        bg_phase: float = 0.0,
        orb_show: float = 0.0,
        particle_phase: float = 0.0,
    ) -> Image.Image:
        img = _draw_bg(bg_phase).convert("RGBA")
        draw = ImageDraw.Draw(img)
        _draw_header(draw)

        for col in range(COLS):
            locked = col < revealed_cols
            spinning = spin_col is not None and col == spin_col and not locked
            for row in range(ROWS):
                sym = grid[row][col]
                if spinning:
                    flick = id_to_sym[random.choice(pool_ids)]
                    bounce = 0.92 + 0.08 * abs(math.sin(spin_t * math.pi * 4))
                    _paste_cell(img, col, row, flick, dim=0.75, scale=bounce)
                elif locked:
                    is_win = (row, col) in win_positions
                    glow = 0.0
                    if show_result and is_win:
                        glow = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(glow_phase * math.pi * 2))
                    _paste_cell(img, col, row, sym, glow=glow, scale=1.0 + (0.06 * glow if glow else 0))
                else:
                    _paste_cell(img, col, row, sym, dim=0.25)

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        if show_result and win_positions and particle_phase > 0:
            _draw_particles(overlay, particle_phase)
            img = Image.alpha_composite(img, overlay)

        draw = ImageDraw.Draw(img)
        _draw_meter(draw, show_result=show_result, orb_show=orb_show)
        return img.convert("P", palette=Image.ADAPTIVE, colors=256)

    frames: list[Image.Image] = []
    durations: list[int] = []

    spin_steps = 7
    for col in range(COLS):
        for step in range(spin_steps):
            t = (step + 1) / spin_steps
            eased = _ease_out_cubic(t)
            frames.append(_make_frame(col, spin_col=col, spin_t=eased, bg_phase=col * 0.08 + t * 0.05))
            durations.append(int(55 + 35 * (1 - eased)))

        frames.append(_make_frame(col + 1, bg_phase=(col + 1) * 0.08))
        durations.append(70)

    hold = 10 if wins else 4
    for i in range(hold):
        phase = i / max(hold - 1, 1)
        orb_show = _ease_in_out_sine(min(1.0, phase * 2)) if orb_mult > 1.0 else 0.0
        frames.append(
            _make_frame(
                COLS,
                show_result=True,
                glow_phase=phase,
                bg_phase=0.5 + phase * 0.3,
                orb_show=orb_show,
                particle_phase=phase if wins else 0.0,
            )
        )
        durations.append(110 if wins else 80)

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    buf.seek(0)
    return buf
