"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-19

Creates the full baseline schema: users, domains, mailboxes, aliases,
password_reset_tokens, audit_logs. Matches the SQLAlchemy models exactly.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Enums rendered as VARCHAR + CHECK (native_enum=False), matching the models.
user_role = sa.Enum(
    "superadmin", "domain_admin", "user",
    name="userrole", native_enum=False, length=20,
)
actor_type = sa.Enum(
    "user", "mailbox", "system",
    name="actortype", native_enum=False, length=20,
)

_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    # ------------------------------------------------------------------ users
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", user_role, server_default="user", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ---------------------------------------------------------------- domains
    op.create_table(
        "domains",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        # catch_all_box FK is added after `mailboxes` exists (use_alter).
        sa.Column("catch_all_box", sa.Uuid(), nullable=True),
        sa.Column("dkim_selector", sa.String(63), nullable=True),
        sa.Column("dkim_private_key", sa.Text(), nullable=True),
        sa.Column("dkim_public_key", sa.Text(), nullable=True),
        sa.Column("dns_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("mx_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("spf_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("dmarc_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_domains"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"],
            name="fk_domains_owner_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index("ix_domains_name", "domains", ["name"], unique=True)

    # -------------------------------------------------------------- mailboxes
    op.create_table(
        "mailboxes",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("local_part", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("quota_mb", sa.Integer(), server_default="1024", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("maildir_path", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mailboxes"),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["domains.id"],
            name="fk_mailboxes_domain_id_domains", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("domain_id", "local_part", name="uq_mailboxes_domain_local"),
    )
    op.create_index("ix_mailboxes_domain_id", "mailboxes", ["domain_id"])

    # Deferred self-referential FK: domains.catch_all_box -> mailboxes.id
    op.create_foreign_key(
        "fk_domains_catch_all_box_mailboxes",
        "domains", "mailboxes",
        ["catch_all_box"], ["id"],
        ondelete="SET NULL",
    )

    # ---------------------------------------------------------------- aliases
    op.create_table(
        "aliases",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("local_part", sa.String(64), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_aliases"),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["domains.id"],
            name="fk_aliases_domain_id_domains", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("domain_id", "local_part", name="uq_aliases_domain_local"),
    )
    op.create_index("ix_aliases_domain_id", "aliases", ["domain_id"])

    # ----------------------------------------------- password_reset_tokens
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("mailbox_id", sa.Uuid(), nullable=True),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_password_reset_tokens_user_id_users", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], ["mailboxes.id"],
            name="fk_password_reset_tokens_mailbox_id_mailboxes", ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens", ["token_hash"], unique=True,
    )

    # ------------------------------------------------------------- audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", actor_type, nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("ix_aliases_domain_id", table_name="aliases")
    op.drop_table("aliases")

    # Drop the deferred FK before mailboxes so domains no longer references it.
    op.drop_constraint("fk_domains_catch_all_box_mailboxes", "domains", type_="foreignkey")
    op.drop_index("ix_mailboxes_domain_id", table_name="mailboxes")
    op.drop_table("mailboxes")

    op.drop_index("ix_domains_name", table_name="domains")
    op.drop_table("domains")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
