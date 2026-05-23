"""In-game (Growtopia) deposit log parsing and auto-credit."""

import re
import time
from typing import Optional, Tuple

from modules.database import get_data, set_data, set_user_data, get_user_data

INGAME_METHOD_KEY = "ingame"
INGAME_MIN_DL = 5.0
INGAME_MIN_DL_UNITS = int(INGAME_MIN_DL * 100)  # log amounts use 0.01 DL units


def dl_amount_from_units(amount_units: int) -> float:
    """Convert log amount (0.01 DL units) to DL (e.g. 1000 -> 10.0)."""
    return amount_units / 100.0


def is_below_ingame_dl_minimum(amount_units: int) -> bool:
    """True when deposit is strictly below the in-game DL minimum (reject if < 5.0 DL)."""
    return dl_amount_from_units(amount_units) < INGAME_MIN_DL
_LOG_PATTERN = re.compile(r"^\s*(\S+)\s+(\d+)\s*$")
_PROCESSED_KEY = "server/ingame_deposit_processed"
_MAX_PROCESSED = 5000


def ensure_ingame_payment_method() -> dict:
    """Ensure the built-in in-game payment method exists in server/payment_methods."""
    methods = get_data("server/payment_methods") or {}
    default = {
        "name": "In-Game Funds",
        "enabled": False,
        "emoji": "🎮",
        "type": "ingame",
        "description": "Deposit via Growtopia donation box",
        "world": "",
        "bot_name": "",
        "webhook_channel_id": None,
        "dl_to_coin_rate": 100,
    }
    if INGAME_METHOD_KEY not in methods:
        methods[INGAME_METHOD_KEY] = default.copy()
        set_data("server/payment_methods", methods)
        return methods[INGAME_METHOD_KEY]

    entry = methods[INGAME_METHOD_KEY]
    if not isinstance(entry, dict):
        entry = default.copy()
    for k, v in default.items():
        entry.setdefault(k, v)
    entry["type"] = "ingame"
    methods[INGAME_METHOD_KEY] = entry
    set_data("server/payment_methods", methods)
    return entry


def is_ingame_method(method_key: str, method_info: dict | None = None) -> bool:
    if method_key == INGAME_METHOD_KEY:
        return True
    if method_info and method_info.get("type") == "ingame":
        return True
    return False


def get_ingame_config() -> dict:
    ensure_ingame_payment_method()
    methods = get_data("server/payment_methods") or {}
    return methods.get(INGAME_METHOD_KEY, {})


def is_ingame_configured(cfg: dict | None = None) -> bool:
    cfg = cfg or get_ingame_config()
    return bool(
        str(cfg.get("world", "")).strip()
        and str(cfg.get("bot_name", "")).strip()
        and cfg.get("webhook_channel_id")
        and float(cfg.get("dl_to_coin_rate", 0) or 0) > 0
    )


def parse_deposit_log(text: str) -> Optional[Tuple[str, int]]:
    """
    Parse log lines like ``vegasback 100`` (growid + amount in 0.01 DL units).

    Returns (growid, amount_units) or None.
    """
    if not text:
        return None
    line = text.strip().splitlines()[0].strip()
    m = _LOG_PATTERN.match(line)
    if not m:
        return None
    growid = m.group(1).strip()
    try:
        amount_units = int(m.group(2))
    except ValueError:
        return None
    if amount_units <= 0 or not growid:
        return None
    return growid, amount_units


def find_user_id_by_growid(growid: str) -> Optional[int]:
    """Case-insensitive GrowID lookup."""
    from modules.database import _get_conn

    if not growid:
        return None
    conn = _get_conn()
    row = conn.execute(
        "SELECT user_id FROM users WHERE growid IS NOT NULL AND LOWER(growid)=LOWER(?)",
        (growid.strip(),),
    ).fetchone()
    if not row:
        return None
    try:
        return int(row["user_id"])
    except (TypeError, ValueError):
        return None


def dl_units_to_coins(amount_units: int, dl_to_coin_rate: float) -> int:
    """Convert 0.01 DL units to bot coins using admin rate (coins per 1 DL)."""
    dl_amount = amount_units / 100.0
    rate = float(dl_to_coin_rate or 0)
    if rate <= 0 or dl_amount <= 0:
        return 0
    return max(1, int(round(dl_amount * rate)))


def _already_processed(message_id: int) -> bool:
    store = get_data(_PROCESSED_KEY) or {}
    return str(message_id) in store


def _mark_processed(message_id: int) -> None:
    store = get_data(_PROCESSED_KEY) or {}
    if not isinstance(store, dict):
        store = {}
    store[str(message_id)] = int(time.time())
    if len(store) > _MAX_PROCESSED:
        for old_id in sorted(store.keys(), key=lambda k: store[k])[: len(store) - _MAX_PROCESSED]:
            del store[old_id]
    set_data(_PROCESSED_KEY, store)


def process_deposit_from_log(
    growid: str,
    amount_units: int,
    *,
    message_id: int,
    raw_log: str,
) -> Tuple[bool, str, Optional[dict]]:
    """
    Credit a user from a parsed in-game log line.

    Returns (success, message_code_or_error, result_dict).
    """
    if _already_processed(message_id):
        return False, "duplicate", None

    from modules.maintenance import is_maintenance_enabled

    if is_maintenance_enabled():
        return False, "maintenance", None

    cfg = get_ingame_config()
    if not cfg.get("enabled"):
        return False, "disabled", None
    if not is_ingame_configured(cfg):
        return False, "not_configured", None

    if is_below_ingame_dl_minimum(amount_units):
        user_id = find_user_id_by_growid(growid)
        return False, "below_minimum_dl", {
            "growid": growid,
            "dl_amount": dl_amount_from_units(amount_units),
            "amount_units": amount_units,
            "min_dl": INGAME_MIN_DL,
            "user_id": user_id,
        }

    user_id = find_user_id_by_growid(growid)
    if not user_id:
        return False, "unknown_growid", {"growid": growid}

    rate = float(cfg.get("dl_to_coin_rate", 0) or 0)
    coins = dl_units_to_coins(amount_units, rate)
    if coins <= 0:
        return False, "zero_coins", None

    deposit_settings = get_data("server/deposit_settings") or {}
    min_deposit = float(deposit_settings.get("min_deposit", 0) or 0)
    dl_amount = dl_amount_from_units(amount_units)
    if min_deposit > 0 and coins < min_deposit:
        return False, "below_minimum_coins", {
            "growid": growid,
            "dl_amount": dl_amount,
            "amount_units": amount_units,
            "coins": coins,
            "min_deposit": min_deposit,
            "user_id": user_id,
        }

    from modules.player import Player
    import modules.race as race_engine
    from modules.deposit_credit import apply_pending_deposit_bonus

    player = Player(user_id)
    prev_balance = player.get_balance("real")
    player.add_balance("real", coins, by="ingame_deposit", reason=f"In-game deposit ({growid})")
    player.record_deposit(coins)
    race_engine.add_entry(user_id, coins, "deposit")

    bonus_amt = 0
    ok_bonus, bonus_amt = apply_pending_deposit_bonus(user_id, coins)
    if ok_bonus and bonus_amt > 0:
        player.add_balance("real", bonus_amt, by="bonus", reason="Deposit bonus")

    deposit_id = f"ingame-{message_id}"
    history = get_user_data(user_id, "deposit_history") or {}
    history[deposit_id] = {
        "deposit_id": deposit_id,
        "amount": coins,
        "requested_amount": coins,
        "confirmed_amount": coins,
        "method_key": INGAME_METHOD_KEY,
        "method_name": cfg.get("name", "In-Game Funds"),
        "growid": growid,
        "dl_amount": dl_amount_from_units(amount_units),
        "raw_log": raw_log,
        "status": "completed",
        "auto": True,
        "timestamp": str(int(time.time())),
        "managed_by": None,
        "bonus_amount_credited": bonus_amt if bonus_amt > 0 else None,
    }
    set_user_data(user_id, "deposit_history", history)
    _mark_processed(message_id)

    return True, "credited", {
        "user_id": user_id,
        "growid": growid,
        "coins": coins,
        "bonus_amount": bonus_amt,
        "dl_amount": dl_amount_from_units(amount_units),
        "prev_balance": prev_balance,
        "new_balance": player.get_balance("real"),
    }


def extract_log_text(message_content: str, embeds: list) -> str:
    """Collect parseable text from a webhook/channel message."""
    parts = []
    if message_content and message_content.strip():
        parts.append(message_content.strip())
    for emb in embeds or []:
        for attr in ("description", "title"):
            val = getattr(emb, attr, None) if emb else None
            if val and str(val).strip():
                parts.append(str(val).strip())
        if emb and emb.fields:
            for field in emb.fields:
                if field.value and str(field.value).strip():
                    parts.append(str(field.value).strip())
    return "\n".join(parts)
