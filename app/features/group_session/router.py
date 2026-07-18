"""
Group reasoning session WebSocket endpoint — Brick 4: Redis turn-lock.
"""
import asyncio
import json
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from .connection_manager import manager
from .models import GroupMessage, GroupSession
from .schemas import CreateSessionRequest, SessionResponse

redis_client = aioredis.from_url("redis://redis:6379", decode_responses=True)

router = APIRouter()

LOCK_TIMEOUT_SECONDS = 30


@router.post("/sessions", response_model=SessionResponse)
def create_session(payload: CreateSessionRequest, db: Session = Depends(get_db)):
    session = GroupSession(
        id=uuid.uuid4(),
        title=payload.title,
        created_by=payload.created_by,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.websocket("/ws/session/{session_id}")
async def group_session_ws(websocket: WebSocket, session_id: str):
    display_name = websocket.query_params.get("display_name", "Anonymous")

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
                    author_id=None,
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

            # Acquire per-session lock before "processing" (stand-in for Claude call)
            lock_key = f"group_session_lock:{session_id}"
            lock = redis_client.lock(lock_key, timeout=LOCK_TIMEOUT_SECONDS)
            got_lock = await lock.acquire(blocking=True, blocking_timeout=LOCK_TIMEOUT_SECONDS)
            if not got_lock:
                await manager.broadcast(session_id, {"type": "error", "content": "Session busy, try again."})
                continue

            try:
                await manager.broadcast(session_id, {"type": "thinking"})
                await asyncio.sleep(2)  # stand-in for the real Claude call, Brick 5
                await manager.broadcast(session_id, {
                    "type": "message",
                    "author": "System",
                    "content": f"(simulated response to: {content})",
                })
            finally:
                await lock.release()

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)