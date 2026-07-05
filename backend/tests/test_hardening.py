"""Tests for the security-hardening changes: production secret validation and
trusted client-IP resolution behind the edge proxy."""
import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core.client_ip import client_ip, client_ip_or_unknown
from app.core.config import Settings

# A fully-populated set of production-grade settings to mutate per test.
_GOOD = dict(
    ENVIRONMENT="production",
    JWT_SECRET_KEY="x" * 64,
    SECRETS_ENCRYPTION_KEY="k" * 44,
    POSTGRES_PASSWORD="real-db-password",
    POSTGRES_MAIL_PASSWORD="real-lookup-password",
    ADMIN_PASSWORD="real-admin-password",
)

_SECRET_FIELDS = (
    "JWT_SECRET_KEY",
    "SECRETS_ENCRYPTION_KEY",
    "POSTGRES_PASSWORD",
    "POSTGRES_MAIL_PASSWORD",
    "ADMIN_PASSWORD",
)


# --------------------------------------------------------------------------- #
# Production placeholder-secret refusal
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("field", _SECRET_FIELDS)
@pytest.mark.parametrize("placeholder", ["changeme", "changeme-strong-db-password", ""])
def test_production_rejects_placeholder_secret(field, placeholder):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**_GOOD, field: placeholder})


def test_production_rejects_short_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**_GOOD, "JWT_SECRET_KEY": "short-secret"})


def test_production_accepts_real_secrets():
    Settings(_env_file=None, **_GOOD)  # must not raise


def test_development_tolerates_placeholders():
    Settings(
        _env_file=None,
        **{**_GOOD, "ENVIRONMENT": "development", "JWT_SECRET_KEY": "changeme"},
    )  # must not raise


# --------------------------------------------------------------------------- #
# Client IP resolution (X-Forwarded-For handling)
# --------------------------------------------------------------------------- #
def _request(headers: dict[str, str], client: tuple[str, int] | None = ("10.0.0.9", 1234)) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": client,
    }
    return Request(scope)


def test_client_ip_uses_last_forwarded_hop_not_spoofable_first():
    # nginx APPENDS the address it saw; earlier entries are attacker-supplied.
    req = _request({"x-forwarded-for": "6.6.6.6, 203.0.113.7"})
    assert client_ip(req) == "203.0.113.7"


def test_client_ip_single_hop():
    assert client_ip(_request({"x-forwarded-for": "203.0.113.7"})) == "203.0.113.7"


def test_client_ip_falls_back_to_peer_address():
    assert client_ip(_request({})) == "10.0.0.9"


def test_client_ip_or_unknown_never_none():
    assert client_ip_or_unknown(_request({}, client=None)) == "unknown"
