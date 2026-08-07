"""IdM provisioning endpoints (service-token authenticated).

The service token is the enforcing control today. A later task adds an edge
block in docker/nginx/templates/10-https.conf.template so these routes are
only reachable on the internal Docker network; until then, do not assume
network-level protection here.
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
