from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.features.group_session.router import router as group_session_router
from app.features.users.router import router as users_router

app = FastAPI(title="Huddle")

BASE_DIR = Path(__file__).resolve().parent

app.mount(
    "/assets",
    StaticFiles(directory=BASE_DIR / "assets"),
    name="assets",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(group_session_router)
app.include_router(users_router)


@app.get("/health")
def health():
    return {"status": "ok"}