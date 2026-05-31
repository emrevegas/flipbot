"""Ed25519 license file signing (private key on Ada host only)."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_KEY_FILE = ROOT / "licensing" / "control" / "license_public.pem"


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def load_private_key() -> Ed25519PrivateKey:
    pem = (os.getenv("LICENSE_PRIVATE_KEY") or "").strip()
    if pem:
        if "\\n" in pem:
            pem = pem.replace("\\n", "\n")
        return serialization.load_pem_private_key(pem.encode(), password=None)
    path = os.getenv("LICENSE_PRIVATE_KEY_PATH", "").strip()
    if path:
        data = Path(path).read_bytes()
        return serialization.load_pem_private_key(data, password=None)
    raise RuntimeError("LICENSE_PRIVATE_KEY or LICENSE_PRIVATE_KEY_PATH missing in ada.env")


def load_public_key() -> Ed25519PublicKey:
    if PUBLIC_KEY_FILE.is_file():
        return serialization.load_pem_public_key(PUBLIC_KEY_FILE.read_bytes())
    pem = (os.getenv("LICENSE_PUBLIC_KEY") or "").strip()
    if pem:
        if "\\n" in pem:
            pem = pem.replace("\\n", "\n")
        return serialization.load_pem_public_key(pem.encode())
    private = load_private_key()
    return private.public_key()


def ensure_public_key_file() -> Path:
    """Write public PEM next to control package (for customer releases)."""
    pub = load_public_key()
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PUBLIC_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_KEY_FILE.write_bytes(pem)
    return PUBLIC_KEY_FILE


def sign_license_payload(payload: dict[str, Any]) -> dict[str, Any]:
    private = load_private_key()
    sig = private.sign(_canonical(payload))
    return {"payload": payload, "signature": base64.b64encode(sig).decode("ascii")}


def verify_license_file(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("payload")
    signature = data.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        raise ValueError("Invalid license file format")
    pub = load_public_key()
    pub.verify(base64.b64decode(signature), _canonical(payload))
    return payload


def generate_keypair_files(directory: Path | None = None) -> tuple[Path, Path]:
    """One-time helper — run from owner machine."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as Gen

    directory = directory or PUBLIC_KEY_FILE.parent
    directory.mkdir(parents=True, exist_ok=True)
    private_key = Gen.generate()
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    priv_path = directory / "license_private.pem"
    pub_path = directory / "license_public.pem"
    priv_path.write_bytes(priv_pem)
    pub_path.write_bytes(pub_pem)
    return priv_path, pub_path
