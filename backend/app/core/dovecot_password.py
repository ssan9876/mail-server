"""
Dovecot-compatible password hashing.

Mailbox passwords are verified by Dovecot (not our API), so they are stored in
a format Dovecot understands: an inline `{SCHEME}` prefix followed by the hash.
We use ARGON2ID, whose PHC string (`$argon2id$v=19$...`) Dovecot parses directly.

Storing the scheme inline (rather than via `default_pass_scheme`) lets us change
algorithms per-record later without a global config change.
"""
from __future__ import annotations

from passlib.context import CryptContext

DOVECOT_SCHEME = "ARGON2ID"

_ctx = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_for_dovecot(password: str) -> str:
    """Return e.g. ``{ARGON2ID}$argon2id$v=19$m=65536,t=3,p=4$...``."""
    return f"{{{DOVECOT_SCHEME}}}{_ctx.hash(password)}"


def verify_dovecot(password: str, stored: str) -> bool:
    """Verify a password against a Dovecot-formatted hash (prefix optional)."""
    if stored.startswith("{") and "}" in stored:
        stored = stored[stored.index("}") + 1:]
    try:
        return _ctx.verify(password, stored)
    except ValueError:
        return False
