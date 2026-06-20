"""
Symmetric encryption for secrets at rest (Fernet / AES-128-CBC + HMAC).

Used to protect DKIM private keys stored in the database. The key comes from
`SECRETS_ENCRYPTION_KEY` and is loaded lazily so an invalid key only fails when
encryption is actually used (not at import time).
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class DecryptionError(Exception):
    """Raised when ciphertext cannot be decrypted (tampered or wrong key)."""


@lru_cache
def _fernet() -> Fernet:
    key = settings.SECRETS_ENCRYPTION_KEY
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise DecryptionError("Unable to decrypt secret.") from exc
