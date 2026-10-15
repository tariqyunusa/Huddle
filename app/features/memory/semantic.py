import json
import logging
import os
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, update

from app.features.memory.models import MemoryDoc

logger = logging.getLogger(__name__)
EXTRACTOR_ENABLED = os.getenv("EXTRACTOR_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
MAX_DOC_CHARS = 6000
MESSAGE_INPUT_CHARS = 9000
EXISTING_DOC_INPUT_CHARS = 6000
MAX_OPS = 6
_SPACE_TRANSLATION = str.maketrans({"\u00a0": " ", "\u2007": " ", "\u202f": " "})
_OP_ALIASES = {"upsert", "set", "write", "add"}
_OP_SCHEMA = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "maxItems": MAX_OPS,
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["create", "update", "append"]},
                    "scope": {"type": "string", "enum": ["user", "session"]},
                    "user": {"type": ["string", "null"]},
                    "path": {"type": "string"},
                    "description": {"type": "string"},
                    "content": {"type": ["string", "null"]},
                    "line": {"type": ["string", "null"]},
                },
                "required": ["op", "scope", "user", "path", "description", "content", "line"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["ops"],
    "additionalProperties": False,
}


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


SYSTEM_PROMPT = """Extract durable semantic memories from the supplied session segment. Return only JSON using this exact output shape:
{"ops":[{"op":"create","scope":"session","user":null,"path":"topics/example.md","description":"One-line description","content":"- [stated] Example fact.","line":null}]}
Every op must include a scope. op must be exactly one of "create", "update" or "append". scope must be exactly "user" or "session". Return at most 6 ops. Every op includes all fields shown; use null for unused user, content, or line values.

Example:
{"ops":[{"op":"create","scope":"user","user":"Alice","path":"profile.md","description":"Tools used","content":"- [stated] I use DBeaver to inspect my Postgres databases.","line":null},{"op":"create","scope":"session","user":null,"path":"project/atlas.md","description":"Project deadline","content":"- [stated] The Atlas launch is planned for June.","line":null}]}

Rules:
- Save only facts explicitly STATED by a human participant. Never save Talon's own claims, advice, search results, or anything inferred or guessed.
- Durable facts only: no one-off questions, transient state, or meeting chatter.
- Never save health or medical details, finances/income/debts, government IDs, account or card numbers, passwords/API keys/tokens, political or religious views, immigration status, sexual orientation, criminal history, or anything about minors.
- First-person statements about the speaker (I/my/me, including tools, preferences, role, or habits) go in that speaker's user scope with user set to the speaker's display name. Facts about the project or system (stack, decisions, deadlines, constraints) go in session scope and must be worded impersonally without first-person pronouns. Ambiguous facts go in user scope. Never file one person's fact under another person.
- Every operation must state its scope explicitly; if a fact's scope is unclear, omit it rather than leaving scope out or guessing.
- Prefer updating or appending to an existing doc over creating a new one. Do not re-add a fact that already exists. Use one fact per line, each line prefixed '- [stated] '. Mark corrections by editing the old line, not adding a contradicting one.
- Each saved line must restate ONE fact a human explicitly stated, in their own terms. Add no elaboration, explanation, or detail the speaker did not say. If it is not clearly stated, omit it.
- Paths are short slugs: topics/<domain>.md, project/<name>.md, or profile.md. Description is one line saying what the doc holds and is under 150 characters.
- For create/update provide full new content. For append provide one new line.
"""


def parse_semantic_ops(raw: str | dict) -> list[SemanticOp]:
    """Parse model output defensively; malformed and invalid operations are ignored."""
    operations, _, _ = _parse_semantic_ops_with_diagnostics(raw)
    return operations


def _first_json_object(raw: str) -> str | None:
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start:index + 1]
    return None


def _decode_semantic_payload(raw: str | dict):
    try:
        payload = raw if isinstance(raw, dict) else raw.strip()
        if isinstance(payload, str):
            payload = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload, flags=re.IGNORECASE)
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                json_block = _first_json_object(payload)
                if json_block is None:
                    return None, f"invalid JSON: {exc.msg}"
                try:
                    payload = json.loads(json_block)
                except json.JSONDecodeError as block_exc:
                    return None, f"invalid JSON object: {block_exc.msg}"
        return payload, None
    except Exception as exc:
        return None, f"could not parse operation payload: {type(exc).__name__}"


def _parse_semantic_ops_with_diagnostics(raw: str | dict) -> tuple[list[SemanticOp], list[str], str | None]:
    payload, decode_error = _decode_semantic_payload(raw)
    if decode_error:
        return [], [], decode_error
    try:
        candidates = payload.get("ops", []) if isinstance(payload, dict) else []
        if not isinstance(candidates, list):
            return [], [], "JSON field 'ops' is not a list"
        valid = []
        dropped = []
        for index, item in enumerate(candidates[:MAX_OPS]):
            try:
                valid.append(SemanticOp.model_validate(item))
            except Exception as exc:
                details = getattr(exc, "errors", None)
                if callable(details):
                    errors = details(include_url=False)
                    reason = repr(errors) if errors else type(exc).__name__
                else:
                    reason = type(exc).__name__
                dropped.append(f"op {index + 1}: {reason}")
                continue
        return valid, dropped, None
    except Exception as exc:
        return [], [], f"could not parse operation payload: {type(exc).__name__}"


def normalize_memory_text(text: str) -> str:
    text = text.translate(_SPACE_TRANSLATION)
    normalized = []
    for char in text:
        if unicodedata.category(char) == "Pd" or char == "−":
            normalized.append("-")
        elif char == "\n" or unicodedata.category(char) != "Cc":
            normalized.append(char)
    return "".join(normalized)


def _normalize_ops_before_validation(payload, db, session_id, authors):
    if not isinstance(payload, dict) or not isinstance(payload.get("ops"), list):
        return payload, []
    normalized = []
    dropped = []
    for index, candidate in enumerate(payload["ops"][:MAX_OPS]):
        if not isinstance(candidate, dict):
            normalized.append(candidate)
            continue
        op = dict(candidate)
        for key in ("op", "scope", "user", "path", "description", "content", "line"):
            if isinstance(op.get(key), str):
                op[key] = normalize_memory_text(op[key])
        op_name = op.get("op", "")
        if isinstance(op_name, str):
            op_name = op_name.strip().lower()
            op["op"] = op_name
        if "scope" not in op:
            logger.info("Dropping semantic op %d because scope is missing; scope will not be inferred", index + 1)
        if op_name in _OP_ALIASES:
            scope = op.get("scope")
            if scope not in {"user", "session"}:
                logger.info(
                    "Semantic op %d uses alias %s without a valid scope; dropping it without inferring scope",
                    index + 1,
                    op_name,
                )
                normalized.append(op)
                continue
            scope_id = session_id if scope == "session" else resolve_user_id(op.get("user"), authors)
            if scope_id is None:
                dropped.append(f"op {index + 1}: user scope could not be resolved")
                continue
            exists = db.query(MemoryDoc).filter_by(
                scope_type=scope,
                scope_id=scope_id,
                path=op.get("path"),
            ).first() is not None
            op["op"] = "update" if exists else "create"
        if op.get("op") == "append" and "line" not in op and isinstance(op.get("content"), str):
            op["line"] = op["content"]
        normalized.append(op)
    return {**payload, "ops": normalized}, dropped


def resolve_user_id(name: str | None, authors: dict[str, uuid.UUID | None]) -> uuid.UUID | None:
    if not name:
        return None
    matching = [user_id for author_name, user_id in authors.items() if author_name.strip().casefold() == name.strip().casefold()]
    if any(user_id is None for user_id in matching):
        return None
    matching_ids = set(matching)
    return next(iter(matching_ids)) if len(matching_ids) == 1 else None


_FIRST_PERSON_MARKER = re.compile(r"\b(?:I|I'm|my|me|mine)\b", re.IGNORECASE)


def guard_semantic_op_scopes(operations: list[SemanticOp], authors: dict[str, uuid.UUID | None]):
    """Keep personal first-person facts out of shared session memory."""
    human_ids = {author_id for author_id in authors.values() if author_id is not None}
    sole_author_name = None
    if len(human_ids) == 1:
        sole_author_id = next(iter(human_ids))
        sole_author_name = next(name for name, author_id in authors.items() if author_id == sole_author_id)

    guarded = []
    dropped = []
    for index, op in enumerate(operations, start=1):
        if op.scope == "user" and resolve_user_id(op.user, authors) is None:
            dropped.append(f"op {index}: user '{op.user}' does not resolve to a human author in these messages")
            logger.warning("Dropping semantic user op for unknown author '%s' (op %d)", op.user, index)
            continue
        if op.scope == "session":
            text = "\n".join(value for value in (op.content, op.line) if value)
            if _FIRST_PERSON_MARKER.search(text):
                if sole_author_name is None:
                    dropped.append(f"op {index}: first-person session fact dropped because there is not exactly one human author")
                    logger.warning("Dropping first-person semantic session op %d; messages do not have exactly one human author", index)
                    continue
                logger.warning("Rerouting first-person semantic session op %d to user scope for %s", index, sole_author_name)
                op = op.model_copy(update={"scope": "user", "user": sole_author_name})
        guarded.append(op)
    return guarded, dropped


def _message_value(message: Mapping, key: str, default=None):
    if not isinstance(message, Mapping):
        raise TypeError("semantic extractor messages must be plain mappings")
    return message.get(key, default)


def build_author_map(messages: list[Mapping]) -> dict[str, uuid.UUID | None]:
    authors: dict[str, uuid.UUID | None] = {}
    author_names_by_key: dict[str, str] = {}
    for message in messages:
        if _message_value(message, "role") != "user":
            continue
        name = str(_message_value(message, "author_name", ""))
        raw_user_id = _message_value(message, "author_id")
        if not name or raw_user_id is None:
            continue
        user_id = uuid.UUID(str(raw_user_id))
        normalized_name = name.casefold()
        key = author_names_by_key.get(normalized_name)
        if key is None:
            author_names_by_key[normalized_name] = name
            authors[name] = user_id
        elif authors[key] != user_id:
            authors[key] = None
    return authors


def build_extractor_input(messages: list[Mapping], existing_docs: list[Mapping]) -> tuple[dict[str, uuid.UUID | None], str]:
    """Build the bounded prompt and author map using plain data only."""
    authors = build_author_map(messages)

    message_lines = []
    message_chars = 0
    for message in messages:
        role = _message_value(message, "role")
        if role not in {"user", "assistant"}:
            continue
        content = str(_message_value(message, "content", "") or "")
        if len(content) > 1200:
            content = content[:1200] + "…"
        author_name = str(_message_value(message, "author_name", ""))[:200]
        created_at = _message_value(message, "created_at", "")
        if isinstance(created_at, datetime):
            created_at = created_at.strftime("%Y-%m-%d %H:%M")
        else:
            created_at = str(created_at)[:16]
        line = f"[{created_at}] {author_name}: {content}"
        extra = len(line) + (1 if message_lines else 0)
        if message_chars + extra > MESSAGE_INPUT_CHARS:
            break
        message_lines.append(line)
        message_chars += extra

    doc_lines = []
    docs_chars = 0
    for doc in existing_docs:
        block = (
            f"[{_message_value(doc, 'scope_type')}:{_message_value(doc, 'scope_id')}] "
            f"{_message_value(doc, 'path')} — {_message_value(doc, 'description')}\n"
            f"{_message_value(doc, 'content', '')}"
        )
        extra = len(block) + (1 if doc_lines else 0)
        if docs_chars + extra > EXISTING_DOC_INPUT_CHARS:
            continue
        doc_lines.append(block)
        docs_chars += extra

    prompt_input = "Episode messages:\n" + "\n".join(message_lines) + "\n\nExisting memory docs:\n" + "\n\n".join(doc_lines)
    return authors, prompt_input


def normalize_doc_content(content: str) -> str:
    lines = []
    seen = set()
    for raw_line in content.splitlines():
        line = normalize_memory_text(raw_line).strip()
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
        line = normalize_memory_text(op.line or "").strip()
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
    if not messages:
        logger.info("Semantic extraction received no messages for session %s; continuing existing flow", session_id)
    if not EXTRACTOR_ENABLED:
        logger.info("Semantic extraction disabled for session %s; exiting early", session_id)
        return
    logger.info("Starting semantic extraction for session %s with %d messages", session_id, len(messages))
    db = None
    try:
        session_id = uuid.UUID(str(session_id))
        from app.db.session import SessionLocal
        from app.features.group_session.talon import client
        from app.features.users.usage import record_usage

        db = SessionLocal()
        authors = build_author_map(messages)
        user_ids = {user_id for user_id in authors.values() if user_id is not None}
        filters = [((MemoryDoc.scope_type == "session") & (MemoryDoc.scope_id == session_id))]
        if user_ids:
            filters.append((MemoryDoc.scope_type == "user") & MemoryDoc.scope_id.in_(user_ids))
        existing_rows = db.query(MemoryDoc).filter(or_(*filters)).order_by(MemoryDoc.updated_at.desc()).all()
        existing_docs = [
            {
                "scope_type": doc.scope_type,
                "scope_id": str(doc.scope_id),
                "path": doc.path,
                "description": doc.description,
                "content": doc.content,
            }
            for doc in existing_rows
        ]
        existing_rows = None
        logger.info(
            "Loaded %d existing semantic docs for session %s",
            len(existing_docs),
            session_id,
        )
        authors, prompt_input = build_extractor_input(messages, existing_docs)

        request = {
            "model": "openai/gpt-oss-120b",
            "max_tokens": 2000,
            "extra_body": {"include_reasoning": False, "reasoning_effort": "low"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt_input},
            ],
        }
        from openai import BadRequestError
        try:
            response = await client.chat.completions.create(
                **request,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "semantic_memory_ops",
                        "strict": True,
                        "schema": _OP_SCHEMA,
                    },
                },
            )
        except BadRequestError:
            logger.info("Groq rejected semantic json_schema output for session %s; retrying json_object", session_id)
            try:
                response = await client.chat.completions.create(
                    **request,
                    response_format={"type": "json_object"},
                )
            except BadRequestError:
                logger.info("Groq rejected semantic json_object output for session %s; retrying prompt-only JSON", session_id)
                response = await client.chat.completions.create(**request)
        choice = response.choices[0]
        raw = choice.message.content or ""
        if not raw.strip():
            logger.warning(
                "Semantic extractor returned empty content for session %s (finish_reason=%s, usage=%s)",
                session_id,
                choice.finish_reason,
                response.usage,
            )
            return
        logger.info("Semantic extractor raw output for session %s (first 1500 chars): %s", session_id, raw[:1500])
        payload, decode_error = _decode_semantic_payload(raw)
        if decode_error:
            operations, dropped, parse_error = [], [], decode_error
            normalization_dropped = []
        else:
            payload, normalization_dropped = _normalize_ops_before_validation(payload, db, session_id, authors)
            operations, dropped, parse_error = _parse_semantic_ops_with_diagnostics(payload)
            dropped = normalization_dropped + dropped
        operations, scope_dropped = guard_semantic_op_scopes(operations, authors)
        dropped.extend(scope_dropped)
        if parse_error:
            logger.info("Semantic extractor JSON parse failure for session %s: %s", session_id, parse_error)
        for reason in dropped:
            logger.info("Dropped invalid semantic operation for session %s: %s", session_id, reason)
        logger.info(
            "Semantic extractor parsed %d ops and dropped %d invalid ops for session %s",
            len(operations),
            len(dropped),
            session_id,
        )
        applied = []
        for op in operations:
            if apply_semantic_op(db, op, session_id, authors, triggering_user_id):
                applied.append(op)
                logger.info(
                    "Applied semantic operation op=%s scope=%s path=%s session=%s",
                    op.op,
                    op.scope,
                    op.path,
                    session_id,
                )
        counts = {op_type: sum(op.op == op_type for op in applied) for op_type in ("create", "update", "append")}
        logger.info(
            "Applied %d semantic ops for session %s (create=%d, update=%d, append=%d)",
            len(applied),
            session_id,
            counts["create"],
            counts["update"],
            counts["append"],
        )
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
