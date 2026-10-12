"""add episodic memory

Revision ID: d14b7c9a2f61
Revises: e24a9b5c1d30
Create Date: 2026-10-12
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d14b7c9a2f61"
down_revision: Union[str, Sequence[str], None] = "e24a9b5c1d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("""
        CREATE TABLE episodes (
            id UUID PRIMARY KEY,
            scope_type VARCHAR NOT NULL,
            scope_id UUID NOT NULL,
            session_id UUID NOT NULL REFERENCES group_sessions(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            embedding vector(384) NOT NULL,
            first_message_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            last_message_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            message_count INTEGER NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_episodes_scope ON episodes (scope_type, scope_id)")
    op.execute("CREATE INDEX ix_episodes_embedding_hnsw ON episodes USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP TABLE episodes")
