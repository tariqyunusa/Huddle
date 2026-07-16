from typing import Dict, List
from fastapi import WebSocket


class ConnectionManager:
    """Tracks live websocket connections per session_id, in-process."""

    def __init__(self):
        self.active: Dict[str, List[WebSocket]] = {}

    async def connect(self, session_id: str, ws: WebSocket):
        await ws.accept()
        self.active.setdefault(session_id, []).append(ws)

    def disconnect(self, session_id: str, ws: WebSocket):
        conns = self.active.get(session_id, [])
        if ws in conns:
            conns.remove(ws)
        if not conns:
            self.active.pop(session_id, None)

    async def broadcast(self, session_id: str, payload: dict):
        dead = []
        for ws in self.active.get(session_id, []):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)


manager = ConnectionManager()
