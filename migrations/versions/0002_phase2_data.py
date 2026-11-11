"""Phase 2: storage keys for CVs, guests, chat activity, cascading deletes, CV chunks with
embeddings (pgvector on Postgres), and database-backed usage counters.

Revision ID: 0002_phase2_data
Revises: 0001_baseline
Create Date: 2026-11-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_phase2_data"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Names SQLite's unnamed foreign keys, so batch mode can drop and recreate them.
SQLITE_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _fk_name(table: str) -> str:
    if op.get_bind().dialect.name == "postgresql":
        return f"{table}_user_id_fkey"  # Postgres's default name for the baseline's foreign key
    return f"fk_{table}_user_id_users"


def _to_storage_key(path: str | None) -> str | None:
    """'C:\\...\\uploads\\user_2\\cv.pdf' -> 'user_2/cv.pdf' (the key under the local uploads folder)."""
    if not path:
        return None
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else parts[-1]


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # --- users: guest flag, CV storage key, CV upload time -------------------------------------
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("cv_key", sa.String(), nullable=True))
        batch.add_column(sa.Column("cv_uploaded_at", sa.DateTime(), nullable=True))

    users = sa.table("users", sa.column("id", sa.Integer), sa.column("cv_filepath", sa.String),
                     sa.column("cv_key", sa.String), sa.column("cv_uploaded_at", sa.DateTime),
                     sa.column("created_at", sa.DateTime), sa.column("profile_completed", sa.Boolean))
    for row in bind.execute(sa.select(users.c.id, users.c.cv_filepath, users.c.created_at)).fetchall():
        if row.cv_filepath:
            bind.execute(users.update().where(users.c.id == row.id).values(
                cv_key=_to_storage_key(row.cv_filepath), cv_uploaded_at=row.created_at))
    bind.execute(users.update().where(users.c.profile_completed.is_(None)).values(profile_completed=False))

    with op.batch_alter_table("users") as batch:
        batch.drop_column("cv_filepath")

    # --- applications / chat_sessions: delete with their user ------------------------------------
    for table in ("applications", "chat_sessions"):
        with op.batch_alter_table(table, naming_convention=SQLITE_NAMING) as batch:
            batch.drop_constraint(_fk_name(table), type_="foreignkey")
            batch.create_foreign_key(f"fk_{table}_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE")
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
    sessions = sa.table("chat_sessions", sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))
    bind.execute(sessions.update().values(updated_at=sessions.c.created_at))

    # --- documents: CV chunks and their embeddings ------------------------------------------------
    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        from pgvector.sqlalchemy import Vector

        embedding_type: sa.types.TypeEngine = Vector()
    else:
        embedding_type = sa.JSON()

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_documents_user_type", "documents", ["user_id", "doc_type"])

    # --- usage_counters: rate limits and daily quotas -------------------------------------------
    op.create_table(
        "usage_counters",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("window_start", sa.Integer(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
    )
    op.create_index("ix_usage_counters_expires_at", "usage_counters", ["expires_at"])


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_table("documents")
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("updated_at")
    for table in ("applications", "chat_sessions"):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_user_id_users", type_="foreignkey")
            batch.create_foreign_key(_fk_name(table) if op.get_bind().dialect.name == "postgresql" else None,
                                     "users", ["user_id"], ["id"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("cv_filepath", sa.String(), nullable=True))
        batch.drop_column("cv_uploaded_at")
        batch.drop_column("cv_key")
        batch.drop_column("is_guest")
