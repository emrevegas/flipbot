"""
Licensed runtime guard — compiled to .so in customer releases.

IP whitelist + machine binding + heartbeat. Must run before bot startup.
Do not rely on licensing/client/run.py (plain text installer only).
"""

from __future__ import annotations

import json
import os
import platform
import socket
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

INSTALL_STATE = Path("install_state.json")
VERSION_FILE = Path("licensing/VERSION")


def _machine_id() -> str:
    node = platform.node() or "unknown"
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"{node}-{uuid.getnode()}").hex


def _public_ip() -> str:
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urlopen(url, timeout=8) as resp:
                ip = resp.read().decode().strip()
                if ip:
                    return ip
        except (URLError, HTTPError, TimeoutError, OSError):
            continue
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _api_post(server: str, path: str, payload: dict) -> dict[str, Any]:
    url = server.rstrip("/") + path
    req = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
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


def _load_state() -> dict[str, Any]:
    if not INSTALL_STATE.exists():
        return {}
    try:
        return json.loads(INSTALL_STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _licensed_install() -> bool:
    if os.getenv("LICENSE_KEY", "").strip():
        return True
    return INSTALL_STATE.exists()


def enforce_or_exit() -> None:
    """
    Block startup when license/IP invalid.
    Skipped only on dev machines without LICENSE_KEY and without install_state.json.
    """
    if not _licensed_install():
        return

    state = _load_state()
    license_key = (os.getenv("LICENSE_KEY") or state.get("license_key") or "").strip().upper()
    from modules.license_env import resolve_license_server_url

    server_url = (
        (os.getenv("LICENSE_SERVER_URL") or state.get("server_url") or "").strip()
        or resolve_license_server_url()
    )
    if not license_key or not server_url:
        print("❌ Licensed install incomplete — run installer.", file=sys.stderr)
        raise SystemExit(1)

    ip = _public_ip()
    mid = _machine_id()
    payload = {"license_key": license_key, "ip": ip, "machine_id": mid}

    try:
        result = _api_post(server_url, "/api/v1/license/validate", payload)
    except RuntimeError as exc:
        print(f"❌ License check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    lic = result.get("license") or {}
    print(f"✅ License OK — {lic.get('plan', '?')} · expires {lic.get('expires_at', '?')} · IP {ip}")

    try:
        _api_post(
            server_url,
            "/api/v1/instance/heartbeat",
            {
                **payload,
                "guild_id": state.get("guild_id") or os.getenv("GUILD_ID"),
                "owner_id": state.get("owner_id") or os.getenv("OWNER_ID"),
                "super_admin_id": state.get("super_admin_id") or os.getenv("SUPER_ADMIN_ID"),
                "bot_version": _local_version(),
            },
        )
    except RuntimeError as exc:
        print(f"⚠️  Heartbeat failed: {exc}", file=sys.stderr)
