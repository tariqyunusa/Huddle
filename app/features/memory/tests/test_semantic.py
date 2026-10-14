from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

from app.features.memory import semantic


def _op(**values):
    defaults = {
        "op": "create",
        "scope": "session",
        "path": "topics/stack.md",
        "description": "Project stack",
        "content": "- [stated] The project uses Python.",
    }
    defaults.update(values)
    return semantic.SemanticOp.model_validate(defaults)


def test_parse_semantic_ops_drops_invalid_operations():
    parsed = semantic.parse_semantic_ops(
        '```json\n{"ops":[{"op":"create","scope":"session","path":"topics/stack.md",'
        '"description":"Stack","content":"Python"},{"op":"delete","scope":"session",'
        '"path":"profile.md","description":"bad"}]}\n```'
    )
    assert len(parsed) == 1
    assert parsed[0].path == "topics/stack.md"
    assert semantic.parse_semantic_ops("not json") == []


def test_user_resolution_requires_a_unique_author():
    first, second = uuid.uuid4(), uuid.uuid4()
    assert semantic.resolve_user_id("alice", {"Alice": first}) == first
    assert semantic.resolve_user_id("Alice", {"Alice": None}) is None
    assert semantic.resolve_user_id("unknown", {"Alice": first}) is None
    assert semantic.resolve_user_id("Alex", {"Alex": first, "alex ": second}) is None


def test_doc_lines_are_deduplicated():
    content = semantic.normalize_doc_content("- [stated] Uses Python\nUses Python\n- [stated] Uses Python")
    assert content == "- [stated] Uses Python"


class _Query:
    def __init__(self, db):
        self.db = db

    def filter_by(self, **kwargs):
        return self

    def first(self):
        return self.db.doc


class _FakeDB:
    def __init__(self, doc, rowcounts=()):
        self.doc = doc
        self.rowcounts = iter(rowcounts)
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        return _Query(self)

    def execute(self, statement):
        rowcount = next(self.rowcounts)
        if rowcount == 0:
            self.doc.version += 1
            self.doc.content = "- [stated] Concurrent edit"
        return SimpleNamespace(rowcount=rowcount)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _existing_doc(content, version=1):
    return SimpleNamespace(
        id=uuid.uuid4(), version=version, content=content,
        scope_type="session", scope_id=uuid.uuid4(), path="topics/stack.md",
    )


def test_document_size_cap_skips_oversized_append():
    doc = _existing_doc("- [stated] " + "x" * 5984)
    db = _FakeDB(doc)
    op = _op(op="append", content=None, line="Add one more durable fact")
    result = semantic.apply_semantic_op(db, op, doc.scope_id, {}, uuid.uuid4())
    assert result is False
    assert db.commits == 0


def test_version_conflict_retries_once():
    doc = _existing_doc("- [stated] Original")
    db = _FakeDB(doc, rowcounts=(0, 1))
    op = _op(op="update", content="- [stated] Updated")
    assert semantic.apply_semantic_op(db, op, doc.scope_id, {}, uuid.uuid4()) is True
    assert db.rollbacks == 1
    assert db.commits == 1


def test_semantic_context_keeps_newest_docs_within_budget():
    now = datetime.utcnow()
    old = SimpleNamespace(scope_type="session", scope_id=uuid.uuid4(), path="project/old.md", content="x" * 45, updated_at=now - timedelta(days=1))
    new = SimpleNamespace(scope_type="session", scope_id=old.scope_id, path="project/new.md", content="new fact", updated_at=now)
    context = semantic.build_semantic_context([old, new], max_chars=80)
    assert "project/new.md" in context
    assert "project/old.md" not in context


def test_visible_docs_never_includes_another_participants_user_docs():
    session_id, author_id, other_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    docs = [
        SimpleNamespace(scope_type="session", scope_id=session_id),
        SimpleNamespace(scope_type="user", scope_id=author_id),
        SimpleNamespace(scope_type="user", scope_id=other_id),
    ]
    visible = semantic.visible_semantic_docs(docs, session_id, author_id)
    assert docs[0] in visible
    assert docs[1] in visible
    assert docs[2] not in visible
