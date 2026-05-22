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
    ftd: int,
    ftd_earnings: float,
    edge_earnings: float,
    claimable: float,
    total_claimed: float,
) -> io.BytesIO:
    W, H = 560, 300
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

    # main card bg with gold border
    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, GOLD, 2)

    # header band
    _rounded_rect(draw, (0, 0, W - 1, 52), RADIUS, (20, 18, 8), None)
    # round bottom corners of header back to flat
    draw.rectangle([0, RADIUS, W, 52], fill=(20, 18, 8))

    draw.text((22, 14), "AFFILIATE PROGRAM", font=_font(17, bold=True), fill=GOLD)

    # code badge
    code_upper = code.upper()
    badge_x = 22
    draw.text((badge_x, 66), "YOUR CODE", font=_font(11), fill=MUTED)

    # code highlight box
    cw = draw.textlength(code_upper, font=_font(22, bold=True)) + 28
    _rounded_rect(draw, (badge_x - 4, 82, badge_x + cw, 116), 8, (20, 30, 50), BLUE, 1)
    draw.text((badge_x + 10, 86), code_upper, font=_font(22, bold=True), fill=BLUE)

    # 3-col stat row 1
    stats1 = [
        ("REFERRALS", str(referrals), WHITE),
        ("FTD", str(ftd), WHITE),
        ("CONV. RATE", f"{(ftd / referrals * 100) if referrals else 0:.1f}%", GOLD),
    ]
    col_w = (W - 44) // 3
    row1_y = 128
    for i, (label, val, color) in enumerate(stats1):
        cx = 22 + i * col_w
        # mini box
        _rounded_rect(draw, (cx, row1_y, cx + col_w - 8, row1_y + 46), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 10, row1_y + 7), label, font=_font(10), fill=MUTED)
        draw.text((cx + 10, row1_y + 22), val, font=_font(16, bold=True), fill=color)

    # 2-col stat row 2
    ftd_rate_pct = int(config.AFFILIATE_FTD_RATE * 100)
    edge_rate_pct = int(config.AFFILIATE_EDGE_RATE * 100)
    stats2 = [
        (f"FTD EARNINGS ({ftd_rate_pct}%)", f"{_fmt(ftd_earnings)} pts", GREEN),
        (f"LIFETIME ({edge_rate_pct}% EDGE)", f"{_fmt(edge_earnings)} pts", GREEN),
    ]
    col2_w = (W - 44) // 2
    row2_y = 184
    for i, (label, val, color) in enumerate(stats2):
        cx = 22 + i * col2_w
        _rounded_rect(draw, (cx, row2_y, cx + col2_w - 8, row2_y + 46), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 10, row2_y + 7), label, font=_font(10), fill=MUTED)
        draw.text((cx + 10, row2_y + 22), val, font=_font(16, bold=True), fill=color)

    # 2-col stat row 3
    stats3 = [
        ("CLAIMABLE", f"{_fmt(claimable)} pts", WHITE),
        ("TOTAL CLAIMED", f"{_fmt(total_claimed)} pts", WHITE),
    ]
    row3_y = 240
    for i, (label, val, color) in enumerate(stats3):
        cx = 22 + i * col2_w
        _rounded_rect(draw, (cx, row3_y, cx + col2_w - 8, row3_y + 46), 8, (18, 25, 40), BORDER, 1)
        draw.text((cx + 10, row3_y + 7), label, font=_font(10), fill=MUTED)
        draw.text((cx + 10, row3_y + 22), val, font=_font(16, bold=True), fill=color)

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


# ── Leaderboard Card ──────────────────────────────────────────────────────────

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
