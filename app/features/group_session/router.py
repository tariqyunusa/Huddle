"""
Group reasoning session WebSocket endpoint — Brick 5: real Claude integration.
"""
import json
import asyncio
import uuid
import os
import logging

import redis.asyncio as aioredis
from redis.exceptions import LockNotOwnedError
from openai import APIStatusError, RateLimitError
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from typing import List
from fastapi.responses import StreamingResponse

from app.db.session import SessionLocal, get_db
from .connection_manager import manager
from .export import generate_document_markdown, markdown_to_docx, markdown_to_pdf
from .models import GroupMessage, GroupSession, GroupParticipant, GroupDocument, GeneratedDocument, SessionInviteLink
from .schemas import CreateSessionRequest, InviteRequest, RedeemInviteLinkRequest, SessionResponse, ParticipantResponse, UpdateSessionRequest
from .talon import build_transcript, fit_chat_context_budget, generate_reply, generate_title, build_document_context
from app.features.users.jwt import decode_access_token
from app.features.users.email import send_session_invite_email
from app.features.users.dependencies import get_current_user, get_verified_user
from app.features.users.models import User
from app.features.users.usage import check_usage_allowed, record_usage, PLAN_TOKEN_LIMITS
from app.features.users.tokens import generate_refresh_token, hash_token
from app.features.memory.episodic import maybe_create_episode, retrieve_episodes, build_memory_context
from app.features.memory.models import Episode, MemoryDoc
from app.features.memory.semantic import build_semantic_context, visible_semantic_docs
from app.features.memory.procedures import build_procedural_context, load_procedural_docs

redis_client = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)

router = APIRouter()
logger = logging.getLogger(__name__)

LOCK_TIMEOUT_SECONDS = 90


@router.post("/sessions/{session_id}/invite-link")
def create_session_invite_link(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    is_member = (
        session.created_by == current_user.id
        or db.query(GroupParticipant)
        .filter(
            GroupParticipant.session_id == session_id,
            GroupParticipant.user_id == current_user.id,
        )
        .first() is not None
    )
    if not is_member:
        raise HTTPException(status_code=403, detail="Only session members can invite others")

    raw_token = generate_refresh_token()
    db.add(SessionInviteLink(
        session_id=session_id,
        created_by=current_user.id,
        token_hash=hash_token(raw_token),
    ))
    db.commit()
    return {"token": raw_token}


@router.post("/sessions/invite-link/redeem", response_model=SessionResponse)
def redeem_session_invite_link(
    payload: RedeemInviteLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    invite = db.query(SessionInviteLink).filter(
        SessionInviteLink.token_hash == hash_token(payload.token)
    ).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite link is invalid")

    session = db.query(GroupSession).filter(GroupSession.id == invite.session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    is_member = (
        session.created_by == current_user.id
        or db.query(GroupParticipant)
        .filter(
            GroupParticipant.session_id == session.id,
            GroupParticipant.user_id == current_user.id,
        )
        .first() is not None
    )
    if not is_member:
        db.add(GroupParticipant(
            session_id=session.id,
            user_id=current_user.id,
            display_name=current_user.display_name,
        ))
        db.commit()

    return session


@router.post("/sessions", response_model=SessionResponse)
def create_session(payload: CreateSessionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_verified_user)):
    session = GroupSession(
        id=uuid.uuid4(),
        title=payload.title,
        created_by=current_user.id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/sessions", response_model=List[SessionResponse])
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    participant_session_ids = (
        db.query(GroupParticipant.session_id)
        .filter(GroupParticipant.user_id == current_user.id)
        .subquery()
    )
    sessions = (
        db.query(GroupSession)
        .filter(
            (GroupSession.created_by == current_user.id)
            | (GroupSession.id.in_(participant_session_ids))
        )
        .order_by(GroupSession.created_at.desc())
        .all()
    )
    return sessions


@router.websocket("/ws/session/{session_id}")
async def group_session_ws(websocket: WebSocket, session_id: str):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return
    
    try:
        user_id = decode_access_token(token)
    except Exception:
        await websocket.close(code=4001)
        return
    
    db = SessionLocal()
    try:
        current_user = db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()
        
    if not current_user:
        await websocket.close(code=4001)
        return
    
    if not current_user.email_verified:
        await websocket.close(code=4005)
        return
    
    display_name = current_user.display_name
    user_id = str(current_user.id)
    
    db = SessionLocal()
    try:
        session_obj = db.query(GroupSession).filter(GroupSession.id == session_id).first()
        if not session_obj:
            await websocket.close(code=4004)
            return
        
        is_member = (
            str(session_obj.created_by) == user_id
            or db.query(GroupParticipant)
            .filter(GroupParticipant.session_id == session_id, GroupParticipant.user_id == user_id)
            .first() is not None
        )
        
        if not is_member:
            await websocket.close(code=4003)
            return
        
        # Only auto-record participation for the creator's first connect —
        # everyone else must already be a participant via invite.
        if str(session_obj.created_by) == user_id:
            existing = (
                db.query(GroupParticipant)
                .filter(GroupParticipant.session_id == session_id, GroupParticipant.user_id == user_id)
                .first()
            )
            if not existing:
                db.add(GroupParticipant(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    user_id=user_id,
                    display_name=display_name,
                ))
                db.commit()
    finally:
        db.close()
    await manager.connect(session_id, websocket)

    # Replay history to the newly connected client
    db = SessionLocal()
    try:
        history = (
            db.query(GroupMessage)
            .filter(GroupMessage.session_id == session_id)
            .order_by(GroupMessage.created_at)
            .all()
        )
        for msg in history:
            await websocket.send_json({
                "type": "message",
                "author": msg.author_name,
                "content": msg.content,
            })
    finally:
        db.close()

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            
            if data.get("type") == "share_document":
                doc_title = data.get("title", "untitled document").strip() or "Untitled document"
                doc_content = data.get("content", "").strip()
                if not doc_content:
                    continue
                
                db = SessionLocal()
                try:
                    doc = GroupDocument(
                        id=uuid.uuid4(),
                        session_id=session_id,
                        added_by=current_user.id,
                        title=doc_title,
                        content=doc_content,
                    )
                    db.add(doc)
                    db.commit()
                finally:
                    db.close()
                    
                await manager.broadcast(session_id, {
                    "type": "document_shared",
                    "title": doc_title,
                    "shared_by": display_name,
                })
                continue
            
            content = data.get("content", "").strip()
            if not content:
                continue
            
            # Check usage limit before persisting/broadcasting
            db = SessionLocal()
            try:
                allowed = check_usage_allowed(current_user.id, db)
            finally:
                db.close()

            if not allowed:
                await manager.broadcast(session_id, {
                    "type": "error",
                    "content": "You've hit your usage limit for this window. It resets in a few hours.",
                })
                continue
            

            # Persist the user's message (unlocked — everyone's prompt lands immediately)
            db = SessionLocal()
            try:
                msg = GroupMessage(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    role="user",
                    author_id=user_id,
                    author_name=display_name,
                    content=content,
                )
                db.add(msg)
                db.commit()
            finally:
                db.close()

            await manager.broadcast(session_id, {
                "type": "message",
                "author": display_name,
                "content": content,
            })

            # Auto-title the session on its first message
            db = SessionLocal()
            try:
                message_count = (
                    db.query(GroupMessage)
                    .filter(GroupMessage.session_id == session_id)
                    .count()
                )
                if message_count == 1:
                    session_obj = db.query(GroupSession).filter(GroupSession.id == session_id).first()
                    if session_obj and (not session_obj.title or session_obj.title == "New session"):
                        try:
                            new_title = await generate_title(content)
                            session_obj.title = new_title
                            db.commit()
                            await manager.broadcast(session_id, {
                                "type": "session_title",
                                "title": new_title,
                            })
                        except Exception as e:
                            print(f"TITLE GENERATION FAILED: {e}")
            finally:
                db.close()

            # Acquire per-session lock before calling Claude
            lock_key = f"group_session_lock:{session_id}"
            lock = redis_client.lock(lock_key, timeout=LOCK_TIMEOUT_SECONDS)
            got_lock = await lock.acquire(blocking=True, blocking_timeout=LOCK_TIMEOUT_SECONDS)
            if not got_lock:
                await manager.broadcast(session_id, {"type": "error", "content": "Session busy, try again."})
                continue

            try:
                await manager.broadcast(session_id, {"type": "thinking"})

                db = SessionLocal()
                try:
                    history = (
                        db.query(GroupMessage)
                        .filter(GroupMessage.session_id == session_id)
                        .order_by(GroupMessage.created_at)
                        .all()
                    )
                    documents= (
                        db.query(GroupDocument)
                        .filter(GroupDocument.session_id == session_id)
                        .order_by(GroupDocument.created_at)
                        .all()
                    )
                    transcript = build_transcript(history)
                finally:
                    db.close()
                    
                doc_context = build_document_context(documents)
                memory_context = ""
                semantic_context = ""
                procedural_context = ""
                latest_human_message = next((m for m in reversed(history) if m.role == "user" and m.author_id), None)
                try:
                    semantic_session_id = uuid.UUID(str(session_id))
                    semantic_db = SessionLocal()
                    try:
                        semantic_query = semantic_db.query(MemoryDoc).filter(
                            MemoryDoc.kind == "semantic",
                            (MemoryDoc.scope_type == "session") & (MemoryDoc.scope_id == semantic_session_id)
                        )
                        if latest_human_message:
                            semantic_query = semantic_db.query(MemoryDoc).filter(
                                MemoryDoc.kind == "semantic",
                                ((MemoryDoc.scope_type == "session") & (MemoryDoc.scope_id == semantic_session_id))
                                | ((MemoryDoc.scope_type == "user") & (MemoryDoc.scope_id == latest_human_message.author_id))
                            )
                        semantic_docs = semantic_query.order_by(MemoryDoc.updated_at.desc()).all()
                    finally:
                        semantic_db.close()
                    semantic_docs = visible_semantic_docs(
                        semantic_docs,
                        semantic_session_id,
                        latest_human_message.author_id if latest_human_message else None,
                    )
                    if latest_human_message:
                        for semantic_doc in semantic_docs:
                            if semantic_doc.scope_type == "user":
                                semantic_doc._semantic_display_name = latest_human_message.author_name
                    semantic_context = build_semantic_context(semantic_docs)
                except Exception:
                    logger.exception("Failed to retrieve semantic memory")
                try:
                    procedural_db = SessionLocal()
                    try:
                        procedural_docs = load_procedural_docs(
                            procedural_db,
                            uuid.UUID(str(session_id)),
                            latest_human_message.author_id if latest_human_message else None,
                        )
                    finally:
                        procedural_db.close()
                    procedural_context = build_procedural_context(procedural_docs)
                except Exception:
                    logger.exception("Failed to retrieve procedural memory")
                latest_user_message = next((m.content for m in reversed(history) if m.role == "user"), "")
                live_window = history[-20:]
                exclude_after = live_window[0].created_at if live_window else None
                try:
                    episodes = await asyncio.wait_for(
                        retrieve_episodes(session_id, latest_user_message, exclude_after=exclude_after),
                        timeout=5,
                    )
                    memory_context = build_memory_context(episodes)
                except Exception:
                    logger.exception("Failed to retrieve episodic memory")

                transcript, memory_context, semantic_context, procedural_context = fit_chat_context_budget(
                    transcript,
                    doc_context,
                    memory_context,
                    semantic_context,
                    procedural_context,
                )

                transcript_chars = sum(len(message.get("content", "")) for message in transcript)
                logger.info(
                    "Talon input size (approx tokens): transcript=%d, document_context=%d, memory_context=%d, semantic_context=%d, procedural_context=%d",
                    transcript_chars // 4,
                    len(doc_context) // 4,
                    len(memory_context) // 4,
                    len(semantic_context) // 4,
                    len(procedural_context) // 4,
                )
                final_messages = []
                if semantic_context:
                    final_messages.append({"role": "user", "content": semantic_context})
                if memory_context:
                    final_messages.append({"role": "user", "content": memory_context})
                if doc_context:
                    final_messages.append({"role": "user", "content": doc_context})
                final_messages.extend(transcript)

                try:
                    reply_text, tokens_used, searched, doc_request = await generate_reply(final_messages, extra_system=procedural_context or None)
                except Exception as e:
                    logger.exception("generate_reply failed (%s): %s", type(e).__name__, e)
                    if isinstance(e, RateLimitError) or (isinstance(e, APIStatusError) and e.status_code in (413, 429)):
                        error_content = "Talon hit its rate limit. Wait a minute and try again."
                    else:
                        error_content = "Talon is temporarily overloaded. Try a shorter message or wait a moment."
                    await manager.broadcast(session_id, {
                        "type": "error",
                        "content": error_content,
                    })
                    continue
                
                if doc_request:
                    db = SessionLocal()
                    try:
                        record_usage(current_user.id, tokens_used, db)
                        markdown_text, doc_tokens = await generate_document_markdown(final_messages, doc_request["instructions"])
                        record_usage(current_user.id, doc_tokens, db)
                        doc_record = GeneratedDocument(
                            id=uuid.uuid4(),
                            session_id=session_id,
                            title=doc_request["title"],
                            markdown_content=markdown_text,
                        )
                        db.add(doc_record)
                        db.commit()
                        db.refresh(doc_record)
                    finally:
                        db.close()
                        
                    await manager.broadcast(session_id, {
                        "type": "document_ready",
                        "title": doc_request["title"],
                        "document_id": str(doc_record.id),
                    })
                    continue
                        

                db = SessionLocal()
                try:
                    new_total = record_usage(current_user.id, tokens_used, db)
                    await manager.broadcast(session_id, {
                        "type": "usage",
                        "tokens_used" : new_total,
                        "tokens_limit": PLAN_TOKEN_LIMITS.get(current_user.plan, PLAN_TOKEN_LIMITS["free"]),
                        "searched": searched
                    })
                finally:
                    db.close()
                    
                db= SessionLocal()
                try:
                    assistant_msg = GroupMessage(
                        id=uuid.uuid4(),
                        session_id=session_id,
                        role="assistant",
                        author_id=None,
                        author_name="Talon",
                        content=reply_text,
                    )
                    db.add(assistant_msg)
                    db.commit()
                finally:
                    db.close()

                await manager.broadcast(session_id, {
                    "type": "message",
                    "author": "Talon",
                    "content": reply_text,
                })
            finally:
                try:
                    await lock.release()
                except LockNotOwnedError:
                    import logging
                    logging.getLogger(__name__).warning("Session lock expired before release for session %s", session_id)
                finally:
                    asyncio.create_task(maybe_create_episode(session_id, current_user.id))

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)
        

        
@router.get("/sessions/{session_id}/participants", response_model=List[ParticipantResponse])
def list_participants(session_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    participants = (
        db.query(GroupParticipant)
        .filter(GroupParticipant.session_id == session_id)
        .all()
    )
    return participants

@router.patch("/sessions/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: uuid.UUID,
    payload: UpdateSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.title = payload.title
    db.commit()
    db.refresh(session)
    return session
    

@router.post("/sessions/{session_id}/invite")
def invite_to_session(
    session_id: uuid.UUID,
    payload: InviteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    is_member = (
        session.created_by == current_user.id
        or db.query(GroupParticipant)
            .filter(GroupParticipant.session_id == session_id, GroupParticipant.user_id == current_user.id)
            .first() is not None
    )
    if not is_member:
        raise HTTPException(status_code=403, detail="Only session members can invite others")

    if payload.user_id:
        target = db.query(User).filter(User.id == payload.user_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")
        target_email = target.email

        existing = (
            db.query(GroupParticipant)
            .filter(GroupParticipant.session_id == session_id, GroupParticipant.user_id == target.id)
            .first()
        )
        if not existing:
            db.add(GroupParticipant(
                id=uuid.uuid4(),
                session_id=session_id,
                user_id=target.id,
                display_name=target.display_name,
            ))
            db.commit()
    elif payload.email:
        target_email = payload.email
        # No account yet — can't pre-authorize a join. See note below.
    else:
        raise HTTPException(status_code=400, detail="Provide an email or user_id")

    raw_token = generate_refresh_token()
    db.add(SessionInviteLink(
        session_id=session_id,
        created_by=current_user.id,
        token_hash=hash_token(raw_token),
    ))
    db.commit()

    try:
        send_session_invite_email(
            target_email,
            current_user.display_name,
            session_id,
            session.title,
            raw_token,
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Email invites aren't fully set up yet - try sharing the link instead."
        )
    return {"message": "Invite sent"}

@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.created_by != current_user.id:
        raise HTTPException(status_code=403, detail="Only the session creator can delete it")

    db.query(Episode).filter(Episode.session_id == session_id).delete()
    db.query(MemoryDoc).filter(MemoryDoc.scope_type == "session", MemoryDoc.scope_id == session_id).delete()
    db.query(GroupMessage).filter(GroupMessage.session_id == session_id).delete()
    db.query(GroupParticipant).filter(GroupParticipant.session_id == session_id).delete()
    db.query(SessionInviteLink).filter(SessionInviteLink.session_id == session_id).delete()
    db.query(GroupDocument).filter(GroupDocument.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"message": "Session deleted"}


@router.post("/sessions/{session_id}/export")
async def export_session(
    session_id: uuid.UUID,
    format: str,  # "docx" or "pdf"
    instructions: str = "Summarize the key points and decisions from this discussion.",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
):
    if format not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="format must be 'docx' or 'pdf'")

    session = db.query(GroupSession).filter(GroupSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    is_member = (
        session.created_by == current_user.id
        or db.query(GroupParticipant)
            .filter(GroupParticipant.session_id == session_id, GroupParticipant.user_id == current_user.id)
            .first() is not None
    )
    if not is_member:
        raise HTTPException(status_code=403, detail="Not a member of this session")

    allowed = check_usage_allowed(current_user.id, db)
    if not allowed:
        raise HTTPException(status_code=429, detail="Usage limit reached")

    history = (
        db.query(GroupMessage)
        .filter(GroupMessage.session_id == session_id)
        .order_by(GroupMessage.created_at)
        .all()
    )
    transcript = build_transcript(history)

    markdown_text = await generate_document_markdown(transcript, instructions)

    if format == "docx":
        file_buffer = markdown_to_docx(markdown_text)
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"{session.title or 'session'}.docx"
    else:
        file_buffer = markdown_to_pdf(markdown_text)
        media_type = "application/pdf"
        filename = f"{session.title or 'session'}.pdf"

    return StreamingResponse(
        file_buffer,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
