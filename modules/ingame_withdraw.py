"""In-game Luci withdraw — coins ↔ DL, staff approve before Luci queue."""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple

from modules.database import get_data, replace_data
from modules.ingame_deposit import format_dl_coin_rate, get_ingame_config, is_ingame_configured
from modules.luci_queue import ITEM_DL, ITEM_WL, enqueue_withdraw

INGAME_WITHDRAW_KEY = "server/ingame_withdrawals"


def _get_withdrawals() -> dict[str, Any]:
    return get_data(INGAME_WITHDRAW_KEY) or {}


def _save_withdrawals(data: dict[str, Any]) -> None:
    replace_data(INGAME_WITHDRAW_KEY, data)


def get_dl_to_coin_rate() -> float:
    cfg = get_ingame_config()
    return float(cfg.get("dl_to_coin_rate", 0) or 0)


def coins_to_withdraw_order(coins: int, rate: float) -> Tuple[str, int, int, float]:
    """
    Convert bot coins to Growtopia delivery.

    Returns (item_type, quantity, item_id, dl_equivalent).
    Uses DL when >= 1 DL; otherwise WL.
    """
    rate = float(rate)
    if rate <= 0:
        raise ValueError("Invalid DL rate")
    dl_amount = coins / rate
    if dl_amount >= 1.0:
        qty = max(1, int(dl_amount))
        return "dl", qty, ITEM_DL, float(qty)
    wl_rate = rate / 100.0
    wl_qty = max(1, int(round(coins / wl_rate)))
    return "wl", wl_qty, ITEM_WL, dl_amount


def validate_withdraw_request(
    coins: int,
    balance: int,
    min_withdrawal: int,
) -> Optional[str]:
    if coins <= 0:
        return "Invalid amount."
    if coins < min_withdrawal:
        return f"Minimum withdrawal is {min_withdrawal} coins."
    if balance < coins:
        return "Insufficient balance."
    cfg = get_ingame_config()
    if not cfg.get("enabled"):
        return "In-game withdrawals are disabled."
    if not is_ingame_configured(cfg):
        return "In-game bot is not configured. Contact staff."
    rate = get_dl_to_coin_rate()
    if rate <= 0:
        return "Exchange rate not configured."
    return None


def build_withdraw_order(
    *,
    user_id: int,
    growid: str,
    world_name: str,
    coins: int,
) -> dict[str, Any]:
    rate = get_dl_to_coin_rate()
    item_type, quantity, item_id, dl_equiv = coins_to_withdraw_order(coins, rate)
    order_id = int(time.time() * 1000)
    return {
        "id": order_id,
        "user_id": user_id,
        "growid": growid.strip(),
        "world_name": world_name.strip().upper(),
        "item_type": item_type,
        "quantity": quantity,
        "item_id": item_id,
        "coins_paid": coins,
        "dl_equivalent": dl_equiv,
        "rate_display": format_dl_coin_rate(rate),
        "created_at": int(time.time()),
    }


def create_withdraw_order(
    *,
    user_id: int,
    growid: str,
    world_name: str,
    coins: int,
) -> dict[str, Any]:
    """Deduct coins first (caller); store pending staff approval — does not enqueue Luci."""
    order = build_withdraw_order(
        user_id=user_id,
        growid=growid,
        world_name=world_name,
        coins=coins,
    )
    wid = str(order["id"])
    store = _get_withdrawals()
    store[wid] = {
        **order,
        "status": "pending",
        "log_channel_id": None,
        "log_message_id": None,
    }
    _save_withdrawals(store)
    return order


def get_pending_withdrawal(withdrawal_id: str) -> Optional[dict[str, Any]]:
    return _get_withdrawals().get(str(withdrawal_id))


def approve_withdrawal(withdrawal_id: str, approved_by: int) -> tuple[bool, str, Optional[dict[str, Any]]]:
    wid = str(withdrawal_id)
    store = _get_withdrawals()
    w = store.get(wid)
    if not w:
        return False, "not_found", None
    if w.get("status") != "pending":
        return False, f"already_{w.get('status')}", w

    enqueue_withdraw(w)
    w["status"] = "approved"
    w["approved_by"] = int(approved_by)
    w["approved_at"] = int(time.time())
    store[wid] = w
    _save_withdrawals(store)
    return True, "ok", w


def reject_withdrawal(withdrawal_id: str, rejected_by: int) -> tuple[bool, str, Optional[dict[str, Any]]]:
    wid = str(withdrawal_id)
    store = _get_withdrawals()
    w = store.get(wid)
    if not w:
        return False, "not_found", None
    if w.get("status") != "pending":
        return False, f"already_{w.get('status')}", w

    w["status"] = "rejected"
    w["rejected_by"] = int(rejected_by)
    w["rejected_at"] = int(time.time())
    store[wid] = w
    _save_withdrawals(store)
    return True, "ok", w


def update_withdrawal_log_ids(withdrawal_id: str, channel_id: int, message_id: int) -> None:
    wid = str(withdrawal_id)
    store = _get_withdrawals()
    if wid not in store:
        return
    store[wid]["log_channel_id"] = int(channel_id)
    store[wid]["log_message_id"] = int(message_id)
    _save_withdrawals(store)
