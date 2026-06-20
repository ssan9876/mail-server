"""
DKIM keypair generation and DNS record construction.

Generates an RSA-2048 keypair; the private key is returned as PEM (the caller
encrypts it before persisting), and the public key is rendered into the
`v=DKIM1` TXT record value that gets published to DNS.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_SELECTOR = "mail"
_RSA_KEY_SIZE = 2048


@dataclass(frozen=True)
class DkimKeypair:
    selector: str
    private_key_pem: str
    public_key_b64: str  # base64 DER (SubjectPublicKeyInfo), for the p= tag


def generate_keypair(selector: str = DEFAULT_SELECTOR) -> DkimKeypair:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=_RSA_KEY_SIZE)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    public_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_b64 = base64.b64encode(public_der).decode("ascii")

    return DkimKeypair(
        selector=selector,
        private_key_pem=private_pem,
        public_key_b64=public_b64,
    )


def dkim_record_name(selector: str, domain: str) -> str:
    """The DNS name of the DKIM TXT record."""
    return f"{selector}._domainkey.{domain}"


def dkim_record_value(public_key_b64: str) -> str:
    """The TXT record value advertising the public key."""
    return f"v=DKIM1; k=rsa; p={public_key_b64}"
