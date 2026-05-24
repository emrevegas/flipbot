"""
SQLite-backed database module.

Drop-in replacement for the original JSON file-based version.
Identical public API: get_data, set_data, replace_data,
get_user_data, set_user_data, get_user_stats, set_user_stats,
get_server_data, set_server_data, check_permission, ge

Lang files (database/lang/*.json) remain on disk — they are read-only
translation tables loaded directly by the Translator class.
"""

import json
import os
import sqlite3
import threading
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
_SUPER_ADMIN_ID: str = os.getenv("SUPER_ADMIN_ID", "")

BASE_DIR    = Path(__file__).resolve().parents[1]
DB_DIR      = BASE_DIR / "database"
DB_PATH     = DB_DIR / "panel.db"
LANG_FOLDER = DB_DIR / "lang"

DB_DIR.mkdir(parents=True, exist_ok=True)

# ── Connection ─────────────────────────────────────────────────────────────
# One connection per thread — avoids "database is locked" under asyncio/thread-pool usage.
_local: threading.local = threading.local()
_init_lock: threading.Lock = threading.Lock()   # only for one-time table creation
_write_lock: threading.Lock = threading.Lock()  # serialises merge-writes across threads


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        c = sqlite3.connect(str(DB_PATH), timeout=30.0)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        with _init_lock:
            _init_tables(c)
        _local.conn = c
    return _local.conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kv_store (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS users (
            user_id               TEXT    PRIMARY KEY,
            balance_real          INTEGER NOT NULL DEFAULT 0,
            balance_demo          INTEGER NOT NULL DEFAULT 0,
            lang                  TEXT    NOT NULL DEFAULT 'en',
            selected_bet          INTEGER NOT NULL DEFAULT 100,
            selected_mode         TEXT    NOT NULL DEFAULT 'real',
            pf_client_seed        TEXT,
            pf_nonce              INTEGER NOT NULL DEFAULT 0,
            rakeback_accumulated  INTEGER NOT NULL DEFAULT 0,
            rakeback_total_earned INTEGER NOT NULL DEFAULT 0,
            growid                TEXT,
            name                  TEXT,
            age                   TEXT,
            email                 TEXT,
            source                TEXT,
            referral_code         TEXT,
            referred_by           TEXT
        );

        CREATE TABLE IF NOT EXISTS user_stats (
            user_id       TEXT    PRIMARY KEY,
            total_plays   INTEGER NOT NULL DEFAULT 0,
            wins          INTEGER NOT NULL DEFAULT 0,
            losses        INTEGER NOT NULL DEFAULT 0,
            ties          INTEGER NOT NULL DEFAULT 0,
            total_wagered INTEGER NOT NULL DEFAULT 0,
            total_profit  INTEGER NOT NULL DEFAULT 0,
            real_plays    INTEGER NOT NULL DEFAULT 0,
            demo_plays    INTEGER NOT NULL DEFAULT 0,
            total_deposit INTEGER NOT NULL DEFAULT 0,
            games_json    TEXT    NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS game_history (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT    NOT NULL,
            game_id TEXT    NOT NULL,
            data    TEXT    NOT NULL,
            UNIQUE(user_id, game_id)
        );

        CREATE TABLE IF NOT EXISTS deposit_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            entry_key TEXT NOT NULL,
            data      TEXT NOT NULL,
            UNIQUE(user_id, entry_key)
        );

        CREATE TABLE IF NOT EXISTS withdraw_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            entry_key TEXT NOT NULL,
            data      TEXT NOT NULL,
            UNIQUE(user_id, entry_key)
        );

        CREATE TABLE IF NOT EXISTS ticket_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            ticket_id TEXT,
            data      TEXT NOT NULL,
            UNIQUE(user_id, ticket_id)
        );

        CREATE TABLE IF NOT EXISTS user_levels (
            user_id         TEXT    PRIMARY KEY,
            level           INTEGER NOT NULL DEFAULT 1,
            last_chest_date TEXT    NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_gh_user ON game_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_dh_user ON deposit_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_wh_user ON withdraw_history(user_id);
        CREATE INDEX IF NOT EXISTS idx_th_user ON ticket_history(user_id);
    """)
    conn.commit()


# ── Internal helpers ───────────────────────────────────────────────────────

def _ensure_user(conn: sqlite3.Connection, uid: str) -> None:
    conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (uid,))


def _ensure_user_stats(conn: sqlite3.Connection, uid: str) -> None:
    conn.execute("INSERT OR IGNORE INTO user_stats(user_id) VALUES(?)", (uid,))


def _kv_get(key: str):
    conn = _get_conn()
    row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["value"])
    except Exception:
        return {}


def _kv_set(key: str, value, merge: bool = True) -> None:
    conn = _get_conn()
    with _write_lock:
        if merge:
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (key,)).fetchone()
            if row:
                try:
                    existing = json.loads(row["value"])
                except Exception:
                    existing = {}
                if isinstance(existing, dict) and isinstance(value, dict):
                    existing.update(value)
                    value = existing
        conn.execute(
            "INSERT OR REPLACE INTO kv_store(key, value) VALUES(?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        conn.commit()


# ── Public API ─────────────────────────────────────────────────────────────

def get_data(path: str):
    """Return data stored at *path*.

    - ``server/xxx``  → kv_store key ``server/xxx``
    - ``{user_id}/xxx`` → delegates to get_user_data
    - ``lang/xx``     → reads database/lang/xx.json directly (read-only)
    - anything else   → kv_store
    """
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return {}
    first = parts[0]

    # Translation files stay on disk
    if first == "lang" and len(parts) >= 2:
        lang_file = LANG_FOLDER / f"{parts[1]}.json"
        if lang_file.exists():
            try:
                return json.loads(lang_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    # User-scoped path e.g. "890502170359779329/balance"
    if first.isdigit():
        user_id = int(first)
        key = "/".join(parts[1:]) if len(parts) > 1 else ""
        return get_user_data(user_id, key)

    return _kv_get(path)


def set_data(path: str, value) -> None:
    """Write *value* to *path* (dict values are merged with existing data)."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return
    first = parts[0]
    if first.isdigit():
        user_id = int(first)
        key = "/".join(parts[1:]) if len(parts) > 1 else ""
        set_user_data(user_id, key, value)
        return
    _kv_set(path, value, merge=True)


def replace_data(path: str, value) -> None:
    """Fully overwrite *path* with *value* — no dict merging."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return
    first = parts[0]
    if first.isdigit():
        user_id = int(first)
        key = "/".join(parts[1:]) if len(parts) > 1 else ""
        set_user_data(user_id, key, value)
        return
    _kv_set(path, value, merge=False)


def get_user_data(user_id: int, key: str):
    """Return the stored data for *user_id* / *key*."""
    conn = _get_conn()
    uid = str(user_id)

    if key == "balance":
        row = conn.execute(
            "SELECT balance_real, balance_demo FROM users WHERE user_id=?", (uid,)
        ).fetchone()
        if not row:
            return {"real": 0, "demo": 0}
        return {"real": row["balance_real"], "demo": row["balance_demo"]}

    if key == "account":
        row = conn.execute(
            "SELECT name, age, email, source, referral_code, referred_by "
            "FROM users WHERE user_id=?",
            (uid,),
        ).fetchone()
        if not row or row["name"] is None:
            return {}
        return {k: row[k] for k in ("name", "age", "email", "source", "referral_code", "referred_by")}

    if key == "lang":
        row = conn.execute("SELECT lang FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return {"language": "en"}
        return {"language": row["lang"]}

    if key == "selected_bet":
        row = conn.execute(
            "SELECT selected_bet, selected_mode FROM users WHERE user_id=?", (uid,)
        ).fetchone()
        if not row:
            return {}
        return {"bet": row["selected_bet"], "mode": row["selected_mode"]}

    if key == "provably_fair":
        row = conn.execute(
            "SELECT pf_client_seed, pf_nonce FROM users WHERE user_id=?", (uid,)
        ).fetchone()
        if not row or row["pf_client_seed"] is None:
            return {}
        return {"client_seed": row["pf_client_seed"], "nonce": row["pf_nonce"]}

    if key == "rakeback":
        row = conn.execute(
            "SELECT rakeback_accumulated, rakeback_total_earned FROM users WHERE user_id=?",
            (uid,),
        ).fetchone()
        if not row:
            return {}
        return {"accumulated": row["rakeback_accumulated"], "total_earned": row["rakeback_total_earned"]}

    if key == "growid":
        row = conn.execute("SELECT growid FROM users WHERE user_id=?", (uid,)).fetchone()
        if not row or row["growid"] is None:
            return {}
        return {"growid": row["growid"]}

    if key == "stats":
        row = conn.execute("SELECT * FROM user_stats WHERE user_id=?", (uid,)).fetchone()
        if not row:
            return {}
        return {
            "total_plays":   row["total_plays"],
            "wins":          row["wins"],
            "losses":        row["losses"],
            "ties":          row["ties"],
            "total_wagered": row["total_wagered"],
            "total_profit":  row["total_profit"],
            "real_plays":    row["real_plays"],
            "demo_plays":    row["demo_plays"],
            "total_deposit": row["total_deposit"],
            "games":         json.loads(row["games_json"]),
        }

    if key == "game_history":
        rows = conn.execute(
            "SELECT game_id, data FROM game_history WHERE user_id=? ORDER BY id", (uid,)
        ).fetchall()
        return {row["game_id"]: json.loads(row["data"]) for row in rows}

    if key == "deposit_history":
        rows = conn.execute(
            "SELECT entry_key, data FROM deposit_history WHERE user_id=? ORDER BY id", (uid,)
        ).fetchall()
        return {row["entry_key"]: json.loads(row["data"]) for row in rows}

    if key == "withdraw_history":
        rows = conn.execute(
            "SELECT entry_key, data FROM withdraw_history WHERE user_id=? ORDER BY id", (uid,)
        ).fetchall()
        return {row["entry_key"]: json.loads(row["data"]) for row in rows}

    if key == "ticket_history":
        rows = conn.execute(
            "SELECT data FROM ticket_history WHERE user_id=? ORDER BY id", (uid,)
        ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    if key == "level":
        conn.execute("INSERT OR IGNORE INTO user_levels(user_id) VALUES(?)", (uid,))
        row = conn.execute(
            "SELECT level, last_chest_date FROM user_levels WHERE user_id=?", (uid,)
        ).fetchone()
        if not row:
            return {"level": 1, "last_chest_date": ""}
        return {"level": row["level"], "last_chest_date": row["last_chest_date"]}

    # Fallback: generic kv_store entry scoped to this user
    return _kv_get(f"user:{uid}:{key}")


def set_user_data(user_id: int, key: str, value) -> None:
    """Persist *value* for *user_id* / *key*."""
    conn = _get_conn()
    uid = str(user_id)

    with _write_lock:
        _ensure_user(conn, uid)

        if key == "balance":
            if isinstance(value, dict):
                cols, params = [], []
                if "real" in value:
                    cols.append("balance_real=?"); params.append(int(value["real"]))
                if "demo" in value:
                    cols.append("balance_demo=?"); params.append(int(value["demo"]))
                if cols:
                    params.append(uid)
                    conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE user_id=?", params)

        elif key == "account":
            if isinstance(value, dict):
                col_map = {
                    "name": "name", "age": "age", "email": "email",
                    "source": "source", "referral_code": "referral_code", "referred_by": "referred_by",
                }
                cols, params = [], []
                for field, col in col_map.items():
                    if field in value:
                        cols.append(f"{col}=?"); params.append(value[field])
                if cols:
                    params.append(uid)
                    conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE user_id=?", params)

        elif key == "lang":
            if isinstance(value, dict) and "language" in value:
                conn.execute("UPDATE users SET lang=? WHERE user_id=?", (value["language"], uid))

        elif key == "selected_bet":
            if isinstance(value, dict):
                cols, params = [], []
                if "bet" in value:
                    cols.append("selected_bet=?"); params.append(int(value["bet"]))
                if "mode" in value:
                    cols.append("selected_mode=?"); params.append(value["mode"])
                if cols:
                    params.append(uid)
                    conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE user_id=?", params)

        elif key == "provably_fair":
            if isinstance(value, dict):
                cols, params = [], []
                if "client_seed" in value:
                    cols.append("pf_client_seed=?"); params.append(value["client_seed"])
                if "nonce" in value:
                    cols.append("pf_nonce=?"); params.append(int(value["nonce"]))
                if cols:
                    params.append(uid)
                    conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE user_id=?", params)

        elif key == "rakeback":
            if isinstance(value, dict):
                cols, params = [], []
                if "accumulated" in value:
                    cols.append("rakeback_accumulated=?"); params.append(int(value["accumulated"]))
                if "total_earned" in value:
                    cols.append("rakeback_total_earned=?"); params.append(int(value["total_earned"]))
                if cols:
                    params.append(uid)
                    conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE user_id=?", params)

        elif key == "growid":
            if isinstance(value, dict) and "growid" in value:
                conn.execute("UPDATE users SET growid=? WHERE user_id=?", (value["growid"], uid))

        elif key == "level":
            if isinstance(value, dict):
                conn.execute("INSERT OR IGNORE INTO user_levels(user_id) VALUES(?)", (uid,))
                cols, params = [], []
                if "level" in value:
                    cols.append("level=?"); params.append(int(value["level"]))
                if "last_chest_date" in value:
                    cols.append("last_chest_date=?"); params.append(str(value["last_chest_date"]))
                if cols:
                    params.append(uid)
                    conn.execute(f"UPDATE user_levels SET {', '.join(cols)} WHERE user_id=?", params)

        elif key == "stats":
            if isinstance(value, dict):
                _ensure_user_stats(conn, uid)
                conn.execute(
                    """UPDATE user_stats
                       SET total_plays=?, wins=?, losses=?, ties=?,
                           total_wagered=?, total_profit=?, real_plays=?,
                           demo_plays=?, total_deposit=?, games_json=?
                       WHERE user_id=?""",
                    (
                        int(value.get("total_plays",   0)),
                        int(value.get("wins",          0)),
                        int(value.get("losses",        0)),
                        int(value.get("ties",          0)),
                        int(value.get("total_wagered", 0)),
                        int(value.get("total_profit",  0)),
                        int(value.get("real_plays",    0)),
                        int(value.get("demo_plays",    0)),
                        int(value.get("total_deposit", 0)),
                        json.dumps(value.get("games", {}), ensure_ascii=False),
                        uid,
                    ),
                )

        elif key == "game_history":
            if isinstance(value, dict):
                for game_id, data in value.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO game_history(user_id, game_id, data) VALUES(?,?,?)",
                        (uid, str(game_id), json.dumps(data, ensure_ascii=False)),
                    )

        elif key == "deposit_history":
            if isinstance(value, dict):
                for entry_key, data in value.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO deposit_history(user_id, entry_key, data) VALUES(?,?,?)",
                        (uid, str(entry_key), json.dumps(data, ensure_ascii=False)),
                    )

        elif key == "withdraw_history":
            if isinstance(value, dict):
                for entry_key, data in value.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO withdraw_history(user_id, entry_key, data) VALUES(?,?,?)",
                        (uid, str(entry_key), json.dumps(data, ensure_ascii=False)),
                    )

        elif key == "ticket_history":
            if isinstance(value, list):
                for ticket in value:
                    ticket_id = str(ticket.get("ticket_id", "")) or None
                    conn.execute(
                        "INSERT OR REPLACE INTO ticket_history(user_id, ticket_id, data) VALUES(?,?,?)",
                        (uid, ticket_id, json.dumps(ticket, ensure_ascii=False)),
                    )

        else:
            # Generic fallback — kv_store with user-scoped key
            kv_key = f"user:{uid}:{key}"
            row = conn.execute("SELECT value FROM kv_store WHERE key=?", (kv_key,)).fetchone()
            if row:
                try:
                    existing = json.loads(row["value"])
                except Exception:
                    existing = {}
                if isinstance(existing, dict) and isinstance(value, dict):
                    existing.update(value)
                    value = existing
            conn.execute(
                "INSERT OR REPLACE INTO kv_store(key, value) VALUES(?,?)",
                (kv_key, json.dumps(value, ensure_ascii=False)),
            )

        conn.commit()


# ── Compatibility wrappers ─────────────────────────────────────────────────

def get_user_stats(user_id: int):
    """Return the stored statistics for a specific user."""
    return get_user_data(user_id, "stats") or {}


def set_user_stats(user_id: int, value) -> None:
    """Write statistics data for a specific user."""
    set_user_data(user_id, "stats", value)


def get_server_data(guild_id=None):
    """Return server-wide config. Pass guild_id for guild-specific sub-section."""
    if guild_id:
        server_data = _kv_get("server/server")
        return server_data.get(str(guild_id), {})
    return _kv_get("server/server")


def set_server_data(guild_id, value=None):
    """Save server config. If value is None, guild_id is treated as the full config dict."""
    if value is None:
        _kv_set("server/server", guild_id, merge=True)
    else:
        server_data = _kv_get("server/server")
        guild_id_str = str(guild_id)
        if guild_id_str not in server_data:
            server_data[guild_id_str] = {}
        server_data[guild_id_str].update(value)
        _kv_set("server/server", server_data, merge=False)


_owner_id: str = ""


def set_owner_id(user_id) -> None:
    """Set the bot application owner ID at runtime (called from bot.on_ready)."""
    global _owner_id
    _owner_id = str(user_id)


def get_super_admin_id() -> str:
    """Return the SUPER_ADMIN_ID set in .env (empty string if not set)."""
    return _SUPER_ADMIN_ID


def is_super_admin(user_id) -> bool:
    """True if user_id matches SUPER_ADMIN_ID or the bot application owner."""
    uid = str(user_id)
    if bool(_SUPER_ADMIN_ID) and uid == str(_SUPER_ADMIN_ID):
        return True
    if bool(_owner_id) and uid == _owner_id:
        return True
    return False


def check_permission(user_id, permission):
    # Super admin always has every permission
    if is_super_admin(user_id):
        return False
    admins = get_data("server/admins")
    permission = permission.lower()
    if str(user_id) not in admins:
        return True
    admin_permission = admins[str(user_id)]
    if isinstance(admin_permission, str):
        admin_permission = [admin_permission]
    if "admin" in admin_permission:
        return False
    if permission in admin_permission:
        return False
    return True


def can_manage_items(user_id) -> bool:
    """True if user may use /items, /cases, and /setprices (admin or item_manager from /user_panel)."""
    if is_super_admin(user_id):
        return True
    admins = get_data("server/admins") or {}
    user_key = str(user_id)
    if user_key not in admins:
        return False
    perms = admins[user_key]
    if isinstance(perms, str):
        perms = [perms]
    return "admin" in perms or "item_manager" in perms


def get_all_registered_user_ids() -> list[str]:
    """Return a list of user_ids that have a completed registration (name is not NULL)."""
    conn = _get_conn()
    rows = conn.execute("SELECT user_id FROM users WHERE name IS NOT NULL").fetchall()
    return [row["user_id"] for row in rows]


def get_platform_alltime_stats() -> dict:
    """Aggregate platform-wide statistics directly from SQLite for efficiency."""
    conn = _get_conn()

    # Build exclusion list: admins and cashiers stored in server/admins kv key
    admins_data = _kv_get("server/admins") or {}
    excluded_ids = tuple(
        uid for uid, perms in admins_data.items()
        if isinstance(perms, list) and any(p in ("admin", "cashier") for p in perms)
        or isinstance(perms, str) and perms in ("admin", "cashier")
    )

    if excluded_ids:
        placeholders = ",".join("?" * len(excluded_ids))
        players_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM users WHERE name IS NOT NULL AND user_id NOT IN ({placeholders})",
            excluded_ids,
        ).fetchone()
        stats_row = conn.execute(
            f"SELECT "
            f"  SUM(total_plays)   AS games, "
            f"  SUM(wins)          AS wins, "
            f"  SUM(losses)        AS losses, "
            f"  SUM(total_wagered) AS wagered, "
            f"  SUM(total_deposit) AS deposit "
            f"FROM user_stats WHERE user_id NOT IN ({placeholders})",
            excluded_ids,
        ).fetchone()
        circ_row = conn.execute(
            f"SELECT SUM(balance_real) AS circ FROM users WHERE user_id NOT IN ({placeholders})",
            excluded_ids,
        ).fetchone()
    else:
        players_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE name IS NOT NULL"
        ).fetchone()
        stats_row = conn.execute(
            "SELECT "
            "  SUM(total_plays)   AS games, "
            "  SUM(wins)          AS wins, "
            "  SUM(losses)        AS losses, "
            "  SUM(total_wagered) AS wagered, "
            "  SUM(total_deposit) AS deposit "
            "FROM user_stats"
        ).fetchone()
        circ_row = conn.execute(
            "SELECT SUM(balance_real) AS circ FROM users"
        ).fetchone()

    wdr_row = conn.execute(
        "SELECT SUM(CAST(json_extract(data, '$.amount') AS INTEGER)) AS total "
        "FROM withdraw_history "
        "WHERE json_extract(data, '$.status') = 'approved'"
    ).fetchone()

    # Add manual finance adjustments from all guilds
    server_kv   = _kv_get("server/server") or {}
    dep_adj = 0
    wdr_adj = 0
    for guild_id, gdata in server_kv.items():
        if not isinstance(gdata, dict):
            continue
        adj = gdata.get("finance_manual_adjustments") or {}
        dep_adj += float(adj.get("deposit", 0))
        wdr_adj += float(adj.get("withdraw", 0))

    return {
        "total_players":    int(players_row["cnt"] if players_row else 0),
        "total_games":      int(stats_row["games"]   or 0),
        "total_wins":       int(stats_row["wins"]    or 0),
        "total_losses":     int(stats_row["losses"]  or 0),
        "total_wagered":    int(stats_row["wagered"] or 0),
        "total_deposit":    int((stats_row["deposit"] or 0) + dep_adj),
        "in_circulation":   int(circ_row["circ"]     or 0),
        "total_withdraw":   int((wdr_row["total"]    or 0) + wdr_adj),
    }


def clear_user_account(user_id) -> None:
    """Clear the registration data (account fields) for a user, resetting them to NULL."""
    conn = _get_conn()
    uid = str(user_id)
    with _write_lock:
        conn.execute(
            "UPDATE users SET name=NULL, age=NULL, email=NULL, source=NULL, "
            "referral_code=NULL, referred_by=NULL WHERE user_id=?",
            (uid,),
        )
        conn.commit()


def _cleanup_user_referrals(uid: str) -> None:
    """Remove user from server/referrals (as owner or referred member)."""
    referrals = get_data("server/referrals") or {}
    if not isinstance(referrals, dict):
        return
    changed = False
    if uid in referrals:
        del referrals[uid]
        changed = True
    for owner_id, data in list(referrals.items()):
        if not isinstance(data, dict):
            continue
        refs = data.get("referred_users") or []
        if uid in refs:
            data["referred_users"] = [r for r in refs if str(r) != uid]
            changed = True
        earnings = data.get("referral_earnings") or {}
        if uid in earnings:
            earnings = dict(earnings)
            del earnings[uid]
            data["referral_earnings"] = earnings
            changed = True
        referrals[owner_id] = data
    if changed:
        set_data("server/referrals", referrals)


def _purge_balance_log_for_user(uid: str) -> int:
    """Remove balance_log entries targeting this user. Returns removed count."""
    balance_log = get_data("server/balance_log") or {}
    if not isinstance(balance_log, dict):
        return 0
    removed = 0
    new_log = {}
    for entry_id, entry in balance_log.items():
        if isinstance(entry, dict) and str(entry.get("user_id")) == uid:
            removed += 1
            continue
        new_log[entry_id] = entry
    if removed:
        replace_data("server/balance_log", new_log)
    return removed


def delete_all_user_data(user_id) -> dict:
    """
    Permanently delete all stored data for a user (registration, balances, history, kv).
    Does not remove server/admins entries — call only after permission checks.
    """
    conn = _get_conn()
    uid = str(user_id)
    summary = {
        "user_id": uid,
        "kv_keys": 0,
        "balance_log_entries": 0,
    }

    with _write_lock:
        kv_rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM kv_store WHERE key LIKE ? OR key=?",
            (f"user:{uid}:%", f"user_txlog/{uid}"),
        ).fetchone()
        summary["kv_keys"] = int(kv_rows["cnt"] if kv_rows else 0)

        conn.execute("DELETE FROM kv_store WHERE key LIKE ?", (f"user:{uid}:%",))
        conn.execute("DELETE FROM kv_store WHERE key=?", (f"user_txlog/{uid}",))

        for table in (
            "game_history",
            "deposit_history",
            "withdraw_history",
            "ticket_history",
            "user_levels",
            "user_stats",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))

        conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
        conn.commit()

    _cleanup_user_referrals(uid)
    summary["balance_log_entries"] = _purge_balance_log_for_user(uid)
    return summary


def can_delete_user_data(user_id) -> tuple[bool, str]:
    """Whether delete_all_user_data may run for this target."""
    uid = str(user_id)
    if is_super_admin(uid):
        return False, "Super admin verisi silinemez."
    admins = get_data("server/admins") or {}
    if uid in admins:
        return False, "Önce admin yetkisini kaldırın, sonra kaydı silin."
    return True, ""


def ge(name):
    emojis = get_data("server/emojis")
    if name in emojis:
        return emojis[name]
    return None