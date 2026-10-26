from datetime import datetime, timedelta
import os
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("GROQ_API_KEY", "unit-test-key")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "unit-test-secret")

from app.features.group_session.talon import SYSTEM_PROMPT, build_system_message, fit_chat_context_budget
from app.features.group_session.models import GroupParticipant
from app.features.memory import procedures, router, semantic
from app.features.memory.models import MemoryDoc


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.filters = {}

    def filter(self, *args):
        for clause in args:
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
        if self.model is MemoryDoc:
            rows = self.db.docs
        elif self.model.__name__ == "GroupSession":
            rows = [self.db.session] if self.db.session else []
        elif self.model is GroupParticipant:
            rows = self.db.participants
        else:
            rows = []
        return [row for row in rows if all(getattr(row, key, None) == value for key, value in self.filters.items())]

    def first(self):
        rows = self._rows()
        return rows[0] if rows else None

    def all(self):
        return self._rows()

    def count(self):
        return len(self._rows())


class _DB:
    def __init__(self, docs=(), session=None, participants=()):
        self.docs = list(docs)
        self.session = session
        self.participants = list(participants)
        self.commits = 0

    def query(self, model):
        return _Query(self, model)

    def add(self, doc):
        self.docs.append(doc)

    def commit(self):
        self.commits += 1

    def refresh(self, doc):
        pass

    def delete(self, doc):
        self.docs.remove(doc)

    def execute(self, statement):
        criteria = statement._where_criteria
        expected = {}
        for clause in criteria:
            expected[clause.left.key] = clause.right.value
        values = statement._values
        values = {key: value.value if hasattr(value, "value") else value for key, value in values.items()}
        doc = next((item for item in self.docs if item.id == expected.get("id") and item.version == expected.get("version")), None)
        if doc:
            for key, value in values.items():
                setattr(doc, key, value)
        return SimpleNamespace(rowcount=1 if doc else 0)


def _doc(scope, scope_id, path="procedures/style.md", content="Keep answers concise.", *, kind="procedural", updated_at=None, version=1):
    return SimpleNamespace(
        id=uuid.uuid4(), scope_type=scope, scope_id=scope_id, kind=kind, path=path,
        description="Style", content=content, version=version, updated_at=updated_at or datetime.utcnow(),
        created_at=datetime.utcnow(), author_user_id=None,
    )


def _body(**values):
    return router.ProceduralDocWrite.model_validate({"description": "Style", "content": "Keep answers concise.", **values})


def test_procedural_path_validation_and_text_limits_normalization():
    assert procedures.validate_procedural_path("procedures/code-review.md") == "procedures/code-review.md"
    for path in ("profile.md", "procedures/../secret.md", "procedures/UPPER.md", "procedures/a.txt", "procedures/" + "a" * 70 + ".md"):
        with pytest.raises(ValueError):
            procedures.validate_procedural_path(path)
    normalized = _body(content="Use short‑lived—examples\x01.\nKeep it brief.")
    assert normalized.content == "Use short-lived-examples.\nKeep it brief."
    with pytest.raises(ValidationError):
        _body(content="x" * 4001)
    with pytest.raises(ValueError):
        _body(description="d" * 151)


def test_user_procedural_write_is_scoped_to_caller_and_max_five_docs():
    owner, other = uuid.uuid4(), uuid.uuid4()
    existing = [_doc("user", owner, f"procedures/rule-{index}.md") for index in range(5)]
    db = _DB(existing)
    with pytest.raises(HTTPException) as exc:
        router.put_user_procedure("procedures/new.md", _body(), db, SimpleNamespace(id=owner))
    assert exc.value.status_code == 422

    db = _DB([_doc("user", owner)])
    result = router.put_user_procedure("procedures/style.md", _body(content="For me only."), db, SimpleNamespace(id=other))
    assert result["scope_id"] == other
    assert db.docs[0].content == "Keep answers concise."


def test_procedural_version_conflict_returns_current_version():
    owner = uuid.uuid4()
    db = _DB([_doc("user", owner, version=4)])
    with pytest.raises(HTTPException) as exc:
        router.put_user_procedure("procedures/style.md", _body(if_version=3), db, SimpleNamespace(id=owner))
    assert exc.value.status_code == 409
    assert exc.value.detail["current_version"] == 4


def test_user_reads_and_deletes_only_owned_procedures():
    owner, other = uuid.uuid4(), uuid.uuid4()
    doc = _doc("user", owner)
    db = _DB([doc])
    assert router.list_user_procedures(db, SimpleNamespace(id=other)) == []
    with pytest.raises(HTTPException) as exc:
        router.delete_memory_doc(doc.id, db, SimpleNamespace(id=other))
    assert exc.value.status_code == 403


def _semantic_edit_body(**values):
    return router.SemanticDocEdit.model_validate({"content": "Updated fact", "if_version": 1, **values})


def test_semantic_edit_owner_and_normalization():
    owner = uuid.uuid4()
    doc = _doc("user", owner, path="profile.md", kind="semantic", content="old", version=1)
    db = _DB([doc])
    result = router.edit_memory_doc(doc.id, _semantic_edit_body(content="  Likes tea—daily\nLikes tea-daily\n\n- [stated] Likes tea-daily\x01"), db, SimpleNamespace(id=owner))
    assert result["content"] == "- [stated] Likes tea-daily"
    assert result["version"] == 2
    assert result["author_user_id"] is None


def test_semantic_edit_non_owner_is_forbidden():
    owner, other = uuid.uuid4(), uuid.uuid4()
    doc = _doc("user", owner, kind="semantic")
    with pytest.raises(HTTPException) as exc:
        router.edit_memory_doc(doc.id, _semantic_edit_body(), _DB([doc]), SimpleNamespace(id=other))
    assert exc.value.status_code == 403


def test_semantic_edit_session_member_and_non_member_permissions():
    creator, member, outsider, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(id=session_id, created_by=creator)
    doc = _doc("session", session_id, path="topics/session.md", kind="semantic")
    db = _DB([doc], session=session, participants=[SimpleNamespace(session_id=session_id, user_id=member)])
    assert router.edit_memory_doc(doc.id, _semantic_edit_body(), db, SimpleNamespace(id=member))["version"] == 2
    with pytest.raises(HTTPException) as exc:
        router.edit_memory_doc(doc.id, _semantic_edit_body(if_version=2), db, SimpleNamespace(id=outsider))
    assert exc.value.status_code == 403


def test_semantic_edit_stale_version_returns_current_content():
    owner = uuid.uuid4()
    doc = _doc("user", owner, path="profile.md", kind="semantic", content="current", version=4)
    with pytest.raises(HTTPException) as exc:
        router.edit_memory_doc(doc.id, _semantic_edit_body(if_version=3), _DB([doc]), SimpleNamespace(id=owner))
    assert exc.value.status_code == 409
    assert exc.value.detail["current_version"] == 4
    assert exc.value.detail["current_content"] == "current"


def test_semantic_edit_rejects_procedural_empty_and_oversized_content():
    owner = uuid.uuid4()
    procedural = _doc("user", owner)
    with pytest.raises(HTTPException) as exc:
        router.edit_memory_doc(procedural.id, _semantic_edit_body(), _DB([procedural]), SimpleNamespace(id=owner))
    assert exc.value.status_code == 400

    semantic_doc = _doc("user", owner, path="profile.md", kind="semantic")
    with pytest.raises(HTTPException) as exc:
        router.edit_memory_doc(semantic_doc.id, _semantic_edit_body(content="\x01 \n\n"), _DB([semantic_doc]), SimpleNamespace(id=owner))
    assert exc.value.status_code == 422
    with pytest.raises(ValueError):
        router.SemanticDocEdit.model_validate({"content": "x" * 6001, "if_version": 1})


def test_session_procedural_write_and_delete_are_creator_only():
    creator, member, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(id=session_id, created_by=creator)
    db = _DB(session=session)
    with pytest.raises(HTTPException) as exc:
        router.put_session_procedure(session_id, "procedures/style.md", _body(), db, SimpleNamespace(id=member))
    assert exc.value.status_code == 403

    doc = _doc("session", session_id)
    db = _DB([doc], session=session)
    with pytest.raises(HTTPException) as exc:
        router.delete_memory_doc(doc.id, db, SimpleNamespace(id=member))
    assert exc.value.status_code == 403


def test_session_procedural_read_is_members_only():
    creator, member, outsider, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(id=session_id, created_by=creator)
    doc = _doc("session", session_id)
    participants = [SimpleNamespace(session_id=session_id, user_id=member)]
    db = _DB([doc], session=session, participants=participants)

    assert router.list_session_procedures(session_id, db, SimpleNamespace(id=member)) == [router._serialize(doc)]
    with pytest.raises(HTTPException) as exc:
        router.list_session_procedures(session_id, db, SimpleNamespace(id=outsider))
    assert exc.value.status_code == 403


def test_procedural_injection_is_private_and_keeps_session_rules():
    session_id, speaker, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    docs = [
        {"kind": "procedural", "scope_type": "session", "scope_id": session_id, "path": "procedures/session.md", "content": "Use shared terms."},
        {"kind": "procedural", "scope_type": "user", "scope_id": speaker, "path": "procedures/me.md", "content": "Be concise.", "display_name": "Alice"},
        {"kind": "procedural", "scope_type": "user", "scope_id": other, "path": "procedures/other.md", "content": "Private rule.", "display_name": "Bob"},
    ]
    visible = procedures.visible_procedural_docs(docs, session_id, speaker)
    context = procedures.build_procedural_context(visible)
    assert "Use shared terms." in context
    assert "Be concise." in context
    assert "Personal instructions for Alice" in context
    assert "Private rule." not in context


def test_procedural_budget_drops_oldest_updated_docs_first():
    now = datetime.utcnow()
    docs = [
        {"kind": "procedural", "scope_type": "session", "scope_id": uuid.uuid4(), "path": "procedures/old.md", "content": "old" * 60, "updated_at": now - timedelta(days=2)},
        {"kind": "procedural", "scope_type": "session", "scope_id": uuid.uuid4(), "path": "procedures/new.md", "content": "new", "updated_at": now},
    ]
    context = procedures.build_procedural_context(docs, max_chars=80)
    assert "procedures/new.md" in context
    assert "procedures/old.md" not in context


def test_chat_budget_drops_episodic_then_semantic_before_trimming_transcript():
    transcript = [
        {"role": "user", "content": "old" * 10},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "latest"},
    ]
    trimmed, episodic, semantic_context, procedural = fit_chat_context_budget(
        transcript, "", "episode", "semantic", "procedure", max_chars=25,
    )
    assert episodic == ""
    assert semantic_context == ""
    assert procedural == "procedure"
    assert any(message["content"] == "latest" for message in trimmed)
    assert all(message["content"] != "old" * 10 for message in trimmed)


def test_chat_budget_drops_procedural_only_after_preserving_latest_user_turn():
    transcript = [{"role": "user", "content": "latest" * 8}]
    trimmed, episodic, semantic_context, procedural = fit_chat_context_budget(
        transcript, "", "episode", "semantic", "procedure", max_chars=30,
    )
    assert episodic == ""
    assert semantic_context == ""
    assert procedural == ""
    assert trimmed == transcript


def test_semantic_extractor_drops_procedural_paths_and_injection_filters_kind():
    payload, dropped = semantic._normalize_ops_before_validation(
        {"ops": [{"op": "create", "scope": "session", "user": None, "path": "procedures/style.md", "description": "Style", "content": "Keep it short."}]},
        None, uuid.uuid4(), {},
    )
    assert payload["ops"] == []
    assert "procedural paths" in dropped[0]
    procedural = _doc("session", uuid.uuid4(), kind="procedural")
    assert semantic.build_semantic_context([procedural]) == ""


def test_semantic_extractor_drops_operation_matching_existing_procedural_doc():
    session_id = uuid.uuid4()
    procedural = _doc("session", session_id, path="topics/legacy.md")
    payload, dropped = semantic._normalize_ops_before_validation(
        {"ops": [{"op": "update", "scope": "session", "user": None, "path": "topics/legacy.md", "description": "Legacy", "content": "Changed."}]},
        _DB([procedural]), session_id, {},
    )
    assert payload["ops"] == []
    assert "existing procedural" in dropped[0]


def test_talon_system_message_keeps_base_prompt_first_and_frames_preferences():
    message = build_system_message("Standing instructions\nSession rules\nBe concise.")
    assert message.startswith(SYSTEM_PROMPT)
    assert "The following are preferences set by the participants" in message
    assert "that user's personal instructions take precedence over session rules" in message
    assert message.endswith("Standing instructions\nSession rules\nBe concise.")
