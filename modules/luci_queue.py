"""Luci dosya kuyruğu — in-game withdraw + deposit inbox."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from config import LUCI_QUEUE_DIR

log = logging.getLogger("flipbot.luci")

PENDING_DIR = LUCI_QUEUE_DIR / "pending"
PROCESSING_DIR = LUCI_QUEUE_DIR / "processing"
RESULTS_DIR = LUCI_QUEUE_DIR / "results"
DEPOSIT_INBOX_DIR = LUCI_QUEUE_DIR / "deposit_inbox"
INDEX_FILE = LUCI_QUEUE_DIR / "pending_index.txt"
PATH_FILE = LUCI_QUEUE_DIR / "QUEUE_PATH.txt"
DEPOSIT_WATCH_FILE = LUCI_QUEUE_DIR / "deposit_watch.json"
BOT_BALANCE_FILE = LUCI_QUEUE_DIR / "bot_balance.json"

ITEM_WL = 242
ITEM_DL = 1796
ITEM_BGL = 7188


def item_id_for(item_type: str) -> int:
    if item_type == "dl":
        return ITEM_DL
    if item_type == "bgl":
        return ITEM_BGL
    return ITEM_WL


def ensure_queue_dirs() -> Path:
    for d in (LUCI_QUEUE_DIR, PENDING_DIR, PROCESSING_DIR, RESULTS_DIR, DEPOSIT_INBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)
    PATH_FILE.write_text(str(LUCI_QUEUE_DIR.resolve()), encoding="utf-8")
    if not INDEX_FILE.exists():
        INDEX_FILE.write_text("", encoding="utf-8")
    return LUCI_QUEUE_DIR.resolve()


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def bot_stock_wl_units(balance: dict[str, Any]) -> int:
    """WL-units: 1 WL + 100 per DL + 10000 per BGL (same as donation.lua)."""
    return (
        int(balance.get("wl") or 0)
        + int(balance.get("dl") or 0) * 100
        + int(balance.get("bgl") or 0) * 10000
    )


def read_bot_balance() -> Optional[dict[str, Any]]:
    return _read_json(BOT_BALANCE_FILE)


def _order_payload(order: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": int(order["id"]),
        "user_id": int(order["user_id"]),
        "growid": str(order["growid"]),
        "item_type": str(order["item_type"]),
        "quantity": int(order["quantity"]),
        "world_name": str(order["world_name"]),
        "item_id": int(order.get("item_id") or item_id_for(order["item_type"])),
        "coins_paid": int(order.get("coins_paid") or 0),
        "created_at": int(order.get("created_at") or time.time()),
    }
    deliveries = order.get("deliveries")
    if isinstance(deliveries, list) and deliveries:
        payload["deliveries"] = [
            {
                "item_type": str(d["item_type"]),
                "quantity": int(d["quantity"]),
                "item_id": int(d.get("item_id") or item_id_for(str(d["item_type"]))),
            }
            for d in deliveries
        ]
        if order.get("delivery_summary"):
            payload["delivery_summary"] = str(order["delivery_summary"])
    return payload


def enqueue_withdraw(order: dict[str, Any]) -> None:
    ensure_queue_dirs()
    oid = int(order["id"])
    body = json.dumps(_order_payload(order), ensure_ascii=False)
    (PENDING_DIR / f"{oid}.json").write_text(body, encoding="utf-8")

    lines = INDEX_FILE.read_text(encoding="utf-8").splitlines()
    ids = [x.strip() for x in lines if x.strip().isdigit()]
    sid = str(oid)
    if sid not in ids:
        ids.append(sid)
    INDEX_FILE.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
    log.info("[Luci] enqueued withdraw #%s", oid)


def set_deposit_watch(seconds: int = 900, user_id: int | None = None) -> None:
    """Luci bot home dünyasında donation dinlesin."""
    ensure_queue_dirs()
    payload = {"until": int(time.time()) + int(seconds)}
    if user_id is not None:
        payload["user_id"] = int(user_id)
    DEPOSIT_WATCH_FILE.write_text(json.dumps(payload), encoding="utf-8")
    log.info("[Luci] deposit watch until %s", payload["until"])




def process_deposit_inbox_sync() -> list[tuple[bool, str, dict | None]]:
    """Process inbox files; returns list of (ok, code, result) for notifications."""
    from modules.ingame_deposit import process_deposit_from_log

    ensure_queue_dirs()
    outcomes: list[tuple[bool, str, dict | None]] = []
    for path in sorted(DEPOSIT_INBOX_DIR.glob("*.json")):
        data = _read_json(path)
        if not data:
            path.unlink(missing_ok=True)
            continue
        growid = str(data.get("growid") or "")
        amount_units = int(data.get("amount_units") or 0)
        if not growid or amount_units <= 0:
            path.unlink(missing_ok=True)
            continue
        msg_id = abs(hash(path.name)) % (2**31 - 1)
        ok, code, result = process_deposit_from_log(
            growid,
            amount_units,
            message_id=msg_id,
            raw_log=f"{growid} {amount_units}",
        )
        if code == "duplicate":
            path.unlink(missing_ok=True)
            continue
        if ok or code in (
            "below_minimum_dl",
            "below_minimum_coins",
            "unknown_growid",
            "zero_coins",
        ):
            path.unlink(missing_ok=True)
            outcomes.append((ok, code, result))
            log.info("[Luci] deposit inbox %s → %s", path.name, code if not ok else "credited")
    return outcomes


async def process_deposit_inbox(bot) -> int:
    outcomes = process_deposit_inbox_sync()
    if not outcomes:
        return 0
    cog = bot.get_cog("IngameDeposit")
    for ok, code, result in outcomes:
        if not cog:
            continue
        if ok and result:
            await cog._notify_user(result)
        elif code == "below_minimum_dl" and result:
            await cog._notify_below_minimum_dl(result)
        elif code == "below_minimum_coins" and result:
            await cog._notify_below_minimum_coins(result)
    return len(outcomes)


def load_processing_order(order_id: int) -> Optional[dict[str, Any]]:
    return _read_json(PROCESSING_DIR / f"{order_id}.json")


async def process_withdraw_results(bot) -> int:
    """Luci results/*.json → kullanıcı DM + bakiye iadesi."""
    from modules.database import get_user_data, set_user_data
    from modules.player import Player
    from modules.ui_v2 import ACCENT_ERROR, ACCENT_SUCCESS, build_detail_panel, send_channel_v2
    from modules.utils import format_balance, get_user_lang

    ensure_queue_dirs()
    handled = 0
    for path in sorted(RESULTS_DIR.glob("*.json")):
        data = _read_json(path)
        if not data or "id" not in data:
            path.unlink(missing_ok=True)
            continue
        oid = int(data["id"])
        status = str(data.get("status", "")).lower()
        reason = str(data.get("reason", "") or "")

        order = load_processing_order(oid)
        if not order:
            path.unlink(missing_ok=True)
            (PENDING_DIR / f"{oid}.json").unlink(missing_ok=True)
            continue

        user_id = int(order["user_id"])
        coins_paid = int(order.get("coins_paid") or 0)
        lang = get_user_lang(user_id)
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)

        history = get_user_data(user_id, "withdraw_history") or {}
        wkey = str(oid)
        entry = history.get(wkey) or {}
        if entry.get("status") in ("rejected", "pending_approval"):
            path.unlink(missing_ok=True)
            (PROCESSING_DIR / f"{oid}.json").unlink(missing_ok=True)
            (PENDING_DIR / f"{oid}.json").unlink(missing_ok=True)
            continue

        entry["status"] = "completed" if status == "completed" else "failed"
        entry["reason"] = reason
        history[wkey] = entry
        set_user_data(user_id, "withdraw_history", history)

        if status == "completed":
            if user:
                view = build_detail_panel(
                    title="✅ In-Game Withdrawal Complete",
                    body=(
                        f"**{order.get('delivery_summary') or (str(order.get('quantity', '?')) + 'x ' + str(order.get('item_type', 'dl')).upper())}** "
                        f"delivered to display box in **`{order.get('world_name', '?')}`**.\n"
                        f"GrowID: `{order.get('growid', '?')}`"
                    ),
                    accent=ACCENT_SUCCESS,
                    emoji="✅",
                )
                try:
                    await send_channel_v2(user, view)
                except Exception:
                    pass
            log.info("[Luci] withdraw #%s completed uid=%s", oid, user_id)
        else:
            if coins_paid > 0 and entry.get("refunded") is not True:
                player = Player(user_id)
                player.add_balance(
                    "real",
                    coins_paid,
                    by="ingame_withdraw_refund",
                    reason=f"Withdraw failed: {reason}",
                )
                entry["refunded"] = True
                history[wkey] = entry
                set_user_data(user_id, "withdraw_history", history)
            if user:
                fail_msg = reason or "unknown"
                if fail_msg == "drop_failed":
                    fail_msg = "Could not drop on display box"
                elif fail_msg == "no_display_box":
                    fail_msg = "No display box found in world"
                elif fail_msg == "warp_failed":
                    fail_msg = "Bot could not enter your world"
                elif fail_msg == "insufficient_bot_stock":
                    fail_msg = "Bot did not have the required locks (BGL/DL/WL)"
                view = build_detail_panel(
                    title="❌ In-Game Withdrawal Failed",
                    body=f"{fail_msg}\n\n**{format_balance(coins_paid, 'real')}** refunded to your balance.",
                    accent=ACCENT_ERROR,
                    emoji="❌",
                )
                try:
                    await send_channel_v2(user, view)
                except Exception:
                    pass
            log.info("[Luci] withdraw #%s failed uid=%s: %s", oid, user_id, reason)

        path.unlink(missing_ok=True)
        (PROCESSING_DIR / f"{oid}.json").unlink(missing_ok=True)
        (PENDING_DIR / f"{oid}.json").unlink(missing_ok=True)
        active = PROCESSING_DIR / "active.txt"
        if active.exists() and active.read_text(encoding="utf-8").strip() == str(oid):
            active.unlink()
        handled += 1

    return handled
