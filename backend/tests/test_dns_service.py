"""Tests for desired-record assembly and the Cloudflare client (mocked HTTP)."""
import httpx
import pytest

from app.services import dns_service
from app.services.dns_service import CLOUDFLARE_API_BASE, CloudflareClient, CloudflareError
from app.schemas.domain import DnsRecord


def test_build_desired_records():
    records = dns_service.build_desired_records(
        "example.com", "mail", "PUBKEY", mail_hostname="mail.host.net"
    )
    by_type = {(r.type, r.name): r for r in records}

    mx = by_type[("MX", "example.com")]
    assert mx.content == "mail.host.net" and mx.priority == 10

    spf = by_type[("TXT", "example.com")]
    assert spf.content.startswith("v=spf1")

    dkim = by_type[("TXT", "mail._domainkey.example.com")]
    assert "p=PUBKEY" in dkim.content

    dmarc = by_type[("TXT", "_dmarc.example.com")]
    assert dmarc.content.startswith("v=DMARC1")


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=CLOUDFLARE_API_BASE, transport=httpx.MockTransport(handler)
    )


async def test_get_zone_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/zones")
        assert request.url.params.get("name") == "example.com"
        return httpx.Response(200, json={"success": True, "result": [{"id": "zone-1"}]})

    cf = CloudflareClient(token="t", http_client=_mock_client(handler))
    assert await cf.get_zone_id("example.com") == "zone-1"
    await cf.aclose()


async def test_get_zone_id_missing_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": True, "result": []})

    cf = CloudflareClient(token="t", http_client=_mock_client(handler))
    with pytest.raises(CloudflareError):
        await cf.get_zone_id("nope.com")
    await cf.aclose()


async def test_upsert_creates_when_absent():
    calls = {"GET": 0, "POST": 0, "PUT": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.method] += 1
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "result": []})
        return httpx.Response(200, json={"success": True, "result": {"id": "new-rec"}})

    cf = CloudflareClient(token="t", http_client=_mock_client(handler))
    rec = DnsRecord(type="TXT", name="example.com", content="v=spf1 mx ~all")
    result = await cf.upsert_dns_record("zone-1", rec)
    assert result["id"] == "new-rec"
    assert calls["POST"] == 1 and calls["PUT"] == 0
    await cf.aclose()


async def test_upsert_updates_when_present():
    calls = {"GET": 0, "POST": 0, "PUT": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls[request.method] += 1
        if request.method == "GET":
            return httpx.Response(200, json={"success": True, "result": [{"id": "rec-1"}]})
        return httpx.Response(200, json={"success": True, "result": {"id": "rec-1"}})

    cf = CloudflareClient(token="t", http_client=_mock_client(handler))
    rec = DnsRecord(type="TXT", name="example.com", content="v=spf1 mx ~all")
    await cf.upsert_dns_record("zone-1", rec)
    assert calls["PUT"] == 1 and calls["POST"] == 0
    await cf.aclose()


async def test_api_error_is_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "errors": [{"message": "bad token"}]})

    cf = CloudflareClient(token="t", http_client=_mock_client(handler))
    with pytest.raises(CloudflareError):
        await cf.get_zone_id("example.com")
    await cf.aclose()
