import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .connection_manager import manager

router = APIRouter()


@router.websocket("/ws/session/{session_id}")
async def group_session_ws(websocket: WebSocket, session_id: str):
    display_name = websocket.query_params.get("display_name", "Anonymous")

    await manager.connect(session_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            content = data.get("content", "").strip()
            if not content:
                continue

            await manager.broadcast(session_id, {
                "type": "message",
                "author": display_name,
                "content": content,
            })
    except WebSocketDisconnect:
        manager.disconnect(session_id, websocket)