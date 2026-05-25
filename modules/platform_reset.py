"""Platform-wide reset — preserves cases/items, game setup, and admin permissions."""
from __future__ import annotations

import json
from typing import Any

from modules.database import _get_conn, _write_lock, get_data, replace_data, set_data

# panel.db server/* keys kept intact
PRESERVED_SERVER_KEYS = frozenset({
    "server/cases",      # /items + /cases catalog
    "server/admins",     # user permissions
    "server/games",      # per-game setup (min/max bet, rigged %, house edge, emojis)
})

FLIPBOT_CLEAR_TABLES = (
    "game_sessions",
    "transactions",
    "promo_uses",
    "active_bonuses",
    "deposit_requests",
    "withdrawal_requests",
    "giveaway_entries",
    "race_entries",
    "user_stats",
    "user_bans",
    "balance_caps",
)


def run_platform_reset() -> dict[str, Any]:
    """Reset panel.db runtime data. Returns summary counts."""
    conn = _get_conn()
    summary: dict[str, Any] = {
        "server_kv_removed": 0,
        "user_kv_removed": 0,
        "users_zeroed": 0,
        "histories_cleared": 0,
    }

    preserved: dict[str, Any] = {}
    for key in PRESERVED_SERVER_KEYS:
        preserved[key] = get_data(key)

    with _write_lock:
        rows = conn.execute(
            "SELECT key FROM kv_store WHERE key LIKE 'server/%'"
        ).fetchall()
        for row in rows:
            key = row["key"]
            if key not in PRESERVED_SERVER_KEYS:
                conn.execute("DELETE FROM kv_store WHERE key=?", (key,))
                summary["server_kv_removed"] += 1

        urows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kv_store WHERE key LIKE 'user:%' OR key LIKE 'user_txlog/%'"
        ).fetchone()
        summary["user_kv_removed"] = int(urows["cnt"] if urows else 0)
        conn.execute("DELETE FROM kv_store WHERE key LIKE 'user:%'")
        conn.execute("DELETE FROM kv_store WHERE key LIKE 'user_txlog/%'")

        for table in (
            "game_history",
            "deposit_history",
            "withdraw_history",
            "ticket_history",
        ):
            conn.execute(f"DELETE FROM {table}")
            summary["histories_cleared"] += conn.total_changes

        conn.execute(
            """UPDATE user_stats SET
               total_plays=0, wins=0, losses=0, ties=0,
               total_wagered=0, total_profit=0, real_plays=0,
               demo_plays=0, total_deposit=0, games_json='{}'"""
        )
        conn.execute("DELETE FROM user_levels")
        conn.execute(
            """UPDATE users SET
               balance_real=0, balance_demo=0,
               rakeback_accumulated=0, rakeback_total_earned=0,
               pf_nonce=0, growid=NULL"""
        )
        ucnt = conn.execute("SELECT COUNT(*) AS cnt FROM users").fetchone()
        summary["users_zeroed"] = int(ucnt["cnt"] if ucnt else 0)

        conn.commit()

    for key, val in preserved.items():
        if val is not None:
            replace_data(key, val if isinstance(val, dict) else {})

    try:
        from modules.promo import reset_all_user_promo_states
        summary["promo_states"] = reset_all_user_promo_states()
    except Exception as exc:
        summary["promo_states"] = {"error": str(exc)}

    return summary


async def run_flipbot_reset() -> dict[str, int]:
    """Clear flipbot.db session/transaction tables (not games config rows)."""
    from database import db as fdb

    db = await fdb.get_db()
    counts: dict[str, int] = {}
    for table in FLIPBOT_CLEAR_TABLES:
        try:
            await db.execute(f"DELETE FROM {table}")
            counts[table] = 1
        except Exception:
            counts[table] = 0
    try:
        await db.execute(
            """UPDATE users SET balance=0, total_wagered=0, total_deposited=0, total_withdrawn=0,
               rakeback_accumulated=0, rakeback_total_claimed=0"""
        )
        counts["users_balance"] = 1
        counts["users_rakeback"] = 1
    except Exception:
        pass
    await db.commit()
    return counts
