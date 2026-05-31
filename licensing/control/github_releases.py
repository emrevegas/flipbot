"""Fetch latest compiled release from GitHub releases repo."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


def fetch_latest_release(platform: str = "linux-x86_64") -> dict[str, Any] | None:
    repo = (os.getenv("RELEASES_GITHUB_REPO") or "").strip()
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    if not repo:
        return None
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode().strip()
            if not raw:
                return None
            data = json.loads(raw)
    except Exception:
        return None

    tag = (data.get("tag_name") or "").lstrip("v")
    for asset in data.get("assets") or []:
        name = asset.get("name") or ""
        if platform in name and name.endswith(".tar.gz"):
            return {
                "version": tag,
                "platform": platform,
                "download_url": asset.get("browser_download_url"),
                "sha256": "",  # optional in release notes
            }
    return None
