from pathlib import Path
from contextlib import asynccontextmanager
import logging
import os

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(levelname)s [%(name)s] %(message)s",
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.features.group_session.router import router as group_session_router
from app.features.users.router import router as users_router
from app.features.billing.router import router as billing_router
from app.features.memory.router import router as memory_router
from app.features.connectors.router import router as github_connector_router
from app.features.connectors.repos import router as repos_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.features.memory.embeddings import preload_model

    await preload_model()
    yield


app = FastAPI(title="Huddle", lifespan=lifespan)

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
app.include_router(billing_router)
app.include_router(memory_router)
app.include_router(github_connector_router)
app.include_router(repos_router)


@app.get("/health")
def health():
    return {"status": "ok"}
