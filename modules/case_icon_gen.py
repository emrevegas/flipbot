"""Compose case icons from chest templates + item emoji overlays."""

from __future__ import annotations

import io
from pathlib import Path

import aiohttp
from PIL import Image

from modules.image_gen import _load_emoji_rgba

TEMPLATES_DIR = Path(__file__).parent.parent / "assets" / "cases" / "templates"
CANVAS = 256

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


def _slot_centers(count: int) -> list[tuple[int, int]]:
    """Pixel centers for 1–4 item overlays inside the chest opening."""
    if count <= 0:
        return []
    if count == 1:
        return [(128, 98)]
    if count == 2:
        return [(106, 98), (150, 98)]
    if count == 3:
        return [(106, 84), (150, 84), (128, 114)]
    return [(106, 84), (150, 84), (106, 114), (150, 114)]


def _item_icon_size(count: int) -> int:
    if count <= 1:
        return 56
    if count == 2:
        return 50
    return 44


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
    count = min(4, len(item_emojis))
    icons = item_emojis[:count]
    size = _item_icon_size(count)
    centers = _slot_centers(count)

    async with aiohttp.ClientSession() as session:
        for em_str, (cx, cy) in zip(icons, centers):
            tile = await _load_emoji_rgba(str(em_str), size, session)
            x = cx - tile.width // 2
            y = cy - tile.height // 2
            base.paste(tile, (x, y), tile)

    out = base.resize((128, 128), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
