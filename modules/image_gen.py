"""
Pillow-based card renderer.
Produces dark-themed cards similar to the reference screenshots.
"""
from __future__ import annotations

import asyncio
import io
import math
import random
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
    from modules.economy import coins_to_usd
    return coins_to_usd(pts)


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


# ── Promo Redeem Card ─────────────────────────────────────────────────────────

def _wrap_text_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


async def render_promo_redeemed_card(
    username: str,
    *,
    title: str,
    code: str,
    reward_label: str,
    reward_value: str,
    reward_sub: str = "",
    terms: list[str] | None = None,
    new_balance: float | None = None,
    avatar_url: str | None = None,
) -> io.BytesIO:
    """Card shown after a promo code is redeemed."""
    W = 560
    RADIUS = 18
    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    PURPLE = (155, 89, 182)
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY
    GREEN = config.CARD_ACCENT_COLOR
    GOLD = config.CARD_GOLD
    terms = terms or []

    tmp = Image.new("RGBA", (W, 10))
    tmp_draw = ImageDraw.Draw(tmp)
    term_font = _font(13)
    term_lines: list[str] = []
    for line in terms:
        term_lines.extend(_wrap_text_lines(tmp_draw, f"• {line}", term_font, W - 64))

    base_h = 300
    extra_h = max(0, len(term_lines) - 3) * 20
    balance_h = 36 if new_balance is not None else 0
    H = base_h + extra_h + balance_h

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    _rounded_rect(draw, (0, 0, W - 1, H - 1), RADIUS, BG, PURPLE, 2)
    _rounded_rect(draw, (0, 0, W - 1, 58), RADIUS, (28, 18, 38), None)
    draw.rectangle([0, RADIUS, W, 58], fill=(28, 18, 38))

    AVATAR = 40
    avatar_img = await _fetch_avatar(avatar_url, AVATAR) if avatar_url else None
    if avatar_img is None:
        avatar_img = _default_avatar(AVATAR, color=PURPLE)
    img.paste(avatar_img, (W - AVATAR - 18, 9), avatar_img)

    draw.text((22, 12), title.upper(), font=_font(16, bold=True), fill=PURPLE)
    uname = username if len(username) <= 22 else username[:19] + "..."
    draw.text((22, 34), uname, font=_font(12), fill=MUTED)

    draw.text((22, 72), "PROMO CODE", font=_font(10), fill=MUTED)
    code_upper = code.upper()
    code_w = draw.textlength(code_upper, font=_font(28, bold=True)) + 32
    _rounded_rect(draw, (22, 88, 22 + code_w, 128), 10, (22, 16, 34), PURPLE, 1)
    draw.text((38, 94), code_upper, font=_font(28, bold=True), fill=WHITE)

    reward_y = 144
    _rounded_rect(draw, (22, reward_y, W - 22, reward_y + 72), 10, (18, 25, 40), BORDER, 1)
    draw.text((36, reward_y + 10), reward_label, font=_font(10), fill=MUTED)
    draw.text((36, reward_y + 28), reward_value, font=_font(24, bold=True), fill=GREEN)
    if reward_sub:
        draw.text((36, reward_y + 56), reward_sub, font=_font(12), fill=MUTED)

    terms_y = reward_y + 88
    draw.text((22, terms_y), "TERMS & CONDITIONS", font=_font(11, bold=True), fill=GOLD)
    y = terms_y + 22
    for line in term_lines:
        draw.text((28, y), line, font=term_font, fill=WHITE)
        y += 20

    if new_balance is not None:
        bal_y = H - 28
        bal_txt = f"New balance: {_fmt(new_balance)} pts"
        draw.text((22, bal_y), bal_txt, font=_font(13, bold=True), fill=GREEN)

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


# ── Limbo GIF ─────────────────────────────────────────────────────────────────

LIMBO_COUNTUP_MS = 800


def _limbo_ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 2.2


def _ratio_log(value: float, *, max_value: float = 50.0) -> float:
    """Map value to 0–1 for vertical bar fill (log scale)."""
    value = max(0.0, float(value))
    cap = max(0.01, float(max_value))
    return min(1.0, math.log1p(value) / math.log1p(cap))


def _draw_limbo_style_bar(
    draw: ImageDraw.ImageDraw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    ratio: float,
    *,
    label: str = "",
    marker_color: tuple[int, int, int] = (255, 196, 0),
    fill_color: tuple[int, int, int] = (28, 38, 72),
    track_fill: tuple[int, int, int] = (12, 18, 34),
    track_outline: tuple[int, int, int] = (42, 52, 88),
    font_label=None,
    font_value=None,
    value_text: str = "",
) -> None:
    """Vertical Limbo-style track — fill rises from bottom with ratio 0–1."""
    ratio = max(0.0, min(1.0, ratio))
    draw.rounded_rectangle(
        [x1, y1, x2, y2], radius=14,
        fill=track_fill, outline=track_outline, width=2,
    )
    inner_h = max(4, y2 - y1 - 8)
    fill_top = int(y1 + 4 + (1.0 - ratio) * inner_h)
    fill_bot = y2 - 4
    if fill_top < fill_bot:
        draw.rectangle([x1 + 4, fill_top, x2 - 4, fill_bot], fill=fill_color)
    draw.line([(x1 - 6, fill_top), (x2 + 6, fill_top)], fill=marker_color, width=3)
    if font_label and label:
        draw.text((x2 + 10, fill_top - 10), label, font=font_label, fill=marker_color)
    if font_value and value_text:
        try:
            vw = draw.textlength(value_text, font=font_value)
        except Exception:
            vw = len(value_text) * 10
        draw.text(((x1 + x2 - vw) // 2, y1 - 28), value_text, font=font_value, fill=(245, 247, 255))


async def render_limbo_gif(
    username: str,
    bet: float,
    target: float,
    crash: float,
    won: bool,
    net_change: float,
) -> io.BytesIO:
    """Animated limbo — multiplier rises to crash, then WIN / LOSS overlay."""
    W, H = 500, 300
    HDR_H = 48
    INFO_H = 44

    BG = (10, 14, 28)
    PANEL = (16, 22, 40)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    DIVIDER = (35, 45, 72)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    CYAN = (56, 189, 248)
    PURPLE = (168, 85, 247)

    from modules.economy import get_coins_per_usd
    pts_per_usd = get_coins_per_usd() or 100.0

    font_hdr = _font(15, bold=True)
    font_big = _font(64, bold=True)
    font_mid = _font(18, bold=True)
    font_sm = _font(12)
    font_res = _font(46, bold=True)
    font_sub = _font(18, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    bar_x1, bar_x2 = 56, W - 56
    bar_y1, bar_y2 = HDR_H + 28, H - INFO_H - 36
    bar_h = bar_y2 - bar_y1

    scale_max = max(target * 1.35, crash * 1.1, 2.0)

    def _y_for_mult(m: float) -> int:
        m = max(1.0, min(m, scale_max))
        ratio = (m - 1.0) / max(scale_max - 1.0, 0.01)
        y = int(bar_y1 + (1.0 - ratio) * (bar_h - 8))
        return max(bar_y1 + 4, min(y, bar_y2 - 4))

    def make_frame(display_mult: float, *, result_text: str = "", net_chg: float = 0.0) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        draw.text((18, 14), "LIMBO", font=font_hdr, fill=CYAN)
        tgt_lbl = f"TARGET  {target:.2f}x"
        tw = _tw(draw, tgt_lbl, font_mid)
        draw.text((W - 18 - tw, 14), tgt_lbl, font=font_mid, fill=GOLD)

        # Track
        draw.rounded_rectangle(
            [bar_x1, bar_y1, bar_x2, bar_y2], radius=14,
            fill=(12, 18, 34), outline=(42, 52, 88), width=2,
        )
        # Fill from rocket height down to bottom of track (y0 must be <= y1)
        cur_y = _y_for_mult(display_mult)
        fill_top = max(bar_y1 + 4, min(cur_y, bar_y2 - 5))
        fill_bot = bar_y2 - 4
        if fill_top < fill_bot:
            draw.rectangle([bar_x1 + 4, fill_top, bar_x2 - 4, fill_bot], fill=(28, 38, 72))

        # Target line
        ty = _y_for_mult(target)
        draw.line([(bar_x1 - 8, ty), (bar_x2 + 8, ty)], fill=GOLD, width=3)
        draw.text((bar_x2 + 12, ty - 10), f"{target:.2f}x", font=font_sm, fill=GOLD)

        # Rocket marker
        rx = (bar_x1 + bar_x2) // 2
        draw.polygon(
            [(rx, cur_y - 22), (rx - 14, cur_y + 6), (rx + 14, cur_y + 6)],
            fill=PURPLE,
        )
        draw.ellipse([rx - 5, cur_y + 4, rx + 5, cur_y + 14], fill=(255, 120, 60))

        mult_str = f"{display_mult:.2f}x"
        mw = _tw(draw, mult_str, font_big)
        draw.text(((W - mw) // 2, HDR_H + 52), mult_str, font=font_big, fill=WHITE)

        # Info bar
        iy = H - INFO_H
        draw.rectangle([0, iy, W, H], fill=(8, 12, 22))
        draw.line([(0, iy), (W, iy)], fill=DIVIDER, width=1)
        uname = (username[:20] + "…") if len(username) > 20 else username
        draw.text((16, iy + 14), uname, font=font_sm, fill=MUTED)
        bet_s = f"Bet {_fmt(bet)}  •  ${bet / pts_per_usd:.2f}"
        bw = _tw(draw, bet_s, font_sm)
        draw.text((W - 16 - bw, iy + 14), bet_s, font=font_sm, fill=MUTED)

        if result_text:
            rc = GREEN if result_text == "WIN" else RED
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(ov)
            od.rectangle([0, HDR_H, W, iy], fill=(0, 0, 0, 175))
            img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
            draw = ImageDraw.Draw(img)
            mid = HDR_H + (iy - HDR_H) // 2
            rw = _tw(draw, result_text, font_res)
            draw.text(((W - rw) // 2, mid - 40), result_text, font=font_res, fill=rc)
            pfx = "+" if net_chg > 0 else ""
            sub = f"{pfx}{_fmt(net_chg)} pts"
            sw = _tw(draw, sub, font_sub)
            sub_col = GREEN if net_chg > 0 else RED
            draw.text(((W - sw) // 2, mid + 12), sub, font=font_sub, fill=sub_col)
            land = f"Landed {crash:.2f}x"
            lw = _tw(draw, land, font_mid)
            draw.text(((W - lw) // 2, mid + 44), land, font=font_mid, fill=MUTED)

        return img

    n_anim = 16
    frame_ms = LIMBO_COUNTUP_MS // n_anim

    frames: list[Image.Image] = []
    durations: list[int] = []

    for i in range(n_anim):
        t = _limbo_ease_out((i + 1) / n_anim)
        display = 1.0 + (crash - 1.0) * t
        frames.append(make_frame(display))
        durations.append(frame_ms)

    result_label = "WIN" if won else "LOSS"
    frames.append(make_frame(crash, result_text=result_label, net_chg=net_change))
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


# ── Slide GIF ────────────────────────────────────────────────────────────────

SLIDE_RESULT_HOLD_MS = 20_000
SLIDE_SPIN_MS = 2_400


def _slide_mult_label(mult: float) -> str:
    if mult <= 0:
        return "0x"
    if mult < 10:
        return f"{mult:g}x"
    return f"{int(mult)}x" if mult == int(mult) else f"{mult:g}x"


def _slide_tier_style(mult: float) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """fill, border, text."""
    if mult >= 50:
        return (42, 32, 8), (255, 210, 50), (255, 235, 120)
    if mult >= 25:
        return (48, 18, 12), (220, 70, 55), (255, 180, 140)
    if mult >= 10:
        return (52, 12, 28), (200, 45, 75), (255, 140, 160)
    if mult >= 5:
        return (32, 18, 52), (168, 85, 247), (220, 180, 255)
    if mult >= 2:
        return (12, 38, 28), (46, 213, 96), (160, 255, 200)
    if mult >= 1:
        return (14, 28, 48), (56, 189, 248), (180, 230, 255)
    if mult >= 0.5:
        return (22, 26, 38), (90, 105, 130), (170, 180, 200)
    return (18, 20, 28), (120, 55, 55), (200, 120, 120)


def _slide_ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 2.4


async def render_slide_gif(
    username: str,
    bet: float,
    result_mult: float,
    *,
    won: bool,
    net_change: float,
) -> io.BytesIO:
    """Slide — multipliers scroll right→left; pointer stops on result; 20s hold, loop once."""
    from Games.slide import random_strip_cell

    W, H = 680, 300
    HDR_H = 52
    INFO_H = 44
    STRIP_Y = 88
    CELL_W, CELL_H = 76, 92
    GAP = 10
    STEP = CELL_W + GAP
    POINTER_X = W // 2

    BG = (8, 12, 24)
    PANEL = (14, 20, 38)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)

    font_user = _font(15, bold=True)
    font_bet = _font(14, bold=True)
    font_cell = _font(17, bold=True)
    font_cell_sm = _font(13, bold=True)
    font_res = _font(40, bold=True)
    font_pts = _font(20, bold=True)
    font_tag = _font(10, bold=True)
    font_hdr = _font(15, bold=True)
    font_sm = _font(12)
    CYAN = (56, 189, 248)
    DIVIDER = (35, 45, 72)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    strip_len = 36
    win_idx = strip_len - 7
    strip: list[float] = [random_strip_cell() for _ in range(strip_len)]
    strip[win_idx] = result_mult

    strip_x_end = POINTER_X - win_idx * STEP - CELL_W // 2
    scroll_dist = STEP * 14
    strip_x_start = strip_x_end + scroll_dist

    def _draw_cell(
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        mult: float,
        *,
        highlight: bool = False,
    ) -> None:
        fill, border, text_col = _slide_tier_style(mult)
        if highlight:
            border = (255, 220, 80)
            fill = tuple(min(255, c + 18) for c in fill)
        draw.rounded_rectangle(
            [x, y, x + CELL_W, y + CELL_H],
            radius=12,
            fill=fill,
            outline=border,
            width=4 if highlight else 2,
        )
        label = _slide_mult_label(mult)
        font = font_cell if len(label) <= 5 else font_cell_sm
        lw = _tw(draw, label, font)
        draw.text((x + (CELL_W - lw) / 2, y + 28), label, font=font, fill=text_col)
        if mult >= 50:
            tag = "MAX"
            tc = GOLD
        elif mult >= 25:
            tag = "RARE"
            tc = (255, 160, 120)
        elif mult >= 10:
            tag = "HOT"
            tc = (255, 120, 150)
        elif mult >= 5:
            tag = "HIGH"
            tc = (200, 160, 255)
        else:
            tag = ""
            tc = MUTED
        if tag:
            tw = _tw(draw, tag, font_tag)
            draw.text((x + (CELL_W - tw) / 2, y + 8), tag, font=font_tag, fill=tc)

    def make_frame(
        strip_x: float,
        *,
        show_result: bool = False,
    ) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        draw.text((18, 16), "SLIDE", font=font_hdr, fill=CYAN)

        track_y1, track_y2 = STRIP_Y - 8, STRIP_Y + CELL_H + 16
        draw.rounded_rectangle(
            [24, track_y1, W - 24, track_y2],
            radius=16,
            fill=(10, 16, 32),
            outline=(40, 52, 82),
            width=2,
        )

        sx = int(strip_x)
        for i, mult in enumerate(strip):
            cx = sx + i * STEP
            if cx + CELL_W < -20 or cx > W + 20:
                continue
            hi = show_result and i == win_idx
            _draw_cell(draw, cx, STRIP_Y, mult, highlight=hi)

        py1, py2 = STRIP_Y - 4, STRIP_Y + CELL_H + 4
        draw.line([(POINTER_X, py1), (POINTER_X, py2)], fill=GOLD, width=3)
        draw.polygon(
            [
                (POINTER_X, py1 - 2),
                (POINTER_X - 12, py1 - 18),
                (POINTER_X + 12, py1 - 18),
            ],
            fill=GOLD,
        )
        draw.polygon(
            [
                (POINTER_X, py2 + 2),
                (POINTER_X - 10, py2 + 16),
                (POINTER_X + 10, py2 + 16),
            ],
            fill=GOLD,
        )

        if show_result:
            res_y = STRIP_Y + CELL_H + 14
            result_label = "WIN" if won else "LOSS"
            rc = GREEN if won else RED
            rw = _tw(draw, result_label, font_res)
            draw.text(((W - rw) / 2, res_y), result_label, font=font_res, fill=rc)
            pfx = "+" if net_change > 0 else ""
            sub = f"{pfx}{_fmt(net_change)} pts  •  {_slide_mult_label(result_mult)}"
            sw = _tw(draw, sub, font_pts)
            sub_col = GREEN if net_change > 0 else RED
            draw.text(((W - sw) / 2, res_y + 44), sub, font=font_pts, fill=sub_col)

        iy = H - INFO_H
        draw.rectangle([0, iy, W, H], fill=(8, 12, 22))
        draw.line([(0, iy), (W, iy)], fill=DIVIDER, width=1)
        uname = (username[:22] + "…") if len(username) > 22 else username
        draw.text((16, iy + 14), uname, font=font_sm, fill=MUTED)
        bet_s = f"Bet {_fmt(bet)} pts"
        bw = _tw(draw, bet_s, font_sm)
        draw.text((W - 16 - bw, iy + 14), bet_s, font=font_sm, fill=MUTED)

        return img

    n_anim = 22
    frame_ms = max(40, SLIDE_SPIN_MS // n_anim)
    frames: list[Image.Image] = []
    durations: list[int] = []

    for i in range(n_anim):
        t = _slide_ease_out((i + 1) / n_anim)
        sx = strip_x_start + (strip_x_end - strip_x_start) * t
        frames.append(make_frame(sx))
        durations.append(frame_ms)

    final = make_frame(strip_x_end, show_result=True)
    frames.append(final)
    durations.append(SLIDE_RESULT_HOLD_MS)
    frames.append(final.copy())
    durations.append(80)

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        optimize=False,
        disposal=2,
    )
    buf.seek(0)
    return buf


# ── HTW (Head-to-Head Wheel) GIF ─────────────────────────────────────────────

HTW_WHEEL_ORDER: list[int] = [
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30,
    8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
]
HTW_RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
HTW_POCKET_STEP = 360.0 / 37.0
HTW_POINTER_DEG = 270.0  # 12 o'clock in Pillow (0° = 3 o'clock, clockwise)
HTW_ANGLE_OFFSET = -2.0   # fine-tune pointer vs pocket (empirical)
HTW_SPIN_MS = 2_400
HTW_RESULT_HOLD_MS = 20_000
_HTW_WHEEL_REV = 3
_htw_wheel_cache: dict[tuple[int, int], Image.Image] = {}


def _htw_draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    fill: tuple[int, int, int] = (255, 255, 255),
) -> None:
    ix, iy = int(x), int(y)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)):
        draw.text((ix + dx, iy + dy), text, font=font, fill=(0, 0, 0))
    draw.text((ix, iy), text, font=font, fill=fill)


def _htw_pocket_fill(n: int) -> tuple[int, int, int]:
    if n == 0:
        return (32, 150, 78)
    if n in HTW_RED_NUMBERS:
        return (196, 42, 48)
    return (18, 22, 32)


def _htw_angle_for_number(num: int) -> float:
    """CCW rotation so the winning pocket sits under the top pointer."""
    idx = HTW_WHEEL_ORDER.index(int(num))
    return HTW_ANGLE_OFFSET - idx * HTW_POCKET_STEP


def _htw_ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 2.6


def _htw_build_wheel(size: int) -> Image.Image:
    """Procedural European roulette wheel (RGBA) with readable pocket numbers."""
    key = (size, _HTW_WHEEL_REV)
    if key in _htw_wheel_cache:
        return _htw_wheel_cache[key]

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size // 2
    outer = size // 2 - 4
    inner = max(20, int(outer * 0.42))
    step = HTW_POCKET_STEP
    gold = (255, 196, 0)
    rim = (58, 66, 88)
    font_pocket = _font(max(9, size // 22), bold=True)

    bbox = [cx - outer, cy - outer, cx + outer, cy + outer]
    top = HTW_POINTER_DEG
    for i, num in enumerate(HTW_WHEEL_ORDER):
        start = top + i * step
        end = top + (i + 1) * step
        draw.pieslice(bbox, start, end, fill=_htw_pocket_fill(num))

    for i in range(37):
        a = math.radians(top + i * step)
        x0 = cx + inner * math.cos(a)
        y0 = cy + inner * math.sin(a)
        x1 = cx + outer * math.cos(a)
        y1 = cy + outer * math.sin(a)
        draw.line([(x0, y0), (x1, y1)], fill=(0, 0, 0, 140), width=1)

    # Pocket numbers on outer ring (outlined for contrast)
    text_r = inner + (outer - inner) * 0.58
    for i, num in enumerate(HTW_WHEEL_ORDER):
        mid = top + (i + 0.5) * step
        rad = math.radians(mid)
        tx = cx + text_r * math.cos(rad)
        ty = cy + text_r * math.sin(rad)
        ns = str(num)
        try:
            tw = draw.textlength(ns, font=font_pocket)
        except Exception:
            tw = len(ns) * 6
        th = font_pocket.size if hasattr(font_pocket, "size") else 10
        _htw_draw_outlined_text(draw, tx - tw / 2, ty - th / 2, ns, font_pocket)

    draw.ellipse(bbox, outline=gold, width=max(2, size // 56))
    draw.ellipse(
        [cx - outer + 7, cy - outer + 7, cx + outer - 7, cy + outer - 7],
        outline=rim, width=2,
    )

    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=(36, 44, 62), outline=gold, width=2)
    arm = inner - 8
    draw.line([(cx - arm, cy), (cx + arm, cy)], fill=(210, 218, 235), width=3)
    draw.line([(cx, cy - arm), (cx, cy + arm)], fill=(210, 218, 235), width=3)
    draw.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=gold)

    _htw_wheel_cache[key] = img
    return img


def _htw_wheel_rotated(size: int, angle_deg: float) -> Image.Image:
    base = _htw_build_wheel(size)
    return base.rotate(angle_deg, resample=Image.BILINEAR, center=(size // 2, size // 2))


def _htw_num_color(n: int) -> tuple[int, int, int]:
    return _htw_pocket_fill(n)


async def render_htw_gif(
    left_name: str,
    right_name: str,
    left_num: int,
    right_num: int,
    bet: float,
    *,
    left_payout: float,
    left_lost: float,
    right_payout: float,
    right_lost: float,
    is_push: bool = False,
) -> io.BytesIO:
    """HTW — spinning numbers, green winner / red loser, payout & loss below."""
    W, H = 620, 340
    BG = (10, 14, 28)
    PANEL = (16, 22, 40)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    CYAN = (56, 189, 248)
    NEUTRAL = (180, 190, 210)

    left_cx, right_cx = W // 4, 3 * W // 4
    card_y = 118
    card_w, card_h = 200, 148
    spin_frames = 26
    loser_dim = 0.42

    font_hdr = _font(14, bold=True)
    font_name = _font(16, bold=True)
    font_spin = _font(72, bold=True)
    font_amt = _font(19, bold=True)
    font_usd = _font(13)
    font_foot = _font(15, bold=True)
    font_push = _font(36, bold=True)

    left_won = left_payout > 0 and left_lost <= 0 and not is_push
    right_won = right_payout > 0 and right_lost <= 0 and not is_push

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    def _short(name: str, mx: int = 14) -> str:
        name = (name or "Player").strip()
        return (name[: mx - 1] + "…") if len(name) > mx else name

    def _draw_player_card(
        base: Image.Image,
        cx: int,
        num: int,
        *,
        spinning: bool,
        show_amounts: bool,
        side: str,
    ) -> Image.Image:
        def _is_loser() -> bool:
            if is_push or not show_amounts:
                return False
            return (side == "left" and not left_won) or (side == "right" and not right_won)
        x1 = cx - card_w // 2
        y1 = card_y
        x2 = x1 + card_w
        y2 = y1 + card_h

        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)

        if show_amounts:
            if is_push:
                border, num_col = GOLD, GOLD
            elif side == "left":
                border = GREEN if left_won else RED
                num_col = GREEN if left_won else RED
            else:
                border = GREEN if right_won else RED
                num_col = GREEN if right_won else RED
        elif spinning:
            border, num_col = (58, 66, 88), WHITE
        else:
            border, num_col = (58, 66, 88), WHITE

        fill = (14, 18, 30) if _is_loser() else (18, 24, 42)
        draw.rounded_rectangle([x1, y1, x2, y2], radius=16, fill=fill, outline=border, width=3)

        ns = str(num)
        nw = _tw(draw, ns, font_spin)
        draw.text((cx - nw / 2, y1 + 18), ns, font=font_spin, fill=num_col)

        if show_amounts:
            if side == "left":
                payout, lost = left_payout, left_lost
            else:
                payout, lost = right_payout, right_lost
            if payout > 0:
                pts_val = payout
                line1 = f"+{_fmt(pts_val)} pts"
                col = GREEN
            elif lost > 0:
                pts_val = lost
                line1 = f"-{_fmt(pts_val)} pts"
                col = RED
            else:
                line1, col, pts_val = "", MUTED, 0.0
            if line1:
                usd = _pts_to_usd(pts_val)
                line2 = f"${usd:,.2f}"
                aw = _tw(draw, line1, font_amt)
                draw.text((cx - aw / 2, y2 - 52), line1, font=font_amt, fill=col)
                uw = _tw(draw, line2, font_usd)
                usd_fill = (*col, 200) if col in (GREEN, RED, GOLD) else (*MUTED, 200)
                draw.text((cx - uw / 2, y2 - 28), line2, font=font_usd, fill=usd_fill)

        dim = loser_dim if _is_loser() else 1.0
        if dim < 1.0:
            r, g, b, a = layer.split()
            a = a.point(lambda p: int(p * dim) if p else 0)
            layer = Image.merge("RGBA", (r, g, b, a))

        out = base.convert("RGBA")
        out = Image.alpha_composite(out, layer)
        return out.convert("RGB")

    def make_frame(
        l_display: int,
        r_display: int,
        *,
        spinning: bool = False,
        final: bool = False,
    ) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        draw.rectangle([0, 0, W, 44], fill=PANEL)
        title = "HTW  •  HEAD TO HEAD"
        tw = _tw(draw, title, font_hdr)
        draw.text(((W - tw) // 2, 12), title, font=font_hdr, fill=CYAN)

        ln, rn = _short(left_name), _short(right_name)
        lw, rw = _tw(draw, ln, font_name), _tw(draw, rn, font_name)
        dim_name = (100, 110, 128)
        if final and not is_push:
            draw.text((left_cx - lw // 2, 58), ln, font=font_name,
                      fill=WHITE if left_won else dim_name)
            draw.text((right_cx - rw // 2, 58), rn, font=font_name,
                      fill=WHITE if right_won else dim_name)
        else:
            draw.text((left_cx - lw // 2, 58), ln, font=font_name, fill=WHITE)
            draw.text((right_cx - rw // 2, 58), rn, font=font_name, fill=WHITE)

        img = _draw_player_card(
            img, left_cx, l_display,
            spinning=spinning and not final,
            show_amounts=final,
            side="left",
        )
        img = _draw_player_card(
            img, right_cx, r_display,
            spinning=spinning and not final,
            show_amounts=final,
            side="right",
        )
        draw = ImageDraw.Draw(img)

        vs_y = card_y + card_h // 2 - 12
        draw.rounded_rectangle([W // 2 - 30, vs_y, W // 2 + 30, vs_y + 36], radius=12, fill=(28, 36, 58))
        draw.text((W // 2 - 12, vs_y + 8), "VS", font=font_name, fill=GOLD)

        if final and is_push:
            pt = "PUSH"
            pw = _tw(draw, pt, font_push)
            draw.text(((W - pw) // 2, card_y + card_h + 14), pt, font=font_push, fill=GOLD)

        uname = _short(left_name, 18)
        draw.text((20, H - 34), uname, font=font_foot, fill=WHITE)
        bet_s = f"Bet {_fmt(bet)} pts"
        usd_s = f"${_pts_to_usd(bet):,.2f}"
        bw = _tw(draw, bet_s, font_foot)
        uw = _tw(draw, usd_s, font_usd)
        draw.text((W - 20 - bw, H - 38), bet_s, font=font_foot, fill=MUTED)
        draw.text((W - 20 - uw, H - 20), usd_s, font=font_usd, fill=MUTED)

        return img

    frames: list[Image.Image] = []
    durations: list[int] = []

    for i in range(spin_frames):
        t = i / max(1, spin_frames - 1)
        eased = _htw_ease_out(t)
        if i >= spin_frames - 2:
            l_display, r_display = left_num, right_num
        elif i >= spin_frames - 8:
            l_display = left_num if random.random() < eased else random.randint(0, 36)
            r_display = right_num if random.random() < eased else random.randint(0, 36)
        else:
            l_display = random.randint(0, 36)
            r_display = random.randint(0, 36)
        spinning = i < spin_frames - 2
        frames.append(make_frame(l_display, r_display, spinning=spinning))
        durations.append(int(55 + eased * 160))

    for _ in range(3):
        frames.append(make_frame(left_num, right_num, final=True))
        durations.append(120)

    final_frame = make_frame(left_num, right_num, final=True)
    frames.append(final_frame)
    durations.append(HTW_RESULT_HOLD_MS)
    for _ in range(2):
        frames.append(final_frame.copy())
        durations.append(HTW_RESULT_HOLD_MS // 2)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
        disposal=2,
    )
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


# ── Slots 3×5 GIF (30 paylines, column cascade) ───────────────────────────────

_SLOTS_COL_FRAMES = 7
_SLOTS_COL_MS = 95
_SLOTS_HOLD_MS = 4_500

_SLOTS_LINE_COLORS = [
    (255, 196, 0),
    (46, 213, 96),
    (56, 189, 248),
    (255, 120, 180),
    (180, 140, 255),
    (255, 140, 60),
    (120, 220, 255),
    (255, 90, 90),
]


async def render_slots_gif(
    *,
    username: str,
    bet: float,
    balance: float,
    grid_ids: list[list[str]],
    wins: list[dict],
    emoji_map: dict[str, str],
    spin_emoji: str,
    payout: float,
    won: bool,
) -> io.BytesIO:
    """3×5 slot — columns lock one-by-one; final frame draws winning paylines."""
    from Games.slot import COLS, PAYLINES, ROWS, SYMBOLS

    W, H = 680, 520
    HDR_H = 56
    WIN_PANEL_H = 100
    INFO_H = 44
    PAD = 16

    BG = (8, 12, 24)
    PANEL = (14, 20, 38)
    MUTED = (110, 120, 145)
    WHITE = (245, 247, 255)
    CYAN = (56, 189, 248)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    WIN_PANEL_BG = (12, 18, 34)
    WIN_PANEL_FILL = (22, 32, 52)

    id_to_sym = {s["id"]: s for s in SYMBOLS}
    pool_ids = [s["id"] for s in SYMBOLS]

    CELL_W, CELL_H = 112, 94
    GAP = 10
    grid_w = COLS * CELL_W + (COLS - 1) * GAP
    grid_h = ROWS * CELL_H + (ROWS - 1) * GAP
    grid_x0 = (W - grid_w) // 2
    grid_y0 = HDR_H + 22
    footer_top = H - INFO_H
    grid_bottom = grid_y0 + grid_h
    win_meter_w = min(360, grid_w + 40)
    win_meter_x0 = (W - win_meter_w) // 2
    gap_above_footer = 12
    gap_below_grid = 12
    win_meter_h = 90
    win_meter_y0 = footer_top - win_meter_h - gap_above_footer
    min_meter_top = grid_bottom + gap_below_grid
    if win_meter_y0 < min_meter_top:
        win_meter_y0 = min_meter_top
        win_meter_h = max(72, footer_top - gap_above_footer - win_meter_y0)

    font_hdr = _font(16, bold=True)
    font_name = _font(14, bold=True)
    font_bet = _font(13, bold=True)
    font_meter_lbl = _font(12, bold=True)
    font_meter_amt = _font(26, bold=True)
    font_meter_pts = _font(14, bold=True)
    font_meter_sub = _font(13, bold=True)
    font_ln = _font(11, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    def _cell_xy(col: int, row: int) -> tuple[int, int]:
        return grid_x0 + col * (CELL_W + GAP), grid_y0 + row * (CELL_H + GAP)

    def _cell_center(col: int, row: int) -> tuple[int, int]:
        x, y = _cell_xy(col, row)
        return x + CELL_W // 2, y + CELL_H // 2

    tokens: set[str] = {spin_emoji}
    for row in grid_ids:
        for sid in row:
            tokens.add(emoji_map.get(sid, id_to_sym.get(sid, {}).get("emoji", "❓")))
    for _ in range(6):
        tokens.add(emoji_map.get(random.choice(pool_ids), "🎰"))

    import aiohttp

    emoji_cache: dict[str, Image.Image] = {}
    async with aiohttp.ClientSession() as session:
        for tok in tokens:
            if tok and tok not in emoji_cache:
                emoji_cache[tok] = await _load_emoji_rgba(str(tok), 62, session)

    def _token_for_id(sid: str) -> str:
        return emoji_map.get(sid, id_to_sym.get(sid, {}).get("emoji", "❓"))

    def _paste_cell(
        base: Image.Image,
        col: int,
        row: int,
        token: str,
        *,
        dim: float = 1.0,
        highlight: bool = False,
    ) -> None:
        x, y = _cell_xy(col, row)
        layer = Image.new("RGBA", (CELL_W, CELL_H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        fill = (18, 24, 42)
        border = (58, 66, 88)
        if highlight:
            fill = (28, 38, 58)
            border = GOLD
        ld.rounded_rectangle([2, 2, CELL_W - 3, CELL_H - 3], radius=12, fill=fill, outline=border, width=3)
        em = emoji_cache.get(token)
        if em is None:
            em = emoji_cache.get(spin_emoji, emoji_cache[list(emoji_cache.keys())[0]])
        em = em.copy()
        ox = (CELL_W - em.width) // 2
        oy = (CELL_H - em.height) // 2
        layer.paste(em, (ox, oy), em)
        if dim < 1.0:
            r, g, b, a = layer.split()
            a = a.point(lambda p: int(p * dim) if p else 0)
            layer = Image.merge("RGBA", (r, g, b, a))
        base.paste(layer, (x, y), layer)

    def _draw_paylines(img: Image.Image) -> None:
        if not wins:
            return
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        shown = wins[:8]
        for wi, w in enumerate(shown):
            line_idx = int(w.get("line_num", 1)) - 1
            if line_idx < 0 or line_idx >= len(PAYLINES):
                continue
            payline = PAYLINES[line_idx]
            count = int(w.get("count", 3))
            col = _SLOTS_LINE_COLORS[wi % len(_SLOTS_LINE_COLORS)]
            pts = [_cell_center(c, payline[c]) for c in range(min(count, COLS))]
            if len(pts) >= 2:
                od.line(pts, fill=(*col, 220), width=5)
            for c, (px, py) in enumerate(pts):
                od.ellipse([px - 6, py - 6, px + 6, py + 6], fill=(*col, 255), outline=(255, 255, 255, 200), width=2)
                if c == 0:
                    lbl = f"L{line_idx + 1}"
                    lw = _tw(od, lbl, font_ln)
                    od.text((px - lw / 2, py - 22), lbl, font=font_ln, fill=(*col, 255))
        img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

    def _draw_win_meter(draw: ImageDraw.ImageDraw, *, show_result: bool) -> None:
        """Casino-style meter: +/- payout (green/red) and balance at bottom."""
        x1, y1 = win_meter_x0, win_meter_y0
        x2, y2 = x1 + win_meter_w, y1 + win_meter_h
        draw.rounded_rectangle(
            [x1, y1, x2, y2],
            radius=14,
            fill=WIN_PANEL_FILL if show_result else WIN_PANEL_BG,
            outline=GREEN if show_result and won else (RED if show_result else (45, 55, 82)),
            width=3 if show_result else 2,
        )
        if not show_result:
            hint = "SPINNING…"
            hw = _tw(draw, hint, font_meter_lbl)
            draw.text(((W - hw) / 2, y1 + (win_meter_h - 14) // 2), hint, font=font_meter_lbl, fill=MUTED)
            return

        if won and payout > 0:
            amt_core = f"+{_fmt(payout)}"
            amt_col = GREEN
        else:
            amt_core = f"-{_fmt(bet)}"
            amt_col = RED

        pts_txt = "pts"
        aw = _tw(draw, amt_core, font_meter_amt)
        pw = _tw(draw, pts_txt, font_meter_pts)
        gap = 7
        block_w = aw + gap + pw
        ax = (W - block_w) / 2
        draw.text((ax, y1 + 10), amt_core, font=font_meter_amt, fill=amt_col)
        draw.text((ax + aw + gap, y1 + 17), pts_txt, font=font_meter_pts, fill=amt_col)

        sep_y = y1 + 46
        draw.line([(x1 + 14, sep_y), (x2 - 14, sep_y)], fill=(45, 55, 82), width=1)

        bal_y = y1 + 54
        bal_lbl = "Balance"
        bal_val = f"{_fmt(balance)} pts"
        draw.text((x1 + 16, bal_y), bal_lbl, font=font_meter_lbl, fill=MUTED)
        bvw = _tw(draw, bal_val, font_meter_sub)
        draw.text((x2 - 16 - bvw, bal_y), bal_val, font=font_meter_sub, fill=WHITE)

    def _make_frame(
        revealed_cols: int,
        *,
        spin_col: int | None = None,
        spin_phase: int = 0,
        final: bool = False,
    ) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        t1, t2 = "SLOT", "MACHINE"
        tw1 = _tw(draw, t1, font_hdr)
        tw2 = _tw(draw, t2, font_hdr)
        draw.text(((W - (tw1 + 12 + tw2)) / 2, 18), t1, font=font_hdr, fill=CYAN)
        draw.text(((W - (tw1 + 12 + tw2)) / 2 + tw1 + 12, 18), t2, font=font_hdr, fill=WHITE)
        sub = "30 LINES"
        sw = _tw(draw, sub, font_hdr)
        draw.text((W - PAD - sw, 18), sub, font=font_hdr, fill=MUTED)

        draw.rounded_rectangle(
            [grid_x0 - 12, grid_y0 - 12, grid_x0 + grid_w + 12, grid_y0 + grid_h + 12],
            radius=18,
            fill=(10, 16, 32),
            outline=(40, 52, 82),
            width=2,
        )

        for col in range(COLS):
            for row in range(ROWS):
                if col < revealed_cols or (final and col < COLS):
                    sid = grid_ids[row][col]
                    _paste_cell(img, col, row, _token_for_id(sid))
                elif spin_col is not None and col == spin_col:
                    sid = pool_ids[(spin_phase + row + col * 3) % len(pool_ids)]
                    _paste_cell(img, col, row, _token_for_id(sid), dim=0.92)
                else:
                    _paste_cell(img, col, row, spin_emoji, dim=0.75)

        draw = ImageDraw.Draw(img)
        _draw_win_meter(draw, show_result=final)

        if final:
            _draw_paylines(img)

        draw = ImageDraw.Draw(img)
        draw.rectangle([0, H - INFO_H, W, H], fill=(8, 12, 22))
        draw.line([(0, H - INFO_H), (W, H - INFO_H)], fill=(35, 45, 72), width=1)
        uname = (username[:20] + "…") if len(username) > 20 else username
        draw.text((18, H - INFO_H + 14), uname, font=font_name, fill=MUTED)
        bet_s = f"Bet {_fmt(bet)} pts"
        bw = _tw(draw, bet_s, font_bet)
        draw.text((W - 18 - bw, H - INFO_H + 14), bet_s, font=font_bet, fill=MUTED)

        return img

    frames: list[Image.Image] = []
    durations: list[int] = []

    for col in range(COLS):
        for phase in range(_SLOTS_COL_FRAMES):
            frames.append(_make_frame(col, spin_col=col, spin_phase=phase))
            durations.append(_SLOTS_COL_MS)
        frames.append(_make_frame(col + 1))
        durations.append(120)

    for _ in range(4):
        frames.append(_make_frame(COLS, final=True))
        durations.append(_SLOTS_HOLD_MS // 4)

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        optimize=False,
        disposal=2,
    )
    buf.seek(0)
    return buf


# ── Stats Card ─────────────────────────────────────────────────────────────────

def render_stats_card(
    username: str,
    stats: dict,
    wagered: float = 0,
    total_deposited: float = 0,
) -> io.BytesIO:
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
        ("DEPOSITED",     f"{_fmt(total_deposited)} pts",                                        BLUE),
        ("PROFIT/LOSS",   (f"+{_fmt(profit)}" if profit >= 0 else _fmt(profit)) + " pts",       ACCENT if profit >= 0 else RED),
        ("BIGGEST WIN",   f"{_fmt(big_win)} pts",                                                GOLD),
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

from modules import card_assets as _card_assets

CARDS_DIR = Path(__file__).parent.parent / "assets" / "cards"
_CARD_CORNER_RADIUS = 12


def _bj_card_size() -> tuple[int, int]:
    return _card_assets.get_display_size()


def clear_bj_card_cache() -> None:
    _card_cache.clear()

_SUIT_CHAR = {"h": "♥", "d": "♦", "c": "♣", "s": "♠"}
_SUIT_COLOR = {"h": (195, 30, 30), "d": (195, 30, 30), "c": (15, 15, 15), "s": (15, 15, 15)}
_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_SUITS = ["h", "d", "c", "s"]
_PLACEHOLDER_W, _PLACEHOLDER_H = 71, 100


def _gen_card_image(rank: str, suit: str) -> Image.Image:
    """Generate a single card image with PIL (always A for ace, never 1)."""
    if rank == "1":
        rank = "A"
    img = Image.new("RGBA", (_PLACEHOLDER_W, _PLACEHOLDER_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, _PLACEHOLDER_W - 1, _PLACEHOLDER_H - 1], radius=7,
                            fill=(245, 245, 245), outline=(180, 180, 180), width=1)
    suit_char = _SUIT_CHAR[suit]
    ink = _SUIT_COLOR[suit]
    f_sm = _font(17, bold=True)
    f_lg = _font(32, bold=True)
    draw.text((5, 5), rank, font=f_sm, fill=ink)
    draw.text((5, 24), suit_char, font=f_sm, fill=ink)
    try:
        sw = draw.textlength(suit_char, font=f_lg)
        rw = draw.textlength(rank, font=f_lg)
    except Exception:
        sw = 18
        rw = 18
    draw.text((_PLACEHOLDER_W // 2 - rw // 2, _PLACEHOLDER_H // 2 - 28), rank, font=f_lg, fill=ink)
    draw.text((_PLACEHOLDER_W // 2 - sw // 2, _PLACEHOLDER_H // 2 + 6), suit_char, font=f_lg, fill=ink)
    return img


def _gen_back_image() -> Image.Image:
    """Generate a card-back image with PIL."""
    img = Image.new("RGBA", (_PLACEHOLDER_W, _PLACEHOLDER_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, _PLACEHOLDER_W - 1, _PLACEHOLDER_H - 1], radius=7,
                            fill=(22, 40, 80), outline=(60, 90, 150), width=2)
    draw.rounded_rectangle([5, 5, _PLACEHOLDER_W - 6, _PLACEHOLDER_H - 6], radius=5,
                            fill=(18, 32, 65))
    # crosshatch pattern
    for i in range(0, _PLACEHOLDER_W, 6):
        draw.line([(5 + i, 5), (5, 5 + i)], fill=(30, 55, 110), width=1)
        draw.line([(5 + i, _PLACEHOLDER_H - 6), (_PLACEHOLDER_W - 6, 5 + i)], fill=(30, 55, 110), width=1)
    return img


def _ensure_card_assets():
    """No-op when import/ or custom PNGs exist; else write procedural fallbacks."""
    if _card_assets.resolve_card_path("Ah"):
        return
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    _gen_back_image().save(CARDS_DIR / "back.png", "PNG")
    for rank in _RANKS:
        for suit in _SUITS:
            _gen_card_image(rank, suit).save(CARDS_DIR / f"{rank}{suit}.png", "PNG")


def _card_key(card_str: str) -> str:
    """Convert deck card string (e.g. 'A♥', '10♣', '0H') to filename key (e.g. 'Ah', '10c')."""
    if not card_str or card_str == "?":
        return "back"
    suit_map = {"♥": "h", "♦": "d", "♣": "c", "♠": "s", "H": "h", "D": "d", "C": "c", "S": "s"}
    suit_char = card_str[-1]
    rank = card_str[:-1] if len(card_str) > 1 else card_str
    if rank == "0":
        rank = "10"
    if rank == "1":
        rank = "A"
    return f"{rank}{suit_map.get(suit_char, 'h')}"


_card_cache: dict[str, Image.Image] = {}


def _load_card_img(key: str) -> Image.Image:
    """Load card from assets/cards/import (or cards/), rounded corners; procedural fallback."""
    cw, ch = _bj_card_size()
    cache_key = f"{key}:{cw}x{ch}"
    if cache_key in _card_cache:
        return _card_cache[cache_key]
    loaded = _card_assets.load_card_image(
        key, cw, ch, corner_radius=_CARD_CORNER_RADIUS,
    )
    if loaded is not None:
        _card_cache[cache_key] = loaded
        return loaded
    if key == "back":
        base = _gen_back_image()
    else:
        suit_letter = key[-1] if key else "h"
        rank = key[:-1] if len(key) > 1 else key
        if rank == "1":
            rank = "A"
        base = (
            _gen_card_image(rank, suit_letter)
            if suit_letter in _SUIT_CHAR
            else _gen_back_image()
        )
    img = _card_assets.round_card_corners(
        base.resize((cw, ch), Image.Resampling.LANCZOS),
        _CARD_CORNER_RADIUS,
    )
    _card_cache[cache_key] = img
    return img


def _draw_card_frame(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    pad: int = 6,
) -> None:
    """Subtle frame around a card slot."""
    fx, fy = x - pad, y - pad
    fw, fh = w + pad * 2, h + pad * 2
    draw.rounded_rectangle(
        [fx, fy, fx + fw - 1, fy + fh - 1],
        radius=14,
        outline=(55, 68, 98),
        width=2,
    )
    draw.rounded_rectangle(
        [fx + 2, fy + 2, fx + fw - 3, fy + fh - 3],
        radius=12,
        outline=(28, 36, 58),
        width=1,
    )


def _paste_card(
    canvas: Image.Image,
    card_str: str,
    x: int,
    y: int,
    face_down: bool = False,
    *,
    framed: bool = False,
):
    """Paste a card image (or back) onto canvas at (x, y)."""
    if framed:
        _draw_card_frame(ImageDraw.Draw(canvas), x, y, *_bj_card_size())
    key = "back" if face_down else _card_key(card_str)
    card_img = _load_card_img(key).copy()
    canvas.paste(card_img, (x, y), card_img)


def hilo_card_display(card: str) -> str:
    """HiLo deck key (AH, 0C) → display string for _paste_card (A♥, 10♣)."""
    if not card or len(card) < 2:
        return card or "?"
    suits = {"C": "♣", "H": "♥", "D": "♦", "S": "♠"}
    suit_c = card[-1]
    rank = card[0]
    if rank == "0":
        rank = "10"
    if rank == "1":
        rank = "A"
    return f"{rank}{suits.get(suit_c, '♠')}"


# ── HiLo GIF ───────────────────────────────────────────────────────────────────

HILO_REVEAL_FRAMES = 14
_HILO_FRAME_PAD = 6


async def render_hilo_gif(
    current_card: str,
    *,
    prev_card: str | None = None,
    reveal_card: str | None = None,
    animate_reveal: bool = False,
    multiplier: float = 1.0,
    bet: float = 0.0,
    username: str = "",
    status: str = "",
    result: str = "",
    net_change: float = 0.0,
) -> io.BytesIO:
    """HiLo: deck (back) left, current card right; slide+flip from deck on reveal."""
    _ensure_card_assets()
    CW, CH = _bj_card_size()
    GAP = 28
    SLOT_PAD = _HILO_FRAME_PAD
    W, H = 580, 290
    BG = (13, 17, 30)
    PANEL = (18, 24, 42)
    WHITE = (255, 255, 255)
    MUTED = (120, 130, 150)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)

    font_title = _font(14, bold=True)
    font_val = _font(18, bold=True)
    font_status = _font(28, bold=True)
    font_sub = _font(14, bold=True)
    font_info = _font(12)
    font_label = _font(11)

    cur_disp = hilo_card_display(current_card)
    rev_disp = hilo_card_display(reveal_card) if reveal_card else cur_disp
    show_face = rev_disp if reveal_card else cur_disp

    deck_x = (W - (CW * 2 + GAP + SLOT_PAD * 4)) // 2 + SLOT_PAD
    play_x = deck_x + CW + GAP
    cy = 56 + (H - 56 - CH) // 2

    back_img = _load_card_img("back")
    face_key = _card_key(show_face)

    def _ease(t: float) -> float:
        return t * t * (3 - 2 * t)

    def _draw_board(*, slide: float = 1.0, flip: float = 1.0, moving: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 42], fill=PANEL)
        draw.text((16, 12), "HI-LO", font=font_title, fill=WHITE)
        mult_str = f"{multiplier:.2f}x"
        try:
            mw = draw.textlength(mult_str, font=font_val)
        except Exception:
            mw = len(mult_str) * 10
        draw.text((W - 16 - mw, 10), mult_str, font=font_val, fill=GOLD)

        _draw_card_frame(draw, deck_x, cy, CW, CH, pad=SLOT_PAD)
        _draw_card_frame(draw, play_x, cy, CW, CH, pad=SLOT_PAD)
        draw.text((deck_x, cy + CH + 8), "DECK", font=font_label, fill=MUTED, anchor="ma")
        draw.text((play_x + CW // 2, cy + CH + 8), "CURRENT", font=font_label, fill=MUTED, anchor="ma")

        # Static deck pile (always face-down)
        img.paste(back_img, (deck_x, cy), back_img)
        if slide < 0.02:
            stack = back_img.copy()
            img.paste(stack, (deck_x + 3, cy - 2), stack)

        if moving and slide < 1.0:
            mx = int(deck_x + (play_x - deck_x) * _ease(slide))
            moving_card = Image.blend(
                back_img,
                _load_card_img(face_key),
                max(0.0, min(1.0, flip)),
            )
            img.paste(moving_card, (mx, cy), moving_card)
        else:
            face = _load_card_img(face_key)
            img.paste(face, (play_x, cy), face)

        if status:
            try:
                sw = draw.textlength(status, font=font_status)
            except Exception:
                sw = len(status) * 16
            col = GREEN if result == "win" else RED if result == "lose" else GOLD if result == "push" else WHITE
            draw.text(((W - sw) // 2, H - 52), status, font=font_status, fill=col)
        if bet > 0:
            info = f"{username[:18]}  •  Bet { _fmt(bet) } pts"
            draw.text((16, H - 24), info, font=font_info, fill=MUTED)
        if net_change != 0.0 and result in ("win", "lose", "cashout"):
            prefix = "+" if net_change > 0 else ""
            sub = f"{prefix}{_fmt(net_change)} pts"
            try:
                sw2 = draw.textlength(sub, font=font_sub)
            except Exception:
                sw2 = len(sub) * 8
            sc = GREEN if net_change > 0 else RED
            draw.text((W - 16 - sw2, H - 24), sub, font=font_sub, fill=sc)
        return img

    frames: list[Image.Image] = []
    durations: list[int] = []

    do_anim = (
        animate_reveal
        and reveal_card
        and hilo_card_display(reveal_card) != hilo_card_display(prev_card or "")
    )
    if do_anim:
        n = HILO_REVEAL_FRAMES
        for i in range(n):
            t = (i + 1) / n
            slide = min(1.0, t / 0.62)
            flip = max(0.0, (t - 0.48) / 0.52)
            moving = slide < 1.0 or flip < 1.0
            frames.append(_draw_board(slide=slide, flip=flip, moving=moving))
            durations.append(85)
        frames.append(_draw_board(slide=1.0, flip=1.0, moving=False))
        durations.append(450 if not result else 18_000)
    else:
        frames.append(_draw_board(slide=1.0, flip=1.0, moving=False))
        durations.append(18_000 if result else 5_000)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=durations, loop=1, optimize=False, disposal=2,
    )
    buf.seek(0)
    return buf


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
    CW0, CH0 = _bj_card_size()
    W = 560
    INFO_H = 46
    dealer_panel_h0 = max(130, CH0 + 40)
    H = dealer_panel_h0 + 14 + 20 + CH0 + 24 + INFO_H

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

    CW, CH = _bj_card_size()
    GAP = 9
    dealer_panel_h = max(130, CH + 40)
    player_y = dealer_panel_h + 14
    player_cards_y = player_y + 20

    font_label  = _font(12)
    font_val    = _font(15, bold=True)
    font_result = _font(44, bold=True)
    font_sub    = _font(20, bold=True)
    font_info   = _font(13)

    from modules.economy import get_coins_per_usd
    pts_per_usd = get_coins_per_usd() or 100.0

    def _draw_frame(ph: list[str], dh: list[str], is_final: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # ── Dealer panel ──────────────────────────────────────────────────────
        draw.rectangle([0, 0, W, dealer_panel_h], fill=PANEL)
        draw.text((18, 10), "DEALER", font=font_label, fill=MUTED)
        vis = [c for c in dh if c != "?"]
        dv_str = str(_bj_hand_value(vis)) if "?" not in dh else "?"
        draw.text((80, 10), dv_str, font=font_val, fill=WHITE)

        for ci, card in enumerate(dh):
            is_hidden = card == "?" and not reveal_dealer
            _paste_card(img, card if not is_hidden else "?", 18 + ci * (CW + GAP), 30, face_down=is_hidden)

        # ── Centre divider ────────────────────────────────────────────────────
        div_y1 = dealer_panel_h
        div_y2 = dealer_panel_h + 14
        draw.rectangle([0, div_y1, W, div_y2], fill=BG)
        draw.line([(18, div_y1 + 7), (W - 18, div_y1 + 7)], fill=DIVIDER, width=1)

        # ── Player panel ──────────────────────────────────────────────────────
        draw.text((18, player_y), "YOUR HAND", font=font_label, fill=MUTED)
        pv = _bj_hand_value(ph)
        pv_color = RED if pv > 21 else WHITE
        draw.text((108, player_y), str(pv), font=font_val, fill=pv_color)

        for ci, card in enumerate(ph):
            _paste_card(img, card, 18 + ci * (CW + GAP), player_cards_y)

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
    from modules.economy import get_coins_per_usd
    pts_per_usd    = get_coins_per_usd() or 100.0

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

        # Frame 0: floor jrf still hidden (brief flash)
        frames.append(make_frame(base, active_fl=jrf))
        durations.append(120)

        # Frame 1: all 4 cells reveal simultaneously
        for c in range(4):
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


# ── Crystals Game ─────────────────────────────────────────────────────────────

CRYSTAL_TYPES: list[str] = ["blue", "white", "black", "purple", "yellow", "green", "red", "aqua"]

CRYSTAL_COLORS: dict[str, tuple[int, int, int]] = {
    "blue":   (66,  135, 245),
    "white":  (220, 225, 255),
    "black":  (65,  65,  75),
    "purple": (160, 50,  235),
    "yellow": (240, 195, 40),
    "green":  (46,  210, 85),
    "red":    (225, 55,  55),
    "aqua":   (35,  215, 230),
}

CRYSTALS_MULTS: dict[str, float] = {
    "quintuple": 20.0,
    "quadruple":  4.80,
    "full_house": 3.84,
    "triple":     2.88,
    "two_pair":   1.92,
    "one_pair":   0.10,
    "no_match":   0.0,
}

COMBO_LABELS: dict[str, str] = {
    "quintuple": "QUINTUPLE!",
    "quadruple": "QUADRUPLE!",
    "full_house": "FULL HOUSE!",
    "triple":    "TRIPLE!",
    "two_pair":  "TWO PAIR!",
    "one_pair":  "ONE PAIR",
    "no_match":  "NO MATCH",
}

_CR_ICON_SZ = 52
_cr_icons: "dict[str, Image.Image]" = {}


def _gen_crystal_icon(color: tuple[int, int, int], sz: int = _CR_ICON_SZ) -> "Image.Image":
    """Draw a gem-cut crystal diamond in the given RGB color."""
    img  = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = sz // 2, sz // 2
    r = sz // 2 - 3

    # Main diamond body
    pts = [(cx, cy - r), (cx + r, cy - r // 3), (cx + r // 2, cy + r),
           (cx - r // 2, cy + r), (cx - r, cy - r // 3)]
    draw.polygon(pts, fill=color + (230,))

    # Darker lower facet
    dark = tuple(max(0, v - 50) for v in color)
    draw.polygon(
        [(cx - r // 2, cy + r), (cx + r // 2, cy + r), (cx, cy)],
        fill=dark + (200,),
    )

    # Bright upper-left shine
    light = tuple(min(255, v + 80) for v in color)
    draw.polygon(
        [(cx, cy - r), (cx + r // 5, cy - r // 3), (cx - r // 3, cy - r // 5)],
        fill=light + (180,),
    )

    # Outline
    draw.polygon(pts, outline=tuple(max(0, v - 30) for v in color) + (255,), width=2)
    return img


def _ensure_crystal_assets() -> None:
    asset_dir = Path("assets/crystals")
    asset_dir.mkdir(parents=True, exist_ok=True)
    for name in CRYSTAL_TYPES:
        path = asset_dir / f"{name}.png"
        if path.exists():
            raw = Image.open(path).convert("RGBA")
        else:
            raw = _gen_crystal_icon(CRYSTAL_COLORS[name], _CR_ICON_SZ)
        _cr_icons[name] = raw.resize((_CR_ICON_SZ, _CR_ICON_SZ), Image.LANCZOS)


def crystals_get_combo(crystals: list[str]) -> str:
    from collections import Counter
    counts = sorted(Counter(crystals).values(), reverse=True)
    if counts[0] == 5:
        return "quintuple"
    if counts[0] == 4:
        return "quadruple"
    if counts[0] == 3 and len(counts) > 1 and counts[1] == 2:
        return "full_house"
    if counts[0] == 3:
        return "triple"
    if counts[0] == 2 and len(counts) > 1 and counts[1] == 2:
        return "two_pair"
    if counts[0] == 2:
        return "one_pair"
    return "no_match"


async def render_crystals_gif(
    crystals: list[str],        # 5 crystal type strings
    combo: str,
    multiplier: float,
    bet: float,
    username: str,
    net_change: float,
    *,
    reveal_count: int = 5,      # how many to show (0 = all hidden)
) -> io.BytesIO:
    """Animated crystals GIF. reveal_count=0 → hidden state; =5 → full reveal."""
    _ensure_crystal_assets()

    NUM      = 5
    SLOT_W   = 82
    SLOT_H   = 100
    SLOT_GAP = 10
    HDR_H    = 42
    INFO_H   = 44
    RES_H    = 58
    PAD_TOP  = 8
    PAD_BOT  = 8
    GRID_Y   = HDR_H + PAD_TOP
    RES_Y    = GRID_Y + SLOT_H + 10

    GRID_TOTAL_W = NUM * SLOT_W + (NUM - 1) * SLOT_GAP
    W = GRID_TOTAL_W + 60
    H = HDR_H + PAD_TOP + SLOT_H + 10 + RES_H + INFO_H + PAD_BOT

    GRID_LEFT = (W - GRID_TOTAL_W) // 2

    BG     = (13, 17, 30)
    PANEL  = (18, 24, 42)
    WHITE  = (255, 255, 255)
    MUTED  = (110, 120, 145)
    DIV    = (35, 45, 70)
    GREEN  = (46, 213, 96)
    RED    = (231, 76, 60)
    GOLD   = (255, 196, 0)
    HIDDEN = (22, 28, 48)
    HIDDEN_BR = (42, 52, 82)

    from modules.economy import get_coins_per_usd
    pts_per_usd = get_coins_per_usd() or 100.0

    font_hdr  = _font(14, bold=True)
    font_slot = _font(11, bold=True)
    font_res  = _font(26, bold=True)
    font_sub  = _font(15, bold=True)
    font_info = _font(13)

    from collections import Counter
    counts = Counter(crystals)
    matching = {c for c, n in counts.items() if n > 1}  # crystal types in a match

    def _text_w(d, text, font):
        try:
            return d.textlength(text, font=font)
        except Exception:
            return len(text) * 7.5

    def make_frame(n_revealed: int, is_final: bool = False) -> Image.Image:
        img  = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # Header
        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        title = "CRYSTALS"
        draw.text((16, 13), title, font=font_hdr, fill=WHITE)
        if multiplier > 0 and is_final:
            m_str = f"{multiplier:.2f}x"
            mw = _text_w(draw, m_str, font_hdr)
            draw.text((W - 16 - mw, 13), m_str, font=font_hdr, fill=GOLD)

        # Info bar
        iy = H - INFO_H
        draw.rectangle([0, iy, W, H], fill=(8, 12, 22))
        draw.line([(0, iy), (W, iy)], fill=DIV, width=1)
        uname = (username[:22] + "…") if len(username) > 22 else username
        draw.text((16, iy + 15), uname, font=font_info, fill=MUTED)
        if bet > 0:
            bs = f"Bet: {_fmt(bet)} pts  •  ${bet / pts_per_usd:.2f}"
            bw = _text_w(draw, bs, font_info)
            draw.text((W - 16 - bw, iy + 15), bs, font=font_info, fill=MUTED)

        # Crystal slots
        for i in range(NUM):
            sx = GRID_LEFT + i * (SLOT_W + SLOT_GAP)
            sy = GRID_Y
            revealed = i < n_revealed
            ctype    = crystals[i] if revealed else None
            is_match = is_final and ctype in matching

            if revealed and ctype:
                base_col = CRYSTAL_COLORS[ctype]
                slot_bg  = tuple(max(0, v - 45) for v in base_col) if is_match else BG
                border   = base_col if is_match else tuple(max(0, v - 10) for v in base_col)
                bw       = 3 if is_match else 2
            else:
                slot_bg = HIDDEN
                border  = HIDDEN_BR
                bw      = 1

            draw.rounded_rectangle(
                [sx, sy, sx + SLOT_W, sy + SLOT_H],
                radius=8, fill=slot_bg, outline=border, width=bw,
            )

            if revealed and ctype and ctype in _cr_icons:
                icon = _cr_icons[ctype]
                ix   = sx + (SLOT_W - _CR_ICON_SZ) // 2
                iy2  = sy + (SLOT_H - _CR_ICON_SZ) // 2 - 4
                img.paste(icon, (ix, iy2), icon)
                # Crystal name label below icon
                lbl = ctype.upper()
                lw  = _text_w(draw, lbl, font_slot)
                draw.text(
                    (sx + (SLOT_W - lw) // 2, sy + SLOT_H - 18),
                    lbl, font=font_slot,
                    fill=CRYSTAL_COLORS[ctype],
                )
            elif not revealed:
                draw.text(
                    (sx + (SLOT_W - 8) // 2, sy + (SLOT_H - 14) // 2),
                    "?", font=font_slot, fill=MUTED,
                )

            # Glow ring on matching slots in final frame
            if is_match:
                glow_col = CRYSTAL_COLORS[ctype] + (80,)
                ov  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                od  = ImageDraw.Draw(ov)
                od.rounded_rectangle(
                    [sx - 2, sy - 2, sx + SLOT_W + 2, sy + SLOT_H + 2],
                    radius=10, outline=CRYSTAL_COLORS[ctype] + (160,), width=3,
                )
                img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
                draw = ImageDraw.Draw(img)

        # Result area
        if is_final:
            label = COMBO_LABELS.get(combo, combo.upper())
            if multiplier >= 2.0:
                res_col = GOLD
            elif multiplier >= 1.0:
                res_col = GREEN
            elif multiplier > 0:
                res_col = (180, 180, 180)
            else:
                res_col = RED
            rw = _text_w(draw, label, font_res)
            draw.text(((W - rw) // 2, RES_Y + 4), label, font=font_res, fill=res_col)
            if net_change != 0.0:
                pfx = "+" if net_change > 0 else ""
                sub = f"{pfx}{_fmt(net_change)} pts  (${abs(net_change) / pts_per_usd:.2f})"
                sc  = GREEN if net_change > 0 else RED
                sw  = _text_w(draw, sub, font_sub)
                draw.text(((W - sw) // 2, RES_Y + 34), sub, font=font_sub, fill=sc)

        return img

    # Build frames: hidden → reveal one by one → final hold
    frames:    list[Image.Image] = []
    durations: list[int]         = []

    if reveal_count == 0:
        # Static hidden state (for initial ".crystals" message before reveal)
        frames.append(make_frame(0))
        durations.append(5_000)
    else:
        # Animate reveal one by one
        frames.append(make_frame(0))
        durations.append(300)
        for n in range(1, NUM + 1):
            frames.append(make_frame(n, is_final=(n == NUM)))
            durations.append(20_000 if n == NUM else 450)

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


# ── Chicken Road Game ─────────────────────────────────────────────────────────

CHICKEN_MULTS: dict[str, list[float]] = {
    "easy":   [1.22, 1.48, 1.79, 2.17, 2.62, 3.17, 3.84, 4.65, 5.63, 6.81, 8.24, 9.97],
    "normal": [1.35, 1.78, 2.35, 3.10, 4.09, 5.40, 7.13, 9.41, 12.42, 16.40],
    "hard":   [1.65, 2.50, 3.78, 5.72, 8.65, 13.10, 19.85, 30.08],
}
CHICKEN_CRASH_PROB: dict[str, float] = {"easy": 0.18, "normal": 0.25, "hard": 0.38}


def chicken_road_num_steps(mode: str) -> int:
    return len(CHICKEN_MULTS.get(mode, CHICKEN_MULTS["easy"]))


def _draw_chicken_sprite(
    draw: "ImageDraw.ImageDraw",
    cx: int,
    cy: int,
    *,
    walk_phase: int = 0,
    squashed: bool = False,
) -> None:
    """Procedural chicken — body, head, comb, beak, legs."""
    if squashed:
        draw.ellipse([cx - 16, cy + 4, cx + 16, cy + 14], fill=(255, 210, 60), outline=(180, 120, 20), width=2)
        draw.ellipse([cx - 10, cy + 2, cx + 6, cy + 10], fill=(255, 180, 40), outline=(160, 90, 10), width=1)
        for dx, dy in [(-12, -4), (8, -6), (0, -10), (14, 0)]:
            draw.line([(cx, cy + 6), (cx + dx, cy + dy)], fill=(255, 255, 255), width=2)
        return

    bob = -2 if walk_phase % 2 else 0
    cy += bob
    # body
    draw.ellipse([cx - 14, cy - 6, cx + 14, cy + 14], fill=(255, 220, 70), outline=(200, 140, 20), width=2)
    # head
    draw.ellipse([cx + 6, cy - 16, cx + 22, cy], fill=(255, 200, 50), outline=(180, 110, 10), width=2)
    # comb
    draw.polygon([(cx + 10, cy - 18), (cx + 14, cy - 24), (cx + 18, cy - 17)], fill=(220, 40, 40))
    # beak
    draw.polygon([(cx + 22, cy - 10), (cx + 30, cy - 7), (cx + 22, cy - 4)], fill=(255, 160, 0))
    # eye
    draw.ellipse([cx + 14, cy - 12, cx + 18, cy - 8], fill=(20, 20, 20))
    # legs
    leg_off = 4 if walk_phase % 2 == 0 else -4
    draw.line([(cx - 4, cy + 14), (cx - 4 + leg_off, cy + 24)], fill=(255, 140, 0), width=3)
    draw.line([(cx + 6, cy + 14), (cx + 6 - leg_off, cy + 24)], fill=(255, 140, 0), width=3)


def _draw_car_sprite(
    draw: "ImageDraw.ImageDraw",
    cx: int,
    cy: int,
    *,
    scale: float = 1.0,
) -> None:
    w = int(38 * scale)
    h = int(22 * scale)
    x1, y1 = cx - w // 2, cy - h // 2
    x2, y2 = cx + w // 2, cy + h // 2
    draw.rounded_rectangle([x1, y1, x2, y2], radius=4, fill=(210, 45, 45), outline=(140, 20, 20), width=2)
    draw.rounded_rectangle([x1 + 6, y1 + 3, x2 - 6, y1 + h // 2], radius=2, fill=(80, 160, 230))
    for wx in (x1 + 8, x2 - 14):
        draw.ellipse([wx, y2 - 4, wx + 10, y2 + 6], fill=(30, 30, 30))
    # headlights
    draw.ellipse([x2 - 6, y1 + 6, x2 - 2, y1 + 12], fill=(255, 255, 180))


async def render_chicken_road_gif(
    cleared_steps: int,
    mode: str,
    bet: float,
    username: str,
    *,
    cross_lane: "int | None" = None,
    cross_result: str = "",
    result: str = "",
    net_change: float = 0.0,
) -> io.BytesIO:
    """Animated Chicken Road GIF — walk, safe crossing, or car crash.

    cleared_steps: lanes already crossed (0 = at start).
    cross_lane: when set, animate crossing this lane index (0-based).
    cross_result: "safe" or "crash" for the animated crossing.
    result: final overlay — "CRASH", "CASHOUT", or "WIN".
    """
    num_steps = chicken_road_num_steps(mode)
    mults = CHICKEN_MULTS.get(mode, CHICKEN_MULTS["easy"])

    W, H = 560, 220
    HDR_H = 44
    ROAD_TOP = 72
    ROAD_H = 88
    INFO_H = 40
    START_X = 36
    FINISH_W = 44
    lane_area = W - START_X - FINISH_W - 12
    lane_w = max(28, lane_area // max(num_steps, 1))

    BG = (13, 17, 30)
    PANEL = (18, 24, 42)
    WHITE = (255, 255, 255)
    MUTED = (110, 120, 145)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    ROAD = (45, 48, 58)
    GRASS = (28, 72, 38)
    SIDEWALK = (90, 90, 98)
    LANE_LINE = (200, 200, 210)

    from modules.economy import get_coins_per_usd
    pts_per_usd = get_coins_per_usd() or 100.0

    font_hdr = _font(14, bold=True)
    font_info = _font(12)
    font_res = _font(40, bold=True)
    font_sub = _font(18, bold=True)
    font_mult = _font(11, bold=True)

    def _chicken_x(cleared: int) -> int:
        if cleared <= 0:
            return START_X + 10
        if cleared >= num_steps:
            return W - FINISH_W // 2 - 4
        return START_X + (cleared - 1) * lane_w + lane_w // 2

    def _text_w(draw_obj: "ImageDraw.ImageDraw", text: str, font: "ImageFont.FreeTypeFont") -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 7.5

    def make_frame(
        chicken_x: int,
        *,
        walk_phase: int = 0,
        squashed: bool = False,
        car_x: "int | None" = None,
        car_y: "int | None" = None,
        cleared: int = 0,
        flash_lane: "int | None" = None,
        overlay: str = "",
        net_chg: float = 0.0,
    ) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # Header
        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        draw.text((14, 12), "CHICKEN ROAD", font=font_hdr, fill=WHITE)
        draw.text((155, 12), f"│  {mode.upper()}", font=font_hdr, fill=MUTED)
        if cleared > 0:
            cur_m = mults[cleared - 1]
            cash = f"💰  {_fmt(bet * cur_m)} pts"
            cw = _text_w(draw, cash, font_hdr)
            draw.text((W - 14 - cw, 12), cash, font=font_hdr, fill=GOLD)
        else:
            hint = "Tap Cross to start"
            hw = _text_w(draw, hint, font_info)
            draw.text((W - 14 - hw, 15), hint, font=font_info, fill=MUTED)

        # Grass + sidewalk
        draw.rectangle([0, HDR_H, W, ROAD_TOP], fill=GRASS)
        draw.rectangle([0, ROAD_TOP + ROAD_H, W, H - INFO_H], fill=GRASS)
        draw.rectangle([0, ROAD_TOP - 6, START_X, ROAD_TOP + ROAD_H + 6], fill=SIDEWALK)

        # Road surface
        draw.rectangle([START_X, ROAD_TOP, W - FINISH_W, ROAD_TOP + ROAD_H], fill=ROAD)

        # Lane markers + cleared highlights
        for i in range(num_steps):
            lx = START_X + i * lane_w
            if i < cleared:
                draw.rectangle([lx + 2, ROAD_TOP + 4, lx + lane_w - 2, ROAD_TOP + ROAD_H - 4], fill=(22, 55, 32))
            elif flash_lane == i:
                draw.rectangle([lx + 2, ROAD_TOP + 4, lx + lane_w - 2, ROAD_TOP + ROAD_H - 4], fill=(55, 45, 18))
            # dashed center line
            for dy in range(ROAD_TOP + 8, ROAD_TOP + ROAD_H - 8, 14):
                draw.rectangle([lx + lane_w - 2, dy, lx + lane_w, dy + 8], fill=LANE_LINE)

        # Finish zone (golden egg)
        fx = W - FINISH_W
        draw.rectangle([fx, ROAD_TOP, W, ROAD_TOP + ROAD_H], fill=(35, 30, 18))
        egg_cx = fx + FINISH_W // 2
        egg_cy = ROAD_TOP + ROAD_H // 2
        draw.ellipse([egg_cx - 14, egg_cy - 18, egg_cx + 14, egg_cy + 18], fill=(255, 215, 80), outline=(200, 160, 30), width=2)
        draw.ellipse([egg_cx - 5, egg_cy - 10, egg_cx + 2, egg_cy - 3], fill=(255, 240, 180))

        # Multiplier labels under lanes
        for i in range(num_steps):
            lx = START_X + i * lane_w + lane_w // 2
            m_str = f"{mults[i]:.2f}x"
            mw = _text_w(draw, m_str, font_mult)
            col = GREEN if i < cleared else (GOLD if flash_lane == i else MUTED)
            draw.text((lx - mw // 2, ROAD_TOP + ROAD_H + 8), m_str, font=font_mult, fill=col)

        chicken_y = ROAD_TOP + ROAD_H // 2 + 8
        _draw_chicken_sprite(draw, chicken_x, chicken_y, walk_phase=walk_phase, squashed=squashed)

        if car_x is not None and car_y is not None:
            _draw_car_sprite(draw, car_x, car_y)

        # Info bar
        iy = H - INFO_H
        draw.rectangle([0, iy, W, H], fill=(8, 12, 22))
        uname = (username[:24] + "…") if len(username) > 24 else username
        draw.text((14, iy + 12), uname, font=font_info, fill=MUTED)
        if bet > 0:
            bs = f"Bet: {_fmt(bet)} pts  •  Step {cleared}/{num_steps}"
            bw = _text_w(draw, bs, font_info)
            draw.text((W - 14 - bw, iy + 12), bs, font=font_info, fill=MUTED)

        if overlay:
            rc = RED if overlay == "CRASH" else GOLD
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(ov)
            od.rectangle([0, HDR_H, W, iy], fill=(0, 0, 0, 155))
            img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
            draw = ImageDraw.Draw(img)
            mid = (HDR_H + iy) // 2
            rw = _text_w(draw, overlay, font_res)
            draw.text(((W - rw) // 2, mid - 32), overlay, font=font_res, fill=rc)
            if net_chg != 0.0:
                pfx = "+" if net_chg > 0 else ""
                sub = f"{pfx}{_fmt(net_chg)} pts"
                sw = _text_w(draw, sub, font_sub)
                sc = GREEN if net_chg > 0 else RED
                draw.text(((W - sw) // 2, mid + 14), sub, font=font_sub, fill=sc)

        return img

    frames: list[Image.Image] = []
    durations: list[int] = []

    road_cy = ROAD_TOP + ROAD_H // 2

    if cross_lane is not None and cross_result:
        lane = cross_lane
        from_x = _chicken_x(cross_lane)
        to_x = _chicken_x(cross_lane + 1)
        walk_frames = 5

        for wf in range(walk_frames):
            t = (wf + 1) / walk_frames
            cx = int(from_x + (to_x - from_x) * t)
            if cross_result == "crash" and wf >= walk_frames - 1:
                break
            frames.append(make_frame(
                cx, walk_phase=wf, cleared=cleared_steps,
                flash_lane=lane,
            ))
            durations.append(140)

        if cross_result == "safe":
            frames.append(make_frame(
                to_x, walk_phase=0, cleared=cleared_steps,
                flash_lane=lane,
            ))
            durations.append(600)
            frames.append(make_frame(
                to_x, walk_phase=0, cleared=cleared_steps,
            ))
            durations.append(4_000)
        else:
            # Car crash sequence
            impact_x = int(from_x + (to_x - from_x) * 0.72)
            crash_y = road_cy - 28
            for cf in range(4):
                car_x = impact_x + 80 - cf * 22
                car_y = crash_y - cf * 8
                cx = int(from_x + (to_x - from_x) * (0.55 + cf * 0.05))
                frames.append(make_frame(
                    cx, walk_phase=cf, cleared=cleared_steps,
                    flash_lane=lane, car_x=car_x, car_y=car_y,
                ))
                durations.append(120)
            # Impact
            frames.append(make_frame(
                impact_x, squashed=True, cleared=cleared_steps,
                flash_lane=lane, car_x=impact_x + 8, car_y=crash_y + 10,
            ))
            durations.append(350)
            frames.append(make_frame(
                impact_x, squashed=True, cleared=cleared_steps,
                overlay=result or "CRASH", net_chg=net_change,
            ))
            durations.append(20_000)

    elif result in ("CASHOUT", "WIN"):
        cx = _chicken_x(cleared_steps)
        frames.append(make_frame(
            cx, cleared=cleared_steps,
            overlay=result, net_chg=net_change,
        ))
        durations.append(20_000)

    else:
        cx = _chicken_x(cleared_steps)
        frames.append(make_frame(cx, cleared=cleared_steps))
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


# ── Coin Flip (Hot / Cold) GIF ───────────────────────────────────────────────

COINFLIP_SPIN_FRAMES = 24
COINFLIP_HOLD_MS = 5_000


def _coinflip_ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 2.5


def _coinflip_paste_card(
    base: Image.Image,
    cx: int,
    cy: int,
    emoji_img: Image.Image,
    *,
    card_w: int,
    card_h: int,
    border: tuple = (58, 66, 88),
    dim: float = 1.0,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Paste emoji card; return image and (x1, y1, x2, y2) bounds."""
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x1, y1 = cx - card_w // 2, cy - card_h // 2
    x2, y2 = x1 + card_w, y1 + card_h
    draw.rounded_rectangle([x1, y1, x2, y2], radius=14, fill=(18, 24, 42), outline=border, width=3)
    ex = cx - emoji_img.width // 2
    ey = cy - emoji_img.height // 2
    layer.paste(emoji_img, (ex, ey), emoji_img)
    if dim < 1.0:
        r, g, b, a = layer.split()
        a = a.point(lambda p: int(p * dim) if p else 0)
        layer = Image.merge("RGBA", (r, g, b, a))
    out = base.convert("RGBA")
    out = Image.alpha_composite(out, layer)
    return out.convert("RGB"), (x1, y1, x2, y2)


async def render_coinflip_gif(
    *,
    mode: str,
    left_name: str,
    right_name: str,
    left_side: str,
    right_side: str,
    result: str,
    hot_emoji: str,
    cold_emoji: str,
    bet: float = 0.0,
    left_payout: float = 0.0,
    left_lost: float = 0.0,
    right_payout: float = 0.0,
    right_lost: float = 0.0,
) -> io.BytesIO:
    """Animated Hot/Cold coin flip. mode: 'bot' (2 columns) or 'pvp' (3 columns)."""
    W, H = 660, 360
    BG = (10, 14, 28)
    PANEL = (16, 22, 40)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    CYAN = (56, 189, 248)
    loser_dim = 0.42

    font_hdr = _font(14, bold=True)
    font_cap = _font(11, bold=True)
    font_name = _font(16, bold=True)
    font_lbl = _font(26, bold=True)
    font_amt = _font(18, bold=True)
    font_foot = _font(15, bold=True)
    font_usd = _font(13)

    async with aiohttp.ClientSession() as session:
        hot_img = await _load_emoji_rgba(hot_emoji, 68, session)
        cold_img = await _load_emoji_rgba(cold_emoji, 68, session)

    left_pick = hot_img if left_side == "HOT" else cold_img
    right_pick = hot_img if right_side == "HOT" else cold_img
    result_img = hot_img if result == "HOT" else cold_img

    left_won = left_payout > 0 and left_lost <= 0
    right_won = right_payout > 0 and right_lost <= 0

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    def _short(name: str, mx: int = 16) -> str:
        name = (name or "Player").strip()
        return (name[: mx - 1] + "…") if len(name) > mx else name

    CARD_W, CARD_H = 152, 130

    if mode == "bot":
        left_cx = W // 5
        center_cx = W // 2
        right_cx = 4 * W // 5
        card_cy = 158
        col_hdr_y = 72
        center_card_w, center_card_h = 120, 110
    else:
        left_cx = W // 4
        center_cx = W // 2
        right_cx = 3 * W // 4
        card_cy = 118 + CARD_H // 2
        col_hdr_y = 58

    def _draw_cf_result_stack(
        draw: ImageDraw.ImageDraw,
        cx: int,
        cy: int,
        wl: str,
        pline: str,
        *,
        wcol: tuple,
        pcol: tuple,
    ) -> None:
        """WIN/LOSE + pts stacked and centered at (cx, cy) — between Choice and Outcome."""
        gap = 8
        b1 = draw.textbbox((0, 0), wl, font=font_lbl)
        b2 = draw.textbbox((0, 0), pline, font=font_amt)
        h1, w1 = b1[3] - b1[1], b1[2] - b1[0]
        h2, w2 = b2[3] - b2[1], b2[2] - b2[0]
        total_h = h1 + gap + h2
        y = cy - total_h // 2
        draw.text((cx - w1 / 2, y), wl, font=font_lbl, fill=wcol)
        draw.text((cx - w2 / 2, y + h1 + gap), pline, font=font_amt, fill=pcol)

    def _make_bot_frame(*, spin_hot: bool, final: bool) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 44], fill=PANEL)
        title = "HOT & COLD  •  COIN FLIP"
        tw = _tw(draw, title, font_hdr)
        draw.text(((W - tw) / 2, 12), title, font=font_hdr, fill=CYAN)

        for label, cx in (("Choice", left_cx), ("Outcome", right_cx)):
            lw = _tw(draw, label, font_cap)
            draw.text((cx - lw / 2, col_hdr_y), label, font=font_cap, fill=MUTED)

        spin_img = hot_img if spin_hot else cold_img
        left_border = (GREEN if left_won else RED) if final else (58, 66, 88)
        right_border = GOLD if final else (58, 66, 88)

        img, _ = _coinflip_paste_card(
            img, left_cx, card_cy, left_pick,
            card_w=CARD_W, card_h=CARD_H, border=left_border,
        )

        if final:
            img, _ = _coinflip_paste_card(
                img, right_cx, card_cy, result_img,
                card_w=CARD_W, card_h=CARD_H, border=right_border,
            )
        else:
            img, _ = _coinflip_paste_card(
                img, center_cx, card_cy, spin_img,
                card_w=center_card_w, card_h=center_card_h,
                border=(72, 80, 100),
            )
            # Outcome slot (revealed on final frame)
            img, _ = _coinflip_paste_card(
                img, right_cx, card_cy, cold_img,
                card_w=CARD_W, card_h=CARD_H, border=(42, 48, 64),
                dim=0.2,
            )

        draw = ImageDraw.Draw(img)

        if final:
            wl = "WIN" if left_won else "LOSE"
            wcol = GREEN if left_won else RED
            if left_won:
                pline = f"+{_fmt(left_payout)} pts"
                pcol = GREEN
            else:
                pline = f"-{_fmt(left_lost)} pts"
                pcol = RED
            _draw_cf_result_stack(
                draw, center_cx, card_cy, wl, pline, wcol=wcol, pcol=pcol,
            )

        uname = _short(left_name, 18)
        draw.text((20, H - 34), uname, font=font_foot, fill=WHITE)
        bet_line = f"Bet {_fmt(bet)} pts"
        usd_line = f"${_pts_to_usd(bet):,.2f}"
        bw = _tw(draw, bet_line, font_foot)
        uw = _tw(draw, usd_line, font_usd)
        draw.text((W - 20 - bw, H - 36), bet_line, font=font_foot, fill=MUTED)
        draw.text((W - 20 - uw, H - 20), usd_line, font=font_usd, fill=MUTED)

        return img

    def _draw_cf_side_result(
        draw: ImageDraw.ImageDraw,
        cx: int,
        box: tuple[int, int, int, int],
        *,
        won: bool,
        payout: float,
        lost: float,
    ) -> None:
        """HTW-style result under each player card (not above)."""
        _x1, _y1, x2, y2 = box
        col = GREEN if won else RED
        lbl = "WIN" if won else "LOSE"
        if won and payout > 0:
            pline = f"+{_fmt(payout)} pts"
            usd_val = payout
        elif lost > 0:
            pline = f"-{_fmt(lost)} pts"
            usd_val = lost
        else:
            pline, usd_val = "", 0.0

        lw = _tw(draw, lbl, font_lbl)
        draw.text((cx - lw / 2, y2 - 72), lbl, font=font_lbl, fill=col)
        if pline:
            aw = _tw(draw, pline, font_amt)
            draw.text((cx - aw / 2, y2 - 46), pline, font=font_amt, fill=col)
            usd = _pts_to_usd(usd_val)
            line2 = f"${usd:,.2f}"
            uw = _tw(draw, line2, font_usd)
            draw.text((cx - uw / 2, y2 - 24), line2, font=font_usd, fill=(*col, 200))

    def _make_pvp_frame(*, spin_hot: bool, final: bool) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 44], fill=PANEL)
        title = "HOT & COLD  •  COIN FLIP  •  PVP"
        tw = _tw(draw, title, font_hdr)
        draw.text(((W - tw) / 2, 12), title, font=font_hdr, fill=CYAN)

        ln, rn = _short(left_name), _short(right_name)
        dim_name = (100, 110, 128)
        name_y = 58
        if final:
            lw_n = _tw(draw, ln, font_name)
            rw_n = _tw(draw, rn, font_name)
            draw.text(
                (left_cx - lw_n / 2, name_y), ln, font=font_name,
                fill=WHITE if left_won else dim_name,
            )
            draw.text(
                (right_cx - rw_n / 2, name_y), rn, font=font_name,
                fill=WHITE if right_won else dim_name,
            )
        else:
            for name, cx in ((ln, left_cx), (rn, right_cx)):
                nw = _tw(draw, name, font_name)
                draw.text((cx - nw / 2, name_y), name, font=font_name, fill=WHITE)

        spin_img = hot_img if spin_hot else cold_img
        left_border = GREEN if final and left_won else (RED if final and not left_won else (58, 66, 88))
        right_border = GREEN if final and right_won else (RED if final and not right_won else (58, 66, 88))

        img, left_box = _coinflip_paste_card(
            img, left_cx, card_cy, left_pick,
            card_w=CARD_W, card_h=CARD_H, border=left_border,
            dim=loser_dim if final and not left_won else 1.0,
        )
        img, right_box = _coinflip_paste_card(
            img, right_cx, card_cy, right_pick,
            card_w=CARD_W, card_h=CARD_H, border=right_border,
            dim=loser_dim if final and not right_won else 1.0,
        )

        center_img = result_img if final else spin_img
        center_border = GOLD if final else (58, 66, 88)
        img, _ = _coinflip_paste_card(
            img, center_cx, card_cy, center_img,
            card_w=120, card_h=110, border=center_border,
        )

        draw = ImageDraw.Draw(img)

        if final:
            _draw_cf_side_result(
                draw, left_cx, left_box,
                won=left_won, payout=left_payout, lost=left_lost,
            )
            _draw_cf_side_result(
                draw, right_cx, right_box,
                won=right_won, payout=right_payout, lost=right_lost,
            )

        bet_line = f"Bet {_fmt(bet)} pts"
        usd_line = f"${_pts_to_usd(bet):,.2f}"
        bw = _tw(draw, bet_line, font_foot)
        uw = _tw(draw, usd_line, font_usd)
        draw.text((W - 20 - bw, H - 36), bet_line, font=font_foot, fill=MUTED)
        draw.text((W - 20 - uw, H - 20), usd_line, font=font_usd, fill=MUTED)
        return img

    make_frame = _make_bot_frame if mode == "bot" else _make_pvp_frame

    frames: list[Image.Image] = []
    durations: list[int] = []
    import random as _rnd

    for i in range(COINFLIP_SPIN_FRAMES):
        t = i / max(1, COINFLIP_SPIN_FRAMES - 1)
        eased = _coinflip_ease_out(t)
        if i >= COINFLIP_SPIN_FRAMES - 2:
            spin_hot = result == "HOT"
        elif i >= COINFLIP_SPIN_FRAMES - 7:
            spin_hot = (result == "HOT") if _rnd.random() < eased else (i % 2 == 0)
        else:
            spin_hot = i % 2 == 0
        frames.append(make_frame(spin_hot=spin_hot, final=False))
        durations.append(int(75 + eased * 145))
    for _ in range(4):
        frames.append(make_frame(spin_hot=(result == "HOT"), final=True))
        durations.append(COINFLIP_HOLD_MS // 4)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=durations, loop=0, disposal=2,
    )
    buf.seek(0)
    return buf


# ── Dice GIF (procedural faces, eased roll) ────────────────────────────────────

DICE_SPIN_FRAMES = 28
DICE_HOLD_MS = 5_000

_DICE_PIP_LAYOUT: dict[int, list[tuple[float, float]]] = {
    1: [(0.50, 0.50)],
    2: [(0.30, 0.30), (0.70, 0.70)],
    3: [(0.30, 0.30), (0.50, 0.50), (0.70, 0.70)],
    4: [(0.30, 0.30), (0.70, 0.30), (0.30, 0.70), (0.70, 0.70)],
    5: [(0.30, 0.30), (0.70, 0.30), (0.50, 0.50), (0.30, 0.70), (0.70, 0.70)],
    6: [
        (0.30, 0.24), (0.30, 0.50), (0.30, 0.76),
        (0.70, 0.24), (0.70, 0.50), (0.70, 0.76),
    ],
}

_dice_face_cache: dict[tuple[int, int], Image.Image] = {}


def _dice_ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3.2


def _render_dice_face(value: int, size: int = 92) -> Image.Image:
    value = max(1, min(6, int(value)))
    key = (value, size)
    cached = _dice_face_cache.get(key)
    if cached is not None:
        return cached.copy()

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = 4
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=14,
        fill=(248, 250, 255),
        outline=(200, 210, 228),
        width=2,
    )
    pip_r = max(5, size // 11)
    pip_col = (28, 36, 52)
    for px, py in _DICE_PIP_LAYOUT[value]:
        cx = int(px * size)
        cy = int(py * size)
        draw.ellipse(
            [cx - pip_r, cy - pip_r, cx + pip_r, cy + pip_r],
            fill=pip_col,
        )
    _dice_face_cache[key] = img.copy()
    return img


def _dice_paste_card(
    base: Image.Image,
    cx: int,
    cy: int,
    face: Image.Image,
    *,
    card_w: int,
    card_h: int,
    border: tuple = (58, 66, 88),
    dim: float = 1.0,
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x1, y1 = cx - card_w // 2, cy - card_h // 2
    x2, y2 = x1 + card_w, y1 + card_h
    draw.rounded_rectangle([x1, y1, x2, y2], radius=14, fill=(18, 24, 42), outline=border, width=3)
    ex = cx - face.width // 2
    ey = cy - face.height // 2
    layer.paste(face, (ex, ey), face)
    if dim < 1.0:
        r, g, b, a = layer.split()
        a = a.point(lambda p: int(p * dim) if p else 0)
        layer = Image.merge("RGBA", (r, g, b, a))
    out = base.convert("RGBA")
    out = Image.alpha_composite(out, layer)
    return out.convert("RGB"), (x1, y1, x2, y2)


def _dice_spin_value(frame_i: int, n_frames: int, final: int, *, salt: int) -> int:
    """Eased roll — fast flicker early, settles on final value."""
    import random as _rnd

    t = frame_i / max(1, n_frames - 1)
    eased = _dice_ease_out(t)
    if frame_i >= n_frames - 2:
        return final
    if frame_i >= n_frames - 6 and _rnd.Random(salt + frame_i).random() < eased:
        return final
    return _rnd.Random(salt + frame_i * 31).randint(1, 6)


async def render_dice_gif(
    *,
    mode: str,
    left_name: str,
    right_name: str,
    left_roll: int,
    right_roll: int,
    bet: float = 0.0,
    left_payout: float = 0.0,
    left_lost: float = 0.0,
    right_payout: float = 0.0,
    right_lost: float = 0.0,
    is_push: bool = False,
) -> io.BytesIO:
    """Animated dice — bot: center WIN/LOSE; pvp: HTW-style side results."""
    W, H = 660, 360
    BG = (10, 14, 28)
    PANEL = (16, 22, 40)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    GOLD = (255, 196, 0)
    CYAN = (56, 189, 248)
    loser_dim = 0.42
    CARD_W, CARD_H = 152, 130
    DICE_SZ = 92

    font_hdr = _font(14, bold=True)
    font_cap = _font(11, bold=True)
    font_name = _font(16, bold=True)
    font_lbl = _font(26, bold=True)
    font_amt = _font(18, bold=True)
    font_foot = _font(15, bold=True)
    font_usd = _font(13)
    font_push = _font(28, bold=True)

    left_won = left_payout > 0 and left_lost <= 0 and not is_push
    right_won = right_payout > 0 and right_lost <= 0 and not is_push

    faces = {v: _render_dice_face(v, DICE_SZ) for v in range(1, 7)}

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    def _short(name: str, mx: int = 16) -> str:
        name = (name or "Player").strip()
        return (name[: mx - 1] + "…") if len(name) > mx else name

    def _draw_center_result(
        draw: ImageDraw.ImageDraw,
        cx: int,
        cy: int,
        *,
        label: str,
        pline: str,
        col: tuple,
        pcol: tuple,
    ) -> None:
        gap = 8
        b1 = draw.textbbox((0, 0), label, font=font_lbl)
        b2 = draw.textbbox((0, 0), pline, font=font_amt) if pline else (0, 0, 0, 0)
        h1, w1 = b1[3] - b1[1], b1[2] - b1[0]
        h2, w2 = (b2[3] - b2[1], b2[2] - b2[0]) if pline else (0, 0)
        total_h = h1 + (gap + h2 if pline else 0)
        y = cy - total_h // 2
        draw.text((cx - w1 / 2, y), label, font=font_lbl, fill=col)
        if pline:
            draw.text((cx - w2 / 2, y + h1 + gap), pline, font=font_amt, fill=pcol)

    def _draw_side_result(
        draw: ImageDraw.ImageDraw,
        cx: int,
        box: tuple[int, int, int, int],
        *,
        won: bool,
        payout: float,
        lost: float,
        is_push: bool = False,
    ) -> None:
        """WIN/LOSE + pts below the dice card frame (not on top of the die)."""
        _x1, _y1, x2, y2 = box
        y_lbl = y2 + 10
        y_pts = y2 + 40
        y_usd = y2 + 62

        if is_push:
            if payout <= 0:
                return
            col = GOLD
            pline = f"+{_fmt(payout)} pts"
            aw = _tw(draw, pline, font_amt)
            draw.text((cx - aw / 2, y_pts), pline, font=font_amt, fill=col)
            usd = _pts_to_usd(payout)
            line2 = f"${usd:,.2f}"
            uw = _tw(draw, line2, font_usd)
            draw.text((cx - uw / 2, y_usd), line2, font=font_usd, fill=(*col, 200))
            return

        col = GREEN if won else RED
        lbl = "WIN" if won else "LOSE"
        if won and payout > 0:
            pline = f"+{_fmt(payout)} pts"
            usd_val = payout
        elif lost > 0:
            pline = f"-{_fmt(lost)} pts"
            usd_val = lost
        else:
            pline, usd_val = "", 0.0
        lw = _tw(draw, lbl, font_lbl)
        draw.text((cx - lw / 2, y_lbl), lbl, font=font_lbl, fill=col)
        if pline:
            aw = _tw(draw, pline, font_amt)
            draw.text((cx - aw / 2, y_pts), pline, font=font_amt, fill=col)
            usd = _pts_to_usd(usd_val)
            line2 = f"${usd:,.2f}"
            uw = _tw(draw, line2, font_usd)
            draw.text((cx - uw / 2, y_usd), line2, font=font_usd, fill=(*col, 200))

    if mode == "bot":
        left_cx = W // 5
        center_cx = W // 2
        right_cx = 4 * W // 5
        card_cy = 158
        col_hdr_y = 72
    else:
        left_cx = W // 4
        center_cx = W // 2
        right_cx = 3 * W // 4
        card_cy = 118 + CARD_H // 2
        col_hdr_y = 58

    def _make_bot_frame(left_v: int, right_v: int, *, final: bool) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 44], fill=PANEL)
        title = "DICE  •  HIGH ROLL WINS"
        tw = _tw(draw, title, font_hdr)
        draw.text(((W - tw) / 2, 12), title, font=font_hdr, fill=CYAN)

        for label, cx in ((_short(left_name, 12), left_cx), (_short(right_name, 12), right_cx)):
            lw = _tw(draw, label, font_cap)
            draw.text((cx - lw / 2, col_hdr_y), label, font=font_cap, fill=MUTED)

        if final:
            if is_push:
                left_border = right_border = GOLD
            elif left_won:
                left_border, right_border = GREEN, RED
            else:
                left_border, right_border = RED, GREEN
        else:
            left_border = right_border = (58, 66, 88)

        img, _ = _dice_paste_card(
            img, left_cx, card_cy, faces[left_v],
            card_w=CARD_W, card_h=CARD_H, border=left_border,
        )
        img, _ = _dice_paste_card(
            img, right_cx, card_cy, faces[right_v],
            card_w=CARD_W, card_h=CARD_H, border=right_border,
        )

        draw = ImageDraw.Draw(img)
        if not final:
            vs_y = card_cy - 14
            draw.rounded_rectangle(
                [center_cx - 30, vs_y, center_cx + 30, vs_y + 36],
                radius=12, fill=(28, 36, 58),
            )
            draw.text((center_cx - 12, vs_y + 8), "VS", font=font_name, fill=GOLD)
        elif is_push:
            pt = "PUSH"
            _draw_center_result(
                draw, center_cx, card_cy, label=pt, pline=f"{_fmt(bet)} pts back",
                col=GOLD, pcol=GOLD,
            )
        else:
            if left_won:
                lbl, pline, col, pcol = "WIN", f"+{_fmt(left_payout)} pts", GREEN, GREEN
            else:
                lbl, pline, col, pcol = "LOSE", f"-{_fmt(left_lost)} pts", RED, RED
            _draw_center_result(draw, center_cx, card_cy, label=lbl, pline=pline, col=col, pcol=pcol)

        uname = _short(left_name, 18)
        draw.text((20, H - 34), uname, font=font_foot, fill=WHITE)
        bet_line = f"Bet {_fmt(bet)} pts"
        usd_line = f"${_pts_to_usd(bet):,.2f}"
        bw = _tw(draw, bet_line, font_foot)
        uw = _tw(draw, usd_line, font_usd)
        draw.text((W - 20 - bw, H - 36), bet_line, font=font_foot, fill=MUTED)
        draw.text((W - 20 - uw, H - 20), usd_line, font=font_usd, fill=MUTED)
        return img

    def _make_pvp_frame(left_v: int, right_v: int, *, final: bool) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, 44], fill=PANEL)
        title = "DICE  •  PVP  •  HIGH ROLL WINS"
        tw = _tw(draw, title, font_hdr)
        draw.text(((W - tw) / 2, 12), title, font=font_hdr, fill=CYAN)

        ln, rn = _short(left_name), _short(right_name)
        dim_name = (100, 110, 128)
        name_y = 58
        if final and not is_push:
            lw_n = _tw(draw, ln, font_name)
            rw_n = _tw(draw, rn, font_name)
            draw.text(
                (left_cx - lw_n / 2, name_y), ln, font=font_name,
                fill=WHITE if left_won else dim_name,
            )
            draw.text(
                (right_cx - rw_n / 2, name_y), rn, font=font_name,
                fill=WHITE if right_won else dim_name,
            )
        else:
            for name, cx in ((ln, left_cx), (rn, right_cx)):
                nw = _tw(draw, name, font_name)
                draw.text((cx - nw / 2, name_y), name, font=font_name, fill=WHITE)

        left_border = GREEN if final and left_won else (RED if final and not left_won and not is_push else (58, 66, 88))
        right_border = GREEN if final and right_won else (RED if final and not right_won and not is_push else (58, 66, 88))
        if final and is_push:
            left_border = right_border = GOLD

        img, left_box = _dice_paste_card(
            img, left_cx, card_cy, faces[left_v],
            card_w=CARD_W, card_h=CARD_H, border=left_border,
            dim=loser_dim if final and not left_won and not is_push else 1.0,
        )
        img, right_box = _dice_paste_card(
            img, right_cx, card_cy, faces[right_v],
            card_w=CARD_W, card_h=CARD_H, border=right_border,
            dim=loser_dim if final and not right_won and not is_push else 1.0,
        )

        draw = ImageDraw.Draw(img)
        vs_y = card_cy - 14
        draw.rounded_rectangle(
            [center_cx - 30, vs_y, center_cx + 30, vs_y + 36],
            radius=12, fill=(28, 36, 58),
        )
        draw.text((center_cx - 12, vs_y + 8), "VS", font=font_name, fill=GOLD)

        if final:
            if is_push:
                pt = "PUSH"
                pw = _tw(draw, pt, font_push)
                draw.text((center_cx - pw / 2, card_cy + 8), pt, font=font_push, fill=GOLD)
                _draw_side_result(
                    draw, left_cx, left_box,
                    won=False, payout=left_payout, lost=0, is_push=True,
                )
                _draw_side_result(
                    draw, right_cx, right_box,
                    won=False, payout=right_payout, lost=0, is_push=True,
                )
            else:
                _draw_side_result(
                    draw, left_cx, left_box,
                    won=left_won, payout=left_payout, lost=left_lost,
                )
                _draw_side_result(
                    draw, right_cx, right_box,
                    won=right_won, payout=right_payout, lost=right_lost,
                )

        bet_line = f"Bet {_fmt(bet)} pts"
        usd_line = f"${_pts_to_usd(bet):,.2f}"
        bw = _tw(draw, bet_line, font_foot)
        uw = _tw(draw, usd_line, font_usd)
        draw.text((W - 20 - bw, H - 36), bet_line, font=font_foot, fill=MUTED)
        draw.text((W - 20 - uw, H - 20), usd_line, font=font_usd, fill=MUTED)
        return img

    make_frame = _make_bot_frame if mode == "bot" else _make_pvp_frame
    frames: list[Image.Image] = []
    durations: list[int] = []
    import random as _rnd

    for i in range(DICE_SPIN_FRAMES):
        t = i / max(1, DICE_SPIN_FRAMES - 1)
        eased = _dice_ease_out(t)
        lv = _dice_spin_value(i, DICE_SPIN_FRAMES, left_roll, salt=left_roll * 17)
        rv = _dice_spin_value(i, DICE_SPIN_FRAMES, right_roll, salt=right_roll * 23 + 7)
        frames.append(make_frame(lv, rv, final=False))
        durations.append(int(70 + eased * 130))

    for _ in range(4):
        frames.append(make_frame(left_roll, right_roll, final=True))
        durations.append(DICE_HOLD_MS // 4)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=durations, loop=0, disposal=2,
    )
    buf.seek(0)
    return buf


# ── Case contents preview (PNG) ───────────────────────────────────────────────

CASE_RARITY_RGB = {
    "common": (155, 160, 175),
    "uncommon": (72, 195, 110),
    "rare": (231, 76, 60),
    "epic": (156, 39, 176),
    "legendary": (255, 196, 0),
}

CASE_RARITY_LABEL = {
    "common": "COMMON",
    "uncommon": "UNCOMMON",
    "rare": "RARE",
    "epic": "EPIC",
    "legendary": "LEGENDARY",
}


async def render_case_contents_image(
    *,
    case_name: str,
    case_price: float,
    rows: list[dict],
) -> io.BytesIO:
    """
    Case loot table: emoji, name, value, drop %, rarity colors (yellow/purple/red tier).
    rows: {emoji, name, value, prob, rarity}
    """
    W = 560
    max_rows = 14
    shown = rows[:max_rows]
    extra = max(0, len(rows) - max_rows)
    row_h = 50
    header_h = 72
    footer_h = 28 if extra else 14
    H = header_h + len(shown) * row_h + footer_h

    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    GOLD = config.CARD_GOLD
    MUTED = config.CARD_TEXT_MUTED
    WHITE = config.CARD_TEXT_PRIMARY

    font_title = _font(16, bold=True)
    font_sub = _font(11, bold=True)
    font_name = _font(13, bold=True)
    font_val = _font(12, bold=True)
    font_pct = _font(11, bold=True)
    font_rar = _font(9, bold=True)
    font_foot = _font(10)

    async with aiohttp.ClientSession() as session:
        emoji_cache: dict[str, Image.Image] = {}
        for row in shown:
            em = str(row.get("emoji", "❓"))
            if em not in emoji_cache:
                emoji_cache[em] = await _load_emoji_rgba(em, 36, session)

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    _rounded_rect(draw, (0, 0, W - 1, H - 1), 16, BG, GOLD, 2)
    draw.rectangle([0, 12, 5, H - 12], fill=GOLD)

    title = f"📦 {case_name.upper()[:28]}"
    draw.text((18, 14), title, font=font_title, fill=WHITE)
    price_line = f"Open: {_fmt(case_price)} pts  ·  ${_pts_to_usd(case_price):,.2f}"
    draw.text((18, 38), price_line, font=font_sub, fill=MUTED)
    draw.text((18, 54), "DROP RATE  ·  VALUE", font=font_rar, fill=GOLD)

    y = header_h
    for row in shown:
        rarity = str(row.get("rarity", "common"))
        rgb = CASE_RARITY_RGB.get(rarity, CASE_RARITY_RGB["common"])
        rlabel = CASE_RARITY_LABEL.get(rarity, rarity.upper())

        draw.rounded_rectangle([12, y + 6, 18, y + row_h - 8], radius=3, fill=rgb)
        draw.rounded_rectangle(
            [20, y + 4, W - 14, y + row_h - 6],
            radius=10,
            fill=(22, 28, 48),
            outline=rgb,
            width=2,
        )

        em_key = str(row.get("emoji", "❓"))
        em_img = emoji_cache.get(em_key)
        if em_img:
            ex = 26 + (36 - em_img.width) // 2
            ey = y + 8 + (36 - em_img.height) // 2
            img.paste(em_img, (ex, ey), em_img)

        name = str(row.get("name", "Item"))[:22]
        draw.text((70, y + 10), name, font=font_name, fill=WHITE)
        draw.text((70, y + 28), rlabel, font=font_rar, fill=rgb)

        val = int(row.get("value", 0))
        prob = float(row.get("prob", 0))
        val_txt = f"{_fmt(val)} pts"
        pct_txt = f"{prob:.2f}%"
        vw = draw.textlength(val_txt, font=font_val)
        pw = draw.textlength(pct_txt, font=font_pct)
        draw.text((W - 22 - vw, y + 10), val_txt, font=font_val, fill=WHITE)
        draw.text((W - 22 - pw, y + 28), pct_txt, font=font_pct, fill=rgb)

        y += row_h

    if extra:
        foot = f"+ {extra} more items"
        fw = draw.textlength(foot, font=font_foot)
        draw.text(((W - fw) / 2, y + 6), foot, font=font_foot, fill=MUTED)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


# ── Case opening reel GIF ─────────────────────────────────────────────────────

CASE_SLOT = 72
CASE_GAP = 10
CASE_VISIBLE = 5
CASE_CENTER_COL = 2
CASE_SPIN_FRAMES = 22
CASE_FRAME_MS = 68
CASE_HOLD_MS = 6_000
CASE_ROW_GAP_MULTI = 26
CASE_FRAME_PAD = 14
CASE_LOSER_DIM = 0.38
CASE_WINNER_SCALE = 1.14

_emoji_img_cache: dict[str, Image.Image] = {}


def _parse_emoji_token(emoji: str) -> tuple[str, str | None]:
    """Return ('custom', id) or ('unicode', char)."""
    s = (emoji or "❓").strip()
    if s.startswith("<") and s.endswith(">"):
        try:
            pe = __import__("discord").PartialEmoji.from_str(s)
            if pe.id:
                return "custom", str(pe.id)
        except Exception:
            pass
    return "unicode", s


def _twemoji_url(char: str) -> str | None:
    cps = "-".join(f"{ord(c):x}" for c in char if ord(c) != 0xFE0F)
    if not cps:
        return None
    return f"https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/{cps}.png"


async def _load_emoji_rgba(
    emoji: str,
    size: int,
    session: aiohttp.ClientSession | None = None,
) -> Image.Image:
    key = f"{emoji}:{size}"
    if key in _emoji_img_cache:
        return _emoji_img_cache[key].copy()

    kind, payload = _parse_emoji_token(emoji)
    url = None
    if kind == "custom" and payload:
        url = f"https://cdn.discordapp.com/emojis/{payload}.png?size=128"
    elif kind == "unicode" and payload:
        url = _twemoji_url(payload)

    img = Image.new("RGBA", (size, size), (32, 36, 52, 255))
    if url:
        own = session is None
        if own:
            session = aiohttp.ClientSession()
        try:
            async with session.get(url) as resp:
                if resp.status == 200:
                    raw = await resp.read()
                    em = Image.open(io.BytesIO(raw)).convert("RGBA")
                    em = em.resize((size - 8, size - 8), Image.LANCZOS)
                    ox = (size - em.width) // 2
                    oy = (size - em.height) // 2
                    img.paste(em, (ox, oy), em)
        except Exception:
            pass
        finally:
            if own:
                await session.close()
    else:
        draw = ImageDraw.Draw(img)
        draw.text((size // 2 - 6, size // 2 - 10), "?", font=_font(22, bold=True), fill=(200, 210, 230))

    _emoji_img_cache[key] = img.copy()
    return img


def _case_ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 2.8


def _draw_case_slot(
    base: Image.Image,
    xy: tuple[int, int],
    emoji_img: Image.Image,
    *,
    dim: float = 1.0,
    scale: float = 1.0,
    highlight: bool = False,
) -> None:
    x, y = xy
    sz = int(CASE_SLOT * scale)
    em = emoji_img.resize((sz - 10, sz - 10), Image.LANCZOS)
    layer = Image.new("RGBA", (CASE_SLOT, CASE_SLOT), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    fill = (22, 28, 44) if not highlight else (28, 40, 62)
    border = (58, 66, 88) if not highlight else (46, 213, 96)
    ld.rounded_rectangle([2, 2, CASE_SLOT - 3, CASE_SLOT - 3], radius=12, fill=fill, outline=border, width=3)
    ox = (CASE_SLOT - em.width) // 2
    oy = (CASE_SLOT - em.height) // 2
    layer.paste(em, (ox, oy), em)
    if dim < 1.0:
        r, g, b, a = layer.split()
        a = a.point(lambda p: int(p * dim) if p else 0)
        layer = Image.merge("RGBA", (r, g, b, a))
    base.paste(layer, (x, y), layer)


async def render_case_open_gif(
    items: list[dict],
    winners: list[dict],
    case_price: float,
    *,
    case_name: str = "Case",
) -> io.BytesIO:
    """CS-style reel GIF — center line, RTL scroll, up to 4 stacked rows."""
    if not items or not winners:
        raise ValueError("items and winners required")

    count = min(4, len(winners))
    winners = winners[:count]
    pool = items if len(items) >= 4 else items * 4

    inner_w = CASE_SLOT * CASE_VISIBLE + CASE_GAP * (CASE_VISIBLE - 1) + 48
    row_gap = CASE_ROW_GAP_MULTI if count > 1 else 10
    row_h = CASE_SLOT + 34 + row_gap
    inner_h = 44 + count * row_h + 16
    cx_line = inner_w // 2

    BG = config.CARD_BG_COLOR
    BORDER = config.CARD_BORDER
    GOLD = config.CARD_GOLD
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)
    MUTED = (120, 130, 155)
    WHITE = (245, 247, 255)

    font_lbl = _font(13, bold=True)
    font_val = _font(14, bold=True)

    async with aiohttp.ClientSession() as session:
        emoji_cache: dict[str, Image.Image] = {}
        for it in pool:
            em = it.get("emoji", "❓")
            if em not in emoji_cache:
                emoji_cache[em] = await _load_emoji_rgba(str(em), CASE_SLOT, session)

        step = CASE_SLOT + CASE_GAP
        win_idx = 18
        reels: list[list[dict]] = []
        for w in winners:
            strip = [random.choice(pool) for _ in range(win_idx)]
            strip.append(w)
            strip.extend(random.choice(pool) for _ in range(4))
            reels.append(strip)

        stop_offsets = [(win_idx - (CASE_CENTER_COL - 1)) * step for _ in range(count)]

        frames: list[Image.Image] = []
        durations: list[int] = []

        def _apply_frame(inner: Image.Image) -> Image.Image:
            pad = CASE_FRAME_PAD
            ow, oh = inner.width + pad * 2, inner.height + pad * 2
            framed = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
            fd = ImageDraw.Draw(framed)
            _rounded_rect(fd, (0, 0, ow - 1, oh - 1), 18, BG, GOLD, 3)
            framed.paste(inner, (pad, pad))
            return framed.convert("RGB")

        def _draw_frame(progress: float, *, final: bool) -> Image.Image:
            img = Image.new("RGB", (inner_w, inner_h), BG)
            draw = ImageDraw.Draw(img)
            title = f"CASE  •  {case_name.upper()[:24]}"
            tw = draw.textlength(title, font=font_lbl)
            draw.text(((inner_w - tw) / 2, 10), title, font=font_lbl, fill=GOLD)
            draw.line([(cx_line, 38), (cx_line, inner_h - 10)], fill=GOLD, width=2)

            view_w = CASE_VISIBLE * (CASE_SLOT + CASE_GAP) - CASE_GAP
            left_x = (inner_w - view_w) // 2

            for row_i, strip in enumerate(reels):
                y0 = 44 + row_i * row_h
                off = int(stop_offsets[row_i] * _case_ease_out(progress)) if not final else stop_offsets[row_i]
                start_col = off // (CASE_SLOT + CASE_GAP)

                for col in range(CASE_VISIBLE + 2):
                    idx = start_col + col - 1
                    if idx < 0 or idx >= len(strip):
                        continue
                    it = strip[idx]
                    em_key = str(it.get("emoji", "❓"))
                    em_img = emoji_cache.get(em_key) or emoji_cache[list(emoji_cache.keys())[0]]
                    x = left_x + col * (CASE_SLOT + CASE_GAP) - (off % (CASE_SLOT + CASE_GAP))
                    is_center = (col == CASE_CENTER_COL) and final
                    dim = 1.0 if is_center or not final else CASE_LOSER_DIM
                    scale = CASE_WINNER_SCALE if is_center else 1.0
                    _draw_case_slot(
                        img, (x, y0), em_img,
                        dim=dim, scale=scale, highlight=is_center,
                    )

                if final:
                    w = winners[row_i]
                    val = float(w.get("value", 0))
                    net = val - case_price
                    if net >= 0:
                        txt = f"+{_fmt(val)} pts"
                        col = GREEN
                    else:
                        txt = f"-{_fmt(case_price - val)} pts"
                        col = RED
                    vw = draw.textlength(txt, font=font_val)
                    draw.text(((inner_w - vw) / 2, y0 + CASE_SLOT + 6), txt, font=font_val, fill=col)
                    usd = _pts_to_usd(abs(net if net >= 0 else case_price - val))
                    uline = f"${usd:,.2f}"
                    uw = draw.textlength(uline, font=font_lbl)
                    draw.text(((inner_w - uw) / 2, y0 + CASE_SLOT + 22), uline, font=font_lbl, fill=MUTED)
                elif count > 1:
                    lbl = f"#{row_i + 1}"
                    draw.text((12, y0 + CASE_SLOT // 2 - 6), lbl, font=font_lbl, fill=MUTED)

            return _apply_frame(img)

        for i in range(CASE_SPIN_FRAMES):
            t = (i + 1) / CASE_SPIN_FRAMES
            frames.append(_draw_frame(t, final=False))
            durations.append(CASE_FRAME_MS)

        for _ in range(4):
            frames.append(_draw_frame(1.0, final=True))
            durations.append(CASE_HOLD_MS // 4)

    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=durations, loop=0, optimize=False, disposal=2,
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


# ── Jackpot (multiplayer) ─────────────────────────────────────────────────────

JACKPOT_SPIN_MS = 3_200
JACKPOT_RESULT_HOLD_MS = 12_000


async def _fetch_avatar_static(url: str, size: int) -> Image.Image:
    """Fetch avatar; GIF URLs are requested as PNG via caller."""
    img = await _fetch_avatar(url, size)
    return img if img is not None else _default_avatar(size)


def _jp_short(name: str, mx: int = 14) -> str:
    name = (name or "Player").strip()
    return (name[: mx - 1] + "…") if len(name) > mx else name


async def render_jackpot_lobby_png(
    players: list[dict],
    *,
    pool: float,
    countdown_secs: int | None = None,
    status_line: str = "",
) -> io.BytesIO:
    """Lobby card — avatars, username, bet, win % per player."""
    from Games.jackpot import format_chance, player_chance

    W, H = 680, max(220, 88 + min(len(players), 8) * 72 + 56)
    BG = (8, 12, 24)
    PANEL = (14, 20, 38)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    CYAN = (56, 189, 248)
    GOLD = (255, 196, 0)
    GREEN = (46, 213, 96)

    font_hdr = _font(16, bold=True)
    font_sm = _font(12)
    font_name = _font(14, bold=True)
    font_bet = _font(13, bold=True)
    font_pct = _font(13, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    avatars: list[Image.Image] = []
    for p in players:
        url = p.get("avatar_url") or ""
        avatars.append(await _fetch_avatar_static(url, 48))

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 52], fill=PANEL)
    draw.text((18, 16), "JACKPOT", font=font_hdr, fill=CYAN)

    sub = status_line or f"Pool {_fmt(pool)} pts  •  {len(players)} player(s)"
    draw.text((18, 34), sub[:70], font=font_sm, fill=MUTED)
    if countdown_secs is not None and countdown_secs > 0:
        cd = f"Starts in {countdown_secs}s"
        cw = _tw(draw, cd, font_sm)
        draw.text((W - 18 - cw, 34), cd, font=font_sm, fill=GOLD)

    y = 64
    cols = 2 if len(players) > 4 else 1
    col_w = (W - 48) // cols
    for i, p in enumerate(players):
        col = i % cols
        row = i // cols
        x = 24 + col * col_w
        py = y + row * 68

        bet = float(p.get("bet") or 0)
        pct = player_chance(bet, pool) * 100.0 if pool > 0 else 0.0
        av = avatars[i] if i < len(avatars) else _default_avatar(48)
        img.paste(av, (x, py), av)

        nx = x + 58
        uname = _jp_short(str(p.get("username") or "Player"), 16)
        draw.text((nx, py + 4), uname, font=font_name, fill=WHITE)
        draw.text((nx, py + 24), f"{_fmt(bet)} pts", font=font_bet, fill=MUTED)
        ch = format_chance(pct)
        cw = _tw(draw, ch, font_pct)
        draw.text((nx + col_w - 70 - cw, py + 24), ch, font=font_pct, fill=GREEN)

    foot_y = H - 36
    draw.line([(24, foot_y - 8), (W - 24, foot_y - 8)], fill=(35, 45, 72), width=1)
    foot = f"Total pool {_fmt(pool)} pts"
    draw.text((24, foot_y), foot, font=font_sm, fill=WHITE)
    if len(players) < 2:
        need = "Need 2+ players to start"
        nw = _tw(draw, need, font_sm)
        draw.text((W - 24 - nw, foot_y), need, font=font_sm, fill=GOLD)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf


async def render_jackpot_spin_gif(
    players: list[dict],
    winner_index: int,
    *,
    pool: float,
    payout: float,
    house_edge_pct: float = 2.0,
) -> io.BytesIO:
    """Scroll player boxes right→left; stop on winner; show +payout."""
    from Games.jackpot import format_chance, player_chance

    W, H = 680, 320
    HDR_H = 52
    STRIP_Y = 72
    BOX_W, BOX_H = 118, 140
    GAP = 12
    STEP = BOX_W + GAP
    POINTER_X = W // 2

    BG = (8, 12, 24)
    PANEL = (14, 20, 38)
    WHITE = (245, 247, 255)
    MUTED = (110, 120, 145)
    GREEN = (46, 213, 96)
    GOLD = (255, 196, 0)
    CYAN = (56, 189, 248)

    font_hdr = _font(15, bold=True)
    font_name = _font(13, bold=True)
    font_sm = _font(11)
    font_pct = _font(12, bold=True)
    font_win = _font(36, bold=True)
    font_pay = _font(22, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    avatars: list[Image.Image] = []
    for p in players:
        url = p.get("avatar_url") or ""
        avatars.append(await _fetch_avatar_static(url, 56))

    n = len(players)
    strip_len = max(28, n * 4 + 12)
    win_slot = strip_len - 6
    strip_indices: list[int] = []
    for _ in range(strip_len):
        strip_indices.append(random.randint(0, n - 1) if n else 0)
    strip_indices[win_slot] = winner_index % n

    strip_x_end = POINTER_X - win_slot * STEP - BOX_W // 2
    scroll_dist = STEP * max(10, n * 2)
    strip_x_start = strip_x_end + scroll_dist

    def _draw_player_box(
        base: Image.Image,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        pidx: int,
        *,
        highlight: bool = False,
    ) -> None:
        if pidx < 0 or pidx >= n:
            return
        p = players[pidx]
        bet = float(p.get("bet") or 0)
        pct = player_chance(bet, pool) * 100.0
        border = GOLD if highlight else (48, 58, 88)
        fill = (18, 26, 48) if not highlight else (28, 36, 58)
        draw.rounded_rectangle(
            [x, y, x + BOX_W, y + BOX_H],
            radius=14,
            fill=fill,
            outline=border,
            width=4 if highlight else 2,
        )
        av = avatars[pidx] if pidx < len(avatars) else _default_avatar(56)
        ax, ay = x + (BOX_W - 56) // 2, y + 10
        base.paste(av, (ax, ay), av)
        uname = _jp_short(str(p.get("username") or "Player"), 12)
        uw = _tw(draw, uname, font_name)
        draw.text((x + (BOX_W - uw) / 2, y + 72), uname, font=font_name, fill=WHITE)
        ch = format_chance(pct)
        cw = _tw(draw, ch, font_pct)
        draw.text((x + (BOX_W - cw) / 2, y + 92), ch, font=font_pct, fill=GREEN)
        bs = f"{_fmt(bet)}"
        bw = _tw(draw, bs, font_sm)
        draw.text((x + (BOX_W - bw) / 2, y + 112), bs, font=font_sm, fill=MUTED)

    def make_frame(strip_x: float, *, final: bool) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        draw.text((18, 14), "JACKPOT", font=font_hdr, fill=CYAN)
        sub = f"Pool {_fmt(pool)} pts  •  {house_edge_pct:g}% fee"
        draw.text((18, 32), sub, font=font_sm, fill=MUTED)

        sx = int(strip_x)
        for i, pidx in enumerate(strip_indices):
            cx = sx + i * STEP
            if cx + BOX_W < -30 or cx > W + 30:
                continue
            hi = final and i == win_slot
            _draw_player_box(img, draw, cx, STRIP_Y, pidx, highlight=hi)

        py1, py2 = STRIP_Y - 4, STRIP_Y + BOX_H + 4
        draw.line([(POINTER_X, py1), (POINTER_X, py2)], fill=GOLD, width=3)
        draw.polygon(
            [(POINTER_X, py1 - 2), (POINTER_X - 12, py1 - 18), (POINTER_X + 12, py1 - 18)],
            fill=GOLD,
        )

        # Final: show WIN + payout ONLY under the winner box.
        if final and n > 0:
            winner_cx = sx + win_slot * STEP + BOX_W // 2
            res_y = STRIP_Y + BOX_H + 10
            wl = "WIN"
            ww = _tw(draw, wl, font_win)
            draw.text((winner_cx - ww / 2, res_y), wl, font=font_win, fill=GREEN)
            pay = f"+{_fmt(payout)} pts"
            pw = _tw(draw, pay, font_pay)
            draw.text((winner_cx - pw / 2, res_y + 44), pay, font=font_pay, fill=GREEN)

        return img

    n_anim = 24

    def _ease(t: float) -> float:
        t = min(1.0, max(0.0, t))
        return 1.0 - (1.0 - t) ** 2.4

    frames: list[Image.Image] = []
    durations: list[int] = []
    frame_ms = max(40, JACKPOT_SPIN_MS // n_anim)
    for i in range(n_anim):
        t = _ease((i + 1) / n_anim)
        sx = strip_x_start + (strip_x_end - strip_x_start) * t
        frames.append(make_frame(sx, final=False))
        durations.append(frame_ms)

    final = make_frame(strip_x_end, final=True)
    frames.append(final)
    durations.append(JACKPOT_RESULT_HOLD_MS)
    frames.append(final.copy())
    durations.append(80)

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        optimize=False,
    )
    buf.seek(0)
    return buf



# ── Market Predict (UP/DOWN) ───────────────────────────────────────────────

async def render_market_predict_gif(
    *,
    username: str,
    bet: float,
    player_side: str,  # "UP" or "DOWN"
    result_side: str,  # "UP" or "DOWN"
    won: bool,
    payout: float,  # credited amount (gross win * (1 - house_edge))
) -> io.BytesIO:
    """Market Predict — center horizontal line; final ends UP or DOWN.

    UI inside GIF:
    - Top app bar: "MARKET" + "PREDICT"
    - Middle: animated price line w/ center mark
    - Bottom: username + bet
    """
    W, H = 680, 360
    HDR_H = 56
    INFO_H = 44
    PAD_L = 18
    PAD_R = 18

    CHART_Y1 = HDR_H + 10
    CHART_Y2 = H - INFO_H - 16
    CHART_H = CHART_Y2 - CHART_Y1
    Y_CENTER = CHART_Y1 + CHART_H // 2

    BG = (8, 12, 24)
    PANEL = (14, 20, 38)
    MUTED = (110, 120, 145)
    WHITE = (245, 247, 255)
    CYAN = (56, 189, 248)
    GOLD = (255, 196, 0)
    GREEN = (46, 213, 96)
    RED = (231, 76, 60)

    if player_side not in ("UP", "DOWN"):
        player_side = "UP"
    if result_side not in ("UP", "DOWN"):
        result_side = "DOWN"

    # Dynamic segment color: above center => green, below => red
    def _seg_col(y: int) -> tuple[int, int, int]:
        return GREEN if y < Y_CENTER else RED

    font_hdr = _font(16, bold=True)
    font_sm = _font(12)
    font_name = _font(14, bold=True)
    font_bet = _font(13, bold=True)
    font_side = _font(48, bold=True)
    font_win = _font(30, bold=True)

    def _tw(draw_obj: ImageDraw.ImageDraw, text: str, font) -> float:
        try:
            return draw_obj.textlength(text, font=font)
        except Exception:
            return len(text) * 8

    # Build random-walk series with smooth steering into final direction.
    n_points = 46
    amp = min(120, CHART_H // 2 - 10)
    final_off = int(amp * 0.72)

    series: list[float] = []
    v = 0.0
    steer_steps = 10
    final_v = (-final_off / amp) if result_side == "UP" else (final_off / amp)
    final_v = max(-1.0, min(1.0, final_v))
    for i in range(n_points):
        if i < n_points - steer_steps:
            # random drift + damping towards center
            v += random.uniform(-0.20, 0.20)
            v *= 0.94
        else:
            # smooth steer into the final direction to avoid a "sudden spike" vibe
            t = (i - (n_points - steer_steps)) / max(1, steer_steps - 1)
            # increasing pull strength near the end
            pull = 0.12 + 0.38 * (t ** 1.6)
            v = (1.0 - pull) * v + pull * final_v
            # small noise even while steering
            v += random.uniform(-0.05, 0.05) * (1.0 - t)
            v *= 0.97
        v = max(-1.0, min(1.0, v))
        series.append(v)

    xs = [PAD_L + i * (W - PAD_L - PAD_R) / (n_points - 1) for i in range(n_points)]

    def _y(val: float) -> float:
        y = Y_CENTER + (val * amp)
        # clamp into chart area
        return max(CHART_Y1 + 6, min(CHART_Y2 - 6, y))

    points = [(int(xs[i]), int(_y(series[i]))) for i in range(n_points)]

    n_anim = 22
    spin_frame_ms = 230
    result_hold_ms = 900
    frames: list[Image.Image] = []
    durations: list[int] = []

    for fi in range(n_anim):
        k = int(1 + (fi + 1) * (n_points - 1) / n_anim)
        k = max(2, min(n_points, k))

        img = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # Header app bar
        draw.rectangle([0, 0, W, HDR_H], fill=PANEL)
        title_left = "MARKET"
        title_right = "PREDICT"
        tw1 = _tw(draw, title_left, font_hdr)
        tw2 = _tw(draw, title_right, font_hdr)
        draw.text(((W - (tw1 + 10 + tw2)) / 2, 18), title_left, font=font_hdr, fill=CYAN)
        draw.text(((W - (tw1 + 10 + tw2)) / 2 + tw1 + 10, 18), title_right, font=font_hdr, fill=WHITE)

        pick = f"{player_side}"
        pw = _tw(draw, pick, font_hdr)
        draw.text((W - PAD_R - pw, 18), pick, font=font_hdr, fill=CYAN)

        # Center mark
        draw.line([(PAD_L, Y_CENTER), (W - PAD_R, Y_CENTER)], fill=GOLD, width=3)

        # Chart grid (subtle)
        for t in range(1, 4):
            yy = CHART_Y1 + t * CHART_H / 4
            draw.line([(PAD_L, int(yy)), (W - PAD_R, int(yy))], fill=(22, 30, 48), width=1)

        # Price line (segment colored by above/below center)
        pts = points[:k]
        for j in range(1, len(pts)):
            x1, y1 = pts[j - 1]
            x2, y2 = pts[j]
            col = _seg_col((y1 + y2) // 2)
            draw.line([(x1, y1), (x2, y2)], fill=col, width=5)
        # endpoint dot
        ex, ey = pts[-1]
        end_col = _seg_col(ey)
        draw.ellipse([ex - 7, ey - 7, ex + 7, ey + 7], fill=end_col, outline=(255, 255, 255), width=2)

        # Bottom info bar
        draw.rectangle([0, H - INFO_H, W, H], fill=(8, 12, 22))
        draw.line([(0, H - INFO_H), (W, H - INFO_H)], fill=(35, 45, 72), width=1)
        uname = (username[:20] + "…") if len(username) > 20 else username
        draw.text((18, H - INFO_H + 14), uname, font=font_name, fill=MUTED)
        bet_s = f"Bet {_fmt(bet)} pts"
        bw = _tw(draw, bet_s, font_bet)
        draw.text((W - 18 - bw, H - INFO_H + 14), bet_s, font=font_bet, fill=MUTED)

        frames.append(img)
        durations.append(spin_frame_ms)

    # Final frame with win summary
    img = frames[-1].copy()
    draw = ImageDraw.Draw(img)
    endx, endy = points[-1]

    # Result block centered; color depends on final position relative to center.
    res_col = GREEN if endy < Y_CENTER else RED
    res_y = Y_CENTER - 70
    label = "WIN" if won else "LOSE"
    lw = _tw(draw, label, font_side)
    draw.text(((W - lw) / 2, res_y), label, font=font_side, fill=res_col)

    if won:
        line2 = f"+{_fmt(payout)} pts"
        col2 = GREEN
    else:
        line2 = f"-{_fmt(bet)} pts"
        col2 = RED
    l2w = _tw(draw, line2, font_win)
    draw.text(((W - l2w) / 2, res_y + 46), line2, font=font_win, fill=col2)

    frames.append(img)
    durations.append(result_hold_ms)
    frames.append(img.copy())
    durations.append(80)

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=1,
        optimize=False,
    )
    buf.seek(0)
    return buf

