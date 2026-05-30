"""Resolve license API base URL — domain gerekmez, IP + port yeterli."""

from __future__ import annotations

import os


def resolve_license_server_url() -> str:
    """
    Priority:
      1. LICENSE_SERVER_URL  (http://1.2.3.4:8787)
      2. LICENSE_SERVER_IP + LICENSE_SERVER_PORT
    """
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
