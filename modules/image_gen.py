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


# ── Towers Game ───────────────────────────────────────────────────────────────

TOWERS_MULTS: dict[str, list[float]] = {
    "easy":   [1.28, 1.64, 2.10, 2.68, 3.43, 4.39, 5.62, 7.19, 9.21, 11.79],
    "normal": [1.88, 3.54, 6.65, 12.51, 23.53, 44.24, 83.18, 156.37, 294.05, 552.90],
    "hard":   [3.76, 14.14, 53.17, 199.92, 751.72, 2826.45, 10627.41, 39959.45, 150261.85, 565184.55],
}
TOWERS_BOMBS: dict[str, int] = {"easy": 1, "normal": 2, "hard": 3}

_TW_ICON_SZ = 26
_tw_gem_img: "Image.Image | None" = None
_tw_bomb_img: "Image.Image | None" = None


def _gen_gem_icon(sz: int) -> "Image.Image":
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = sz // 2, sz // 2
    r = sz // 2 - 2
    pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    draw.polygon(pts, fill=(46, 213, 96), outline=(25, 140, 60), width=2)
    # inner facet lines
    draw.line([(cx, cy - r + 3), (cx, cy + r - 3)], fill=(100, 255, 150, 140), width=1)
    # shine
    shine = [(cx - r // 3, cy - r + 2), (cx + r // 3, cy - r + 2), (cx + 2, cy - 3), (cx - 2, cy - 3)]
    draw.polygon(shine, fill=(180, 255, 200, 180))
    return img


def _gen_bomb_icon(sz: int) -> "Image.Image":
    img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = sz // 2, sz // 2 + sz // 12
    r = sz // 2 - 4
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(38, 38, 38), outline=(90, 90, 90), width=2)
    # fuse
    fx, fy = cx + r - 2, cy - r + 2
    draw.line([(fx, fy), (fx + 3, fy - 5), (fx + 5, fy - 9)], fill=(180, 130, 40), width=2)
    # spark
    draw.ellipse([fx + 3, fy - 12, fx + 9, fy - 8], fill=(255, 210, 0))
    # body shine
    sr = max(2, r // 4)
    draw.ellipse([cx - sr - 2, cy - r + 3, cx + sr - 2, cy - r + sr + 5], fill=(75, 75, 75))
    return img


def _ensure_tower_assets() -> None:
    global _tw_gem_img, _tw_bomb_img
    if _tw_gem_img is not None and _tw_bomb_img is not None:
        return
    asset_dir = Path("assets")
    asset_dir.mkdir(parents=True, exist_ok=True)
    gem_path  = asset_dir / "gem.png"
    bomb_path = asset_dir / "bomb.png"
    if gem_path.exists():
        raw = Image.open(gem_path).convert("RGBA")
    else:
        raw = _gen_gem_icon(_TW_ICON_SZ)
    _tw_gem_img = raw.resize((_TW_ICON_SZ, _TW_ICON_SZ), Image.LANCZOS)
    if bomb_path.exists():
        raw = Image.open(bomb_path).convert("RGBA")
    else:
        raw = _gen_bomb_icon(_TW_ICON_SZ)
    _tw_bomb_img = raw.resize((_TW_ICON_SZ, _TW_ICON_SZ), Image.LANCZOS)


async def render_towers_gif(
    grid: "list[list[str]]",        # 10 floors × 4 cols, "gem" or "bomb"
    picks: "list[int | None]",      # per floor: chosen col index, or None
    active_floor: int,              # floor player should act on next (0=bottom)
    mode: str,
    bet: float,
    username: str,
    *,
    just_revealed_floor: "int | None" = None,
    result: str = "",               # "BOOM" | "CASHOUT" | ""
    net_change: float = 0.0,
) -> io.BytesIO:
    """Animated Towers GIF.

    - just_revealed_floor=N: animate the reveal of floor N (picked cell first,
      then others left-to-right), then show result or next active floor.
    - result="BOOM": BOOM overlay on final frame (20 s hold).
    - result="CASHOUT": CASHOUT overlay on final frame (20 s hold).
    - Otherwise: single static frame (initial state or inter-floor state).
    """
    _ensure_tower_assets()

    NUM_FL      = 10
    W           = 460
    CELL_W      = 72
    CELL_H      = 40
    CELL_GAP_X  = 8
    CELL_GAP_Y  = 6
    MULT_W      = 58       # width of left multiplier label column
    GRID_LEFT   = MULT_W + 6
    HDR_H       = 44
    GRID_TOP    = HDR_H + 2
    ROW_STRIDE  = CELL_H + CELL_GAP_Y
    INFO_H      = 46
    H = GRID_TOP + NUM_FL * ROW_STRIDE - CELL_GAP_Y + 6 + INFO_H

    # Colours
    BG               = (13, 17, 30)
    PANEL            = (18, 24, 42)
    ACTIVE_ROW_BG    = (22, 30, 52)
    WHITE            = (255, 255, 255)
    MUTED            = (110, 120, 145)
    DIVIDER          = (35, 45, 70)
    GREEN            = (46, 213, 96)
    RED              = (231, 76, 60)
    GOLD             = (255, 196, 0)
    CELL_HIDDEN_BG   = (22, 28, 48)
    CELL_HIDDEN_BR   = (42, 52, 80)
    CELL_ACTIVE_BG   = (28, 38, 66)
    CELL_ACTIVE_BR   = GOLD
    CELL_GEM_BG      = (16, 50, 28)
    CELL_GEM_BR      = GREEN
    CELL_BOMB_BG     = (55, 16, 16)
    CELL_BOMB_BR     = RED
    CELL_FUTURE_BG   = (16, 20, 34)
    CELL_FUTURE_BR   = (30, 38, 60)

    mults          = TOWERS_MULTS.get(mode, TOWERS_MULTS["easy"])
    pts_per_usd    = config.POINTS_PER_USD or 100.0

    font_hdr   = _font(14, bold=True)
    font_mult  = _font(11, bold=True)
    font_cell  = _font(11, bold=True)
    font_res   = _font(44, bold=True)
    font_sub   = _font(20, bold=True)
    font_info  = _font(13)
    font_fl    = _font(12, bold=True)

    def _floor_y(f: int) -> int:
        return GRID_TOP + (NUM_FL - 1 - f) * ROW_STRIDE

    def _cell_x(col: int) -> int:
        return GRID_LEFT + col * (CELL_W + CELL_GAP_X)

    def _text_w(draw_obj: "ImageDraw.ImageDraw", text: str, font: "ImageFont.FreeTypeFont") -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 7.5

    def make_frame(
        revealed: "list[list[str | None]]",
        active_fl: "int | None",
        show_mult_fl: "int | None" = None,
        result_text: str = "",
        net_chg: float = 0.0,
    ) -> Image.Image:
        img  = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # ── Header ────────────────────────────────────────────────────────────
        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        draw.text((16, 13), "TOWERS", font=font_hdr, fill=WHITE)
        draw.text((95, 13), f"│  {mode.upper()}", font=font_hdr, fill=MUTED)
        if active_fl is not None and 0 < active_fl <= NUM_FL:
            cur = mults[active_fl - 1]
            val = bet * cur
            cash_str = f"💰  {_fmt(val)} pts"
            cw = _text_w(draw, cash_str, font_hdr)
            draw.text((W - 16 - cw, 13), cash_str, font=font_hdr, fill=GOLD)

        # ── Info bar ──────────────────────────────────────────────────────────
        iy = H - INFO_H
        draw.rectangle([0, iy, W, H], fill=(8, 12, 22))
        draw.line([(0, iy), (W, iy)], fill=DIVIDER, width=1)
        uname = (username[:22] + "…") if len(username) > 22 else username
        draw.text((16, iy + 16), uname, font=font_info, fill=MUTED)
        if bet > 0:
            bet_s = f"Bet: {_fmt(bet)} pts  •  ${bet / pts_per_usd:.2f}"
            bw = _text_w(draw, bet_s, font_info)
            draw.text((W - 16 - bw, iy + 16), bet_s, font=font_info, fill=MUTED)

        # ── Grid ──────────────────────────────────────────────────────────────
        for f in range(NUM_FL):
            fy        = _floor_y(f)
            is_active = (active_fl == f)

            # Active floor row background highlight
            if is_active:
                draw.rectangle([0, fy - 2, W, fy + CELL_H + 2], fill=ACTIVE_ROW_BG)

            # Multiplier label
            m_str = f"{mults[f]:.2f}x"
            if show_mult_fl == f:
                m_col = GOLD
            elif active_fl is not None and f < active_fl:
                m_col = GREEN
            elif is_active:
                m_col = WHITE
            else:
                m_col = MUTED
            mw = _text_w(draw, m_str, font_mult)
            draw.text((MULT_W - mw - 4, fy + (CELL_H - 12) // 2), m_str, font=font_mult, fill=m_col)

            # Cells
            for c in range(4):
                cx   = _cell_x(c)
                cell = revealed[f][c]  # None / "gem" / "bomb"

                if cell == "gem":
                    bg, br = CELL_GEM_BG, CELL_GEM_BR
                elif cell == "bomb":
                    bg, br = CELL_BOMB_BG, CELL_BOMB_BR
                elif is_active:
                    bg, br = CELL_ACTIVE_BG, CELL_ACTIVE_BR
                elif active_fl is not None and f > active_fl:
                    bg, br = CELL_FUTURE_BG, CELL_FUTURE_BR
                else:
                    bg, br = CELL_HIDDEN_BG, CELL_HIDDEN_BR

                bw = 2 if (is_active or cell is not None) else 1
                draw.rounded_rectangle(
                    [cx, fy, cx + CELL_W, fy + CELL_H],
                    radius=5, fill=bg, outline=br, width=bw,
                )

                if cell == "gem" and _tw_gem_img:
                    ix = cx + (CELL_W - _TW_ICON_SZ) // 2
                    iy2 = fy + (CELL_H - _TW_ICON_SZ) // 2
                    img.paste(_tw_gem_img, (ix, iy2), _tw_gem_img)
                elif cell == "bomb" and _tw_bomb_img:
                    ix = cx + (CELL_W - _TW_ICON_SZ) // 2
                    iy2 = fy + (CELL_H - _TW_ICON_SZ) // 2
                    img.paste(_tw_bomb_img, (ix, iy2), _tw_bomb_img)
                elif cell is None and is_active:
                    q = "?"
                    qw = _text_w(draw, q, font_cell)
                    draw.text(
                        (cx + (CELL_W - qw) // 2, fy + (CELL_H - 14) // 2),
                        q, font=font_cell, fill=MUTED,
                    )

        # Multiplier flash banner on show_mult_fl row
        if show_mult_fl is not None:
            fy = _floor_y(show_mult_fl)
            gx1 = GRID_LEFT
            gx2 = GRID_LEFT + 4 * (CELL_W + CELL_GAP_X) - CELL_GAP_X
            ov  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od  = ImageDraw.Draw(ov)
            od.rectangle([gx1, fy, gx2, fy + CELL_H], fill=(0, 0, 0, 130))
            img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
            draw = ImageDraw.Draw(img)
            banner = f"✓  {mults[show_mult_fl]:.2f}x"
            bw2 = _text_w(draw, banner, font_fl)
            draw.text(
                (gx1 + (gx2 - gx1 - bw2) // 2, fy + (CELL_H - 14) // 2),
                banner, font=font_fl, fill=GOLD,
            )

        # Result overlay
        if result_text:
            rc = RED if result_text == "BOOM" else GOLD
            ov   = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od   = ImageDraw.Draw(ov)
            od.rectangle([0, 0, W, iy], fill=(0, 0, 0, 168))
            img  = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
            draw = ImageDraw.Draw(img)
            mid  = iy // 2
            rw   = _text_w(draw, result_text, font_res)
            draw.text(((W - rw) // 2, mid - 36), result_text, font=font_res, fill=rc)
            if net_chg != 0.0:
                pfx = "+" if net_chg > 0 else ""
                sub = f"{pfx}{_fmt(net_chg)} pts  (${abs(net_chg) / pts_per_usd:.2f})"
                sub_col = GREEN if net_chg > 0 else RED
                sw = _text_w(draw, sub, font_sub)
                draw.text(((W - sw) // 2, mid + 16), sub, font=font_sub, fill=sub_col)

        return img

    # ── Build frames ──────────────────────────────────────────────────────────

    def base_revealed(up_to_floor: int) -> "list[list[str | None]]":
        """All floors below up_to_floor fully revealed; rest hidden."""
        rev: list[list[str | None]] = [[None] * 4 for _ in range(NUM_FL)]
        for f in range(up_to_floor):
            if picks[f] is not None:
                for c in range(4):
                    rev[f][c] = grid[f][c]
        return rev

    frames: list[Image.Image] = []
    durations: list[int] = []

    if just_revealed_floor is not None:
        jrf      = just_revealed_floor
        pick_col = picks[jrf]
        base     = base_revealed(jrf)

        # Frame 0: floor jrf still hidden
        frames.append(make_frame(base, active_fl=jrf))
        durations.append(120)

        # Frame 1: picked cell reveals
        base[jrf][pick_col] = grid[jrf][pick_col]
        frames.append(make_frame(base, active_fl=jrf))
        durations.append(440)

        # Frame 2: all other cells reveal simultaneously
        for c in [x for x in range(4) if x != pick_col]:
            base[jrf][c] = grid[jrf][c]
        frames.append(make_frame(base, active_fl=jrf))
        durations.append(400)

        if grid[jrf][pick_col] == "gem" and result != "BOOM":
            # Show multiplier banner on cleared floor, then move to next floor
            frames.append(make_frame(base, active_fl=active_floor, show_mult_fl=jrf))
            durations.append(900)
            # Final: active_floor ready for player
            frames.append(make_frame(base, active_fl=active_floor))
            durations.append(5_000)
        else:
            # BOOM — 20 s final frame
            frames.append(make_frame(base, active_fl=jrf, result_text="BOOM", net_chg=net_change))
            durations.append(20_000)

    elif result == "CASHOUT":
        base = base_revealed(active_floor)
        frames.append(make_frame(base, active_fl=active_floor, result_text="CASHOUT", net_chg=net_change))
        durations.append(20_000)

    else:
        # Initial state (or plain static update)
        base = base_revealed(active_floor)
        frames.append(make_frame(base, active_fl=active_floor))
        durations.append(5_000)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        optimize=False,
        disposal=2,
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



