#!/usr/bin/env python3
"""Import renamed VegasBet cards from assets/cards/import/ into assets/cards/."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.card_assets import (  # noqa: E402
    IMPORT_DIR,
    copy_sources_to_import,
    get_display_size,
    import_folder,
    save_display_size,
)

CURSOR_IMAGES = Path.home() / "AppData/Roaming/Cursor/User/workspaceStorage/empty-window/images"
RENAME_MAP = IMPORT_DIR / "rename_map.json"


def _apply_rename_map() -> tuple[int, list[str]]:
    if not RENAME_MAP.exists():
        return 0, ["rename_map.json missing"]
    mapping: dict[str, str] = json.loads(RENAME_MAP.read_text(encoding="utf-8"))
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    n = 0
    errors: list[str] = []
    for src_name, label in mapping.items():
        src = CURSOR_IMAGES / src_name
        if not src.exists():
            errors.append(f"missing: {src_name}")
            continue
        if label in seen:
            continue
        seen.add(label)
        dest = IMPORT_DIR / f"{label}.png"
        shutil.copy2(src, dest)
        n += 1
    return n, errors


def _cursor_full_cards() -> list[Path]:
    if not CURSOR_IMAGES.exists():
        return []
    out: list[Path] = []
    for p in CURSOR_IMAGES.glob("*.png"):
        try:
            if p.stat().st_size < 400_000:
                continue
            from PIL import Image

            if Image.open(p).size == (920, 1280):
                out.append(p)
        except OSError:
            pass
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import BJ card PNGs from assets/cards/import/")
    parser.add_argument("--width", type=int, default=None, help="Display width in GIF (default from display.json)")
    parser.add_argument("--height", type=int, default=None, help="Display height in GIF")
    parser.add_argument(
        "--fetch-raw",
        action="store_true",
        help="Copy chat-uploaded full cards from Cursor cache into import/ as _raw_XX.png",
    )
    parser.add_argument(
        "--from-map",
        action="store_true",
        help="Copy labeled cards from rename_map.json into import/ (AC.png, …)",
    )
    args = parser.parse_args()

    if args.from_map:
        n, errors = _apply_rename_map()
        print(f"Copied {n} labeled cards into {IMPORT_DIR}")
        if errors:
            print("Issues:", ", ".join(errors[:12]))
        return 0 if n >= 52 else 1

    if args.fetch_raw:
        sources = _cursor_full_cards()
        n = copy_sources_to_import(sources)
        print(f"Copied {n} full-size cards to {IMPORT_DIR}")
        print("Then: python scripts/import_cards.py --from-map && python scripts/import_cards.py")
        return 0

    w, h = get_display_size()
    if args.width:
        w = args.width
    if args.height:
        h = args.height
    save_display_size(w, h)

    imported, skipped = import_folder(width=w, height=h)
    print(f"Display size: {w}x{h}")
    print(f"Imported ({len(imported)}): {', '.join(sorted(imported)) or '—'}")
    if skipped:
        print(f"Skipped (bad name): {', '.join(skipped)}")
        print("Expected names: AC.png 0H.png 10S.png 1D.png CB.png (rank + C/H/D/S, CB=back)")
    return 0 if imported else 1


if __name__ == "__main__":
    raise SystemExit(main())
