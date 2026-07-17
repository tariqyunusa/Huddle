from fastapi import FastAPI

from app.features.users.models import User
from app.features.group_session.router import router as group_session_router

app = FastAPI(title="Huddle")

app.include_router(group_session_router)


@app.get("/health")
def health():
    return {"status": "ok"}

 