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


# ── Blackjack card assets ──────────────────────────────────────────────────────

CARDS_DIR = Path(__file__).parent.parent / "assets" / "cards"
_CARD_W, _CARD_H = 71, 100  # target card size

_SUIT_CHAR = {"h": "♥", "d": "♦", "c": "♣", "s": "♠"}
_SUIT_COLOR = {"h": (195, 30, 30), "d": (195, 30, 30), "c": (15, 15, 15), "s": (15, 15, 15)}
_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_SUITS = ["h", "d", "c", "s"]


def _gen_card_image(rank: str, suit: str) -> Image.Image:
    """Generate a single card image with PIL."""
    img = Image.new("RGBA", (_CARD_W, _CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, _CARD_W - 1, _CARD_H - 1], radius=7,
                            fill=(245, 245, 245), outline=(180, 180, 180), width=1)
    suit_char = _SUIT_CHAR[suit]
    ink = _SUIT_COLOR[suit]
    f_sm = _font(13)
    f_lg = _font(26, bold=True)
    draw.text((4, 4), rank, font=f_sm, fill=ink)
    draw.text((4, 18), suit_char, font=f_sm, fill=ink)
    try:
        sw = draw.textlength(suit_char, font=f_lg)
        rw = draw.textlength(rank, font=f_lg)
    except Exception:
        sw = 18
        rw = 18
    draw.text((_CARD_W // 2 - rw // 2, _CARD_H // 2 - 24), rank, font=f_lg, fill=ink)
    draw.text((_CARD_W // 2 - sw // 2, _CARD_H // 2 + 4), suit_char, font=f_lg, fill=ink)
    return img


def _gen_back_image() -> Image.Image:
    """Generate a card-back image with PIL."""
    img = Image.new("RGBA", (_CARD_W, _CARD_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, _CARD_W - 1, _CARD_H - 1], radius=7,
                            fill=(22, 40, 80), outline=(60, 90, 150), width=2)
    draw.rounded_rectangle([5, 5, _CARD_W - 6, _CARD_H - 6], radius=5,
                            fill=(18, 32, 65))
    # crosshatch pattern
    for i in range(0, _CARD_W, 6):
        draw.line([(5 + i, 5), (5, 5 + i)], fill=(30, 55, 110), width=1)
        draw.line([(5 + i, _CARD_H - 6), (_CARD_W - 6, 5 + i)], fill=(30, 55, 110), width=1)
    return img


def _ensure_card_assets():
    """Generate missing card PNGs into assets/cards/ on first run."""
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    back_path = CARDS_DIR / "back.png"
    if not back_path.exists():
        _gen_back_image().save(back_path, "PNG")
    for rank in _RANKS:
        for suit in _SUITS:
            p = CARDS_DIR / f"{rank}{suit}.png"
            if not p.exists():
                _gen_card_image(rank, suit).save(p, "PNG")


def _card_key(card_str: str) -> str:
    """Convert deck card string (e.g. 'A♥', '10♣') to filename key (e.g. 'Ah', '10c')."""
    if not card_str or card_str == "?":
        return "back"
    suit_map = {"♥": "h", "♦": "d", "♣": "c", "♠": "s"}
    suit_char = card_str[-1]
    rank = card_str[:-1] if len(card_str) > 1 else card_str
    return f"{rank}{suit_map.get(suit_char, 'h')}"


_card_cache: dict[str, Image.Image] = {}


def _load_card_img(key: str) -> Image.Image:
    """Load a card image (from assets or auto-generated), cached in memory."""
    if key in _card_cache:
        return _card_cache[key]
    path = CARDS_DIR / f"{key}.png"
    if path.exists():
        img = Image.open(path).convert("RGBA").resize((_CARD_W, _CARD_H), Image.LANCZOS)
    else:
        if key == "back":
            img = _gen_back_image()
        else:
            # parse rank/suit from key
            suit_letter = key[-1] if key else "h"
            rank = key[:-1] if len(key) > 1 else key
            img = _gen_card_image(rank, suit_letter) if suit_letter in _SUIT_CHAR else _gen_back_image()
    _card_cache[key] = img
    return img


def _paste_card(canvas: Image.Image, card_str: str, x: int, y: int, face_down: bool = False):
    """Paste a card image (or back) onto canvas at (x, y)."""
    key = "back" if face_down else _card_key(card_str)
    card_img = _load_card_img(key).copy()
    canvas.paste(card_img, (x, y), card_img)


# ── Blackjack GIF ──────────────────────────────────────────────────────────────

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
    *,
    reveal_dealer: bool = False,
    result_text: str = "",
    net_change: float = 0.0,
    bet: float = 0.0,
    username: str = "",
    animate_from: int = 0,
) -> io.BytesIO:
    """Animated BJ GIF.

    - animate_from=0: full interleaved deal animation (initial deal).
    - animate_from=N: only animate player cards from index N onward (for hits).
      The first frame shows the board before the new card; subsequent frames
      reveal new cards one by one.
    - If result_text is set, final frame shows outcome + net_change; lasts 20 s.
    - GIF plays once (no loop).
    - Info bar below player cards shows username / bet / USD.
    """
    _ensure_card_assets()

    # Layout constants
    W = 560
    INFO_H = 46          # height of info bar at bottom
    H = 310 + INFO_H     # total height

    BG       = (13, 17, 30)
    PANEL    = (18, 24, 42)
    WHITE    = (255, 255, 255)
    MUTED    = (120, 130, 150)
    DIVIDER  = (35, 45, 70)
    GREEN    = (46, 213, 96)
    RED      = (231, 76, 60)
    GOLD     = (255, 196, 0)
    BLUE     = (88, 101, 242)

    RESULT_COLORS = {
        "BLACKJACK": GOLD, "WIN": GREEN, "PUSH": BLUE,
        "BUST": RED, "LOSS": RED,
    }

    CW, CH = _CARD_W, _CARD_H   # 71 × 100
    GAP = 9

    font_label  = _font(12)
    font_val    = _font(15, bold=True)
    font_result = _font(44, bold=True)
    font_sub    = _font(20, bold=True)
    font_info   = _font(13)

    pts_per_usd = config.POINTS_PER_USD or 100.0

    def _draw_frame(ph: list[str], dh: list[str], is_final: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # ── Dealer panel ──────────────────────────────────────────────────────
        draw.rectangle([0, 0, W, 130], fill=PANEL)
        draw.text((18, 10), "DEALER", font=font_label, fill=MUTED)
        vis = [c for c in dh if c != "?"]
        dv_str = str(_bj_hand_value(vis)) if "?" not in dh else "?"
        draw.text((80, 10), dv_str, font=font_val, fill=WHITE)

        for ci, card in enumerate(dh):
            is_hidden = card == "?" and not reveal_dealer
            _paste_card(img, card if not is_hidden else "?", 18 + ci * (CW + GAP), 30, face_down=is_hidden)

        # ── Centre divider ────────────────────────────────────────────────────
        draw.rectangle([0, 130, W, 144], fill=BG)
        draw.line([(18, 137), (W - 18, 137)], fill=DIVIDER, width=1)

        # ── Player panel ──────────────────────────────────────────────────────
        draw.text((18, 148), "YOUR HAND", font=font_label, fill=MUTED)
        pv = _bj_hand_value(ph)
        pv_color = RED if pv > 21 else WHITE
        draw.text((108, 148), str(pv), font=font_val, fill=pv_color)

        for ci, card in enumerate(ph):
            _paste_card(img, card, 18 + ci * (CW + GAP), 168)

        # ── Info bar ──────────────────────────────────────────────────────────
        info_y = H - INFO_H
        draw.rectangle([0, info_y, W, H], fill=(8, 12, 22))
        draw.line([(0, info_y), (W, info_y)], fill=DIVIDER, width=1)

        uname_str = (username[:22] + "…") if len(username) > 22 else username
        draw.text((18, info_y + 14), uname_str, font=font_info, fill=MUTED)

        if bet > 0:
            bet_str = f"Bet: {_fmt(bet)} pts  •  ${bet / pts_per_usd:.2f}"
            try:
                bw = draw.textlength(bet_str, font=font_info)
            except Exception:
                bw = len(bet_str) * 7
            draw.text((W - 18 - bw, info_y + 14), bet_str, font=font_info, fill=MUTED)

        # ── Result overlay (final frame only) ─────────────────────────────────
        if is_final and result_text:
            rc = RESULT_COLORS.get(result_text, WHITE)
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.rectangle([0, 0, W, info_y], fill=(0, 0, 0, 155))
            merged = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw2 = ImageDraw.Draw(merged)

            # Outcome text (large, centred vertically in the cards area)
            mid_y = (info_y) // 2
            try:
                rw = draw2.textlength(result_text, font=font_result)
            except Exception:
                rw = len(result_text) * 28
            draw2.text(((W - rw) // 2, mid_y - 36), result_text, font=font_result, fill=rc)

            # Net change line
            if net_change != 0.0:
                prefix = "+" if net_change > 0 else ""
                sub_str = f"{prefix}{_fmt(net_change)} pts  (${abs(net_change) / pts_per_usd:.2f})"
                sub_color = GREEN if net_change > 0 else RED
                try:
                    sw = draw2.textlength(sub_str, font=font_sub)
                except Exception:
                    sw = len(sub_str) * 12
                draw2.text(((W - sw) // 2, mid_y + 16), sub_str, font=font_sub, fill=sub_color)

            return merged

        return img

    # ── Build frame sequence ──────────────────────────────────────────────────
    frames: list[Image.Image] = []
    durations: list[int] = []

    if animate_from == 0:
        # Full interleaved deal animation (initial deal)
        ph_so_far: list[str] = []
        dh_so_far: list[str] = []
        max_c = max(len(player_hand), len(dealer_hand))
        for i in range(max_c):
            if i < len(player_hand):
                ph_so_far = player_hand[: i + 1]
            if i < len(dealer_hand):
                dh_so_far = dealer_hand[: i + 1]
            frames.append(_draw_frame(list(ph_so_far), list(dh_so_far)))
            durations.append(380)
    else:
        # Hit animation: existing board shown instantly, then each new card appears
        # Frame 0: board state BEFORE the new card(s) — very brief flash
        frames.append(_draw_frame(player_hand[:animate_from], dealer_hand))
        durations.append(80)
        # Frames: reveal each new card one at a time
        for i in range(animate_from, len(player_hand)):
            frames.append(_draw_frame(player_hand[: i + 1], dealer_hand))
            durations.append(380)

    # Final frame (with result overlay if provided)
    frames.append(_draw_frame(list(player_hand), list(dealer_hand), is_final=True))
    durations.append(20_000 if result_text else 5_000)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,        # 1 = play once, do not repeat
        optimize=False,
        disposal=2,
    )
    buf.seek(0)
    return buf


async def render_bj_static(
    player_hand: list[str],
    dealer_hand: list[str],
    *,
    reveal_dealer: bool = False,
    bet: float = 0.0,
    username: str = "",
) -> io.BytesIO:
    """Single static PNG of the current BJ board — used for mid-game hit updates."""
    return await render_bj_gif(
        player_hand, dealer_hand,
        reveal_dealer=reveal_dealer,
        result_text="",
        net_change=0.0,
        bet=bet,
        username=username,
    )


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



