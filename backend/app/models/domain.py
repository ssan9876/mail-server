"""Email domains hosted by the platform."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Domain(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "domains"

    name: Mapped[str] = mapped_column(String(253), unique=True, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional catch-all target mailbox. Self-referential FK to mailboxes is
    # created with use_alter so the two tables can be emitted in any order.
    catch_all_box: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("mailboxes.id", ondelete="SET NULL", use_alter=True,
                   name="fk_domains_catch_all_box_mailboxes"),
        nullable=True,
    )

    # --- DKIM ---
    dkim_selector: Mapped[str | None] = mapped_column(String(63), nullable=True)
    dkim_private_key: Mapped[str | None] = mapped_column(Text, nullable=True)  # Fernet-encrypted PEM
    dkim_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- DNS verification flags ---
    dns_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mx_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    spf_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dmarc_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Relationships ---
    owner: Mapped["User | None"] = relationship(back_populates="domains")  # noqa: F821
    mailboxes: Mapped[list["Mailbox"]] = relationship(  # noqa: F821
        back_populates="domain",
        cascade="all, delete-orphan",
        foreign_keys="Mailbox.domain_id",
    )
    aliases: Mapped[list["Alias"]] = relationship(  # noqa: F821
        back_populates="domain",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Domain {self.name}>"
