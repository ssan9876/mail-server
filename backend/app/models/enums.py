"""Enumerations shared across models, rendered as VARCHAR + CHECK constraints."""
import enum


class UserRole(str, enum.Enum):
    """Control-plane operator roles (not mailbox users)."""

    SUPERADMIN = "superadmin"      # manages everything, including other admins
    DOMAIN_ADMIN = "domain_admin"  # manages mailboxes/aliases for owned domains
    USER = "user"                  # self-service for a single mailbox


class ActorType(str, enum.Enum):
    """Who performed an audited action."""

    USER = "user"
    MAILBOX = "mailbox"
    SYSTEM = "system"
