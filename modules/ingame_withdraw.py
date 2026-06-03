"""In-game Luci withdraw — coins ↔ DL using ingame deposit rate."""

from __future__ import annotations

import time
from typing import Optional, Tuple

from modules.ingame_deposit import format_dl_coin_rate, get_ingame_config, is_ingame_configured
from modules.luci_queue import ITEM_DL, ITEM_WL, enqueue_withdraw


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


def create_withdraw_order(
    *,
    user_id: int,
    growid: str,
    world_name: str,
    coins: int,
) -> dict:
    rate = get_dl_to_coin_rate()
    item_type, quantity, item_id, dl_equiv = coins_to_withdraw_order(coins, rate)
    order_id = int(time.time() * 1000)
    order = {
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
    enqueue_withdraw(order)
    return order
