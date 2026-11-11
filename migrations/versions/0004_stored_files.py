"""Stored files table: CVs kept inside the database (STORAGE_BACKEND=database), so the free demo
needs no separate storage bucket.

Revision ID: 0004_stored_files
Revises: 0003_token_key
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_stored_files"
down_revision: str | None = "0003_token_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("stored_files")
