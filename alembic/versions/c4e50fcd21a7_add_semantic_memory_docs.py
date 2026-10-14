"""add semantic memory docs

Revision ID: c4e50fcd21a7
Revises: d14b7c9a2f61
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4e50fcd21a7"
down_revision: Union[str, Sequence[str], None] = "d14b7c9a2f61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_docs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), server_default="semantic", nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("author_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_id", "path", name="uq_memory_docs_scope_path"),
    )
    op.create_index("ix_memory_docs_scope", "memory_docs", ["scope_type", "scope_id"])


def downgrade() -> None:
    op.drop_index("ix_memory_docs_scope", table_name="memory_docs")
    op.drop_table("memory_docs")
