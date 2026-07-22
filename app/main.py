from fastapi import FastAPI

from app.features.users.models import User
from app.features.group_session.router import router as group_session_router
from app.features.users.router import router as users_router

app = FastAPI(title="Huddle")

app.include_router(group_session_router)
app.include_router(users_router)

@app.get("/health")
def health():
    return {"status": "ok"}

 