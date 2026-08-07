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

# actor_type is VARCHAR + CHECK (native_enum=False), so widening it means
# dropping and recreating the constraint rather than an ALTER TYPE.
_OLD_ACTORS = "'user', 'mailbox', 'system'"
_NEW_ACTORS = "'user', 'mailbox', 'system', 'idm'"


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

    # SQLite cannot drop a CHECK constraint; tests build the schema with
    # create_all rather than migrations, so skipping there is correct.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("ck_audit_logs_actortype", "audit_logs", type_="check")
        op.create_check_constraint(
            "actortype", "audit_logs", f"actor_type IN ({_NEW_ACTORS})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Any 'idm' rows would violate the narrowed constraint. Audit logs are
        # append-only and must not be deleted, so they are relabelled 'system'
        # — the closest surviving value — rather than dropped.
        op.execute("UPDATE audit_logs SET actor_type = 'system' WHERE actor_type = 'idm'")
        op.drop_constraint("ck_audit_logs_actortype", "audit_logs", type_="check")
        op.create_check_constraint(
            "actortype", "audit_logs", f"actor_type IN ({_OLD_ACTORS})"
        )

    op.drop_table("idm_identity_aliases")
    op.drop_index("ix_idm_identities_external_id", table_name="idm_identities")
    op.drop_table("idm_identities")
    op.drop_index("ix_idm_service_tokens_token_hash", table_name="idm_service_tokens")
    op.drop_table("idm_service_tokens")
