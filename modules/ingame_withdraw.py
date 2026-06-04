"""In-game Luci withdraw — coins ↔ DL, staff approve before Luci queue."""

from __future__ import annotations

import time
from typing import Any, Optional, Tuple

from modules.database import get_data, replace_data
from modules.ingame_deposit import format_dl_coin_rate, get_ingame_config, is_ingame_configured
from modules.luci_queue import (
    ITEM_BGL,
    ITEM_DL,
    ITEM_WL,
    bot_stock_wl_units,
    enqueue_withdraw,
    read_bot_balance,
)

WL_UNITS_PER_DL = 100
WL_UNITS_PER_BGL = 10000

INGAME_WITHDRAW_KEY = "server/ingame_withdrawals"


def _get_withdrawals() -> dict[str, Any]:
    return get_data(INGAME_WITHDRAW_KEY) or {}


def _save_withdrawals(data: dict[str, Any]) -> None:
    replace_data(INGAME_WITHDRAW_KEY, data)


def get_dl_to_coin_rate() -> float:
    cfg = get_ingame_config()
    return float(cfg.get("dl_to_coin_rate", 0) or 0)


def dl_amount_to_wl_units(dl_amount: float) -> int:
    """1 DL = 100 WL-units; 1 BGL = 10000 WL-units (matches donation listener)."""
    return max(0, int(round(float(dl_amount) * WL_UNITS_PER_DL)))


def wl_units_to_deliveries(units: int) -> list[dict[str, Any]]:
    """Split WL-units into BGL + DL + WL stacks (e.g. 13200 → 1 BGL + 32 DL)."""
    units = int(units)
    if units <= 0:
        raise ValueError("zero delivery")
    bgl = units // WL_UNITS_PER_BGL
    rem = units % WL_UNITS_PER_BGL
    dl = rem // WL_UNITS_PER_DL
    wl = rem % WL_UNITS_PER_DL
    out: list[dict[str, Any]] = []
    if bgl > 0:
        out.append({"item_type": "bgl", "quantity": bgl, "item_id": ITEM_BGL})
    if dl > 0:
        out.append({"item_type": "dl", "quantity": dl, "item_id": ITEM_DL})
    if wl > 0:
        out.append({"item_type": "wl", "quantity": wl, "item_id": ITEM_WL})
    return out


def format_delivery_summary(deliveries: list[dict[str, Any]]) -> str:
    return " + ".join(f"{int(d['quantity'])}x {str(d['item_type']).upper()}" for d in deliveries)


def format_bot_stock_short(balance: dict[str, Any]) -> str:
    bgl = int(balance.get("bgl") or 0)
    dl = int(balance.get("dl") or 0)
    wl = int(balance.get("wl") or 0)
    equiv_dl = bot_stock_wl_units(balance) / WL_UNITS_PER_DL
    return f"{bgl} BGL, {dl} DL, {wl} WL (~{equiv_dl:.1f} DL)"


def coins_to_deliveries(coins: int, rate: float) -> Tuple[list[dict[str, Any]], float, str, int]:
    """
    Convert coins to ordered delivery lines (BGL before DL before WL).

    Returns (deliveries, dl_equivalent, summary, required_wl_units).
    """
    rate = float(rate)
    if rate <= 0:
        raise ValueError("Invalid DL rate")
    dl_amount = coins / rate
    units = dl_amount_to_wl_units(dl_amount)
    if units <= 0:
        units = 1
    deliveries = wl_units_to_deliveries(units)
    return deliveries, float(dl_amount), format_delivery_summary(deliveries), units


def validate_bot_stock(required_wl_units: int) -> Optional[str]:
    balance = read_bot_balance()
    if not balance:
        return "Bot lock balance is not available yet. Try again in a few seconds."
    have = bot_stock_wl_units(balance)
    if have < required_wl_units:
        need_dl = required_wl_units / WL_UNITS_PER_DL
        have_dl = have / WL_UNITS_PER_DL
        return (
            f"Bot does not have enough locks for this withdrawal "
            f"(need ~{need_dl:.1f} DL, bot has ~{have_dl:.1f} DL: {format_bot_stock_short(balance)})."
        )
    return None


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
    _, _, _, required_units = coins_to_deliveries(coins, rate)
    return validate_bot_stock(required_units)


def build_withdraw_order(
    *,
    user_id: int,
    growid: str,
    world_name: str,
    coins: int,
) -> dict[str, Any]:
    rate = get_dl_to_coin_rate()
    deliveries, dl_equiv, summary, required_units = coins_to_deliveries(coins, rate)
    primary = deliveries[0]
    order_id = int(time.time() * 1000)
    return {
        "id": order_id,
        "user_id": user_id,
        "growid": growid.strip(),
        "world_name": world_name.strip().upper(),
        "item_type": primary["item_type"],
        "quantity": int(primary["quantity"]),
        "item_id": int(primary["item_id"]),
        "deliveries": deliveries,
        "delivery_summary": summary,
        "required_wl_units": required_units,
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

    required = int(w.get("required_wl_units") or 0)
    if required <= 0:
        _, _, _, required = coins_to_deliveries(int(w.get("coins_paid") or 0), get_dl_to_coin_rate())
    stock_err = validate_bot_stock(required)
    if stock_err:
        return False, "insufficient_bot_stock", w

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
