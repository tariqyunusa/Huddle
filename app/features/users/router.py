import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.db.session import get_db
from app.features.users.jwt import create_access_token
from .models import User, PasswordResetToken
from .schemas import CreateUserRequest, ForgotPasswordRequest, LoginResponse, UserResponse, ResetPasswordRequest
from .security import hash_password
from .schemas import LoginRequest, LoginResponse
from .security import verify_password
from .jwt import create_access_token
from .email import send_password_reset_email

router = APIRouter()


@router.post("/users", response_model=UserResponse)
def create_user(payload: CreateUserRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    
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
    

@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if user:
        reset_token = PasswordResetToken(
            id=uuid.uuid4(),
            user_id=user.id,
            expires_at=datetime.utcnow() + timedelta(minutes=30)
        )
        db.add(reset_token)
        db.commit()
        db.refresh(reset_token)
        send_password_reset_email(user.email, reset_token.token)
        
    return{"message": "if that email exists, a reset link has been sent."}

@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset_token = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == payload.token,
        PasswordResetToken.expires_at > datetime.utcnow(),
        PasswordResetToken.used == False
    ).first()
    
    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    
    user = db.query(User).filter(User.id == reset_token.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.password_hash = hash_password(payload.new_password)
    reset_token.used = True
    
    db.commit()
    
    return {"message": "Password has been reset successfully."}