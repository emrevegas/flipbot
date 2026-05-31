"""Encrypt VDS credentials at rest in Ada license DB."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    raw = (os.getenv("LICENSE_DB_ENCRYPTION_KEY") or os.getenv("LICENSE_SIGNING_SECRET") or "").strip()
    if not raw:
        raise RuntimeError("LICENSE_DB_ENCRYPTION_KEY or LICENSE_SIGNING_SECRET required in ada.env")
    digest = hashlib.sha256(raw.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_text(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode("ascii")


def decrypt_text(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
