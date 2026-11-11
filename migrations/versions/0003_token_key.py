"""Per-user token key: login tokens carry it and are rejected when it doesn't match.

SQLite reuses the id of a deleted user, so a token minted for a deleted account must not work for
the next account that gets the same id. Rotating the key also signs a user out everywhere.

Revision ID: 0003_token_key
Revises: 0002_phase2_data
Create Date: 2026-11-11
"""
import secrets
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_token_key"
down_revision: str | None = "0002_phase2_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("token_key", sa.String(), nullable=True))
    bind = op.get_bind()
    users = sa.table("users", sa.column("id", sa.Integer), sa.column("token_key", sa.String))
    for (user_id,) in bind.execute(sa.select(users.c.id)).fetchall():
        bind.execute(users.update().where(users.c.id == user_id).values(token_key=secrets.token_hex(16)))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_key")
