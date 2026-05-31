"""
Licensed runtime guard — compiled to .so in customer releases.

Validates signed license.dat locally and checks GitHub for newer releases.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

LICENSE_FILE = Path("license.dat")
VERSION_FILE = Path("licensing/VERSION")
PUBLIC_KEY_FILE = Path("licensing/control/license_public.pem")


def _licensed_install() -> bool:
    if LICENSE_FILE.exists():
        return True
    if os.getenv("LICENSE_KEY", "").strip():
        return True
    return False


def _verify_license_file() -> dict:
    from licensing.control.license_sign import verify_license_file

    if not LICENSE_FILE.exists():
        raise RuntimeError("license.dat missing — run setup via license bot")
    try:
        data = json.loads(LICENSE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("Invalid license.dat") from exc
    payload = verify_license_file(data)
    if payload.get("status") not in ("active",):
        raise RuntimeError(f"License status: {payload.get('status')}")
    expires = int(payload.get("expires_at") or 0)
    if expires < time.time():
        raise RuntimeError("License expired")
    env_key = (os.getenv("LICENSE_KEY") or "").strip().upper()
    file_key = (payload.get("license_key") or "").strip().upper()
    if env_key and file_key and env_key != file_key:
        raise RuntimeError("LICENSE_KEY does not match license.dat")
    return payload


def _local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return "0.0.0"


def _check_github_update(payload: dict) -> None:
    repo = (payload.get("releases_repo") or os.getenv("RELEASES_GITHUB_REPO") or "").strip()
    if not repo:
        return
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"}),
            timeout=15,
        ) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return
    tag = (data.get("tag_name") or "").lstrip("v")

    def _parse_ver(v: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", v)
        return tuple(int(x) for x in parts) if parts else (0,)

    if _parse_ver(tag) > _parse_ver(_local_version()):
        print(f"ℹ️  Update available: v{tag} (current v{_local_version()}) — use /manage_bot setup on license bot")


def enforce_or_exit() -> None:
    """Block startup when license invalid. Skipped on dev machines without license.dat."""
    if not _licensed_install():
        return
    try:
        payload = _verify_license_file()
    except Exception as exc:
        print(f"❌ License check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    days = max(0, int((int(payload["expires_at"]) - time.time()) / 86400))
    print(f"✅ License OK — {payload.get('license_key', '?')} · {days} day(s) left")
    _check_github_update(payload)
