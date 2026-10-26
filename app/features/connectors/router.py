import os

from app.features.users.models import User

from fastapi import APIRouter, Depends
from app.features.users.dependencies import get_current_user
from jose import jwt
from datetime import time
from app.db.session import SessionLocal, get_db

router = APIRouter(prefix="/github", tags=["github"])
@router.get("/install-url")
def install_url(current_user: User = Depends(get_current_user)):
    state = jwt.encode({"sub": str(current_user.id), "purpose": "github-install", "exp": int(time.time()) + 600}, os.environ["JWT_SECRET_KEY"], algorithm="HS256")