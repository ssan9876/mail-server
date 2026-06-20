"""Virtual mailboxes — the actual email accounts (local_part@domain)."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Mailbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mailboxes"
    __table_args__ = (
        UniqueConstraint("domain_id", "local_part", name="uq_mailboxes_domain_local"),
    )

    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False, index=True
    )
    local_part: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)  # Dovecot-compatible
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quota_mb: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    maildir_path: Mapped[str] = mapped_column(String(512), nullable=False)

    domain: Mapped["Domain"] = relationship(  # noqa: F821
        back_populates="mailboxes",
        foreign_keys=[domain_id],
    )

    @property
    def address(self) -> str:
        """Full email address — requires `domain` to be loaded."""
        return f"{self.local_part}@{self.domain.name}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mailbox {self.local_part}@domain:{self.domain_id}>"
