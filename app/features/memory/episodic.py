import logging
import os
import uuid
from datetime import datetime

from app.features.group_session.models import GroupMessage
from app.features.group_session.models import GroupDocument
from .embeddings import embed
from .models import Episode

logger = logging.getLogger(__name__)
EPISODE_EVERY_N_MESSAGES = int(os.getenv("EPISODE_EVERY_N_MESSAGES", "20"))
_in_progress: set[str] = set()


def should_create_episode(message_count: int, threshold: int = EPISODE_EVERY_N_MESSAGES) -> bool:
    return message_count >= threshold


def episode_is_before_live_window(last_message_at: datetime, exclude_after: datetime | None) -> bool:
    return exclude_after is None or last_message_at < exclude_after


def build_memory_context(episodes, max_chars: int = 6000) -> str:
    """Format ranked episodes, removing lowest ranked entries first to fit."""
    selected = list(episodes)
    while selected:
        blocks = ["[Memory — earlier in this session]"]
        for episode in selected:
            start = episode.first_message_at.strftime("%Y-%m-%d")
            end = episode.last_message_at.strftime("%Y-%m-%d")
            blocks.append(f"{start} to {end}: {episode.summary}")
        result = "\n".join(blocks)
        if len(result) <= max_chars:
            return result
        selected.pop()
    return ""


async def maybe_create_episode(session_id, triggering_user_id):
    from app.db.session import SessionLocal
    from app.features.group_session.talon import client
    from app.features.users.usage import record_usage

    key = str(session_id)
    if key in _in_progress:
        return
    _in_progress.add(key)
    db = SessionLocal()
    try:
        latest = db.query(Episode).filter(Episode.session_id == session_id).order_by(Episode.last_message_at.desc()).first()
        query = db.query(GroupMessage).filter(GroupMessage.session_id == session_id)
        if latest:
            query = query.filter(GroupMessage.created_at > latest.last_message_at)
        messages = query.order_by(GroupMessage.created_at).all()
        if not should_create_episode(len(messages)):
            return

        transcript = "\n".join(f"[{m.created_at:%Y-%m-%d %H:%M}] {m.author_name}: {m.content}" for m in messages)
        doc_titles = [row[0] for row in db.query(GroupDocument.title).filter(GroupDocument.session_id == session_id).all()]
        if doc_titles:
            transcript += "\nShared document titles: " + ", ".join(doc_titles)
        response = await client.chat.completions.create(
            model="openai/gpt-oss-120b", max_tokens=400,
            extra_body={"include_reasoning": False},
            messages=[
                {"role": "system", "content": "Summarize this session segment for episodic memory. Keep decisions, open questions, who said what when it matters, and shared-document titles. Be concise and factual."},
                {"role": "user", "content": transcript},
            ],
        )
        summary = (response.choices[0].message.content or "").strip()
        if not summary:
            return
        vector = (await embed([summary]))[0]
        episode = Episode(
            id=uuid.uuid4(), scope_type="session", scope_id=session_id, session_id=session_id,
            summary=summary, embedding=vector, first_message_at=messages[0].created_at,
            last_message_at=messages[-1].created_at, message_count=len(messages),
        )
        db.add(episode)
        db.commit()
        tokens = response.usage.total_tokens if response.usage else 0
        record_usage(triggering_user_id, tokens, db)
    except Exception:
        db.rollback()
        logger.exception("Failed to create episodic memory for session %s", session_id)
    finally:
        db.close()
        _in_progress.discard(key)


async def retrieve_episodes(session_id, query_text: str, k: int = 3, exclude_after: datetime | None = None):
    if not query_text:
        return []
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        vector = (await embed([query_text]))[0]
        query = db.query(Episode).filter(
            Episode.scope_type == "session", Episode.scope_id == session_id,
        )
        if exclude_after is not None:
            query = query.filter(Episode.last_message_at < exclude_after)
        return query.order_by(Episode.embedding.cosine_distance(vector)).limit(k).all()
    finally:
        db.close()
