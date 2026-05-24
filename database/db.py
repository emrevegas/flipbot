"""Async SQLite database layer."""
from __future__ import annotations

import aiosqlite
import asyncio
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "flipbot.db"
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _init_tables()
    return _db


async def _init_tables():
    db = _db
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id     TEXT PRIMARY KEY,
            username    TEXT,
            balance     REAL NOT NULL DEFAULT 0,
            total_wagered REAL NOT NULL DEFAULT 0,
            total_deposited REAL NOT NULL DEFAULT 0,
            total_withdrawn REAL NOT NULL DEFAULT 0,
            rakeback_accumulated REAL NOT NULL DEFAULT 0,
            rakeback_total_claimed REAL NOT NULL DEFAULT 0,
            registered_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            last_seen   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            type        TEXT NOT NULL,
            amount      REAL NOT NULL,
            note        TEXT,
            by_id       TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS affiliates (
            user_id         TEXT PRIMARY KEY,
            code            TEXT UNIQUE NOT NULL,
            net_earnings    REAL NOT NULL DEFAULT 0,
            total_claimed   REAL NOT NULL DEFAULT 0,
            claimable       REAL NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS affiliate_refs (
            ref_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_id    TEXT NOT NULL,
            referred_id     TEXT NOT NULL,
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(affiliate_id, referred_id)
        );

        CREATE TABLE IF NOT EXISTS affiliate_daily_settlements (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_id    TEXT NOT NULL,
            referred_id     TEXT NOT NULL,
            date_str        TEXT NOT NULL,
            deposits        REAL NOT NULL DEFAULT 0,
            withdrawals     REAL NOT NULL DEFAULT 0,
            net             REAL NOT NULL DEFAULT 0,
            earned          REAL NOT NULL DEFAULT 0,
            settled_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(affiliate_id, referred_id, date_str)
        );

        CREATE TABLE IF NOT EXISTS rakeback_tiers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            min_wagered REAL NOT NULL DEFAULT 0,
            rate        REAL NOT NULL DEFAULT 0.03,
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS promo_codes (
            code        TEXT PRIMARY KEY,
            reward      REAL NOT NULL,
            max_uses    INTEGER NOT NULL DEFAULT 0,
            uses        INTEGER NOT NULL DEFAULT 0,
            expires_at  INTEGER,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_by  TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS promo_uses (
            user_id     TEXT NOT NULL,
            code        TEXT NOT NULL,
            used_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (user_id, code)
        );

        CREATE TABLE IF NOT EXISTS games (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            min_bet     REAL NOT NULL DEFAULT 10,
            max_bet     REAL NOT NULL DEFAULT 100000,
            rigged_chance REAL NOT NULL DEFAULT 0.05,
            house_edge  REAL NOT NULL DEFAULT 0.02
        );

        CREATE TABLE IF NOT EXISTS game_sessions (
            user_id     TEXT PRIMARY KEY,
            game        TEXT NOT NULL,
            bet         REAL NOT NULL DEFAULT 0,
            state       TEXT NOT NULL DEFAULT '{}',
            started_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS user_stats (
            user_id     TEXT PRIMARY KEY,
            games_played INTEGER NOT NULL DEFAULT 0,
            wins        INTEGER NOT NULL DEFAULT 0,
            losses      INTEGER NOT NULL DEFAULT 0,
            biggest_win REAL NOT NULL DEFAULT 0,
            biggest_loss REAL NOT NULL DEFAULT 0,
            total_profit REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS user_bans (
            user_id     TEXT PRIMARY KEY,
            reason      TEXT,
            banned_by   TEXT,
            banned_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            muted       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS balance_caps (
            user_id     TEXT PRIMARY KEY,
            cap         REAL NOT NULL,
            set_by      TEXT,
            set_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS global_settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS cases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            price       REAL NOT NULL DEFAULT 100,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS case_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id     INTEGER NOT NULL,
            item_name   TEXT NOT NULL,
            item_value  REAL NOT NULL DEFAULT 0,
            chance      REAL NOT NULL DEFAULT 1.0,
            FOREIGN KEY(case_id) REFERENCES cases(id)
        );

        CREATE TABLE IF NOT EXISTS deposit_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            amount      REAL,
            method      TEXT,
            status      TEXT NOT NULL DEFAULT 'pending',
            note        TEXT,
            approved_by TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS withdrawal_requests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            amount      REAL NOT NULL,
            method      TEXT,
            status      TEXT NOT NULL DEFAULT 'pending',
            note        TEXT,
            approved_by TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS payment_methods (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            details     TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS bonuses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT UNIQUE NOT NULL,
            description TEXT,
            bonus_pct   REAL NOT NULL DEFAULT 0,
            wager_req   REAL NOT NULL DEFAULT 0,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS active_bonuses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            bonus_id    INTEGER NOT NULL,
            bonus_amount REAL NOT NULL DEFAULT 0,
            wagered     REAL NOT NULL DEFAULT 0,
            wager_req   REAL NOT NULL DEFAULT 0,
            completed   INTEGER NOT NULL DEFAULT 0,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS giveaways (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id  TEXT UNIQUE,
            channel_id  TEXT,
            prize_pts   REAL NOT NULL,
            winners_count INTEGER NOT NULL DEFAULT 1,
            ends_at     INTEGER NOT NULL,
            ended       INTEGER NOT NULL DEFAULT 0,
            created_by  TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL,
            user_id     TEXT NOT NULL,
            entered_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY(giveaway_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS races (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            prize_pts   REAL NOT NULL,
            ends_at     INTEGER NOT NULL,
            ended       INTEGER NOT NULL DEFAULT 0,
            created_by  TEXT,
            created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS race_entries (
            race_id     INTEGER NOT NULL,
            user_id     TEXT NOT NULL,
            wagered     REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(race_id, user_id)
        );
    """)

    # seed default games
    await db.executescript("""
        INSERT OR IGNORE INTO games (id, name, enabled, min_bet, max_bet, rigged_chance, house_edge)
        VALUES
            ('coinflip',  'Coin Flip',  1, 10, 100000, 0.05, 0.02),
            ('dice',      'Dice',       1, 10, 100000, 0.05, 0.02),
            ('roulette',  'Roulette',   1, 10, 100000, 0.05, 0.02),
            ('mines',     'Mines',      1, 10, 100000, 0.05, 0.02),
            ('hilo',      'Hi-Lo',      1, 10, 100000, 0.05, 0.02),
            ('blackjack', 'Blackjack',  1, 10, 100000, 0.05, 0.02),
            ('limbo',     'Limbo',      1, 10, 100000, 0.05, 0.02),
            ('slots',     'Slots',      1, 10, 100000, 0.05, 0.02),
            ('towers',    'Towers',     1, 10, 100000, 0.05, 0.02),
            ('crystals',  'Crystals',   1, 10, 100000, 0.05, 0.02),
            ('chicken_road', 'Chicken Road', 1, 10, 100000, 0.05, 0.02);
    """)
    # Seed default rakeback tiers if none exist
    count = (await (await db.execute("SELECT COUNT(*) FROM rakeback_tiers")).fetchone())[0]
    if count == 0:
        await db.executemany(
            "INSERT OR IGNORE INTO rakeback_tiers (name, min_wagered, rate, sort_order) VALUES (?,?,?,?)",
            [
                ("Bronze",   0,        0.03, 0),
                ("Silver",   5_000,    0.05, 1),
                ("Gold",     25_000,   0.08, 2),
                ("Platinum", 100_000,  0.12, 3),
                ("Diamond",  500_000,  0.18, 4),
            ],
        )
    await db.commit()


# ── Rakeback tier helpers ──────────────────────────────────────────────────────

async def get_rakeback_tiers() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM rakeback_tiers ORDER BY min_wagered ASC"
    )).fetchall()
    return [dict(r) for r in rows]


async def upsert_rakeback_tier(name: str, min_wagered: float, rate: float) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO rakeback_tiers (name, min_wagered, rate, sort_order)
           VALUES (?,?,?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM rakeback_tiers))
           ON CONFLICT(name) DO UPDATE SET min_wagered=excluded.min_wagered, rate=excluded.rate""",
        (name, min_wagered, rate),
    )
    await db.commit()


async def delete_rakeback_tier(name: str) -> None:
    db = await get_db()
    count = (await (await db.execute("SELECT COUNT(*) FROM rakeback_tiers")).fetchone())[0]
    if count <= 1:
        raise ValueError("Cannot delete the last tier.")
    await db.execute("DELETE FROM rakeback_tiers WHERE name=?", (name,))
    await db.commit()


# ── User helpers ───────────────────────────────────────────────────────────────

async def ensure_user(user_id: int | str, username: str = "") -> dict:
    db = await get_db()
    uid = str(user_id)
    row = await (await db.execute(
        "SELECT * FROM users WHERE user_id = ?", (uid,)
    )).fetchone()
    if row:
        if username:
            await db.execute(
                "UPDATE users SET username=?, last_seen=? WHERE user_id=?",
                (username, int(time.time()), uid),
            )
            await db.commit()
        return dict(row)
    await db.execute(
        "INSERT INTO users (user_id, username) VALUES (?, ?)",
        (uid, username),
    )
    # Ensure stats row
    await db.execute(
        "INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)", (uid,)
    )
    await db.commit()
    return await ensure_user(uid)


async def get_user(user_id: int | str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM users WHERE user_id = ?", (str(user_id),)
    )).fetchone()
    return dict(row) if row else None


async def add_balance(user_id: int | str, amount: float, *, note: str = "", by: str = "") -> float:
    db = await get_db()
    uid = str(user_id)
    await ensure_user(uid)
    await db.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
        (amount, uid),
    )
    await db.execute(
        "INSERT INTO transactions (user_id, type, amount, note, by_id) VALUES (?, ?, ?, ?, ?)",
        (uid, "credit" if amount >= 0 else "debit", abs(amount), note or "", str(by)),
    )
    await db.commit()
    row = await (await db.execute("SELECT balance FROM users WHERE user_id=?", (uid,))).fetchone()
    return float(row["balance"])


async def set_balance(user_id: int | str, amount: float, *, note: str = "", by: str = "") -> float:
    db = await get_db()
    uid = str(user_id)
    await ensure_user(uid)
    cur = (await get_user(uid) or {}).get("balance", 0)
    diff = amount - float(cur)
    await db.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, uid))
    await db.execute(
        "INSERT INTO transactions (user_id, type, amount, note, by_id) VALUES (?, ?, ?, ?, ?)",
        (uid, "set", abs(diff), note or "set balance", str(by)),
    )
    await db.commit()
    return amount


async def add_wager(user_id: int | str, amount: float) -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        "UPDATE users SET total_wagered = total_wagered + ? WHERE user_id = ?",
        (amount, uid),
    )
    # Update active race entry if any
    race = await get_active_race()
    if race:
        await db.execute(
            """INSERT INTO race_entries (race_id, user_id, wagered) VALUES (?, ?, ?)
               ON CONFLICT(race_id, user_id) DO UPDATE SET wagered = wagered + ?""",
            (race["id"], uid, amount, amount),
        )
    # Update active bonus wager
    await db.execute(
        """UPDATE active_bonuses SET wagered = wagered + ?
           WHERE user_id = ? AND completed = 0""",
        (amount, uid),
    )
    await db.commit()


async def record_deposit(user_id: int | str, amount: float) -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        "UPDATE users SET total_deposited = total_deposited + ? WHERE user_id = ?",
        (amount, uid),
    )
    await db.commit()


# ── Promo helpers ──────────────────────────────────────────────────────────────

async def get_promo(code: str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM promo_codes WHERE code = ? COLLATE NOCASE", (code,)
    )).fetchone()
    return dict(row) if row else None


async def has_used_promo(user_id: int | str, code: str) -> bool:
    db = await get_db()
    row = await (await db.execute(
        "SELECT 1 FROM promo_uses WHERE user_id=? AND code=? COLLATE NOCASE",
        (str(user_id), code),
    )).fetchone()
    return row is not None


async def use_promo(user_id: int | str, code: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO promo_uses (user_id, code) VALUES (?, ?)",
        (str(user_id), code),
    )
    await db.execute(
        "UPDATE promo_codes SET uses = uses + 1 WHERE code = ? COLLATE NOCASE",
        (code,),
    )
    await db.commit()


# ── Affiliate helpers ──────────────────────────────────────────────────────────

async def get_affiliate(user_id: int | str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM affiliates WHERE user_id=?", (str(user_id),)
    )).fetchone()
    return dict(row) if row else None


async def get_affiliate_by_code(code: str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM affiliates WHERE UPPER(code)=UPPER(?)", (code,)
    )).fetchone()
    return dict(row) if row else None


async def create_affiliate(user_id: int | str, code: str) -> dict:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        "INSERT INTO affiliates (user_id, code) VALUES (?, ?)",
        (uid, code.upper()),
    )
    await db.commit()
    return await get_affiliate(uid)


async def get_affiliate_refs(affiliate_id: int | str) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM affiliate_refs WHERE affiliate_id=?",
        (str(affiliate_id),),
    )).fetchall()
    return [dict(r) for r in rows]


async def add_affiliate_net_earnings(affiliate_id: int | str, amount: float) -> None:
    """Credit `amount` points to an affiliate's claimable balance (net-deposit commission)."""
    db = await get_db()
    uid = str(affiliate_id)
    await db.execute(
        "UPDATE affiliates SET net_earnings=net_earnings+?, claimable=claimable+? WHERE user_id=?",
        (amount, amount, uid),
    )
    await db.commit()


async def settle_affiliate_daily(date_str: str) -> list[dict]:
    """Settle affiliate commissions for `date_str` (YYYY-MM-DD).
    For every referred user: net = approved deposits − approved withdrawals on that day.
    Referrer earns AFFILIATE_NET_RATE * max(net, 0).
    Returns list of settlement rows that were actually created.
    """
    import config as _cfg
    db = await get_db()

    # Get all affiliate → referred pairs
    refs = await (await db.execute("SELECT affiliate_id, referred_id FROM affiliate_refs")).fetchall()

    # Day boundaries (UTC) from date_str
    import datetime
    day_start = int(datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(
        tzinfo=datetime.timezone.utc).timestamp())
    day_end = day_start + 86400

    settled = []
    for ref in refs:
        aff_id = ref["affiliate_id"]
        ref_id = ref["referred_id"]

        # Skip already-settled
        existing = await (await db.execute(
            "SELECT 1 FROM affiliate_daily_settlements WHERE affiliate_id=? AND referred_id=? AND date_str=?",
            (aff_id, ref_id, date_str),
        )).fetchone()
        if existing:
            continue

        # Sum approved deposits for that day
        dep_row = await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM deposit_requests "
            "WHERE user_id=? AND status='approved' AND created_at>=? AND created_at<?",
            (ref_id, day_start, day_end),
        )).fetchone()
        wd_row = await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawal_requests "
            "WHERE user_id=? AND status='approved' AND created_at>=? AND created_at<?",
            (ref_id, day_start, day_end),
        )).fetchone()

        deposits = float(dep_row["total"]) if dep_row else 0.0
        withdrawals = float(wd_row["total"]) if wd_row else 0.0
        net = deposits - withdrawals
        earned = max(net, 0.0) * _cfg.AFFILIATE_NET_RATE

        await db.execute(
            """INSERT OR IGNORE INTO affiliate_daily_settlements
               (affiliate_id, referred_id, date_str, deposits, withdrawals, net, earned)
               VALUES (?,?,?,?,?,?,?)""",
            (aff_id, ref_id, date_str, deposits, withdrawals, net, earned),
        )

        if earned > 0:
            await db.execute(
                "UPDATE affiliates SET net_earnings=net_earnings+?, claimable=claimable+? WHERE user_id=?",
                (earned, earned, aff_id),
            )
            settled.append({
                "affiliate_id": aff_id, "referred_id": ref_id,
                "date_str": date_str, "earned": earned, "net": net,
            })

    await db.commit()
    return settled


async def get_affiliate_today_net(referred_id: int | str) -> dict:
    """Return today's deposits/withdrawals/net for a referred user (live, unsettled)."""
    import datetime, config as _cfg
    db = await get_db()
    now = datetime.datetime.now(datetime.timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    day_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    day_end = day_start + 86400
    uid = str(referred_id)

    dep_row = await (await db.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM deposit_requests "
        "WHERE user_id=? AND status='approved' AND created_at>=? AND created_at<?",
        (uid, day_start, day_end),
    )).fetchone()
    wd_row = await (await db.execute(
        "SELECT COALESCE(SUM(amount),0) AS total FROM withdrawal_requests "
        "WHERE user_id=? AND status='approved' AND created_at>=? AND created_at<?",
        (uid, day_start, day_end),
    )).fetchone()

    deposits = float(dep_row["total"]) if dep_row else 0.0
    withdrawals = float(wd_row["total"]) if wd_row else 0.0
    net = deposits - withdrawals
    return {"deposits": deposits, "withdrawals": withdrawals, "net": net,
            "earned_today": max(net, 0.0) * _cfg.AFFILIATE_NET_RATE, "date_str": date_str}


async def claim_affiliate(user_id: int | str) -> float:
    db = await get_db()
    uid = str(user_id)
    aff = await get_affiliate(uid)
    if not aff or float(aff["claimable"]) <= 0:
        return 0.0
    amount = float(aff["claimable"])
    await db.execute(
        "UPDATE affiliates SET claimable=0, total_claimed=total_claimed+? WHERE user_id=?",
        (amount, uid),
    )
    await add_balance(uid, amount, note="Affiliate claim")
    await db.commit()
    return amount


# ── Rakeback helpers ───────────────────────────────────────────────────────────

async def add_rakeback(user_id: int | str, amount: float) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE users SET rakeback_accumulated=rakeback_accumulated+? WHERE user_id=?",
        (amount, str(user_id)),
    )
    await db.commit()


async def claim_rakeback(user_id: int | str) -> float:
    db = await get_db()
    uid = str(user_id)
    user = await get_user(uid)
    if not user:
        return 0.0
    amount = float(user["rakeback_accumulated"])
    if amount <= 0:
        return 0.0
    await db.execute(
        "UPDATE users SET rakeback_accumulated=0, rakeback_total_claimed=rakeback_total_claimed+? WHERE user_id=?",
        (amount, uid),
    )
    await add_balance(uid, amount, note="Rakeback claim")
    return amount


async def leaderboard(limit: int = 10) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM users ORDER BY balance DESC LIMIT ?", (limit,)
    )).fetchall()
    return [dict(r) for r in rows]


# ── Game helpers ───────────────────────────────────────────────────────────────

async def get_game_config(game_id: str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM games WHERE id=?", (game_id,)
    )).fetchone()
    return dict(row) if row else None


async def get_all_games() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute("SELECT * FROM games ORDER BY name")).fetchall()
    return [dict(r) for r in rows]


async def get_game_session(user_id: int | str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM game_sessions WHERE user_id=?", (str(user_id),)
    )).fetchone()
    return dict(row) if row else None


async def set_game_session(user_id: int | str, game: str, bet: float, state: str) -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        """INSERT INTO game_sessions (user_id, game, bet, state, started_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET game=?, bet=?, state=?, started_at=?""",
        (uid, game, bet, state, int(time.time()), game, bet, state, int(time.time())),
    )
    await db.commit()


async def clear_game_session(user_id: int | str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM game_sessions WHERE user_id=?", (str(user_id),))
    await db.commit()


# ── User stats helpers ─────────────────────────────────────────────────────────

async def get_user_stats(user_id: int | str) -> dict:
    db = await get_db()
    uid = str(user_id)
    row = await (await db.execute(
        "SELECT * FROM user_stats WHERE user_id=?", (uid,)
    )).fetchone()
    if not row:
        await db.execute("INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)", (uid,))
        await db.commit()
        row = await (await db.execute(
            "SELECT * FROM user_stats WHERE user_id=?", (uid,)
        )).fetchone()
    return dict(row) if row else {}


async def record_game_result(user_id: int | str, won: bool, profit: float) -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute("INSERT OR IGNORE INTO user_stats (user_id) VALUES (?)", (uid,))
    if won:
        await db.execute(
            """UPDATE user_stats SET
               games_played = games_played + 1,
               wins = wins + 1,
               total_profit = total_profit + ?,
               biggest_win = MAX(biggest_win, ?)
               WHERE user_id=?""",
            (profit, profit, uid),
        )
    else:
        await db.execute(
            """UPDATE user_stats SET
               games_played = games_played + 1,
               losses = losses + 1,
               total_profit = total_profit + ?,
               biggest_loss = MAX(biggest_loss, ?)
               WHERE user_id=?""",
            (profit, abs(profit), uid),
        )
    await db.commit()


# ── Ban helpers ────────────────────────────────────────────────────────────────

async def ban_user(user_id: int | str, reason: str = "", banned_by: str = "") -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        """INSERT INTO user_bans (user_id, reason, banned_by)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET reason=?, banned_by=?, banned_at=strftime('%s','now'), muted=0""",
        (uid, reason, banned_by, reason, banned_by),
    )
    await db.commit()


async def unban_user(user_id: int | str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM user_bans WHERE user_id=? AND muted=0", (str(user_id),))
    await db.commit()


async def mute_user(user_id: int | str, banned_by: str = "") -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        """INSERT INTO user_bans (user_id, reason, banned_by, muted)
           VALUES (?, 'muted', ?, 1)
           ON CONFLICT(user_id) DO UPDATE SET muted=1""",
        (uid, banned_by),
    )
    await db.commit()


async def unmute_user(user_id: int | str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE user_bans SET muted=0 WHERE user_id=?", (str(user_id),)
    )
    # remove row if not banned either
    await db.execute(
        "DELETE FROM user_bans WHERE user_id=? AND muted=0 AND (reason IS NULL OR reason='muted')",
        (str(user_id),)
    )
    await db.commit()


async def is_banned(user_id: int | str) -> bool:
    db = await get_db()
    row = await (await db.execute(
        "SELECT 1 FROM user_bans WHERE user_id=? AND muted=0", (str(user_id),)
    )).fetchone()
    return row is not None


async def is_muted(user_id: int | str) -> bool:
    db = await get_db()
    row = await (await db.execute(
        "SELECT 1 FROM user_bans WHERE user_id=? AND muted=1", (str(user_id),)
    )).fetchone()
    return row is not None


# ── Balance cap helpers ────────────────────────────────────────────────────────

async def set_balance_cap(user_id: int | str, cap: float, set_by: str = "") -> None:
    db = await get_db()
    uid = str(user_id)
    await db.execute(
        """INSERT INTO balance_caps (user_id, cap, set_by)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET cap=?, set_by=?, set_at=strftime('%s','now')""",
        (uid, cap, set_by, cap, set_by),
    )
    await db.commit()


async def remove_balance_cap(user_id: int | str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM balance_caps WHERE user_id=?", (str(user_id),))
    await db.commit()


async def get_balance_cap(user_id: int | str) -> float | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT cap FROM balance_caps WHERE user_id=?", (str(user_id),)
    )).fetchone()
    return float(row["cap"]) if row else None


async def get_global_setting(key: str, default: str = "") -> str:
    db = await get_db()
    row = await (await db.execute(
        "SELECT value FROM global_settings WHERE key=?", (key,)
    )).fetchone()
    return row["value"] if row else default


async def set_global_setting(key: str, value: str) -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO global_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
        (key, value, value),
    )
    await db.commit()


# ── Cases helpers ──────────────────────────────────────────────────────────────

async def get_case(name: str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM cases WHERE LOWER(name)=LOWER(?)", (name,)
    )).fetchone()
    return dict(row) if row else None


async def get_all_cases() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute("SELECT * FROM cases WHERE enabled=1 ORDER BY name")).fetchall()
    return [dict(r) for r in rows]


async def get_case_items(case_id: int) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM case_items WHERE case_id=? ORDER BY chance DESC", (case_id,)
    )).fetchall()
    return [dict(r) for r in rows]


# ── Payment helpers ────────────────────────────────────────────────────────────

async def get_active_payment_methods() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM payment_methods WHERE enabled=1"
    )).fetchall()
    return [dict(r) for r in rows]


# ── Giveaway helpers ───────────────────────────────────────────────────────────

async def get_active_giveaway(message_id: str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM giveaways WHERE message_id=?", (message_id,)
    )).fetchone()
    return dict(row) if row else None


async def get_giveaway_entries(giveaway_id: int) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM giveaway_entries WHERE giveaway_id=?", (giveaway_id,)
    )).fetchall()
    return [dict(r) for r in rows]


# ── Race helpers ───────────────────────────────────────────────────────────────

async def get_active_race() -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        "SELECT * FROM races WHERE ended=0 ORDER BY created_at DESC LIMIT 1"
    )).fetchone()
    return dict(row) if row else None


async def get_race_leaderboard(race_id: int, limit: int = 10) -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        """SELECT re.user_id, re.wagered, u.username
           FROM race_entries re
           LEFT JOIN users u ON u.user_id = re.user_id
           WHERE re.race_id=?
           ORDER BY re.wagered DESC LIMIT ?""",
        (race_id, limit),
    )).fetchall()
    return [dict(r) for r in rows]


# ── Bonus helpers ──────────────────────────────────────────────────────────────

async def get_active_bonus(user_id: int | str) -> dict | None:
    db = await get_db()
    row = await (await db.execute(
        """SELECT ab.*, b.name as bonus_name, b.description
           FROM active_bonuses ab
           JOIN bonuses b ON b.id = ab.bonus_id
           WHERE ab.user_id=? AND ab.completed=0
           ORDER BY ab.created_at DESC LIMIT 1""",
        (str(user_id),),
    )).fetchone()
    return dict(row) if row else None


async def get_all_bonuses() -> list[dict]:
    db = await get_db()
    rows = await (await db.execute(
        "SELECT * FROM bonuses WHERE enabled=1 ORDER BY name"
    )).fetchall()
    return [dict(r) for r in rows]


# ── Broadcast helpers ──────────────────────────────────────────────────────────

async def get_all_user_ids() -> list[str]:
    db = await get_db()
    rows = await (await db.execute("SELECT user_id FROM users")).fetchall()
    return [r["user_id"] for r in rows]
