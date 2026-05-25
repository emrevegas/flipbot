"""Blackjack card PNG import and display sizing."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

CARDS_DIR = Path(__file__).parent.parent / "assets" / "cards"
IMPORT_DIR = CARDS_DIR / "import"
DISPLAY_CFG = CARDS_DIR / "display.json"

# Emoji-style names (AC, 0H, 1D, 10S, CB) → asset keys (Ac, 10h, back)
_RANK_MAP = {
    "A": "A",
    "2": "2",
    "3": "3",
    "4": "4",
    "5": "5",
    "6": "6",
    "7": "7",
    "8": "8",
    "9": "9",
    "0": "10",
    "10": "10",
    "J": "J",
    "Q": "Q",
    "K": "K",
}
_SUIT_MAP = {"C": "c", "H": "h", "D": "d", "S": "s", "c": "c", "h": "h", "d": "d", "s": "s"}

_DEFAULT_W, _DEFAULT_H = 92, 128
_CUSTOM_MIN_BYTES = 8_000


def get_display_size() -> tuple[int, int]:
    if DISPLAY_CFG.exists():
        try:
            data = json.loads(DISPLAY_CFG.read_text(encoding="utf-8"))
            return int(data["width"]), int(data["height"])
        except (KeyError, TypeError, ValueError):
            pass
    return _DEFAULT_W, _DEFAULT_H


def save_display_size(width: int, height: int) -> None:
    DISPLAY_CFG.write_text(
        json.dumps({"width": width, "height": height}, indent=2),
        encoding="utf-8",
    )


def normalize_import_stem(stem: str) -> str | None:
    """AC / 0H / 1D / 10S / CB → Ac / 10h / back."""
    s = stem.strip().upper()
    if s in ("CB", "BACK", "CARD_BACK"):
        return "back"
    m = re.fullmatch(r"(10|[A2-90JQK1])([CHDS])", s)
    if not m:
        return None
    rank = _RANK_MAP.get(m.group(1))
    suit = _SUIT_MAP.get(m.group(2))
    if not rank or not suit:
        return None
    return "back" if rank == "back" else f"{rank}{suit}"


def is_custom_asset(path: Path) -> bool:
    try:
        return path.exists() and path.stat().st_size >= _CUSTOM_MIN_BYTES
    except OSError:
        return False


def resize_card(img: Image.Image, width: int, height: int) -> Image.Image:
    if img.size == (width, height):
        return img
    return img.resize((width, height), Image.Resampling.LANCZOS)


def key_to_import_stem(key: str) -> str:
    """Asset key Ah / 10c / back → import filename stem (AH / 0C / CB)."""
    if key == "back":
        return "CB"
    rank = key[:-1] if len(key) > 1 else key
    suit = key[-1].upper()
    if rank == "10":
        rank = "0"
    return f"{rank}{suit}"


def resolve_card_path(key: str) -> Path | None:
    """Prefer large PNGs in import/, then assets/cards/."""
    found: list[Path] = []
    if key == "back":
        names = ("CB.png", "back.png", "BACK.png", "Card_Back.png")
        for folder in (IMPORT_DIR, CARDS_DIR):
            for name in names:
                p = folder / name
                if p.is_file():
                    found.append(p)
    else:
        stem = key_to_import_stem(key)
        if IMPORT_DIR.is_dir():
            for src in IMPORT_DIR.glob("*.png"):
                if src.name.startswith("_"):
                    continue
                norm = normalize_import_stem(src.stem)
                if norm == key:
                    found.append(src)
        for folder in (IMPORT_DIR, CARDS_DIR):
            for name in (
                f"{stem}.png",
                f"{key}.png",
                f"{key[0].upper()}{key[1:]}.png" if len(key) > 1 else f"{key}.png",
            ):
                p = folder / name
                if p.is_file():
                    found.append(p)
    if not found:
        return None
    # Prefer committed resized PNGs in assets/cards/ (VDS has no import/*.png).
    for p in found:
        if p.parent == CARDS_DIR and is_custom_asset(p):
            return p
    return max(found, key=lambda p: p.stat().st_size)


def round_card_corners(img: Image.Image, radius: int = 12) -> Image.Image:
    """Soft rounded corners on RGBA card."""
    img = img.convert("RGBA")
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    img.putalpha(Image.composite(img.split()[3], Image.new("L", (w, h), 0), mask))
    return img


def load_card_image(
    key: str,
    width: int,
    height: int,
    *,
    corner_radius: int = 12,
) -> Image.Image | None:
    path = resolve_card_path(key)
    if not path:
        return None
    img = Image.open(path).convert("RGBA")
    img = resize_card(img, width, height)
    if corner_radius > 0:
        img = round_card_corners(img, corner_radius)
    return img


def import_one(src: Path, *, width: int, height: int) -> str | None:
    key = normalize_import_stem(src.stem)
    if not key:
        return None
    img = Image.open(src).convert("RGBA")
    out = resize_card(img, width, height)
    dest = CARDS_DIR / ("back.png" if key == "back" else f"{key}.png")
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    out.save(dest, "PNG", optimize=True)
    return dest.name


def import_folder(
    folder: Path | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
) -> tuple[list[str], list[str]]:
    """Import all PNGs from import/ (renamed stems) into assets/cards/."""
    folder = folder or IMPORT_DIR
    w, h = (width, height) if width and height else get_display_size()
    save_display_size(w, h)

    imported: list[str] = []
    skipped: list[str] = []
    if not folder.exists():
        return imported, skipped

    for src in sorted(folder.glob("*.png")):
        if src.name.startswith("_"):
            continue
        name = import_one(src, width=w, height=h)
        if name:
            imported.append(name)
        else:
            skipped.append(src.name)
    return imported, skipped


def copy_sources_to_import(sources: list[Path], dest_dir: Path | None = None) -> int:
    """Copy raw PNGs into import/ for manual rename (AC.png, 0H.png, …)."""
    dest_dir = dest_dir or IMPORT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, src in enumerate(sources, 1):
        if not src.exists():
            continue
        target = dest_dir / f"_raw_{i:02d}.png"
        shutil.copy2(src, target)
        n += 1
    return n
