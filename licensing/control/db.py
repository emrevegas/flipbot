"""SQLite persistence for Ada license bot."""

from __future__ import annotations

import os
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from licensing.control.pricing import PLAN_DAYS

ROOT = Path(__file__).resolve().parents[2]


def _now() -> int:
    return int(time.time())


def _gen_key() -> str:
    part = lambda n: "".join(
        secrets.choice(string.ascii_uppercase + string.digits) for _ in range(n)
    )
    return f"VEGAS-{part(4)}-{part(4)}-{part(4)}"


class LicenseControlDB:
    def __init__(self, path: str | Path | None = None):
        default = ROOT / "data" / "ada_license.db"
        self.path = Path(path or os.getenv("ADA_LICENSE_DB_PATH", str(default)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS balances (
                    discord_id TEXT PRIMARY KEY,
                    balance REAL NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS licenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unused',
                    owner_discord_id TEXT,
                    customer_label TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    activated_at INTEGER,
                    vds_bound INTEGER NOT NULL DEFAULT 0,
                    vds_slot_open INTEGER NOT NULL DEFAULT 1,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_licenses_owner ON licenses(owner_discord_id);
                CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);

                CREATE TABLE IF NOT EXISTS vds_hosts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id TEXT NOT NULL UNIQUE,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    password_enc TEXT NOT NULL,
                    os_type TEXT NOT NULL,
                    license_key TEXT,
                    deploy_status TEXT NOT NULL DEFAULT 'connected',
                    bot_version TEXT,
                    install_dir TEXT,
                    guild_id TEXT,
                    last_error TEXT,
                    last_seen INTEGER,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deploy_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    discord_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS releases (
                    version TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                """
            )

    # ── Balance ─────────────────────────────────────────────

    def get_balance(self, discord_id: str) -> float:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT balance FROM balances WHERE discord_id = ?",
                (str(discord_id),),
            ).fetchone()
            return float(row["balance"]) if row else 0.0

    def add_balance(self, discord_id: str, amount: float) -> float:
        did = str(discord_id)
        now = _now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT balance FROM balances WHERE discord_id = ?", (did,)
            ).fetchone()
            new_bal = (float(row["balance"]) if row else 0.0) + amount
            if row:
                conn.execute(
                    "UPDATE balances SET balance = ?, updated_at = ? WHERE discord_id = ?",
                    (new_bal, now, did),
                )
            else:
                conn.execute(
                    "INSERT INTO balances (discord_id, balance, updated_at) VALUES (?, ?, ?)",
                    (did, new_bal, now),
                )
            return new_bal

    def deduct_balance(self, discord_id: str, amount: float) -> float:
        bal = self.get_balance(discord_id)
        if bal < amount:
            raise ValueError("Insufficient balance")
        did = str(discord_id)
        now = _now()
        new_bal = bal - amount
        with self._conn() as conn:
            conn.execute(
                "UPDATE balances SET balance = ?, updated_at = ? WHERE discord_id = ?",
                (new_bal, now, did),
            )
        return new_bal

    # ── Licenses ────────────────────────────────────────────

    def create_license(
        self,
        plan: str,
        *,
        owner_discord_id: str | None = None,
        customer_label: str | None = None,
        status: str = "unused",
    ) -> dict[str, Any]:
        days = PLAN_DAYS.get(plan)
        if not days:
            raise ValueError(f"Unknown plan: {plan}")
        now = _now()
        key = _gen_key()
        expires = now + days * 86400
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO licenses (
                    license_key, plan, status, owner_discord_id, customer_label,
                    created_at, expires_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    plan,
                    status,
                    owner_discord_id,
                    customer_label,
                    now,
                    expires,
                    now if owner_discord_id and status == "active" else None,
                ),
            )
        return self.get_license(key)

    def get_license(self, license_key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM licenses WHERE license_key = ?",
                (license_key.strip().upper(),),
            ).fetchone()
        return dict(row) if row else None

    def bind_license_to_user(self, license_key: str, discord_id: str) -> dict[str, Any]:
        key = license_key.strip().upper()
        did = str(discord_id)
        lic = self.get_license(key)
        if not lic:
            raise ValueError("License key not found")
        if lic["status"] in ("revoked", "suspended"):
            raise ValueError(f"License is {lic['status']}")
        if lic["expires_at"] < _now():
            raise ValueError("License has expired")
        if lic["owner_discord_id"] and lic["owner_discord_id"] != did:
            raise ValueError("License already belongs to another user")
        if self.get_vds(did):
            raise ValueError("Remove your VDS before using another license slot.")
        now = _now()
        # Re-open VDS slot after removal (same key, /use_license again)
        slot_open = 1
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE licenses
                SET owner_discord_id = ?, status = 'active',
                    activated_at = COALESCE(activated_at, ?),
                    vds_slot_open = ?
                WHERE license_key = ?
                """,
                (did, now, slot_open, key),
            )
        return self.get_license(key) or {}

    def can_add_vds(self, discord_id: str) -> tuple[bool, str]:
        lic = self.user_active_license(discord_id)
        if not lic:
            return False, "No active license — use `/use_license` first."
        if self.get_vds(discord_id):
            return False, "You already have a VDS. Remove it before adding another."
        if not int(lic.get("vds_slot_open") or 0):
            return False, "Run `/use_license` again with your key to unlock a new VDS slot."
        return True, lic["license_key"]

    def user_active_license(self, discord_id: str) -> dict[str, Any] | None:
        did = str(discord_id)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM licenses
                WHERE owner_discord_id = ? AND status = 'active'
                ORDER BY expires_at DESC LIMIT 1
                """,
                (did,),
            ).fetchone()
        if not row:
            return None
        lic = dict(row)
        if lic["expires_at"] < _now():
            self.set_license_status(lic["license_key"], "expired")
            return None
        return lic

    def set_license_status(self, license_key: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE licenses SET status = ? WHERE license_key = ?",
                (status, license_key.strip().upper()),
            )

    def extend_license(self, license_key: str, extra_days: int) -> dict[str, Any]:
        key = license_key.strip().upper()
        lic = self.get_license(key)
        if not lic:
            raise ValueError("License not found")
        base = max(lic["expires_at"], _now())
        new_exp = base + int(extra_days) * 86400
        with self._conn() as conn:
            conn.execute(
                "UPDATE licenses SET expires_at = ?, status = 'active' WHERE license_key = ?",
                (new_exp, key),
            )
        return self.get_license(key) or {}

    def mark_vds_bound(self, license_key: str, bound: bool = True) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE licenses SET vds_bound = ? WHERE license_key = ?",
                (1 if bound else 0, license_key.strip().upper()),
            )

    def list_licenses(self, *, status: str | None = None, limit: int = 25) -> list[dict]:
        q = "SELECT * FROM licenses"
        params: list[Any] = []
        if status:
            q += " WHERE status = ?"
            params.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def purchase_license(self, discord_id: str, plan: str) -> dict[str, Any]:
        from licensing.control.pricing import plan_price

        price = plan_price(plan)
        if price <= 0:
            raise ValueError("Plan price not configured")
        self.deduct_balance(discord_id, price)
        return self.create_license(
            plan,
            owner_discord_id=str(discord_id),
            status="active",
        )

    # ── VDS ─────────────────────────────────────────────────

    def get_vds(self, discord_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vds_hosts WHERE discord_id = ?",
                (str(discord_id),),
            ).fetchone()
        return dict(row) if row else None

    def save_vds(
        self,
        discord_id: str,
        *,
        host: str,
        port: int,
        username: str,
        password_enc: str,
        os_type: str,
        license_key: str,
    ) -> dict[str, Any]:
        did = str(discord_id)
        if self.get_vds(did):
            raise ValueError("You already have a VDS registered. Remove it before adding another.")
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO vds_hosts (
                    discord_id, host, port, username, password_enc, os_type,
                    license_key, deploy_status, created_at, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'connected', ?, ?)
                """,
                (did, host, port, username, password_enc, os_type, license_key, now, now),
            )
        self.mark_vds_bound(license_key, True)
        with self._conn() as conn:
            conn.execute(
                "UPDATE licenses SET vds_slot_open = 0 WHERE license_key = ?",
                (license_key.strip().upper(),),
            )
        return self.get_vds(did) or {}

    def delete_vds(self, discord_id: str) -> bool:
        did = str(discord_id)
        vds = self.get_vds(did)
        if not vds:
            return False
        with self._conn() as conn:
            conn.execute("DELETE FROM vds_hosts WHERE discord_id = ?", (did,))
        if vds.get("license_key"):
            self.mark_vds_bound(vds["license_key"], False)
        return True

    def update_vds_deploy(
        self,
        discord_id: str,
        *,
        deploy_status: str | None = None,
        bot_version: str | None = None,
        install_dir: str | None = None,
        guild_id: str | None = None,
        last_error: str | None = None,
    ) -> None:
        did = str(discord_id)
        fields = []
        values: list[Any] = []
        if deploy_status is not None:
            fields.append("deploy_status = ?")
            values.append(deploy_status)
        if bot_version is not None:
            fields.append("bot_version = ?")
            values.append(bot_version)
        if install_dir is not None:
            fields.append("install_dir = ?")
            values.append(install_dir)
        if guild_id is not None:
            fields.append("guild_id = ?")
            values.append(guild_id)
        if last_error is not None:
            fields.append("last_error = ?")
            values.append(last_error)
        fields.append("last_seen = ?")
        values.append(_now())
        values.append(did)
        if not fields:
            return
        with self._conn() as conn:
            conn.execute(
                f"UPDATE vds_hosts SET {', '.join(fields)} WHERE discord_id = ?",
                values,
            )

    def list_all_vds(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM vds_hosts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_deploy(self, discord_id: str, action: str, status: str, message: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO deploy_logs (discord_id, action, status, message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(discord_id), action, status, message[:2000], _now()),
            )

    # ── Releases ────────────────────────────────────────────

    def register_release(
        self, version: str, platform: str, download_url: str, sha256: str
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO releases (version, platform, download_url, sha256, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (version, platform, download_url, sha256, _now()),
            )

    def latest_release(self, platform: str = "linux-x86_64") -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM releases WHERE platform = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (platform,),
            ).fetchone()
        return dict(row) if row else None
