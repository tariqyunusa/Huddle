import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.users.jwt import create_access_token
from .models import User
from .schemas import CreateUserRequest, LoginResponse, UserResponse
from .security import hash_password
from .schemas import LoginRequest, LoginResponse
from .security import verify_password
from .jwt import create_access_token

router = APIRouter()


@router.post("/users", response_model=UserResponse)
def create_user(payload: CreateUserRequest, db: Session = Depends(get_db)):
    user = User(
        id=uuid.uuid4(),
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(str(user.id))
    return LoginResponse(
        access_token=token,
        user_id=user.id,
        display_name=user.display_name,
    )