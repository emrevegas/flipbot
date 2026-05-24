#!/usr/bin/env python3
"""
VegasBet (vegasbot) → FlipBot item library migration.

Reads ``server/cases`` from VegasBot ``panel.db`` and merges the global
``items`` map into FlipBot's ``panel.db`` (same key).

Default paths (VDS):
  Source: C:\\Users\\Administrator\\Desktop\\vegasbot\\database\\panel.db
  Target: C:\\Users\\Administrator\\Desktop\\flipbot\\database\\panel.db

Usage (on the server, bot stopped recommended):
  cd C:\\Users\\Administrator\\Desktop\\flipbot
  python scripts\\migrate_vegas_items_to_flipbot.py

  python scripts\\migrate_vegas_items_to_flipbot.py --dry-run
  python scripts\\migrate_vegas_items_to_flipbot.py --full
  python scripts\\migrate_vegas_items_to_flipbot.py --overwrite
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

KV_KEY = "server/cases"

DEFAULT_VEGAS = Path(r"C:\Users\Administrator\Desktop\vegasbot\database\panel.db")
DEFAULT_FLIP = Path(r"C:\Users\Administrator\Desktop\flipbot\database\panel.db")


def _load_cases_json(db_path: Path) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key = ?", (KV_KEY,)
        ).fetchone()
        if not row or not row[0]:
            return {"items": {}, "cases": {}, "community_cases": {}, "settings": {}}
        data = json.loads(row[0])
        if not isinstance(data, dict):
            raise ValueError(f"{db_path}: {KV_KEY} is not a JSON object")
        return data
    finally:
        conn.close()


def _save_cases_json(db_path: Path, data: dict, *, dry_run: bool) -> None:
    if dry_run:
        return
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        payload = json.dumps(data, ensure_ascii=False)
        conn.execute(
            "INSERT OR REPLACE INTO kv_store(key, value) VALUES (?, ?)",
            (KV_KEY, payload),
        )
        conn.commit()
    finally:
        conn.close()


def _backup_db(db_path: Path, *, dry_run: bool) -> Path | None:
    if dry_run:
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = db_path.with_suffix(db_path.suffix + f".bak-{ts}")
    shutil.copy2(db_path, dest)
    return dest


def _norm_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    try:
        value = int(item.get("value", 0))
    except (TypeError, ValueError):
        value = 0
    emoji = str(item.get("emoji", "❓")).strip() or "❓"
    return {"name": name, "emoji": emoji, "value": value}


def merge_items(
    target: dict,
    source: dict,
    *,
    overwrite: bool,
) -> tuple[int, int, int]:
    """Returns (added, updated, skipped)."""
    tgt_items = target.setdefault("items", {})
    src_items = source.get("items") or {}
    if not isinstance(src_items, dict):
        raise ValueError("Source items is not a dict")

    added = updated = skipped = 0
    for iid, raw in src_items.items():
        iid = str(iid)
        norm = _norm_item(raw)
        if norm is None:
            skipped += 1
            continue
        if iid in tgt_items and not overwrite:
            skipped += 1
            continue
        if iid in tgt_items:
            updated += 1
        else:
            added += 1
        tgt_items[iid] = norm
    return added, updated, skipped


def merge_bucket(
    target: dict,
    source: dict,
    bucket: str,
    *,
    overwrite: bool,
) -> tuple[int, int, int]:
    tgt = target.setdefault(bucket, {})
    src = source.get(bucket) or {}
    if not isinstance(src, dict):
        return 0, 0, 0
    added = updated = skipped = 0
    for cid, case in src.items():
        cid = str(cid)
        if not isinstance(case, dict):
            skipped += 1
            continue
        if cid in tgt and not overwrite:
            skipped += 1
            continue
        if cid in tgt:
            updated += 1
        else:
            added += 1
        tgt[cid] = case
    return added, updated, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate VegasBot case item library into FlipBot panel.db",
    )
    parser.add_argument(
        "--vegas-db",
        type=Path,
        default=DEFAULT_VEGAS,
        help=f"VegasBot panel.db (default: {DEFAULT_VEGAS})",
    )
    parser.add_argument(
        "--flip-db",
        type=Path,
        default=DEFAULT_FLIP,
        help=f"FlipBot panel.db (default: {DEFAULT_FLIP})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing item/case ids in FlipBot",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also copy official cases and community_cases buckets",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not copy flipbot panel.db before write",
    )
    args = parser.parse_args()

    vegas_path: Path = args.vegas_db.resolve()
    flip_path: Path = args.flip_db.resolve()

    print("VegasBet -> FlipBot migration")
    print(f"  Source: {vegas_path}")
    print(f"  Target: {flip_path}")
    if args.dry_run:
        print("  Mode:   DRY RUN (no writes)")
    print()

    src = _load_cases_json(vegas_path)
    tgt = _load_cases_json(flip_path)

    src_item_count = len(src.get("items") or {})
    tgt_item_count_before = len(tgt.get("items") or {})
    print(f"Source items: {src_item_count}")
    print(f"Target items (before): {tgt_item_count_before}")

    if src_item_count == 0:
        print("\nNo items in VegasBot - nothing to migrate.")
        return 1

    if not args.dry_run and not args.no_backup:
        bak = _backup_db(flip_path, dry_run=False)
        print(f"\nBackup: {bak}")

    ia, iu, is_ = merge_items(tgt, src, overwrite=args.overwrite)
    print(f"\nItems: +{ia} new, ~{iu} updated, {is_} skipped")

    if args.full:
        ca, cu, cs = merge_bucket(tgt, src, "cases", overwrite=args.overwrite)
        print(f"Official cases: +{ca} new, ~{cu} updated, {cs} skipped")
        cca, ccu, ccs = merge_bucket(tgt, src, "community_cases", overwrite=args.overwrite)
        print(f"Community cases: +{cca} new, ~{ccu} updated, {ccs} skipped")
        # settings: only publish_fee if missing
        src_set = src.get("settings") or {}
        if isinstance(src_set, dict) and src_set:
            tgt.setdefault("settings", {})
            if "publish_fee" not in tgt["settings"] and "publish_fee" in src_set:
                tgt["settings"]["publish_fee"] = src_set["publish_fee"]
                print("Settings: copied publish_fee")

    tgt.setdefault("cases", {})
    tgt.setdefault("community_cases", {})
    tgt.setdefault("settings", {})

    after = len(tgt.get("items") or {})
    print(f"\nTarget items (after): {after}")

    _save_cases_json(flip_path, tgt, dry_run=args.dry_run)
    if args.dry_run:
        print("\nDry run complete - re-run without --dry-run to apply.")
    else:
        print("\nDone. Restart FlipBot and use .cases or /items.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(1)
