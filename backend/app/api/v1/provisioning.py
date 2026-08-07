"""IdM provisioning endpoints (service-token authenticated).

These routes are blocked at the Nginx edge and are reachable only on the
internal Docker network — see docker/nginx/templates/10-https.conf.template.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_service_token

router = APIRouter(
    prefix="/provisioning",
    tags=["provisioning"],
    dependencies=[Depends(require_service_token)],
)


@router.get("/health")
async def health() -> dict[str, str]:
    """Validates the caller's token and touches nothing else."""
    return {"status": "ok"}
