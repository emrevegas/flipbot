#!/usr/bin/env python3
"""
Build obfuscated Linux release with Cython and register on license server.

Usage (on Ubuntu build machine):
  python licensing/build/build_release.py --version 1.0.1

Env:
  LICENSE_SERVER_URL
  LICENSE_ADMIN_KEY
  RELEASES_GITHUB_REPO   owner/repo for compiled tarballs
  GITHUB_TOKEN           optional — upload release asset
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLATFORM = "linux-x86_64"
BUILD_DIRS = ("modules", "cogs", "Games", "database")
# bot.py is a plain stub; core + license guard are inside modules/ (.so)
LICENSED_BOT_STUB = '''"""Licensed runtime entry — edit blocked; logic is compiled."""
from modules.flipbot_launcher import run

if __name__ == "__main__":
    run()
'''
COPY_PATHS = (
    "assets",
    "database/lang",
    "licensing/VERSION",
    "requirements.txt",
    "config.py",
)


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.check_call(cmd, cwd=cwd or ROOT)


def _compile_cython(staging: Path) -> None:
    """Compile Python packages to .so inside staging tree."""
    for pkg in BUILD_DIRS:
        src = ROOT / pkg
        if not src.exists():
            continue
        dst = staging / pkg
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        setup_code = f'''
from setuptools import setup
from Cython.Build import cythonize
from pathlib import Path
import glob

root = Path(r"{dst}")
py_files = [str(p) for p in root.rglob("*.py") if p.name != "__init__.py"]
setup(
    ext_modules=cythonize(
        py_files,
        compiler_directives={{"language_level": "3"}},
        nthreads=4,
    ),
    script_args=["build_ext", "--inplace"],
)
'''
        setup_path = staging / f"setup_{pkg.replace('/', '_')}.py"
        setup_path.write_text(setup_code, encoding="utf-8")
        _run([sys.executable, str(setup_path)], cwd=staging)
        for py in dst.rglob("*.py"):
            if py.name == "__init__.py":
                continue
            py.unlink(missing_ok=True)
        for c_file in dst.rglob("*.c"):
            c_file.unlink(missing_ok=True)
        setup_path.unlink(missing_ok=True)


def _create_tarball(staging: Path, version: str) -> Path:
    out_dir = ROOT / "dist"
    out_dir.mkdir(exist_ok=True)
    archive = out_dir / f"flipbot-{version}-{PLATFORM}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname=".")
    return archive


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _upload_github_release(archive: Path, version: str) -> str:
    repo = os.getenv("RELEASES_GITHUB_REPO", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not repo or not token:
        return f"file://{archive.resolve()}"

    tag = f"v{version}"
    api = f"https://api.github.com/repos/{repo}/releases"
    import urllib.request

    def api_call(method: str, url: str, data: dict | None = None) -> dict:
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    try:
        rel = api_call("POST", api, {"tag_name": tag, "name": tag, "draft": False, "prerelease": False})
    except Exception:
        # release may exist
        list_url = f"{api}/tags/{tag}"
        rel = api_call("GET", list_url)

    upload_url = rel["upload_url"].split("{")[0]
    asset_name = archive.name
    with archive.open("rb") as f:
        data = f.read()
    req = urllib.request.Request(
        f"{upload_url}?name={asset_name}",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/gzip",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        asset = json.loads(resp.read().decode())
    return asset["browser_download_url"]


def _register_release(version: str, download_url: str, digest: str) -> None:
    from licensing.client.license_client import LicenseClient
    from licensing.common.server_url import resolve_license_server_url

    admin_key = os.getenv("LICENSE_ADMIN_KEY", "")
    server = resolve_license_server_url() or os.getenv("LICENSE_SERVER_URL", "http://127.0.0.1:8787")
    if not admin_key:
        print("⚠️  LICENSE_ADMIN_KEY not set — skipping server registration.")
        return
    client = LicenseClient(server)
    client.admin_register_release(
        admin_key,
        version=version,
        platform=PLATFORM,
        download_url=download_url,
        sha256=digest,
        notes=f"Built on {PLATFORM}",
    )
    print("✅ Release registered on license server.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Cython release for licensed bots")
    parser.add_argument("--version", required=True, help="Semver e.g. 1.0.1")
    parser.add_argument("--skip-cython", action="store_true", help="Package source without compiling")
    args = parser.parse_args()
    version = args.version.strip()

    (ROOT / "licensing" / "VERSION").write_text(version + "\n", encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "bundle"
        staging.mkdir()
        for rel in COPY_PATHS:
            src = ROOT / rel
            if not src.exists():
                continue
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        (staging / "bot.py").write_text(LICENSED_BOT_STUB, encoding="utf-8")

        if not args.skip_cython:
            try:
                import Cython  # noqa: F401
            except ImportError:
                print("Installing Cython…")
                _run([sys.executable, "-m", "pip", "install", "cython", "setuptools"])
            _compile_cython(staging)
        else:
            for pkg in BUILD_DIRS:
                src = ROOT / pkg
                if src.exists():
                    shutil.copytree(src, staging / pkg)

        archive = _create_tarball(staging, version)
    digest = _sha256(archive)
    print(f"Archive: {archive}  sha256={digest}")

    download_url = _upload_github_release(archive, version)
    print(f"Download URL: {download_url}")
    _register_release(version, download_url, digest)
    manifest = {
        "version": version,
        "platform": PLATFORM,
        "download_url": download_url,
        "sha256": digest,
    }
    manifest_path = ROOT / "dist" / f"manifest-{version}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
