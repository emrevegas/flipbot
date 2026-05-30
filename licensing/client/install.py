#!/usr/bin/env python3
"""Flipbot licensed installer — run once on customer VDS."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licensing.client.license_client import (  # noqa: E402
    LicenseClient,
    fetch_public_ip,
    machine_id,
    save_install_state,
    write_env,
)

RUNTIME_DIR = ROOT / "runtime"
VERSION_FILE = ROOT / "licensing" / "VERSION"


def _prompt(label: str, *, secret: bool = False, default: str = "") -> str:
    import getpass

    hint = f" [{default}]" if default else ""
    while True:
        if secret:
            val = getpass.getpass(f"{label}{hint}: ")
        else:
            val = input(f"{label}{hint}: ").strip()
        if not val and default:
            return default
        if val:
            return val
        print("  (required)")


def _download_release(client: LicenseClient, platform_name: str = "linux-x86_64") -> dict:
    rel = client.latest_release(platform_name)
    url = rel["download_url"]
    print(f"\nDownloading v{rel['version']} from release server…")
    import urllib.request

    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    expected = (rel.get("sha256") or "").strip().lower()
    if expected:
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise RuntimeError("SHA256 mismatch — download corrupted or tampered.")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if RUNTIME_DIR.exists():
        for child in RUNTIME_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(RUNTIME_DIR)
    print(f"Runtime extracted to {RUNTIME_DIR}")
    return rel


def main() -> int:
    print("=" * 56)
    print("  Flipbot Licensed Installer")
    print("=" * 56)

    default_ip = os.getenv("LICENSE_SERVER_IP", "").strip()
    default_port = os.getenv("LICENSE_SERVER_PORT", "8787").strip() or "8787"
    server_ip = _prompt("Ana VDS IP (license sunucusu)", default=default_ip or "127.0.0.1")
    server_port = _prompt("License API port", default=default_port)
    server_url = f"http://{server_ip.strip()}:{server_port.strip()}"
    print(f"License server: {server_url}")

    client = LicenseClient(server_url)

    ip = fetch_public_ip()
    mid = machine_id()
    print(f"\nDevice IP: {ip}")
    print(f"Machine ID: {mid[:16]}…")

    license_key = _prompt("License key").upper()
    print("\nActivating license (IP whitelist)…")
    try:
        act = client.activate(license_key, ip=ip, mid=mid)
    except RuntimeError as exc:
        print(f"\n❌ Activation failed: {exc}")
        return 1
    print(f"✅ License OK — expires <t:{act['license']['expires_at']}>")

    print("\n--- Bot configuration ---")
    token = _prompt("Discord bot TOKEN", secret=True)
    crypto_mnemonic = _prompt("Crypto deposit mnemonic (24 words)", secret=True)
    treasury_mnemonic = _prompt("Treasury mnemonic (24 words)", secret=True)
    guild_id = _prompt("Guild ID")
    owner_id = _prompt("Owner ID")
    super_admin_id = _prompt("Super Admin ID", default=owner_id)

    write_env(
        {
            "TOKEN": token,
            "OWNER_ID": owner_id,
            "SUPER_ADMIN_ID": super_admin_id,
            "LICENSE_KEY": license_key,
            "LICENSE_SERVER_IP": server_ip.strip(),
            "LICENSE_SERVER_PORT": server_port.strip(),
            "LICENSE_SERVER_URL": server_url,
            "GUILD_ID": guild_id,
            "CRYPTO_MNEMONIC": crypto_mnemonic,
            "TREASURY_MNEMONIC": treasury_mnemonic,
            "PREFIX": ".",
        }
    )

    platform_name = "linux-x86_64"
    try:
        rel = _download_release(client, platform_name)
    except RuntimeError as exc:
        print(f"\n⚠️  Release download failed: {exc}")
        print("Install config saved — run installer again when a build is published.")
        rel = {"version": "0.0.0"}

    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(str(rel.get("version", "0.0.0")), encoding="utf-8")

    save_install_state(
        {
            "license_key": license_key,
            "server_url": server_url,
            "server_ip": server_ip.strip(),
            "server_port": server_port.strip(),
            "machine_id": mid,
            "whitelisted_ip": ip,
            "guild_id": guild_id,
            "owner_id": owner_id,
            "super_admin_id": super_admin_id,
            "installed_version": rel.get("version", "0.0.0"),
            "platform": platform_name,
        }
    )

    client.heartbeat(
        license_key,
        ip=ip,
        mid=mid,
        guild_id=guild_id,
        owner_id=owner_id,
        super_admin_id=super_admin_id,
        bot_version=str(rel.get("version", "0.0.0")),
    )

    print("\n" + "=" * 56)
    print("  ✅ Installation complete!")
    print("  Start bot:  python licensing/client/run.py")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
