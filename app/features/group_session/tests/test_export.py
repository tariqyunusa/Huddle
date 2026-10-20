import io
import os
import asyncio
import sys
import types
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("GROQ_API_KEY", "unit-test-key")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("RESEND_API_KEY", "unit-test-key")

export_module = types.ModuleType("app.features.group_session.export")
export_module.generate_document_markdown = None
export_module.markdown_to_docx = None
export_module.markdown_to_pdf = None
sys.modules.setdefault("app.features.group_session.export", export_module)

from app.features.group_session import router
from app.features.group_session.models import GroupMessage, GroupSession


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.filters = {}

    def filter(self, *clauses):
        for clause in clauses:
            left = getattr(clause, "left", None)
            right = getattr(clause, "right", None)
            if getattr(getattr(clause, "operator", None), "__name__", None) == "eq" and hasattr(left, "key") and hasattr(right, "value"):
                self.filters[left.key] = right.value
        return self

    def filter_by(self, **kwargs):
        self.filters.update(kwargs)
        return self

    def order_by(self, *args):
        return self

    def _rows(self):
        rows = [self.db.session] if self.model is GroupSession else self.db.messages if self.model is GroupMessage else []
        return [row for row in rows if all(getattr(row, key, None) == value for key, value in self.filters.items())]

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()


class _DB:
    def __init__(self, session, messages=()):
        self.session = session
        self.messages = list(messages)

    def query(self, model):
        return _Query(self, model)


def _message(session_id, text="The project is ready"):
    return SimpleNamespace(role="user", content=text, author_name="Alice", session_id=session_id)


def test_export_unpacks_markdown_and_records_usage_before_conversion(monkeypatch):
    owner = uuid.uuid4()
    session = SimpleNamespace(id=uuid.uuid4(), created_by=owner, title="Roadmap / 2026")
    db = _DB(session, [_message(session.id)])
    calls = []

    async def generate(transcript, instructions):
        assert transcript
        assert instructions == "Summarize the key points and decisions from this discussion."
        return "# Export", 321

    def record_usage(user_id, tokens, active_db):
        calls.append((user_id, tokens, active_db))

    def convert(markdown_text):
        assert markdown_text == "# Export"
        assert calls == [(owner, 321, db)]
        return io.BytesIO(b"docx")

    monkeypatch.setattr(router, "check_usage_allowed", lambda user_id, active_db: True)
    monkeypatch.setattr(router, "record_usage", record_usage)
    monkeypatch.setattr(router, "generate_document_markdown", generate)
    monkeypatch.setattr(router, "markdown_to_docx", convert)

    response = asyncio.run(router.export_session(session.id, "docx", db=db, current_user=SimpleNamespace(id=owner)))

    assert calls == [(owner, 321, db)]
    assert 'filename="Roadmap  2026.docx"' in response.headers["content-disposition"]
    assert "filename*=UTF-8''Roadmap%20%202026.docx" in response.headers["content-disposition"]


def test_export_filename_sanitization_handles_spaces_unicode_slashes_and_empty_titles():
    assert router._export_filename_header("Project Plan", "pdf") == ("Project Plan.pdf", "Project%20Plan.pdf")
    ascii_name, encoded = router._export_filename_header("Crème 東京/Plan", "docx")
    assert ascii_name == "Creme Plan.docx"
    assert encoded == "Cr%C3%A8me%20%E6%9D%B1%E4%BA%ACPlan.docx"
    assert router._export_filename_header("../../", "pdf") == ("session.pdf", "session.pdf")
    assert router._export_filename_header(None, "pdf") == ("session.pdf", "session.pdf")


def test_export_rejects_empty_transcript_without_model_call_or_usage(monkeypatch):
    owner = uuid.uuid4()
    session = SimpleNamespace(id=uuid.uuid4(), created_by=owner, title="Empty")
    db = _DB(session)
    called = []
    monkeypatch.setattr(router, "check_usage_allowed", lambda user_id, active_db: True)
    monkeypatch.setattr(router, "generate_document_markdown", lambda *args: called.append(args))
    monkeypatch.setattr(router, "record_usage", lambda *args: called.append(args))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.export_session(session.id, "docx", db=db, current_user=SimpleNamespace(id=owner)))

    assert exc.value.status_code == 422
    assert "no messages" in exc.value.detail
    assert called == []


def test_export_rejects_empty_model_content_without_recording_usage(monkeypatch):
    owner = uuid.uuid4()
    session = SimpleNamespace(id=uuid.uuid4(), created_by=owner, title="Empty")
    db = _DB(session, [_message(session.id)])
    calls = []

    async def generate(transcript, instructions):
        return " \n ", 456

    monkeypatch.setattr(router, "check_usage_allowed", lambda user_id, active_db: True)
    monkeypatch.setattr(router, "generate_document_markdown", generate)
    monkeypatch.setattr(router, "record_usage", lambda *args: calls.append(args))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.export_session(session.id, "pdf", db=db, current_user=SimpleNamespace(id=owner)))

    assert exc.value.status_code == 502
    assert "empty export content" in exc.value.detail
    assert calls == []
