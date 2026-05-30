"""License server SQLite persistence."""

from __future__ import annotations

import json
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PLAN_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
}


def _now() -> int:
    return int(time.time())


def _gen_key() -> str:
    part = lambda n: "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(n))
    return f"VEGAS-{part(4)}-{part(4)}-{part(4)}"


class LicenseDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
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
                CREATE TABLE IF NOT EXISTS licenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT NOT NULL UNIQUE,
                    plan TEXT NOT NULL,
                    customer_discord_id TEXT,
                    customer_label TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    whitelisted_ip TEXT,
                    machine_id TEXT,
                    activated_at INTEGER,
                    notes TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_licenses_status ON licenses(status);
                CREATE INDEX IF NOT EXISTS idx_licenses_expires ON licenses(expires_at);

                CREATE TABLE IF NOT EXISTS instances (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT NOT NULL,
                    guild_id TEXT,
                    owner_id TEXT,
                    super_admin_id TEXT,
                    bot_version TEXT,
                    ip TEXT,
                    machine_id TEXT,
                    last_seen INTEGER NOT NULL,
                    install_meta TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_instances_key ON instances(license_key);

                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    sha256 TEXT,
                    created_at INTEGER NOT NULL,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT,
                    action TEXT NOT NULL,
                    target TEXT,
                    details TEXT,
                    created_at INTEGER NOT NULL
                );
                """
            )

    def audit(self, action: str, *, actor: str = "system", target: str = "", details: dict | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (actor, action, target, details, created_at) VALUES (?,?,?,?,?)",
                (actor, action, target, json.dumps(details or {}), _now()),
            )

    def create_license(
        self,
        plan: str,
        *,
        customer_discord_id: str | None = None,
        customer_label: str | None = None,
        custom_days: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        plan = plan.lower().strip()
        if plan not in PLAN_DAYS and custom_days is None:
            raise ValueError(f"Invalid plan: {plan}")
        days = int(custom_days) if custom_days is not None else PLAN_DAYS[plan]
        days = max(1, days)
        key = _gen_key()
        created = _now()
        expires = created + days * 86400
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO licenses
                (license_key, plan, customer_discord_id, customer_label, status, created_at, expires_at, notes)
                VALUES (?,?,?,?, 'active', ?, ?, ?)
                """,
                (key, plan, customer_discord_id, customer_label, created, expires, notes),
            )
        row = self.get_license(key)
        assert row
        self.audit("license.create", target=key, details={"plan": plan, "days": days})
        return row

    def get_license(self, license_key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM licenses WHERE license_key = ?",
                (license_key.strip().upper(),),
            ).fetchone()
        return dict(row) if row else None

    def list_licenses(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM licenses WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM licenses ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, license_key: str, status: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE licenses SET status = ? WHERE license_key = ?",
                (status, license_key.strip().upper()),
            )
        ok = cur.rowcount > 0
        if ok:
            self.audit("license.status", target=license_key, details={"status": status})
        return ok

    def extend_license(self, license_key: str, extra_days: int) -> dict[str, Any] | None:
        extra_days = max(1, int(extra_days))
        lic = self.get_license(license_key)
        if not lic:
            return None
        base = max(int(lic["expires_at"]), _now())
        new_exp = base + extra_days * 86400
        with self._conn() as conn:
            conn.execute(
                "UPDATE licenses SET expires_at = ?, status = 'active' WHERE license_key = ?",
                (new_exp, license_key.strip().upper()),
            )
        self.audit("license.extend", target=license_key, details={"extra_days": extra_days})
        return self.get_license(license_key)

    def activate_license(
        self,
        license_key: str,
        *,
        ip: str,
        machine_id: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        lic = self.get_license(license_key)
        if not lic:
            return False, "Invalid license key.", None
        if lic["status"] == "revoked":
            return False, "License revoked.", lic
        if lic["status"] == "suspended":
            return False, "License suspended.", lic
        if int(lic["expires_at"]) < _now():
            self.set_status(license_key, "expired")
            return False, "License expired.", lic

        stored_ip = (lic.get("whitelisted_ip") or "").strip()
        stored_machine = (lic.get("machine_id") or "").strip()

        if stored_ip and stored_ip != ip:
            return False, "License bound to another IP address.", lic
        if stored_machine and stored_machine != machine_id:
            return False, "License bound to another device.", lic

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE licenses
                SET whitelisted_ip = ?, machine_id = ?, activated_at = COALESCE(activated_at, ?)
                WHERE license_key = ?
                """,
                (ip, machine_id, _now(), license_key.strip().upper()),
            )
        self.audit("license.activate", target=license_key, details={"ip": ip, "machine_id": machine_id})
        return True, "OK", self.get_license(license_key)

    def validate_license(
        self,
        license_key: str,
        *,
        ip: str,
        machine_id: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        lic = self.get_license(license_key)
        if not lic:
            return False, "Invalid license key.", None
        if lic["status"] != "active":
            return False, f"License status: {lic['status']}.", lic
        if int(lic["expires_at"]) < _now():
            self.set_status(license_key, "expired")
            return False, "License expired.", lic
        if not lic.get("whitelisted_ip"):
            return False, "License not activated — run installer first.", lic
        if lic["whitelisted_ip"] != ip:
            return False, "IP not whitelisted for this license.", lic
        if lic.get("machine_id") and lic["machine_id"] != machine_id:
            return False, "Device mismatch.", lic
        return True, "OK", lic

    def upsert_instance(
        self,
        license_key: str,
        *,
        ip: str,
        machine_id: str,
        guild_id: str | None = None,
        owner_id: str | None = None,
        super_admin_id: str | None = None,
        bot_version: str | None = None,
        install_meta: dict | None = None,
    ) -> None:
        meta_json = json.dumps(install_meta or {})
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM instances WHERE license_key = ?",
                (license_key.strip().upper(),),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE instances SET
                        guild_id = COALESCE(?, guild_id),
                        owner_id = COALESCE(?, owner_id),
                        super_admin_id = COALESCE(?, super_admin_id),
                        bot_version = COALESCE(?, bot_version),
                        ip = ?, machine_id = ?, last_seen = ?, install_meta = ?
                    WHERE license_key = ?
                    """,
                    (
                        guild_id, owner_id, super_admin_id, bot_version,
                        ip, machine_id, _now(), meta_json, license_key.strip().upper(),
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO instances
                    (license_key, guild_id, owner_id, super_admin_id, bot_version, ip, machine_id, last_seen, install_meta)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        license_key.strip().upper(), guild_id, owner_id, super_admin_id,
                        bot_version, ip, machine_id, _now(), meta_json,
                    ),
                )

    def list_instances(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT i.*, l.plan, l.status AS license_status, l.expires_at
                FROM instances i
                LEFT JOIN licenses l ON l.license_key = i.license_key
                ORDER BY i.last_seen DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def register_release(
        self,
        version: str,
        platform: str,
        download_url: str,
        *,
        sha256: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO releases (version, platform, download_url, sha256, created_at, notes)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(version) DO UPDATE SET
                    platform = excluded.platform,
                    download_url = excluded.download_url,
                    sha256 = excluded.sha256,
                    notes = excluded.notes
                """,
                (version, platform, download_url, sha256, _now(), notes),
            )
        self.audit("release.register", target=version, details={"platform": platform, "url": download_url})
        return self.latest_release(platform)

    def latest_release(self, platform: str = "linux-x86_64") -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM releases
                WHERE platform = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (platform,),
            ).fetchone()
        return dict(row) if row else None
