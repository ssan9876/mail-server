"""idm sync: service tokens, identity links, and the idm actor type

Revision ID: 0003_idm_sync
Revises: 0002_mail_lookup_grants
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_idm_sync"
down_revision: Union[str, None] = "0002_mail_lookup_grants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")

# NOTE: adding `idm` to ActorType needs NO schema change. SQLAlchemy's
# `Enum(..., native_enum=False)` defaults to `create_constraint=False`, so
# `audit_logs.actor_type` is a plain VARCHAR(20) with no CHECK constraint to
# widen — verified by compiling the 0001 table definition. Validation is
# Python-side (`validate_strings=True`), and 'idm' fits the existing column.


def upgrade() -> None:
    op.create_table(
        "idm_service_tokens",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idm_service_tokens"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_idm_service_tokens_created_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_idm_service_tokens_token_hash",
        "idm_service_tokens", ["token_hash"], unique=True,
    )

    op.create_table(
        "idm_identities",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("idm_username", sa.String(255), nullable=True),
        sa.Column("mailbox_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_payload_hash", sa.String(64), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idm_identities"),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], ["mailboxes.id"],
            name="fk_idm_identities_mailbox_id_mailboxes", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_idm_identities_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_idm_identities_external_id", "idm_identities", ["external_id"], unique=True
    )

    op.create_table(
        "idm_identity_aliases",
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("alias_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("identity_id", "alias_id", name="pk_idm_identity_aliases"),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["idm_identities.id"],
            name="fk_idm_identity_aliases_identity_id_idm_identities",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["aliases.id"],
            name="fk_idm_identity_aliases_alias_id_aliases", ondelete="CASCADE",
        ),
    )

    # No actor_type work here — see the module note above.


def downgrade() -> None:
    # 'idm' is not a member of ActorType after this migration is reversed, and
    # the column's Python-side validation would raise when loading such a row.
    # Audit logs are append-only and must never be deleted, so existing rows
    # are relabelled to 'system' — the closest surviving value — not dropped.
    op.execute("UPDATE audit_logs SET actor_type = 'system' WHERE actor_type = 'idm'")

    op.drop_table("idm_identity_aliases")
    op.drop_index("ix_idm_identities_external_id", table_name="idm_identities")
    op.drop_table("idm_identities")
    op.drop_index("ix_idm_service_tokens_token_hash", table_name="idm_service_tokens")
    op.drop_table("idm_service_tokens")
