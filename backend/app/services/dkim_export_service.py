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


def _prune_orphans(root: Path, keep: set[str]) -> int:
    """Delete exported keys for domains that no longer exist. Returns the count.

    Without this, `delete_domain` removed the database row — including the only
    ENCRYPTED copy of the private key — while the DECRYPTED export stayed on
    the shared volume forever.

    That is not merely untidy. Rspamd resolves signing keys by filesystem path
    (`path = "/dkim/$domain/$selector.key"`) with a fallback
    `selector = "mail"`, so the presence of the FILE is what makes a domain
    signable — `selectors.map` is not the gate. A deleted domain therefore
    stayed indefinitely signable, with no rotation path left (rotation operates
    on a `Domain` row that no longer exists) and, because this application
    never withdraws DNS records either, with its `_domainkey` TXT record still
    published.

    Deliberately conservative about WHAT it deletes: only `*.key` files, and
    then the directory itself only if that left it empty. It never recurses and
    never removes anything it did not write, so a misconfigured
    `DKIM_KEYS_PATH` cannot turn this into an arbitrary-delete primitive.
    """
    removed = 0
    for child in root.iterdir():
        if not child.is_dir() or child.name in keep:
            continue
        for key_file in child.glob("*.key"):
            key_file.unlink()
            removed += 1
        try:
            child.rmdir()
        except OSError:
            # Something else lives in there — leave it rather than recurse.
            pass
    return removed


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

    # Runs AFTER the writes so a domain that both exists and was just written
    # is never a prune candidate, whatever order the rows came back in.
    orphans = _prune_orphans(root_path, {domain.name for domain in domains})
    if orphans:
        logger.info("DKIM export removed %d orphaned key file(s)", orphans)

    return len(domains)


async def try_sync(db: AsyncSession) -> bool:
    """Best-effort export; never raises. Used after domain mutations where the
    DKIM volume may be absent (dev/tests).

    Catches `DecryptionError` as well as `OSError`. It previously caught only
    the latter, so its "never raises" contract was false in exactly the case
    that matters: after a `SECRETS_ENCRYPTION_KEY` rotation without
    re-encryption, one undecryptable domain would propagate out of every
    domain mutation and 500 the request.
    """
    try:
        await sync_all(db)
        return True
    except PermissionError as exc:
        # Split out from the OSError arm below because it is not a benign
        # "volume absent in dev" case at all: it means the export can NEVER
        # succeed on this deployment, so every domain stays unsigned until
        # someone changes the filesystem. A `warning` reading "skipped" made a
        # permanent, total failure look like a transient one, and it stayed
        # invisible until an explicit POST /domains/dkim/sync returned a 500.
        #
        # Real cause seen in the field: the dkim_keys volume is owned by root
        # while this process runs as uid 10001, so _write_key cannot create the
        # per-domain directory. The image now creates /dkim with the right
        # owner, but Docker applies that only when a volume is FIRST created —
        # an existing deployment needs a one-time chown, which is what this
        # message tells the operator to do.
        logger.error(
            "DKIM export FAILED — no domain can be signed until this is fixed. "
            "All outbound mail is leaving UNSIGNED. The DKIM volume is not "
            "writable by this process (uid %s): %s. Fix with: docker run --rm "
            "-v <stack>_dkim_keys:/d alpine chown -R 10001:10001 /d",
            os.getuid(),
            exc,
        )
        return False
    except (OSError, crypto.DecryptionError) as exc:
        logger.warning("DKIM export skipped: %s", exc)
        return False
