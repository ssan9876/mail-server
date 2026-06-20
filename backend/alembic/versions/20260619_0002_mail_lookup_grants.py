"""grant read-only lookup access to the mail_lookup role

Revision ID: 0002_mail_lookup_grants
Revises: 0001_initial
Create Date: 2026-06-19

Postfix and Dovecot authenticate/look up addresses via SQL using the
least-privilege `mail_lookup` role (created in docker/postgres/initdb).
This migration grants exactly the read access those daemons need and nothing
more. No-op on non-PostgreSQL backends (e.g. the SQLite test database).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002_mail_lookup_grants"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables the mail daemons read for auth + routing.
_LOOKUP_TABLES = ("domains", "mailboxes", "aliases")


def _role_name() -> str:
    """The mail-lookup role name (matches POSTGRES_MAIL_USER from the env).

    Validated to a safe SQL identifier since it is interpolated into DDL.
    """
    import os
    import re

    name = os.getenv("POSTGRES_MAIL_USER", "mail_lookup")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid POSTGRES_MAIL_USER role name: {name!r}")
    return name


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    role = _role_name()
    # Tolerate a missing role so migrations don't hard-fail in environments
    # where the role wasn't pre-created (it normally is, by initdb).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} LOGIN;
            END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role};")
    for table in _LOOKUP_TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {role};")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    role = _role_name()
    for table in _LOOKUP_TABLES:
        op.execute(f"REVOKE SELECT ON {table} FROM {role};")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role};")
