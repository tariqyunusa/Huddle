import unicodedata
from datetime import datetime
from typing import Mapping

from sqlalchemy import or_

from app.features.memory.models import MemoryDoc
from app.features.users.models import User

MAX_PROCEDURAL_DOCS = 5
MAX_PROCEDURAL_CONTENT_CHARS = 4000
MAX_PROCEDURAL_DESCRIPTION_CHARS = 150
MAX_PROCEDURAL_PATH_CHARS = 80


def normalize_procedural_text(text: str) -> str:
    normalized = []
    for char in text:
        category = unicodedata.category(char)
        if category == "Pd" or char == "−":
            normalized.append("-")
        elif category != "Cc" or char == "\n":
            normalized.append(char)
    return "".join(normalized).replace("\u00a0", " ").replace("\u2007", " ").replace("\u202f", " ")


def validate_procedural_path(path: str) -> str:
    if (
        len(path) > MAX_PROCEDURAL_PATH_CHARS
        or not path.startswith("procedures/")
        or not path.endswith(".md")
        or ".." in path
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789/_-." for char in path)
        or path == "procedures/.md"
    ):
        raise ValueError("Path must be a procedures/*.md slug using lowercase letters, digits, '/', '_', '-' or '.' (max 80 characters)")
    return path


def _value(doc: MemoryDoc | Mapping, key: str, default=None):
    return doc.get(key, default) if isinstance(doc, Mapping) else getattr(doc, key, default)


def load_procedural_docs(db, session_id, speaker_user_id):
    query = db.query(MemoryDoc).filter(MemoryDoc.kind == "procedural")
    scopes = [(MemoryDoc.scope_type == "session") & (MemoryDoc.scope_id == session_id)]
    if speaker_user_id is not None:
        scopes.append((MemoryDoc.scope_type == "user") & (MemoryDoc.scope_id == speaker_user_id))
    docs = query.filter(or_(*scopes)).order_by(MemoryDoc.updated_at.desc()).all()
    display_name = None
    if speaker_user_id is not None:
        speaker = db.query(User).filter(User.id == speaker_user_id).first()
        display_name = speaker.display_name if speaker else "you"
    snapshots = [
        {
            "id": doc.id,
            "scope_type": doc.scope_type,
            "scope_id": doc.scope_id,
            "kind": doc.kind,
            "path": doc.path,
            "description": doc.description,
            "content": doc.content,
            "version": doc.version,
            "updated_at": doc.updated_at,
            "display_name": display_name if doc.scope_type == "user" else None,
        }
        for doc in docs
    ]
    return visible_procedural_docs(snapshots, session_id, speaker_user_id)


def visible_procedural_docs(docs, session_id, speaker_user_id):
    return [
        doc for doc in docs
        if (
            _value(doc, "kind") == "procedural"
            and (
                (_value(doc, "scope_type") == "session" and _value(doc, "scope_id") == session_id)
                or (
                    speaker_user_id is not None
                    and _value(doc, "scope_type") == "user"
                    and _value(doc, "scope_id") == speaker_user_id
                )
            )
        )
    ]


def build_procedural_context(docs, max_chars: int = 2500) -> str:
    selected = list(docs)
    selected.sort(key=lambda doc: _value(doc, "updated_at") or datetime.min, reverse=True)
    while selected:
        session_docs = [doc for doc in selected if _value(doc, "scope_type") == "session"]
        user_docs = [doc for doc in selected if _value(doc, "scope_type") == "user"]
        sections = ["Standing instructions"]
        if session_docs:
            sections.append("Session rules\n" + "\n\n".join(
                f"{_value(doc, 'path')}\n{_value(doc, 'content', '')}" for doc in session_docs
            ))
        if user_docs:
            name = next((_value(doc, "display_name") for doc in user_docs if _value(doc, "display_name")), "you")
            sections.append(f"Personal instructions for {name}\n" + "\n\n".join(
                f"{_value(doc, 'path')}\n{_value(doc, 'content', '')}" for doc in user_docs
            ))
        context = "\n\n".join(sections)
        if len(context) <= max_chars:
            return context if len(selected) > 0 and (session_docs or user_docs) else ""
        oldest = min(selected, key=lambda doc: _value(doc, "updated_at") or datetime.min)
        selected.remove(oldest)
    return ""
