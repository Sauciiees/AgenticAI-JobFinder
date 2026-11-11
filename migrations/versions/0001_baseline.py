"""Baseline: the schema as it was before migrations existed (created by Base.metadata.create_all).

Existing local databases already have these tables; db_migrate.py stamps them at this revision
instead of running it.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("cv_filename", sa.String(), nullable=True),
        sa.Column("cv_filepath", sa.String(), nullable=True),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("university", sa.String(), nullable=True),
        sa.Column("degree", sa.String(), nullable=True),
        sa.Column("gpa", sa.Float(), nullable=True),
        sa.Column("graduation_year", sa.Integer(), nullable=True),
        sa.Column("skills", sa.Text(), nullable=True),
        sa.Column("work_experience", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.String(), nullable=True),
        sa.Column("github_url", sa.String(), nullable=True),
        sa.Column("preferred_job_titles", sa.Text(), nullable=True),
        sa.Column("preferred_locations", sa.Text(), nullable=True),
        sa.Column("profile_completed", sa.Boolean(), nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("job_title", sa.String(), nullable=True),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("logo_url", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("assets_preview", sa.Text(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_applications_id", "applications", ["id"])

    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_sessions_id", "chat_sessions", ["id"])
    op.create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"], unique=True)


def downgrade() -> None:
    op.drop_table("chat_sessions")
    op.drop_table("applications")
    op.drop_table("users")
