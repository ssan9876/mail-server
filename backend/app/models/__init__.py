"""
Model registry.

Importing this package imports every model, ensuring they are all registered
on `Base.metadata` (required for Alembic autogenerate and `create_all`).
"""
from app.models.alias import Alias
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.domain import Domain
from app.models.enums import ActorType, UserRole
from app.models.mailbox import Mailbox
from app.models.password_reset import PasswordResetToken
from app.models.user import User

__all__ = [
    "Base",
    "User",
    "Domain",
    "Mailbox",
    "Alias",
    "PasswordResetToken",
    "AuditLog",
    "UserRole",
    "ActorType",
]
