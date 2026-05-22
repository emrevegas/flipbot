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
            ftd_earnings    REAL NOT NULL DEFAULT 0,
            edge_earnings   REAL NOT NULL DEFAULT 0,
            total_claimed   REAL NOT NULL DEFAULT 0,
            claimable       REAL NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        );

        CREATE TABLE IF NOT EXISTS affiliate_refs (
            ref_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_id    TEXT NOT NULL,
            referred_id     TEXT NOT NULL,
            ftd_paid        INTEGER NOT NULL DEFAULT 0,
            first_deposit   REAL NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(affiliate_id, referred_id)
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
    """)
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


async def add_affiliate_earnings(affiliate_id: int | str, ftd: float = 0, edge: float = 0) -> None:
    db = await get_db()
    uid = str(affiliate_id)
    await db.execute(
        "UPDATE affiliates SET ftd_earnings=ftd_earnings+?, edge_earnings=edge_earnings+?, claimable=claimable+? WHERE user_id=?",
        (ftd, edge, ftd + edge, uid),
    )
    await db.commit()


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
