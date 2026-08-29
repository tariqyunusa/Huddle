"""
Group reasoning session WebSocket endpoint — Brick 5: real Claude integration.
"""
import json
import uuid
import os

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from typing import List

from app.db.session import SessionLocal, get_db
from .connection_manager import manager
from .models import GroupMessage, GroupSession, GroupParticipant
from .schemas import CreateSessionRequest, SessionResponse, ParticipantResponse
from .claude_client import build_transcript, call_claude
from app.features.users.jwt import decode_access_token

from app.features.users.dependencies import get_current_user
from app.features.users.models import User

redis_client = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)

router = APIRouter()

LOCK_TIMEOUT_SECONDS = 30


@router.post("/sessions", response_model=SessionResponse)
def create_session(payload: CreateSessionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
    
    display_name = current_user.display_name
    user_id = str(current_user.id)
    await manager.connect(session_id, websocket)

    # Record participation (if this user hasn't joined this session before)
    if user_id:
        db = SessionLocal()
        try:
            existing = (
                db.query(GroupParticipant)
                .filter(
                    GroupParticipant.session_id == session_id,
                    GroupParticipant.user_id == user_id,
                )
                .first()
            )
            if not existing:
                participant = GroupParticipant(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    user_id=user_id,
                    display_name=display_name,
                )
                db.add(participant)
                db.commit()
        finally:
            db.close()

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
            content = data.get("content", "").strip()
            if not content:
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
                    transcript = build_transcript(history)
                finally:
                    db.close()

                reply_text = await call_claude(transcript)

                db = SessionLocal()
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
                await lock.release()

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