"""Unit tests for at-rest encryption and DKIM key generation."""
import base64

import pytest

from app.core import crypto
from app.core.crypto import DecryptionError
from app.services import dkim_service


def test_encrypt_decrypt_roundtrip():
    secret = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    ciphertext = crypto.encrypt(secret)
    assert ciphertext != secret
    assert crypto.decrypt(ciphertext) == secret


def test_decrypt_rejects_tampered_ciphertext():
    ciphertext = crypto.encrypt("hello")
    # Flip a character mid-token: keeps valid base64 + length, breaks integrity.
    chars = list(ciphertext)
    i = len(chars) // 2
    chars[i] = "A" if chars[i] != "A" else "B"
    with pytest.raises(DecryptionError):
        crypto.decrypt("".join(chars))


def test_generate_keypair_shapes():
    kp = dkim_service.generate_keypair("mail")
    assert kp.selector == "mail"
    assert "BEGIN PRIVATE KEY" in kp.private_key_pem
    # public_key_b64 must be valid base64 DER.
    decoded = base64.b64decode(kp.public_key_b64)
    assert len(decoded) > 100


def test_dkim_record_helpers():
    name = dkim_service.dkim_record_name("mail", "example.com")
    assert name == "mail._domainkey.example.com"
    value = dkim_service.dkim_record_value("PUBKEY")
    assert value == "v=DKIM1; k=rsa; p=PUBKEY"
