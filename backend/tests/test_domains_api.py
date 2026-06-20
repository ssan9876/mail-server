"""Integration tests for the domains API: CRUD, scoping, DNS endpoints."""
import pytest

from app.api.deps import get_cloudflare_client, get_dns_resolver
from app.core.config import settings
from app.models.enums import UserRole
from tests.conftest import login_headers

DOMAINS = "/api/v1/domains"


# --------------------------------------------------------------------------- #
# Fakes for the injectable DNS dependencies
# --------------------------------------------------------------------------- #
class FakeResolver:
    def __init__(self, txt, mx):
        self._txt, self._mx = txt, mx

    async def resolve_txt(self, name):
        return self._txt.get(name, [])

    async def resolve_mx(self, name):
        return self._mx.get(name, [])


class FakeCloudflare:
    def __init__(self):
        self.published = []

    async def publish_records(self, domain_name, records):
        self.published.append((domain_name, records))
        return []


# --------------------------------------------------------------------------- #
# CRUD + DKIM
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("superadmin")
async def test_create_domain_generates_dkim(client):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await client.post(DOMAINS, json={"name": "Example.COM"}, headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "example.com"  # normalized
    assert body["dkim_selector"] == "mail"
    assert body["dns_verified"] is False
    # Private key must never be serialized.
    assert "dkim_private_key" not in body


@pytest.mark.usefixtures("superadmin")
async def test_duplicate_domain_conflicts(client):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    await client.post(DOMAINS, json={"name": "dup.com"}, headers=headers)
    resp = await client.post(DOMAINS, json={"name": "dup.com"}, headers=headers)
    assert resp.status_code == 409


@pytest.mark.usefixtures("superadmin")
async def test_dns_records_endpoint_lists_all_four(client):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = (await client.post(DOMAINS, json={"name": "recs.com"}, headers=headers)).json()
    resp = await client.get(f"{DOMAINS}/{created['id']}/dns-records", headers=headers)
    assert resp.status_code == 200
    types = sorted(r["type"] for r in resp.json())
    assert types == ["MX", "TXT", "TXT", "TXT"]


@pytest.mark.usefixtures("superadmin")
async def test_dkim_rotation_changes_state(client):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = (await client.post(DOMAINS, json={"name": "rot.com"}, headers=headers)).json()
    before = (await client.get(f"{DOMAINS}/{created['id']}/dns-records", headers=headers)).json()
    before_dkim = [r for r in before if "_domainkey" in r["name"]][0]["content"]

    resp = await client.post(f"{DOMAINS}/{created['id']}/dkim/rotate", headers=headers)
    assert resp.status_code == 200

    after = (await client.get(f"{DOMAINS}/{created['id']}/dns-records", headers=headers)).json()
    after_dkim = [r for r in after if "_domainkey" in r["name"]][0]["content"]
    assert before_dkim != after_dkim  # new key material


# --------------------------------------------------------------------------- #
# Role scoping
# --------------------------------------------------------------------------- #
async def test_regular_user_is_forbidden(client, make_user):
    await make_user("user@example.com", role=UserRole.USER)
    headers = await login_headers(client, "user@example.com", "password123456")
    assert (await client.get(DOMAINS, headers=headers)).status_code == 403


async def test_domain_admin_only_sees_own_domains(client, make_user, superadmin):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    await make_user("a@example.com", role=UserRole.DOMAIN_ADMIN)
    await make_user("b@example.com", role=UserRole.DOMAIN_ADMIN)
    headers_a = await login_headers(client, "a@example.com", "password123456")
    headers_b = await login_headers(client, "b@example.com", "password123456")

    await client.post(DOMAINS, json={"name": "a-owned.com"}, headers=headers_a)
    await client.post(DOMAINS, json={"name": "b-owned.com"}, headers=headers_b)

    a_list = (await client.get(DOMAINS, headers=headers_a)).json()
    assert [d["name"] for d in a_list] == ["a-owned.com"]

    # Superadmin sees both.
    su_headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    su_list = (await client.get(DOMAINS, headers=su_headers)).json()
    assert {d["name"] for d in su_list} == {"a-owned.com", "b-owned.com"}


async def test_domain_admin_cannot_access_others_domain(client, make_user):
    await make_user("owner@example.com", role=UserRole.DOMAIN_ADMIN)
    await make_user("intruder@example.com", role=UserRole.DOMAIN_ADMIN)
    owner_headers = await login_headers(client, "owner@example.com", "password123456")
    intruder_headers = await login_headers(client, "intruder@example.com", "password123456")

    created = (await client.post(DOMAINS, json={"name": "secret.com"}, headers=owner_headers)).json()
    # Intruder gets 404 (existence hidden), not 200.
    resp = await client.get(f"{DOMAINS}/{created['id']}", headers=intruder_headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# DNS publish + verify (injected fakes)
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("superadmin")
async def test_publish_dns_calls_cloudflare(client, app):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    fake = FakeCloudflare()
    app.dependency_overrides[get_cloudflare_client] = lambda: fake

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = (await client.post(DOMAINS, json={"name": "pub.com"}, headers=headers)).json()
    resp = await client.post(f"{DOMAINS}/{created['id']}/dns/publish", headers=headers)

    assert resp.status_code == 200
    assert fake.published and fake.published[0][0] == "pub.com"


@pytest.mark.usefixtures("superadmin")
async def test_verify_dns_updates_flags(client, app):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = (await client.post(DOMAINS, json={"name": "verify.com"}, headers=headers)).json()

    resolver = FakeResolver(
        txt={
            "verify.com": ["v=spf1 mx ~all"],
            "mail._domainkey.verify.com": ["v=DKIM1; k=rsa; p=AAAA"],
            "_dmarc.verify.com": ["v=DMARC1; p=quarantine"],
        },
        mx={"verify.com": [(10, settings.MAIL_HOSTNAME)]},
    )
    app.dependency_overrides[get_dns_resolver] = lambda: resolver

    resp = await client.post(f"{DOMAINS}/{created['id']}/dns/verify", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["dns_verified"] is True

    # Flag persisted on the domain.
    fetched = (await client.get(f"{DOMAINS}/{created['id']}", headers=headers)).json()
    assert fetched["dns_verified"] is True
