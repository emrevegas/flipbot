"""Shared license API client for installer + launcher."""

from __future__ import annotations

import json
import os
import platform
import socket
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INSTALL_STATE_FILE = Path("install_state.json")
ENV_FILE = Path(".env")


def resolve_license_server_url() -> str:
    try:
        from modules.license_env import resolve_license_server_url as _resolve

        return _resolve()
    except ImportError:
        from licensing.common.server_url import resolve_license_server_url as _resolve

        return _resolve()


DEFAULT_SERVER = resolve_license_server_url() or "http://127.0.0.1:8787"


def machine_id() -> str:
    node = platform.node() or "unknown"
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{node}-{uuid.getnode()}").hex


def fetch_public_ip() -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urlopen(url, timeout=8) as resp:
                ip = resp.read().decode().strip()
                if ip:
                    return ip
        except (URLError, HTTPError, TimeoutError, OSError):
            continue
    # Fallback — local outbound guess
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _request(
    method: str,
    path: str,
    *,
    server_url: str,
    payload: dict | None = None,
    admin_key: str | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    url = server_url.rstrip("/") + path
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as exc:
        body = exc.read().decode()
        try:
            detail = json.loads(body).get("detail", body)
        except json.JSONDecodeError:
            detail = body or str(exc)
        raise RuntimeError(str(detail)) from exc
    except URLError as exc:
        raise RuntimeError(f"License server unreachable: {exc}") from exc


class LicenseClient:
    def __init__(self, server_url: str | None = None):
        self.server_url = (server_url or resolve_license_server_url() or DEFAULT_SERVER).rstrip("/")

    def activate(self, license_key: str, *, ip: str | None = None, mid: str | None = None) -> dict:
        return _request(
            "POST",
            "/api/v1/license/activate",
            server_url=self.server_url,
            payload={
                "license_key": license_key.strip().upper(),
                "ip": ip or fetch_public_ip(),
                "machine_id": mid or machine_id(),
            },
        )

    def validate(self, license_key: str, *, ip: str | None = None, mid: str | None = None) -> dict:
        return _request(
            "POST",
            "/api/v1/license/validate",
            server_url=self.server_url,
            payload={
                "license_key": license_key.strip().upper(),
                "ip": ip or fetch_public_ip(),
                "machine_id": mid or machine_id(),
            },
        )

    def heartbeat(
        self,
        license_key: str,
        *,
        ip: str | None = None,
        mid: str | None = None,
        guild_id: str | None = None,
        owner_id: str | None = None,
        super_admin_id: str | None = None,
        bot_version: str | None = None,
    ) -> dict:
        return _request(
            "POST",
            "/api/v1/instance/heartbeat",
            server_url=self.server_url,
            payload={
                "license_key": license_key.strip().upper(),
                "ip": ip or fetch_public_ip(),
                "machine_id": mid or machine_id(),
                "guild_id": guild_id,
                "owner_id": owner_id,
                "super_admin_id": super_admin_id,
                "bot_version": bot_version,
            },
        )

    def latest_release(self, platform_name: str = "linux-x86_64") -> dict:
        url = f"{self.server_url}/api/v1/releases/latest?platform={platform_name}"
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    # Admin
    def admin_create_license(self, admin_key: str, **kwargs) -> dict:
        return _request(
            "POST",
            "/api/v1/admin/licenses",
            server_url=self.server_url,
            payload=kwargs,
            admin_key=admin_key,
        )

    def admin_list_licenses(self, admin_key: str, **params) -> dict:
        q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        path = "/api/v1/admin/licenses" + (f"?{q}" if q else "")
        return _request("GET", path, server_url=self.server_url, admin_key=admin_key)

    def admin_list_instances(self, admin_key: str, limit: int = 50) -> dict:
        return _request(
            "GET",
            f"/api/v1/admin/instances?limit={limit}",
            server_url=self.server_url,
            admin_key=admin_key,
        )

    def admin_set_status(self, admin_key: str, license_key: str, status: str) -> dict:
        return _request(
            "PATCH",
            f"/api/v1/admin/licenses/{license_key.strip().upper()}/status",
            server_url=self.server_url,
            payload={"status": status},
            admin_key=admin_key,
        )

    def admin_extend(self, admin_key: str, license_key: str, extra_days: int) -> dict:
        return _request(
            "POST",
            f"/api/v1/admin/licenses/{license_key.strip().upper()}/extend",
            server_url=self.server_url,
            payload={"extra_days": extra_days},
            admin_key=admin_key,
        )

    def admin_register_release(self, admin_key: str, **kwargs) -> dict:
        return _request(
            "POST",
            "/api/v1/admin/releases",
            server_url=self.server_url,
            payload=kwargs,
            admin_key=admin_key,
        )


def load_install_state() -> dict[str, Any]:
    if not INSTALL_STATE_FILE.exists():
        return {}
    return json.loads(INSTALL_STATE_FILE.read_text(encoding="utf-8"))


def save_install_state(data: dict[str, Any]) -> None:
    INSTALL_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_env(values: dict[str, str]) -> None:
    lines: list[str] = []
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v
            else:
                lines.append(line)
    existing.update(values)
    out = [f"{k}={v}" for k, v in sorted(existing.items())]
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
