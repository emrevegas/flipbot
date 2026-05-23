"""
Startup authorization check.
- AUTH_URL and AUTH_SECRET are hardcoded in source (obfuscated in PyInstaller bundle).
- INSTANCE_TOKEN is read from .env (unique per VDS, written by deploy wizard).
- If running from .py source OR as the main bot on dev/main VDS, auth is skipped.
- If running as a PyInstaller frozen bundle (buyer VDS), token is ALWAYS required.
"""

import hashlib
import hmac
import json
import os
import sys
import urllib.request

# Hardcoded — embedded in the PyInstaller bundle binary
_AUTH_URL  = "http://31.210.40.211:8001"
_SECRET    = "vegasbot_vegas_123"


def _is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def verify() -> None:
    # Running from .py source (dev machine or main VDS) — skip auth
    if not _is_frozen():
        print("[Auth] Source mode — skipping.")
        return

    # Compiled .pyd — token is mandatory, no exceptions
    token = os.getenv("INSTANCE_TOKEN", "").strip()
    if not token:
        print("[Auth] \u274c No INSTANCE_TOKEN. Bot is not authorized.")
        sys.exit(1)

    sig  = hmac.new(_SECRET.encode(), token.encode(), hashlib.sha256).hexdigest()
    body = json.dumps({"token": token, "signature": sig}).encode()
    req  = urllib.request.Request(
        f"{_AUTH_URL}/auth",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                print("[Auth] \u2705 Authorized.")
                return
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("detail", e.reason)
        except Exception:
            detail = e.reason
        print(f"[Auth] \u274c Not authorized: {detail}")
        sys.exit(1)
    except Exception as exc:
        print(f"[Auth] \u274c Cannot reach auth server: {exc}")
        sys.exit(1)

    print("[Auth] \u274c Not authorized.")
    sys.exit(1)
