"""
Pillow-based card renderer.
Produces dark-themed cards similar to the reference screenshots.
"""
from __future__ import annotations

import asyncio
import io
import math
from pathlib import Path
from typing import Tuple

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config

FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"
CACHE_DIR = Path(__file__).parent.parent / "assets" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    candidates = [
        FONTS_DIR / ("bold.ttf" if bold else "regular.ttf"),
        FONTS_DIR / ("Bold.ttf" if bold else "Regular.ttf"),
        FONTS_DIR / "Inter-Bold.ttf" if bold else FONTS_DIR / "Inter-Regular.ttf",
    ]
    for p in candidates:
        if p.exists():
            f = ImageFont.truetype(str(p), size)
            _font_cache[key] = f
            return f
    # fall back to default
    f = ImageFont.load_default()
    return f


# ── helpers ───────────────────────────────────────────────────────────────────

def _rounded_rect(draw: ImageDraw.ImageDraw, xy: tuple, radius: int, fill, border=None, border_width=2):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill,
                           outline=border, width=border_width)


def _circle_avatar(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


async def _fetch_avatar(url: str, size: int = 100) -> Image.Image | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as r:
                if r.status == 200:
                    data = await r.read()
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
                    return _circle_avatar(img, size)
    except Exception:
        pass
    return None


def _default_avatar(size: int, color=(56, 189, 248)) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((0, 0, size, size), fill=color)
    # simple silhouette
    draw.ellipse((size // 4, size // 6, size * 3 // 4, size * 2 // 3), fill=(200, 220, 255))
    draw.ellipse((size // 8, size // 2, size * 7 // 8, size), fill=(200, 220, 255))
    return img


def _pts_to_usd(pts: float) -> float:
    return pts / config.POINTS_PER_USD


def _fmt(n: float) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.2f}K"
    return f"{n:.2f}"


# ── Balance Card ──────────────────────────────────────────────────────────────

async def render_balance_card(
    username: str,
    balance: float,
    *,
    avatar_url: str | None = None,
    total_wagered: float = 0,
    total_deposited: float = 0,
) -> io.BytesIO:
    W, H = 560, 200
    RADIUS = 18
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    ACCENT = config.CARD_ACCENT_COLOR
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    BLUE = config.CARD_HIGHLIGHT
    GOLD = config.CARD_GOLD

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # background card
    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, BORDER, 2)

    # left accent stripe
    draw.rounded_rectangle([0, 0, 6, H - 1], radius=RADIUS, fill=ACCENT)

    # avatar
    AVATAR_SIZE = 76
    AVATAR_X, AVATAR_Y = 28, (H - AVATAR_SIZE) // 2

    avatar_img = None
    if avatar_url:
        avatar_img = await _fetch_avatar(avatar_url, AVATAR_SIZE)
    if avatar_img is None:
        avatar_img = _default_avatar(AVATAR_SIZE)

    # avatar ring
    ring_size = AVATAR_SIZE + 6
    ring_img = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring_img)
    ring_draw.ellipse((0, 0, ring_size, ring_size), fill=ACCENT)
    img.paste(ring_img, (AVATAR_X - 3, AVATAR_Y - 3), ring_img)
    img.paste(avatar_img, (AVATAR_X, AVATAR_Y), avatar_img)

    TEXT_X = AVATAR_X + AVATAR_SIZE + 22

    # username label
    draw.text((TEXT_X, 32), "Balance", font=_font(13), fill=MUTED)

    # username
    draw.text((TEXT_X, 50), username, font=_font(20, bold=True), fill=WHITE)

    # big balance
    pts_str = f"{_fmt(balance)} pts"
    draw.text((TEXT_X, 80), pts_str, font=_font(34, bold=True), fill=BLUE)

    # usd equivalent
    usd = _pts_to_usd(balance)
    draw.text((TEXT_X, 122), f"${usd:.2f} USD", font=_font(15), fill=MUTED)

    # divider
    draw.line([(TEXT_X, 148), (W - 24, 148)], fill=BORDER, width=1)

    # stats row
    stats = [
        ("WAGERED", f"{_fmt(total_wagered)} pts"),
        ("DEPOSITED", f"{_fmt(total_deposited)} pts"),
    ]
    col_w = (W - TEXT_X - 24) // len(stats)
    for i, (label, val) in enumerate(stats):
        cx = TEXT_X + i * col_w
        draw.text((cx, 158), label, font=_font(11), fill=MUTED)
        draw.text((cx, 174), val, font=_font(14, bold=True), fill=WHITE)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Affiliate Card ────────────────────────────────────────────────────────────

async def render_affiliate_card(
    username: str,
    code: str,
    referrals: int,
    net_earnings: float,
    claimable: float,
    total_claimed: float,
    today_earning: float = 0.0,
) -> io.BytesIO:
    """Affiliate card — commission = 10% of (daily deposits − withdrawals) per referred user."""
    W, H = 560, 310
    RADIUS = 18
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    GOLD = config.CARD_GOLD
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    GREEN = config.CARD_ACCENT_COLOR
    BLUE = config.CARD_HIGHLIGHT

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # card bg + gold border
    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, GOLD, 2)

    # header band
    _rounded_rect(draw, (0, 0, W - 1, 52), RADIUS, (20, 18, 8), None)
    draw.rectangle([0, RADIUS, W, 52], fill=(20, 18, 8))
    draw.text((22, 14), "AFFILIATE PROGRAM", font=_font(17, bold=True), fill=GOLD)

    # username right-aligned in header
    un_w = draw.textlength(username, font=_font(13))
    draw.text((W - 22 - un_w, 18), username, font=_font(13), fill=MUTED)

    # code badge
    code_upper = code.upper()
    badge_x = 22
    draw.text((badge_x, 64), "YOUR CODE", font=_font(10), fill=MUTED)
    cw = draw.textlength(code_upper, font=_font(22, bold=True)) + 28
    _rounded_rect(draw, (badge_x - 4, 80, badge_x + cw, 114), 8, (20, 30, 50), BLUE, 1)
    draw.text((badge_x + 10, 84), code_upper, font=_font(22, bold=True), fill=BLUE)

    # rate label
    rate_pct = int(config.AFFILIATE_NET_RATE * 100)
    rate_txt = f"{rate_pct}% of (daily dep − wd)"
    draw.text((badge_x + cw + 14, 90), rate_txt, font=_font(11), fill=MUTED)

    # row 1: referrals / today's earning
    col2_w = (W - 44) // 2
    row1_y = 126
    stats1 = [
        ("REFERRED USERS", str(referrals), WHITE),
        ("TODAY'S EARN (UNSETTLED)", f"+{_fmt(today_earning)} pts", GREEN),
    ]
    for i, (label, val, color) in enumerate(stats1):
        cx = 22 + i * col2_w
        _rounded_rect(draw, (cx, row1_y, cx + col2_w - 8, row1_y + 50), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 10, row1_y + 7), label, font=_font(10), fill=MUTED)
        draw.text((cx + 10, row1_y + 23), val, font=_font(17, bold=True), fill=color)

    # row 2: net earnings / claimable
    row2_y = 186
    stats2 = [
        (f"NET EARNINGS ({rate_pct}%)", f"{_fmt(net_earnings)} pts", GREEN),
        ("CLAIMABLE NOW", f"{_fmt(claimable)} pts", GOLD),
    ]
    for i, (label, val, color) in enumerate(stats2):
        cx = 22 + i * col2_w
        _rounded_rect(draw, (cx, row2_y, cx + col2_w - 8, row2_y + 50), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 10, row2_y + 7), label, font=_font(10), fill=MUTED)
        draw.text((cx + 10, row2_y + 23), val, font=_font(17, bold=True), fill=color)

    # row 3: total claimed (full width)
    row3_y = 246
    _rounded_rect(draw, (22, row3_y, W - 22, row3_y + 50), 8, (18, 25, 40), BORDER, 1)
    draw.text((32, row3_y + 7), "TOTAL CLAIMED", font=_font(10), fill=MUTED)
    draw.text((32, row3_y + 23), f"{_fmt(total_claimed)} pts", font=_font(17, bold=True), fill=WHITE)
    settle_note = "Settled daily 00:00 UTC"
    sn_w = draw.textlength(settle_note, font=_font(10))
    draw.text((W - 22 - sn_w, row3_y + 30), settle_note, font=_font(10), fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Rakeback Card ─────────────────────────────────────────────────────────────

async def render_rakeback_card(
    username: str,
    accumulated: float,
    total_claimed: float,
    total_wagered: float,
    tier_name: str,
    tier_rate: float,
    next_tier_name: str | None = None,
    next_tier_min: float | None = None,
) -> io.BytesIO:
    W, H = 560, 230
    RADIUS = 18
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    PURPLE = (168, 85, 247)
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    GREEN = config.CARD_ACCENT_COLOR
    BLUE = config.CARD_HIGHLIGHT

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, BORDER, 2)
    draw.rounded_rectangle([0, 0, 6, H - 1], radius=RADIUS, fill=PURPLE)

    # title
    draw.text((24, 18), "RAKEBACK", font=_font(14, bold=True), fill=PURPLE)
    draw.text((24 + draw.textlength("RAKEBACK", font=_font(14, bold=True)) + 10, 20),
              f"({int(tier_rate * 100)}% — {tier_name})", font=_font(13), fill=MUTED)

    # big claimable
    draw.text((24, 46), "Available to Claim", font=_font(12), fill=MUTED)
    draw.text((24, 64), f"{_fmt(accumulated)} pts", font=_font(36, bold=True), fill=GREEN)
    usd = _pts_to_usd(accumulated)
    draw.text((24, 108), f"${usd:.4f} USD", font=_font(14), fill=MUTED)

    # divider
    draw.line([(24, 132), (W - 24, 132)], fill=BORDER, width=1)

    # stats
    stats = [
        ("TOTAL WAGERED", f"{_fmt(total_wagered)} pts"),
        ("TOTAL CLAIMED", f"{_fmt(total_claimed)} pts"),
    ]
    if next_tier_name and next_tier_min:
        remaining = max(0, next_tier_min - total_wagered)
        stats.append((f"TO {next_tier_name.upper()}", f"{_fmt(remaining)} pts left"))

    col_w = (W - 48) // len(stats)
    for i, (label, val) in enumerate(stats):
        cx = 24 + i * col_w
        draw.text((cx, 142), label, font=_font(11), fill=MUTED)
        draw.text((cx, 158), val, font=_font(14, bold=True), fill=WHITE)

    # progress bar to next tier
    if next_tier_min and total_wagered < next_tier_min:
        bar_x, bar_y = 24, 195
        bar_w = W - 48
        bar_h = 10
        pct = min(1.0, total_wagered / next_tier_min)
        draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=5, fill=BORDER)
        if pct > 0:
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + int(bar_w * pct), bar_y + bar_h],
                radius=5,
                fill=PURPLE,
            )
        draw.text((bar_x, bar_y + 14), f"{pct*100:.1f}% to {next_tier_name}", font=_font(11), fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Rakeback Card ─────────────────────────────────────────────────────────────

async def render_leaderboard_card(rows: list[dict], bot) -> io.BytesIO:
    W = 520
    ROW_H = 48
    H = 70 + ROW_H * len(rows) + 20
    RADIUS = 16
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    GOLD = config.CARD_GOLD
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    BLUE = config.CARD_HIGHLIGHT
    medals = ["🥇", "🥈", "🥉"]

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, BORDER, 2)

    draw.text((22, 18), "LEADERBOARD", font=_font(16, bold=True), fill=GOLD)
    draw.text((22 + draw.textlength("LEADERBOARD", font=_font(16, bold=True)) + 10, 20),
              "Top Balances", font=_font(13), fill=MUTED)
    draw.line([(22, 48), (W - 22, 48)], fill=BORDER, width=1)

    for i, row in enumerate(rows):
        y = 56 + i * ROW_H
        rank_color = [GOLD, (192, 192, 192), (205, 127, 50)][i] if i < 3 else MUTED
        # rank
        draw.text((22, y + 14), f"#{i+1}", font=_font(14, bold=True), fill=rank_color)
        # username
        uname = str(row.get("username") or row.get("user_id", "?"))[:20]
        draw.text((70, y + 14), uname, font=_font(15, bold=(i < 3)), fill=WHITE)
        # balance
        bal = float(row.get("balance", 0))
        bal_str = f"{_fmt(bal)} pts"
        bw = draw.textlength(bal_str, font=_font(15, bold=True))
        draw.text((W - 22 - bw, y + 14), bal_str, font=_font(15, bold=True), fill=BLUE)
        # separator
        if i < len(rows) - 1:
            draw.line([(22, y + ROW_H - 1), (W - 22, y + ROW_H - 1)], fill=BORDER, width=1)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Game Result Card ───────────────────────────────────────────────────────────

async def render_game_result_card(
    game: str,
    result: str,
    amount: float,
    payout: float,
    details: dict | None = None,
) -> io.BytesIO:
    W, H = 560, 220
    RADIUS = 18
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    ACCENT = config.CARD_ACCENT_COLOR
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    BLUE = config.CARD_HIGHLIGHT
    GOLD = config.CARD_GOLD
    RED = (231, 76, 60)

    won = payout > amount
    tie = abs(payout - amount) < 0.001
    result_color = ACCENT if (won and not tie) else (GOLD if tie else RED)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, BORDER, 2)
    stripe_color = ACCENT if (won and not tie) else (GOLD if tie else RED)
    draw.rounded_rectangle([0, 0, 6, H - 1], radius=RADIUS, fill=stripe_color)

    draw.text((24, 18), game.upper(), font=_font(14, bold=True), fill=MUTED)
    draw.text((24, 42), result.upper(), font=_font(38, bold=True), fill=result_color)

    draw.line([(24, 100), (W - 24, 100)], fill=BORDER, width=1)

    net = payout - amount
    net_str = f"+{_fmt(net)} pts" if net >= 0 else f"{_fmt(net)} pts"
    net_color = ACCENT if net >= 0 else RED

    stats_row = [
        ("BET",    f"{_fmt(amount)} pts", WHITE),
        ("PAYOUT", f"{_fmt(payout)} pts", BLUE),
        ("NET",    net_str,               net_color),
    ]
    col_w = (W - 48) // len(stats_row)
    for i, (label, val, color) in enumerate(stats_row):
        cx = 24 + i * col_w
        draw.text((cx, 112), label, font=_font(11), fill=MUTED)
        draw.text((cx, 128), val,   font=_font(16, bold=True), fill=color)

    if details:
        y = 162
        for k, v in list(details.items())[:3]:
            draw.text((24, y), f"{k}:", font=_font(12), fill=MUTED)
            draw.text((120, y), str(v), font=_font(12, bold=True), fill=WHITE)
            y += 18

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Mines Grid Card ────────────────────────────────────────────────────────────

def render_mines_grid(
    grid_size: int,
    mines: set[int],
    revealed: set[int],
    bet: float,
    multiplier: float,
    game_over: bool = False,
) -> io.BytesIO:
    COLS = grid_size
    ROWS = grid_size
    CELL = 60
    PAD = 12
    HEADER = 70
    W = COLS * (CELL + PAD) + PAD
    H = HEADER + ROWS * (CELL + PAD) + PAD
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    ACCENT = config.CARD_ACCENT_COLOR
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    BLUE = config.CARD_HIGHLIGHT
    MINE_COLOR = (80, 20, 20)
    SAFE_COLOR = (20, 50, 30)
    HIDDEN_COLOR = (20, 30, 50)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _rounded_rect(draw, (0, 0, W - 1, H - 1), 16, BG, BORDER, 2)

    draw.text((PAD, 10), "MINES", font=_font(14, bold=True), fill=ACCENT)
    draw.text((PAD, 30), f"Bet: {_fmt(bet)} pts", font=_font(12), fill=MUTED)
    mult_str = f"{multiplier:.2f}x"
    mult_w = draw.textlength(mult_str, font=_font(18, bold=True))
    draw.text((W - PAD - mult_w - 4, 26), mult_str, font=_font(18, bold=True), fill=BLUE)

    for idx in range(COLS * ROWS):
        col = idx % COLS
        row = idx // COLS
        x = PAD + col * (CELL + PAD)
        y = HEADER + row * (CELL + PAD)

        if idx in revealed:
            color = MINE_COLOR if idx in mines else SAFE_COLOR
            label = "X" if idx in mines else "OK"
        elif game_over and idx in mines:
            color = (60, 15, 15)
            label = "X"
        else:
            color = HIDDEN_COLOR
            label = ""

        _rounded_rect(draw, (x, y, x + CELL, y + CELL), 8, color, BORDER, 1)
        cell_label = f"{chr(65 + row)}{col + 1}"
        lw = draw.textlength(cell_label, font=_font(10))
        draw.text((x + (CELL - lw) // 2, y + 4), cell_label, font=_font(10), fill=MUTED)
        if label:
            lw2 = draw.textlength(label, font=_font(14, bold=True))
            draw.text((x + (CELL - lw2) // 2, y + 26), label, font=_font(14, bold=True), fill=WHITE)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Slots Card ─────────────────────────────────────────────────────────────────

def render_slots_card(reels: list[str], bet: float, win_amount: float) -> io.BytesIO:
    W, H = 480, 200
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    ACCENT = config.CARD_ACCENT_COLOR
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _rounded_rect(draw, (0, 0, W - 1, H - 1), 16, BG, BORDER, 2)
    draw.text((22, 14), "SLOTS", font=_font(14, bold=True), fill=ACCENT)

    reel_w = 80
    reel_h = 80
    reel_gap = 20
    start_x = (W - (len(reels) * reel_w + (len(reels) - 1) * reel_gap)) // 2
    reel_y = 44

    for i, sym in enumerate(reels):
        rx = start_x + i * (reel_w + reel_gap)
        _rounded_rect(draw, (rx, reel_y, rx + reel_w, reel_y + reel_h), 10, (20, 30, 50), BORDER, 2)
        sw = draw.textlength(sym, font=_font(36))
        draw.text((rx + (reel_w - sw) // 2, reel_y + 20), sym, font=_font(36), fill=WHITE)

    if win_amount > 0:
        draw.text((22, 144), f"WIN! +{_fmt(win_amount)} pts", font=_font(18, bold=True), fill=ACCENT)
    else:
        draw.text((22, 144), "No win", font=_font(16), fill=MUTED)

    draw.text((W - 160, 148), f"Bet: {_fmt(bet)} pts", font=_font(13), fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Stats Card ─────────────────────────────────────────────────────────────────

def render_stats_card(username: str, stats: dict, wagered: float = 0) -> io.BytesIO:
    W, H = 560, 260
    RADIUS = 18
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    ACCENT = config.CARD_ACCENT_COLOR
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    BLUE = config.CARD_HIGHLIGHT
    GOLD = config.CARD_GOLD
    RED = (231, 76, 60)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, BORDER, 2)
    draw.rounded_rectangle([0, 0, 6, H - 1], radius=RADIUS, fill=BLUE)

    draw.text((24, 18), "PLAYER STATS", font=_font(14, bold=True), fill=MUTED)
    draw.text((24, 38), username[:24], font=_font(22, bold=True), fill=WHITE)

    played   = int(stats.get("games_played", 0))
    wins     = int(stats.get("wins", 0))
    losses   = int(stats.get("losses", 0))
    wr       = (wins / played * 100) if played else 0
    profit   = float(stats.get("total_profit", 0))
    big_win  = float(stats.get("biggest_win", 0))
    big_loss = float(stats.get("biggest_loss", 0))

    draw.line([(24, 74), (W - 24, 74)], fill=BORDER, width=1)

    col_w = (W - 48) // 4
    row1 = [
        ("GAMES PLAYED", str(played),     WHITE),
        ("WINS",         str(wins),        ACCENT),
        ("LOSSES",       str(losses),      RED),
        ("WIN RATE",     f"{wr:.1f}%",     GOLD),
    ]
    for i, (label, val, color) in enumerate(row1):
        cx = 24 + i * col_w
        _rounded_rect(draw, (cx, 82, cx + col_w - 6, 130), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 8, 90),  label, font=_font(10), fill=MUTED)
        draw.text((cx + 8, 106), val,   font=_font(16, bold=True), fill=color)

    row2 = [
        ("TOTAL WAGERED", f"{_fmt(wagered)} pts",                                                WHITE),
        ("PROFIT/LOSS",   (f"+{_fmt(profit)}" if profit >= 0 else _fmt(profit)) + " pts",       ACCENT if profit >= 0 else RED),
        ("BIGGEST WIN",   f"{_fmt(big_win)} pts",                                                GOLD),
        ("BIGGEST LOSS",  f"{_fmt(big_loss)} pts",                                               RED),
    ]
    for i, (label, val, color) in enumerate(row2):
        cx = 24 + i * col_w
        _rounded_rect(draw, (cx, 140, cx + col_w - 6, 188), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 8, 148), label, font=_font(10), fill=MUTED)
        draw.text((cx + 8, 164), val,   font=_font(13, bold=True), fill=color)

    bar_x, bar_y = 24, 204
    bar_w = W - 48
    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + 10], radius=5, fill=BORDER)
    if wr > 0:
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + int(bar_w * min(wr / 100, 1.0)), bar_y + 10],
            radius=5, fill=ACCENT,
        )
    draw.text((bar_x, bar_y + 14), f"Win rate: {wr:.1f}%", font=_font(11), fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Race Leaderboard Card ──────────────────────────────────────────────────────

def render_race_card(rows: list[dict]) -> io.BytesIO:
    W = 520
    ROW_H = 48
    H = 70 + ROW_H * max(len(rows), 1) + 20
    RADIUS = 16
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    GOLD = config.CARD_GOLD
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    ACCENT = config.CARD_ACCENT_COLOR

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, BORDER, 2)

    draw.text((22, 18), "RACE STANDINGS", font=_font(16, bold=True), fill=GOLD)
    draw.line([(22, 48), (W - 22, 48)], fill=BORDER, width=1)

    if not rows:
        draw.text((22, 62), "No entries yet.", font=_font(14), fill=MUTED)
    else:
        for i, row in enumerate(rows):
            y = 56 + i * ROW_H
            rank_color = [GOLD, (192, 192, 192), (205, 127, 50)][i] if i < 3 else MUTED
            draw.text((22, y + 14), f"#{i+1}", font=_font(14, bold=True), fill=rank_color)
            uname = str(row.get("username") or row.get("user_id", "?"))[:20]
            draw.text((70, y + 14), uname, font=_font(15, bold=(i < 3)), fill=WHITE)
            wag_str = f"{_fmt(float(row.get('wagered', 0)))} pts wagered"
            ww = draw.textlength(wag_str, font=_font(13))
            draw.text((W - 22 - ww, y + 16), wag_str, font=_font(13), fill=ACCENT)
            if i < len(rows) - 1:
                draw.line([(22, y + ROW_H - 1), (W - 22, y + ROW_H - 1)], fill=BORDER, width=1)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Blackjack GIF ──────────────────────────────────────────────────────────────

def _draw_card_on_img(img: "Image.Image", x: int, y: int, card: str, face_down: bool = False):
    from PIL import ImageDraw as _ID
    draw = _ID.Draw(img)
    W, H = 58, 84
    bg = (20, 30, 50) if face_down else (240, 240, 240)
    draw.rounded_rectangle([x, y, x + W, y + H], radius=6, fill=bg, outline=(60, 80, 120), width=2)
    if face_down:
        draw.rounded_rectangle([x + 4, y + 4, x + W - 4, y + H - 4], radius=4, fill=(30, 50, 80))
        return
    suit = card[-1] if card else "?"
    rank = card[:-1] if len(card) > 1 else card
    red_suits = {"♥", "♦"}
    color = (200, 30, 30) if suit in red_suits else (10, 10, 10)
    font_sm = _font(13)
    font_lg = _font(20)
    draw.text((x + 4, y + 4), rank, font=font_sm, fill=color)
    draw.text((x + 4, y + 18), suit, font=font_sm, fill=color)
    try:
        fw = draw.textlength(suit, font=font_lg)
    except Exception:
        fw = 14
    draw.text((x + (W - fw) // 2, y + H // 2 - 12), suit, font=font_lg, fill=color)


async def render_bj_gif(
    player_hand: list,
    dealer_hand: list,
    reveal_dealer: bool = False,
    result_text: str = "",
) -> io.BytesIO:
    """Create animated GIF showing cards being dealt one by one."""
    from PIL import Image as _Img, ImageDraw as _ID

    W, H = 520, 260
    BG = (13, 17, 30)
    WHITE = (255, 255, 255)
    MUTED = (140, 150, 170)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    CW, CH = 60, 86
    GAP = 8

    def _hv(hand):
        total, aces = 0, 0
        for c in hand:
            if c == "?":
                continue
            r = c[:-1] if len(c) > 1 else c
            if r in ("J", "Q", "K"):
                total += 10
            elif r == "A":
                total += 11
                aces += 1
            else:
                try:
                    total += int(r)
                except Exception:
                    pass
        while total > 21 and aces:
            total -= 10
            aces -= 1
        return total

    font_label = _font(13)
    font_val = _font(18)
    font_result = _font(32, bold=True)

    # Build step-by-step deal sequence (interleaved p/d cards)
    all_steps: list[tuple[list, list]] = []
    ph, dh = [], []
    max_cards = max(len(player_hand), len(dealer_hand))
    for i in range(max_cards):
        if i < len(player_hand):
            ph = player_hand[: i + 1]
        if i < len(dealer_hand):
            dh = dealer_hand[: i + 1]
        all_steps.append((list(ph), list(dh)))

    if result_text:
        all_steps.append((list(player_hand), list(dealer_hand)))

    frames: list = []
    durations: list[int] = []

    for step_i, (ph_s, dh_s) in enumerate(all_steps):
        img = _Img.new("RGB", (W, H), BG)
        draw = _ID.Draw(img)

        # Dealer area
        draw.text((20, 12), "DEALER", font=font_label, fill=MUTED)
        visible_dealer = [c for c in dh_s if c != "?"]
        dv_str = str(_hv(visible_dealer)) if "?" not in dh_s else "?"
        draw.text((90, 12), dv_str, font=font_label, fill=WHITE)

        for ci, card in enumerate(dh_s):
            cx = 20 + ci * (CW + GAP)
            is_hidden = card == "?" and not reveal_dealer
            _draw_card_on_img(img, cx, 34, card if not is_hidden else "?", face_down=is_hidden)

        # Divider
        draw.line([(20, 140), (W - 20, 140)], fill=(30, 40, 60), width=1)

        # Player area
        draw.text((20, 148), "YOU", font=font_label, fill=MUTED)
        pv = _hv(ph_s)
        pv_color = RED if pv > 21 else WHITE
        draw.text((60, 148), str(pv), font=font_label, fill=pv_color)

        for ci, card in enumerate(ph_s):
            cx = 20 + ci * (CW + GAP)
            _draw_card_on_img(img, cx, 168, card, face_down=False)

        # Result overlay on last frame
        is_last = step_i == len(all_steps) - 1
        if result_text and is_last:
            result_colors = {
                "BLACKJACK": GOLD, "WIN": GREEN, "PUSH": (88, 101, 242),
                "BUST": RED, "LOSS": RED,
            }
            rc = result_colors.get(result_text, WHITE)
            overlay = _Img.new("RGBA", (W, H), (0, 0, 0, 0))
            od = _ID.Draw(overlay)
            od.rectangle([0, 0, W, H], fill=(0, 0, 0, 140))
            img_rgba = img.convert("RGBA")
            img = _Img.alpha_composite(img_rgba, overlay).convert("RGB")
            draw = _ID.Draw(img)
            try:
                fw = draw.textlength(result_text, font=font_result)
            except Exception:
                fw = len(result_text) * 18
            draw.text(((W - fw) // 2, H // 2 - 20), result_text, font=font_result, fill=rc)

        frames.append(img)
        durations.append(2000 if is_last else 400)

    if not frames:
        frames = [_Img.new("RGB", (W, H), BG)]
        durations = [500]

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    buf.seek(0)
    return buf


# ── Case Opening Card ──────────────────────────────────────────────────────────

def render_case_open_card(item_name: str, item_value: float, case_name: str) -> io.BytesIO:
    W, H = 480, 180
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    GOLD = config.CARD_GOLD
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    BLUE = config.CARD_HIGHLIGHT

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _rounded_rect(draw, (0, 0, W - 1, H - 1), 16, BG, GOLD, 2)
    draw.rounded_rectangle([0, 0, 6, H - 1], radius=16, fill=GOLD)

    draw.text((22, 14), f"CASE: {case_name.upper()}", font=_font(12), fill=MUTED)
    draw.text((22, 36), "YOU RECEIVED", font=_font(13, bold=True), fill=GOLD)
    draw.text((22, 60), item_name[:30], font=_font(28, bold=True), fill=WHITE)
    val_str = f"{_fmt(item_value)} pts"
    draw.text((22, 100), val_str, font=_font(22, bold=True), fill=BLUE)
    usd = item_value / 100
    draw.text((22, 132), f"approx ${usd:.2f} USD", font=_font(13), fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Blackjack GIF ──────────────────────────────────────────────────────────────

def _draw_playing_card(img: Image.Image, x: int, y: int, card: str, face_down: bool = False):
    """Draw a single playing card at (x, y) onto img."""
    draw = ImageDraw.Draw(img)
    CW, CH = 58, 84
    if face_down:
        draw.rounded_rectangle([x, y, x + CW, y + CH], radius=6,
                                fill=(25, 45, 80), outline=(60, 90, 140), width=2)
        draw.rounded_rectangle([x + 4, y + 4, x + CW - 4, y + CH - 4], radius=4,
                                fill=(18, 35, 65))
        return

    draw.rounded_rectangle([x, y, x + CW, y + CH], radius=6,
                            fill=(238, 238, 238), outline=(180, 180, 180), width=1)

    suit = card[-1] if card and len(card) >= 1 else "?"
    rank = card[:-1] if len(card) > 1 else card
    ink = (195, 30, 30) if suit in {"♥", "♦"} else (15, 15, 15)

    fsm = _font(12)
    flg = _font(20, bold=True)
    draw.text((x + 4, y + 3), rank, font=fsm, fill=ink)
    draw.text((x + 4, y + 16), suit, font=fsm, fill=ink)
    try:
        sw = draw.textlength(suit, font=flg)
    except Exception:
        sw = 16
    draw.text((x + (CW - sw) // 2, y + CH // 2 - 13), suit, font=flg, fill=ink)


def _bj_hand_value(hand: list[str]) -> int:
    total, aces = 0, 0
    for card in hand:
        if card in ("?", ""):
            continue
        rank = card[:-1] if len(card) > 1 else card
        if rank in ("J", "Q", "K"):
            total += 10
        elif rank == "A":
            total += 11
            aces += 1
        else:
            try:
                total += int(rank)
            except ValueError:
                pass
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


async def render_bj_gif(
    player_hand: list[str],
    dealer_hand: list[str],
    reveal_dealer: bool = False,
    result_text: str = "",
) -> io.BytesIO:
    """Animated GIF: cards dealt step-by-step, optional result overlay on last frame."""
    W, H = 520, 260
    BG = (13, 17, 30)
    WHITE = (255, 255, 255)
    MUTED = (140, 150, 170)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    BLUE = (88, 101, 242)
    CW, CH = 58, 84
    GAP = 8

    RESULT_COLORS = {
        "BLACKJACK": GOLD, "WIN": GREEN, "PUSH": BLUE,
        "BUST": RED, "LOSS": RED,
    }

    flabel = _font(12)
    fval   = _font(16, bold=True)
    fresult = _font(34, bold=True)

    def _make_frame(p_cards: list[str], d_cards: list[str], final: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        draw.text((20, 10), "DEALER", font=flabel, fill=MUTED)
        dv = _bj_hand_value([c for c in d_cards if c != "?"])
        draw.text((80, 10), "?" if "?" in d_cards else str(dv), font=fval, fill=WHITE)

        for ci, card in enumerate(d_cards):
            face_dn = (card == "?" and not reveal_dealer)
            _draw_playing_card(img, 20 + ci * (CW + GAP), 30, card if not face_dn else "?", face_down=face_dn)

        draw.line([(20, 138), (W - 20, 138)], fill=(30, 40, 60), width=1)

        draw.text((20, 146), "YOUR HAND", font=flabel, fill=MUTED)
        pv = _bj_hand_value(p_cards)
        draw.text((110, 146), str(pv), font=fval, fill=(RED if pv > 21 else WHITE))

        for ci, card in enumerate(p_cards):
            _draw_playing_card(img, 20 + ci * (CW + GAP), 165, card)

        if final and result_text:
            rc = RESULT_COLORS.get(result_text, WHITE)
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle([0, 0, W, H], fill=(0, 0, 0, 150))
            merged = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw2 = ImageDraw.Draw(merged)
            try:
                fw = draw2.textlength(result_text, font=fresult)
            except Exception:
                fw = len(result_text) * 22
            draw2.text(((W - fw) // 2, H // 2 - 20), result_text, font=fresult, fill=rc)
            return merged

        return img

    frames: list[Image.Image] = []
    durations: list[int] = []

    # Interleave deal: p[0], d[0], p[1], d[1]
    p_so_far: list[str] = []
    d_so_far: list[str] = []
    for i in range(max(len(player_hand), len(dealer_hand))):
        if i < len(player_hand):
            p_so_far = player_hand[: i + 1]
        if i < len(dealer_hand):
            d_so_far = dealer_hand[: i + 1]
        frames.append(_make_frame(list(p_so_far), list(d_so_far)))
        durations.append(350)

    # Final frame
    frames.append(_make_frame(list(player_hand), list(dealer_hand), final=True))
    durations.append(2500 if result_text else 1000)

    if not frames:
        frames = [Image.new("RGB", (W, H), BG)]
        durations = [500]

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True,
        append_images=frames[1:],
        duration=durations, loop=0, optimize=False,
    )
    buf.seek(0)
    return buf
