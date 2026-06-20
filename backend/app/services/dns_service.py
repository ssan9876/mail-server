"""
DNS automation.

Two responsibilities:
  1. Build the set of DNS records a domain needs (MX, SPF, DKIM, DMARC).
  2. Publish them to Cloudflare via its v4 API (idempotent upsert by type+name).

The Cloudflare client takes an injectable httpx.AsyncClient so it can be driven
by a MockTransport in tests with no network access.
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.schemas.domain import DnsRecord
from app.services import dkim_service

CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    """Raised when the Cloudflare API returns an error or no result."""


# --------------------------------------------------------------------------- #
# Desired record set
# --------------------------------------------------------------------------- #
def build_desired_records(
    domain_name: str,
    dkim_selector: str,
    dkim_public_key_b64: str,
    *,
    mail_hostname: str | None = None,
) -> list[DnsRecord]:
    """The canonical records for a fully-configured mail domain."""
    mail_host = mail_hostname or settings.MAIL_HOSTNAME
    records = [
        DnsRecord(type="MX", name=domain_name, content=mail_host, priority=10),
        DnsRecord(type="TXT", name=domain_name, content="v=spf1 mx ~all"),
        DnsRecord(
            type="TXT",
            name=dkim_service.dkim_record_name(dkim_selector, domain_name),
            content=dkim_service.dkim_record_value(dkim_public_key_b64),
        ),
        DnsRecord(
            type="TXT",
            name=f"_dmarc.{domain_name}",
            content=(
                f"v=DMARC1; p=quarantine; rua=mailto:dmarc@{domain_name}; "
                "adkim=s; aspf=s; fo=1"
            ),
        ),
    ]
    return records


# --------------------------------------------------------------------------- #
# Cloudflare client
# --------------------------------------------------------------------------- #
class CloudflareClient:
    def __init__(self, token: str | None = None, http_client: httpx.AsyncClient | None = None):
        self._token = token or settings.CLOUDFLARE_API_TOKEN
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=CLOUDFLARE_API_BASE,
            timeout=httpx.Timeout(15.0),
        )

    async def __aenter__(self) -> "CloudflareClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _unwrap(self, response: httpx.Response) -> object:
        try:
            body = response.json()
        except ValueError as exc:
            raise CloudflareError(f"Non-JSON response ({response.status_code}).") from exc
        if not body.get("success", False):
            raise CloudflareError(str(body.get("errors") or "Cloudflare API error"))
        return body.get("result")

    async def get_zone_id(self, domain_name: str) -> str:
        resp = await self._client.get("/zones", params={"name": domain_name}, headers=self._headers)
        result = self._unwrap(resp)
        if not result:
            raise CloudflareError(f"No Cloudflare zone found for {domain_name}.")
        return result[0]["id"]  # type: ignore[index]

    async def list_dns_records(
        self, zone_id: str, *, record_type: str | None = None, name: str | None = None
    ) -> list[dict]:
        params = {}
        if record_type:
            params["type"] = record_type
        if name:
            params["name"] = name
        resp = await self._client.get(
            f"/zones/{zone_id}/dns_records", params=params, headers=self._headers
        )
        return self._unwrap(resp) or []  # type: ignore[return-value]

    async def upsert_dns_record(self, zone_id: str, record: DnsRecord) -> dict:
        """Create the record, or update it in place if one with the same
        type+name already exists (idempotent)."""
        payload: dict = {
            "type": record.type,
            "name": record.name,
            "content": record.content,
            "ttl": record.ttl,
        }
        if record.priority is not None:
            payload["priority"] = record.priority

        existing = await self.list_dns_records(zone_id, record_type=record.type, name=record.name)
        if existing:
            record_id = existing[0]["id"]
            resp = await self._client.put(
                f"/zones/{zone_id}/dns_records/{record_id}",
                json=payload,
                headers=self._headers,
            )
        else:
            resp = await self._client.post(
                f"/zones/{zone_id}/dns_records",
                json=payload,
                headers=self._headers,
            )
        return self._unwrap(resp)  # type: ignore[return-value]

    async def publish_records(self, domain_name: str, records: list[DnsRecord]) -> list[dict]:
        zone_id = await self.get_zone_id(domain_name)
        return [await self.upsert_dns_record(zone_id, rec) for rec in records]
