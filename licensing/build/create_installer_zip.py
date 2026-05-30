#!/usr/bin/env python3
"""Create customer installer ZIP (no bot source)."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "dist" / "flipbot-installer.zip"

INCLUDE = [
    "licensing/__init__.py",
    "licensing/common/__init__.py",
    "licensing/common/server_url.py",
    "licensing/client/__init__.py",
    "licensing/client/install.py",
    "licensing/client/run.py",
    "licensing/client/license_client.py",
    "licensing/VERSION",
    "requirements.txt",
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in INCLUDE:
            path = ROOT / rel
            if path.exists():
                zf.write(path, rel)
        readme = (
            "Flipbot Installer\n"
            "=================\n\n"
            "1. pip install -r requirements.txt\n"
            "2. python licensing/client/install.py\n"
            "3. python licensing/client/run.py\n"
        )
        zf.writestr("README.txt", readme)
    print(f"Created {OUT}")


if __name__ == "__main__":
    main()
