"""
Export DKIM private keys from the database to the shared `dkim_keys` volume so
Rspamd can sign outbound mail.

Layout written under the DKIM root (default ``/dkim``):
    /dkim/<domain>/<selector>.key      decrypted PKCS8 PEM private key
    /dkim/selectors.map                "<domain> <selector>" per line

Rspamd's dkim_signing module reads these via ``path = "/dkim/$domain/$selector.key"``
and ``selector_map = "/dkim/selectors.map"``.

Security note: key files are written world-readable (0644) because the backend
and Rspamd run as different uids but share this named volume, which is internal
to the compose stack and never published to the host. A leaked DKIM key permits
message-signing spoofing only (not transport interception) and is cheaply
rotated via the DKIM-rotate endpoint.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import settings
from app.models.domain import Domain

logger = logging.getLogger("mailserver.dkim")

_KEY_MODE = 0o644
_MAP_NAME = "selectors.map"


def _write_key(domain: Domain, root: Path) -> None:
    pem = crypto.decrypt(domain.dkim_private_key)  # type: ignore[arg-type]
    domain_dir = root / domain.name
    domain_dir.mkdir(parents=True, exist_ok=True)
    key_path = domain_dir / f"{domain.dkim_selector}.key"
    key_path.write_text(pem)
    os.chmod(key_path, _KEY_MODE)


async def sync_all(db: AsyncSession, root: str | os.PathLike[str] | None = None) -> int:
    """(Re)write every domain's key file and the selector map. Returns the
    number of domains exported."""
    root_path = Path(root or settings.DKIM_KEYS_PATH)
    root_path.mkdir(parents=True, exist_ok=True)

    result = await db.execute(
        select(Domain).where(
            Domain.dkim_private_key.is_not(None),
            Domain.dkim_selector.is_not(None),
        )
    )
    domains = list(result.scalars().all())

    lines: list[str] = []
    for domain in domains:
        _write_key(domain, root_path)
        lines.append(f"{domain.name} {domain.dkim_selector}")

    map_content = "\n".join(sorted(lines))
    (root_path / _MAP_NAME).write_text(map_content + "\n" if lines else "")
    return len(domains)


async def try_sync(db: AsyncSession) -> bool:
    """Best-effort export; never raises. Used after domain mutations where the
    DKIM volume may be absent (dev/tests)."""
    try:
        await sync_all(db)
        return True
    except OSError as exc:
        logger.warning("DKIM export skipped: %s", exc)
        return False
