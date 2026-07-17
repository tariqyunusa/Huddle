"""
Group reasoning session WebSocket endpoint — Brick 3: persistence.
"""
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.session import SessionLocal
from app.features.group_session.models import GroupMessage
from .connection_manager import manager

router = APIRouter()


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

            # Persist before broadcasting
            db = SessionLocal()
            try:
                msg = GroupMessage(
                    id=uuid.uuid4(),
                    session_id=session_id,
                    role="user",
                    author_id=None,  # wire up real auth later
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

    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)