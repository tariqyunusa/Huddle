import logging
import os
from time import perf_counter
import uuid
from datetime import datetime

from app.features.group_session.models import GroupMessage
from app.features.group_session.models import GroupDocument
from .embeddings import embed
from .models import Episode

logger = logging.getLogger(__name__)
EPISODE_EVERY_N_MESSAGES = int(os.getenv("EPISODE_EVERY_N_MESSAGES", "4"))
EPISODE_SUMMARY_ENABLED = os.getenv("EPISODE_SUMMARY_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
_in_progress: set[str] = set()


def should_create_episode(message_count: int, threshold: int = EPISODE_EVERY_N_MESSAGES) -> bool:
    return message_count >= threshold


def episode_is_before_live_window(last_message_at: datetime, exclude_after: datetime | None) -> bool:
    return exclude_after is None or last_message_at < exclude_after


def build_memory_context(episodes, max_chars: int = 3000) -> str:
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
    import asyncio
    from openai import APIStatusError, RateLimitError

    if not EPISODE_SUMMARY_ENABLED:
        return
    await asyncio.sleep(10)

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

        input_budget = 9000
        included_messages = []
        transcript_lines = []
        transcript_length = 0
        for message in messages:
            content = message.content or ""
            if len(content) > 1200:
                content = content[:1200] + "…"
            line = f"[{message.created_at:%Y-%m-%d %H:%M}] {message.author_name}: {content}"
            added_length = len(line) + (1 if transcript_lines else 0)
            if transcript_length + added_length > input_budget:
                break
            transcript_lines.append(line)
            included_messages.append(message)
            transcript_length += added_length
        transcript = "\n".join(transcript_lines)
        doc_titles = [row[0] for row in db.query(GroupDocument.title).filter(GroupDocument.session_id == session_id).all()]
        if doc_titles:
            title_prefix = "\nShared document titles: "
            remaining = input_budget - len(transcript) - len(title_prefix)
            if remaining > 0:
                titles = ", ".join(doc_titles)
                if len(titles) > remaining:
                    titles = titles[:max(0, remaining - 1)] + "…"
                transcript += title_prefix + titles
        summary_started = perf_counter()
        response = await client.chat.completions.create(
            model="openai/gpt-oss-120b", max_tokens=300,
            extra_body={"include_reasoning": False},
            messages=[
                {"role": "system", "content": "Summarize this session segment for episodic memory. Keep decisions, open questions, who said what when it matters, and shared-document titles. Be concise and factual."},
                {"role": "user", "content": transcript},
            ],
        )
        logger.info("Episode Groq summary call completed in %.2f seconds", perf_counter() - summary_started)
        summary = (response.choices[0].message.content or "").strip()
        if not summary:
            return
        vector = (await embed([summary]))[0]
        episode = Episode(
            id=uuid.uuid4(), scope_type="session", scope_id=session_id, session_id=session_id,
            summary=summary, embedding=vector, first_message_at=included_messages[0].created_at,
            last_message_at=included_messages[-1].created_at, message_count=len(included_messages),
        )
        db.add(episode)
        db.commit()
        tokens = response.usage.total_tokens if response.usage else 0
        record_usage(triggering_user_id, tokens, db)
    except RateLimitError:
        db.rollback()
        logger.warning("Episode summary rate limited for session %s", session_id)
    except APIStatusError as exc:
        if exc.status_code in (413, 429):
            db.rollback()
            logger.warning("Episode summary rejected with status %s for session %s", exc.status_code, session_id)
        else:
            db.rollback()
            logger.exception("Failed to create episodic memory for session %s", session_id)
    except Exception:
        db.rollback()
        logger.exception("Failed to create episodic memory for session %s", session_id)
    finally:
        db.close()
        _in_progress.discard(key)


async def retrieve_episodes(session_id, query_text: str, k: int = 3, exclude_after: datetime | None = None):
    started = perf_counter()
    try:
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
    finally:
        logger.info("Episode retrieval completed in %.2f seconds", perf_counter() - started)
