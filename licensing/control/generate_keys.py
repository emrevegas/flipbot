#!/usr/bin/env python3
"""Generate Ed25519 keypair for license signing."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from licensing.control.license_sign import generate_keypair_files

if __name__ == "__main__":
    priv, pub = generate_keypair_files()
    print(f"Private key: {priv}")
    print(f"Public key:  {pub}")
    print("Set LICENSE_PRIVATE_KEY_PATH in ada.env to the private PEM path.")
