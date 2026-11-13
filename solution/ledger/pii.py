"""PII protection.

* Matching events to leads needs equality on the phone number, but a plain SHA-256 of a
  10-digit number can be brute-forced in minutes (only ~4e9 candidates). So we store a
  *keyed* hash (HMAC-SHA256) - useless without the secret key.
* Fields marked pii:true (name, phone) are stored encrypted (Fernet = AES-128-CBC + HMAC).
* The key comes from $LEDGER_PII_KEY, or is generated once into '<db>.key' (kept outside the
  database, gitignored). In production this would come from a secret manager / KMS.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

KEY_ENV = "LEDGER_PII_KEY"


def _load_master_key(key_path: Path) -> bytes:
    env = os.environ.get(KEY_ENV)
    if env:
        return env.encode("utf-8")
    if key_path.exists():
        return key_path.read_text().strip().encode("utf-8")
    key = base64.urlsafe_b64encode(secrets.token_bytes(32))
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


class PiiVault:
    def __init__(self, master_key: bytes):
        self._hash_key = hmac.digest(master_key, b"ledger/phone-hash", "sha256")
        enc_key = hmac.digest(master_key, b"ledger/field-encryption", "sha256")
        self._fernet = Fernet(base64.urlsafe_b64encode(enc_key))

    @classmethod
    def for_database(cls, db_path: str) -> "PiiVault":
        return cls(_load_master_key(Path(str(db_path) + ".key")))

    def phone_hash(self, canonical_phone: str) -> bytes:
        return hmac.digest(self._hash_key, canonical_phone.encode("utf-8"), "sha256")

    def key_check(self) -> str:
        """Value stored in the DB so a wrong key is detected instead of silently mis-linking."""
        return hmac.digest(self._hash_key, b"key-check", "sha256").hex()

    def encrypt(self, data: dict) -> bytes:
        return self._fernet.encrypt(json.dumps(data, sort_keys=True).encode("utf-8"))

    def decrypt(self, blob: bytes) -> dict:
        return json.loads(self._fernet.decrypt(blob).decode("utf-8"))


def last4(canonical_phone: str) -> str:
    return canonical_phone[-4:]
