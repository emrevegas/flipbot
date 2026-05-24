"""Compose case icons from chest templates + item emoji overlays."""

from __future__ import annotations

import hashlib
import io
import math
import random
from pathlib import Path

import aiohttp
from PIL import Image

from modules.image_gen import _load_emoji_rgba

TEMPLATES_DIR = Path(__file__).parent.parent / "assets" / "cases" / "templates"
CANVAS = 256

# Chest interior bowl on 256×256 template (x0, y0, x1, y1)
INTERIOR_BOUNDS = (76, 56, 180, 128)

TEMPLATE_LABELS: dict[str, str] = {
    "wood": "Classic Wood",
    "ice": "Ice Crystal",
    "nature": "Overgrown",
    "gold": "Golden Ornate",
    "steampunk": "Steampunk",
    "ocean": "Oceanic",
    "celestial": "Celestial",
    "gothic": "Gothic",
}

TEMPLATE_IDS = list(TEMPLATE_LABELS.keys())


def _layout_seed(template_id: str, item_emojis: list[str]) -> int:
    key = f"{template_id}|{'|'.join(item_emojis)}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)


def _base_sizes(count: int) -> list[int]:
    if count == 1:
        return [92]
    if count == 2:
        return [84, 80]
    if count == 3:
        return [78, 74, 70]
    return [74, 70, 66, 62]


def _scatter_anchors(count: int, rng: random.Random) -> list[tuple[int, int]]:
    """Loose anchor points inside the chest — not a symmetric grid."""
    pools: dict[int, list[tuple[int, int]]] = {
        1: [(128, 92)],
        2: [(108, 94), (152, 88)],
        3: [(102, 82), (154, 90), (128, 112)],
        4: [(98, 78), (158, 84), (104, 112), (150, 108)],
    }
    anchors = list(pools.get(count, pools[4][:count]))
    rng.shuffle(anchors)
    out: list[tuple[int, int]] = []
    for ax, ay in anchors:
        out.append((
            ax + rng.randint(-14, 14),
            ay + rng.randint(-10, 10),
        ))
    return out


def _random_tilt(rng: random.Random) -> float:
    # Mostly sideways; occasional stronger flip.
    base = rng.uniform(22, 58) * rng.choice([-1.0, 1.0])
    if rng.random() < 0.25:
        base += rng.uniform(-12, 12)
    return base


def _rotated_half_extents(size: float, angle_deg: float) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    half = size / 2
    xs: list[float] = []
    ys: list[float] = []
    for x, y in ((-half, -half), (half, -half), (half, half), (-half, half)):
        xs.append(x * cos_a - y * sin_a)
        ys.append(x * sin_a + y * cos_a)
    return max(abs(min(xs)), abs(max(xs))), max(abs(min(ys)), abs(max(ys)))


def _fits_interior(cx: int, cy: int, size: float, angle_deg: float, margin: int = 2) -> bool:
    x0, y0, x1, y1 = INTERIOR_BOUNDS
    hw, hh = _rotated_half_extents(size, angle_deg)
    return (
        cx - hw >= x0 + margin
        and cx + hw <= x1 - margin
        and cy - hh >= y0 + margin
        and cy + hh <= y1 - margin
    )


def _plan_layout(template_id: str, item_emojis: list[str]) -> list[tuple[int, int, int, float, float]]:
    """Return (cx, cy, size, angle_deg, scale) per item — scattered inside chest."""
    count = min(4, len(item_emojis))
    rng = random.Random(_layout_seed(template_id, item_emojis[:count]))
    sizes = _base_sizes(count)
    rng.shuffle(sizes)
    anchors = _scatter_anchors(count, rng)

    placements: list[tuple[int, int, int, float, float]] = []

    for anchor, base_size in zip(anchors, sizes):
        scale = rng.uniform(0.96, 1.1)
        size = int(base_size * scale)
        angle = _random_tilt(rng)

        placed = False
        for _ in range(32):
            cx = anchor[0] + rng.randint(-10, 10)
            cy = anchor[1] + rng.randint(-8, 8)
            trial_angle = angle + rng.uniform(-8, 8)
            if _fits_interior(cx, cy, size, trial_angle, margin=3):
                placements.append((cx, cy, size, trial_angle, scale))
                placed = True
                break

        if not placed:
            cx, cy = anchor
            placements.append((cx, cy, size, angle, scale))

    placements.sort(key=lambda p: (p[1], p[0]))
    return placements


def _paste_scattered(base: Image.Image, tile: Image.Image, cx: int, cy: int, angle_deg: float) -> None:
    rotated = tile.rotate(
        angle_deg,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )
    x = cx - rotated.width // 2
    y = cy - rotated.height // 2
    base.paste(rotated, (x, y), rotated)


def _load_template(template_id: str) -> Image.Image:
    path = TEMPLATES_DIR / f"{template_id}.png"
    if not path.exists():
        raise FileNotFoundError(f"Case template not found: {template_id}")
    return Image.open(path).convert("RGBA")


async def render_case_icon(template_id: str, item_emojis: list[str]) -> bytes:
    """Return PNG bytes (128×128) for Discord application emoji upload."""
    if not item_emojis:
        raise ValueError("At least one item emoji required")
    if template_id not in TEMPLATE_LABELS:
        raise ValueError(f"Unknown template: {template_id}")

    base = _load_template(template_id).resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    icons = item_emojis[:4]
    layout = _plan_layout(template_id, icons)

    async with aiohttp.ClientSession() as session:
        for em_str, (cx, cy, size, angle, _scale) in zip(icons, layout):
            tile = await _load_emoji_rgba(str(em_str), size, session)
            _paste_scattered(base, tile, cx, cy, angle)

    out = base.resize((128, 128), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
