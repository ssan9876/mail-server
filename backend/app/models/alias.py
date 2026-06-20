"""Address aliases — forward mail from one address to one or more destinations.

A row with `local_part = '@'` represents a domain catch-all expressed as an
alias (an alternative to `Domain.catch_all_box`); the service layer keeps the
two mechanisms consistent.
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Alias(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "aliases"
    __table_args__ = (
        UniqueConstraint("domain_id", "local_part", name="uq_aliases_domain_local"),
    )

    domain_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Source local part, or "@" for a catch-all alias.
    local_part: Mapped[str] = mapped_column(String(64), nullable=False)
    # Comma-separated destination address(es).
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    domain: Mapped["Domain"] = relationship(back_populates="aliases")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Alias {self.local_part}@domain:{self.domain_id} -> {self.destination}>"
