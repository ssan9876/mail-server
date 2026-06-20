"""Tests for DNS verification logic using a fake resolver (no network)."""
from app.services import dns_verify_service


class FakeResolver:
    def __init__(self, txt: dict[str, list[str]], mx: dict[str, list[tuple[int, str]]]):
        self._txt = txt
        self._mx = mx

    async def resolve_txt(self, name: str) -> list[str]:
        return self._txt.get(name, [])

    async def resolve_mx(self, name: str) -> list[tuple[int, str]]:
        return self._mx.get(name, [])


async def test_all_records_verified():
    resolver = FakeResolver(
        txt={
            "example.com": ["v=spf1 mx ~all"],
            "mail._domainkey.example.com": ["v=DKIM1; k=rsa; p=AAAA"],
            "_dmarc.example.com": ["v=DMARC1; p=quarantine"],
        },
        mx={"example.com": [(10, "mail.host.net")]},
    )
    result = await dns_verify_service.verify_domain(
        resolver, "example.com", "mail", mail_hostname="mail.host.net"
    )
    assert result.dns_verified
    assert result.mx_verified and result.spf_verified
    assert result.dkim_verified and result.dmarc_verified


async def test_partial_verification():
    resolver = FakeResolver(
        txt={"example.com": ["v=spf1 mx ~all"]},  # SPF only
        mx={"example.com": [(10, "someone-elses-host.net")]},  # wrong MX
    )
    result = await dns_verify_service.verify_domain(
        resolver, "example.com", "mail", mail_hostname="mail.host.net"
    )
    assert result.spf_verified
    assert not result.mx_verified
    assert not result.dkim_verified
    assert not result.dmarc_verified
    assert not result.dns_verified


async def test_missing_selector_skips_dkim():
    resolver = FakeResolver(txt={}, mx={})
    result = await dns_verify_service.verify_domain(resolver, "example.com", None)
    assert not result.dkim_verified
