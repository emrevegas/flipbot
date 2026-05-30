"""Same logic as modules/license_env — for installer package without modules/."""

from __future__ import annotations

import os


def resolve_license_server_url() -> str:
    url = (os.getenv("LICENSE_SERVER_URL") or "").strip().rstrip("/")
    if url:
        return url
    ip = (os.getenv("LICENSE_SERVER_IP") or "").strip()
    if not ip:
        return ""
    port = (os.getenv("LICENSE_SERVER_PORT") or "8787").strip() or "8787"
    if ip.startswith("http://") or ip.startswith("https://"):
        return ip.rstrip("/")
    return f"http://{ip}:{port}"
