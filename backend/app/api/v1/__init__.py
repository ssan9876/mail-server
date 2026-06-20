"""v1 API router aggregation."""
from fastapi import APIRouter

from app.api.v1 import (
    aliases,
    audit,
    auth,
    domains,
    mailbox_portal,
    mailboxes,
    password_reset,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(password_reset.router)
api_router.include_router(mailbox_portal.router)
api_router.include_router(domains.router)
api_router.include_router(mailboxes.router)
api_router.include_router(aliases.router)
api_router.include_router(audit.router)
