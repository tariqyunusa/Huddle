import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        Index("ix_episodes_scope", "scope_type", "scope_id"),
        Index(
            "ix_episodes_embedding_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_type = Column(String, nullable=False)
    scope_id = Column(UUID(as_uuid=True), nullable=False)
    session_id = Column(UUID(as_uuid=True), ForeignKey("group_sessions.id", ondelete="CASCADE"), nullable=False)
    summary = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=False)
    first_message_at = Column(DateTime, nullable=False)
    last_message_at = Column(DateTime, nullable=False)
    message_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
