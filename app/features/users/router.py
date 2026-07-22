import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from .models import User
from .schemas import CreateUserRequest, UserResponse

router = APIRouter()


@router.post("/users", response_model=UserResponse)
def create_user(payload: CreateUserRequest, db: Session = Depends(get_db)):
    user = User(
        id=uuid.uuid4(),
        email=payload.email,
        display_name=payload.display_name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user