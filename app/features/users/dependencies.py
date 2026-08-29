from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from app.db.session import get_db
from .jwt import decode_access_token
from .models import User

def get_current_user(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
    
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    
    token = authorization.removeprefix("Bearer ")
    try:
        user_id = decode_access_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token or expired token")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail=" User not found")
    
    return user