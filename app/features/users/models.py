"""
Minimal user model — just enough to anchor auth-related FKs.
Expand later with password hash, email verification, etc. when you build real auth.
"""

import uuid
from datetime import datetime
import secrets

from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Boolean,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    plan = Column(String, default="free", nullable=False)
    paystack_customer_code = Column(String, unique=True, nullable=True)
    paystack_subscription_code = Column(String, unique=True, nullable=True)
    bachs_customer_id = Column(String, unique=True, nullable=True)
    bachs_subscription_id = Column(String, unique=True, nullable=True)
    email_verified = Column(Boolean, default=False, nullable=False)
    
    token_count = Column(Integer, default=0, nullable=False)
    window_started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    
class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    
    id= Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    

    
class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token = Column(String, unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BachsWebhookEvent(Base):
    __tablename__ = "bachs_webhook_events"

    event_id = Column(String, primary_key=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class GitHubInstallation(Base):
    __tablename__ = "github_installations"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    installation_id = Column(BigInteger, nullable=False)
    account_login = Column(String, nullable=False)
    account_type = Column(String, nullable=False)
    repository_selection = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint('user_id', 'installation_id',),)
    
class Repo(Base):
    __tablename__ = "repos"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_type = Column(String, nullable=False, default="session")
    scope_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending | syncing | ready | failed
    file_count = Column(Integer, nullable=False, default=0)
    last_synced_at = Column(DateTime(timezone=True))
    installation_id = Column(BigInteger, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("scope_type", "scope_id", "name"),)


class CodeFile(Base):
    __tablename__ = "code_files"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_id = Column(UUID(as_uuid=True), ForeignKey("repos.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String, nullable=False)
    content_hash = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("repo_id", "path"),)


class CodeChunk(Base):
    __tablename__ = "code_chunks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repo_id = Column(UUID(as_uuid=True), ForeignKey("repos.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("code_files.id", ondelete="CASCADE"), nullable=False, index=True)
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384), nullable=False)