import json
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, update

from app.features.memory.models import MemoryDoc

logger = logging.getLogger(__name__)
EXTRACTOR_ENABLED = os.getenv("EXTRACTOR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
MAX_DOC_CHARS = 6000
MESSAGE_INPUT_CHARS = 9000
EXISTING_DOC_INPUT_CHARS = 6000
MAX_OPS = 6


class SemanticOp(BaseModel):
    model_config = ConfigDict(extra="ignore")

    op: Literal["create", "update", "append"]
    scope: Literal["user", "session"]
    user: str | None = None
    path: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=149)
    content: str | None = None
    line: str | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if self.scope == "user" and not self.user:
            raise ValueError("user scope requires a user name")
        if "\n" in self.description or "\r" in self.description:
            raise ValueError("description must be one line")
        if not re.fullmatch(r"(?:topics/[a-z0-9-]+|project/[a-z0-9-]+|profile)\.md", self.path):
            raise ValueError("invalid memory path")
        if self.op in {"create", "update"} and not self.content:
            raise ValueError("create and update require content")
        if self.op == "append" and (not self.line or "\n" in self.line or "\r" in self.line):
            raise ValueError("append requires a single line")
        return self


SYSTEM_PROMPT = """Extract durable semantic memories from the supplied session segment. Return only JSON with shape {\"ops\":[...]} and at most 6 operations.

Rules:
- Save only facts explicitly STATED by a human participant. Never save Talon's own claims, advice, search results, or anything inferred or guessed.
- Durable facts only: no one-off questions, transient state, or meeting chatter.
- Never save health or medical details, finances/income/debts, government IDs, account or card numbers, passwords/API keys/tokens, political or religious views, immigration status, sexual orientation, criminal history, or anything about minors.
- Facts about a person go in that person's user scope; project facts go in session scope; ambiguous facts default to user scope. Never file one person's fact under another person.
- Prefer updating or appending to an existing doc over creating a new one. Do not re-add a fact that already exists. Use one fact per line, each line prefixed '- [stated] '. Mark corrections by editing the old line, not adding a contradicting one.
- Paths are short slugs: topics/<domain>.md, project/<name>.md, or profile.md. Description is one line saying what the doc holds and is under 150 characters.
- For create/update provide full new content. For append provide one new line.
"""


def parse_semantic_ops(raw: str | dict) -> list[SemanticOp]:
    """Parse model output defensively; malformed and invalid operations are ignored."""
    try:
        payload = raw if isinstance(raw, dict) else raw.strip()
        if isinstance(payload, str):
            payload = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload, flags=re.IGNORECASE)
            payload = json.loads(payload)
        candidates = payload.get("ops", []) if isinstance(payload, dict) else []
        if not isinstance(candidates, list):
            return []
        valid = []
        for item in candidates[:MAX_OPS]:
            try:
                valid.append(SemanticOp.model_validate(item))
            except Exception:
                continue
        return valid
    except Exception:
        return []


def resolve_user_id(name: str | None, authors: dict[str, uuid.UUID | None]) -> uuid.UUID | None:
    if not name:
        return None
    matching = [user_id for author_name, user_id in authors.items() if author_name.strip().casefold() == name.strip().casefold()]
    if any(user_id is None for user_id in matching):
        return None
    matching_ids = set(matching)
    return next(iter(matching_ids)) if len(matching_ids) == 1 else None


def normalize_doc_content(content: str) -> str:
    lines = []
    seen = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith("- [stated] "):
            line = "- [stated] " + line.lstrip("- ")
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)


def _content_for_op(op: SemanticOp, existing: str | None) -> str | None:
    if op.op == "append":
        line = (op.line or "").strip()
        if not line.startswith("- [stated] "):
            line = "- [stated] " + line.lstrip("- ")
        lines = normalize_doc_content(existing or "")
        if line in lines.splitlines():
            return lines
        return "\n".join(part for part in (lines, line) if part)
    return normalize_doc_content(op.content or "")


def apply_semantic_op(db, op: SemanticOp, session_id, authors: dict[str, uuid.UUID], triggering_user_id) -> bool:
    """Apply one operation. Updates use optimistic locking and retry once on conflict."""
    scope_id = session_id if op.scope == "session" else resolve_user_id(op.user, authors)
    if scope_id is None:
        return False
    if op.scope == "user" and resolve_user_id(op.user, authors) is None:
        return False

    if op.op == "create":
        if db.query(MemoryDoc).filter_by(scope_type=op.scope, scope_id=scope_id, path=op.path).first():
            return False
        content = _content_for_op(op, None)
        if not content:
            return False
        if len(content) > MAX_DOC_CHARS:
            logger.warning("Skipping semantic memory op because document exceeds %s characters", MAX_DOC_CHARS)
            return False
        db.add(MemoryDoc(
            id=uuid.uuid4(), scope_type=op.scope, scope_id=scope_id, kind="semantic", path=op.path,
            description=op.description[:149], content=content, version=1,
            author_user_id=triggering_user_id, created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        ))
        db.commit()
        return True

    for attempt in range(2):
        doc = db.query(MemoryDoc).filter_by(scope_type=op.scope, scope_id=scope_id, path=op.path).first()
        if not doc:
            return False
        new_content = _content_for_op(op, doc.content)
        if new_content is None or new_content == doc.content:
            return False
        if len(new_content) > MAX_DOC_CHARS:
            logger.warning("Skipping semantic memory op because document exceeds %s characters", MAX_DOC_CHARS)
            return False
        result = db.execute(
            update(MemoryDoc)
            .where(MemoryDoc.id == doc.id, MemoryDoc.version == doc.version)
            .values(
                content=new_content,
                description=op.description[:149],
                author_user_id=triggering_user_id,
                version=doc.version + 1,
                updated_at=datetime.utcnow(),
            )
        )
        if result.rowcount == 1:
            db.commit()
            return True
        db.rollback()
        if attempt == 0:
            continue
    return False


def _render_semantic_context(docs: list[MemoryDoc]) -> str:
    user_docs = [doc for doc in docs if doc.scope_type == "user"]
    session_docs = [doc for doc in docs if doc.scope_type == "session"]
    blocks = ["[Memory — known facts]"]
    if user_docs:
        display_name = getattr(user_docs[0], "_semantic_display_name", "you")
        blocks.append(f"About {display_name}")
        blocks.extend(f"{doc.path}\n{doc.content}" for doc in user_docs)
    if session_docs:
        blocks.append("About this session")
        blocks.extend(f"{doc.path}\n{doc.content}" for doc in session_docs)
    return "\n".join(blocks) if len(blocks) > 1 else ""


def build_semantic_context(docs, max_chars: int = 3000) -> str:
    ordered = sorted(docs, key=lambda doc: doc.updated_at or datetime.min, reverse=True)
    selected = list(ordered)
    while selected:
        context = _render_semantic_context(selected)
        if len(context) <= max_chars:
            return context
        selected.pop()
    return ""


def visible_semantic_docs(docs, session_id, latest_user_id):
    """Apply the chat privacy boundary: session facts plus only the latest author's facts."""
    session_id = uuid.UUID(str(session_id))
    return [
        doc for doc in docs
        if (doc.scope_type == "session" and doc.scope_id == session_id)
        or (latest_user_id is not None and doc.scope_type == "user" and doc.scope_id == latest_user_id)
    ]


async def extract_semantic(session_id, messages, triggering_user_id):
    """Extract and persist durable facts. Failures are logged and never escape."""
    if not EXTRACTOR_ENABLED:
        return
    db = None
    try:
        session_id = uuid.UUID(str(session_id))
        from app.db.session import SessionLocal
        from app.features.group_session.talon import client
        from app.features.users.usage import record_usage

        db = SessionLocal()
        authors: dict[str, uuid.UUID | None] = {}
        for message in messages:
            if message.role == "user" and message.author_id:
                matching_name = next((name for name in authors if name.casefold() == message.author_name.casefold()), None)
                if matching_name is None:
                    authors[message.author_name] = message.author_id
                elif authors[matching_name] != message.author_id:
                    authors[matching_name] = None

        message_lines = []
        message_chars = 0
        for message in messages:
            if message.role not in {"user", "assistant"}:
                continue
            content = message.content or ""
            if len(content) > 1200:
                content = content[:1200] + "…"
            line = f"[{message.created_at:%Y-%m-%d %H:%M}] {message.author_name}: {content}"
            extra = len(line) + (1 if message_lines else 0)
            if message_chars + extra > MESSAGE_INPUT_CHARS:
                break
            message_lines.append(line)
            message_chars += extra

        user_ids = set(authors.values())
        filters = [((MemoryDoc.scope_type == "session") & (MemoryDoc.scope_id == session_id))]
        if user_ids:
            filters.append((MemoryDoc.scope_type == "user") & MemoryDoc.scope_id.in_(user_ids))
        existing_docs = db.query(MemoryDoc).filter(or_(*filters)).order_by(MemoryDoc.updated_at.desc()).all()
        doc_lines = []
        docs_chars = 0
        for doc in existing_docs:
            block = f"[{doc.scope_type}:{doc.scope_id}] {doc.path} — {doc.description}\n{doc.content}"
            extra = len(block) + (1 if doc_lines else 0)
            if docs_chars + extra > EXISTING_DOC_INPUT_CHARS:
                continue
            doc_lines.append(block)
            docs_chars += extra

        request = {
            "model": "openai/gpt-oss-120b",
            "max_tokens": 700,
            "extra_body": {"include_reasoning": False},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Episode messages:\n" + "\n".join(message_lines) + "\n\nExisting memory docs:\n" + "\n\n".join(doc_lines)},
            ],
        }
        from openai import APIStatusError
        try:
            response = await client.chat.completions.create(**request, response_format={"type": "json_object"})
        except APIStatusError as exc:
            detail = str(exc).lower()
            if exc.status_code != 400 or not any(term in detail for term in ("response_format", "json_object", "json mode")):
                raise
            response = await client.chat.completions.create(**request)
        raw = response.choices[0].message.content or ""
        operations = parse_semantic_ops(raw)
        for op in operations:
            apply_semantic_op(db, op, session_id, authors, triggering_user_id)
        tokens = response.usage.total_tokens if response.usage else 0
        record_usage(triggering_user_id, tokens, db)
    except Exception:
        logger.exception("Failed to extract semantic memory for session %s", session_id)
        if db is not None:
            try:
                db.rollback()
            except Exception:
                logger.exception("Failed to roll back semantic memory extraction for session %s", session_id)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close semantic memory session for %s", session_id)
