"""
DNS verification — confirm that a domain's published records actually resolve.

The resolver is abstracted behind a Protocol so tests can inject a fake without
touching the network. The default implementation uses dnspython's async resolver.
"""
from __future__ import annotations

from typing import Protocol

from app.core.config import settings
from app.schemas.domain import VerificationResult
from app.services import dkim_service


class DnsResolver(Protocol):
    async def resolve_txt(self, name: str) -> list[str]: ...
    async def resolve_mx(self, name: str) -> list[tuple[int, str]]: ...


class DefaultDnsResolver:
    """dnspython-backed resolver. Missing records resolve to an empty list."""

    async def resolve_txt(self, name: str) -> list[str]:
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(name, "TXT")
        except (dns.exception.DNSException, Exception):
            return []
        out: list[str] = []
        for rdata in answer:
            # TXT rdata may be chunked into multiple strings; join them.
            out.append(b"".join(rdata.strings).decode("utf-8", "replace"))
        return out

    async def resolve_mx(self, name: str) -> list[tuple[int, str]]:
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(name, "MX")
        except (dns.exception.DNSException, Exception):
            return []
        return [(int(r.preference), str(r.exchange).rstrip(".").lower()) for r in answer]


def _normalize_host(host: str) -> str:
    return host.rstrip(".").lower()


async def verify_domain(
    resolver: DnsResolver,
    domain_name: str,
    dkim_selector: str | None,
    *,
    mail_hostname: str | None = None,
) -> VerificationResult:
    mail_host = _normalize_host(mail_hostname or settings.MAIL_HOSTNAME)

    # MX -> must point at our mail host.
    mx_records = await resolver.resolve_mx(domain_name)
    mx_ok = any(exchange == mail_host for _pref, exchange in mx_records)

    # SPF -> a TXT beginning with v=spf1.
    apex_txt = await resolver.resolve_txt(domain_name)
    spf_ok = any(t.lower().startswith("v=spf1") for t in apex_txt)

    # DKIM -> selector TXT advertising a public key.
    dkim_ok = False
    if dkim_selector:
        dkim_txt = await resolver.resolve_txt(
            dkim_service.dkim_record_name(dkim_selector, domain_name)
        )
        dkim_ok = any(("v=dkim1" in t.lower() and "p=" in t) for t in dkim_txt)

    # DMARC -> _dmarc TXT.
    dmarc_txt = await resolver.resolve_txt(f"_dmarc.{domain_name}")
    dmarc_ok = any(t.lower().startswith("v=dmarc1") for t in dmarc_txt)

    return VerificationResult(
        mx_verified=mx_ok,
        spf_verified=spf_ok,
        dkim_verified=dkim_ok,
        dmarc_verified=dmarc_ok,
        dns_verified=all([mx_ok, spf_ok, dkim_ok, dmarc_ok]),
    )
