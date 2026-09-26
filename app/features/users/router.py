from typing import List
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from app.db.session import get_db
from app.features.users.jwt import create_access_token
from .models import User, PasswordResetToken
from .schemas import CreateUserRequest, ForgotPasswordRequest, LoginResponse, UserResponse, ResetPasswordRequest, UserSearchResult, RefreshRequest
from .security import hash_password
from .schemas import LoginRequest, LoginResponse
from .security import verify_password
from .jwt import create_access_token
from .email import send_password_reset_email
from .dependencies import get_current_user
from .email import send_verification_email
from .models import EmailVerificationToken
from .models import RefreshToken
from .jwt import REFRESH_TOKEN_EXPIRE_DAYS

router = APIRouter()


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user

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
    
    verify_token = EmailVerificationToken(
        id=uuid.uuid4(),
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(hours=24) 
    )
    db.add(verify_token)
    db.commit()
    db.refresh(verify_token)
    
    try:
        send_verification_email(user.email, verify_token.token)
    except Exception as e:
        print(f"SIGNUP VERIFICATION EMAIL FAILED: {e}")
    return user

@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token = create_access_token(str(user.id))
    
    refresh_token = RefreshToken(
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    
    db.add(refresh_token)
    db.commit()
    db.refresh(refresh_token)
    
    
    return LoginResponse(
    access_token=access_token,
    refresh_token=refresh_token.token,
    user_id=user.id,
    display_name=user.display_name,
    email_verified=user.email_verified,
)
    
@router.post("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    record = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.token == token)
        .first()
    )
    if not record or record.used or record.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")

    user = db.query(User).filter(User.id == record.user_id).first()
    user.email_verified = True
    record.used = True
    db.commit()

    return {"message": "Email verified"}

@router.post("/resend-verification")
def resend_verification(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.email_verified:
        return {"message": "Already verified"}

    verify_token = EmailVerificationToken(
        id=uuid.uuid4(),
        user_id=current_user.id,
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(verify_token)
    db.commit()
    db.refresh(verify_token)

    try:
        send_verification_email(current_user.email, verify_token.token)
    except Exception:
        raise HTTPException(status_code=503, detail="Couldn't send verification email right now")

    return {"message": "Verification email sent"}

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

@router.get("/users/search", response_model=List[UserSearchResult])
def search_users(query: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
     if len(query.strip()) < 2:
         return []
     results = (
         db.query(User)
         .filter(User.display_name.ilike(f"%{query}%"))
         .filter(User.id != current_user.id)
         .limit(10)
         .all()
     )
     return results
 
@router.post("/refresh", response_model=LoginResponse)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    record = db.query(RefreshToken).filter(
        RefreshToken.token == payload.refresh_token,
        RefreshToken.revoked == False,
        RefreshToken.expires_at > datetime.utcnow(),
    ).first()
    
    if not record:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    
    user = db.query(User).filter(User.id == record.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    
    record.revoked = True
    
    new_access_token = create_access_token(str(user.id))
    new_refresh = RefreshToken(
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(new_refresh)
    db.commit()
    db.refresh(new_refresh)
    
    return LoginResponse(
        access_token=new_access_token,
        refresh_token=new_refresh.token,
        user_id=user.id,
        display_name=user.display_name,
        email_verified=user.email_verified,
    )
    
@router.post("/logout")
def logout(payload: RefreshRequest, db: Session = Depends(get_db)):
    db.query(RefreshToken).filter(RefreshToken.token == payload.refresh_token).update({"revoked": True})
    db.commit()
    return {"message": "Logged out"}