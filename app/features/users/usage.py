import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import User

FREE_TIER_TOKEN_LIMIT = int(os.environ.get("FREE_TIER_TOKEN_LIMIT", "15000"))
WINDOW_HOURS = int(os.environ.get("USAGE_WINDOW_HOURS", "5"))


def check_usage_allowed(user_id, db: Session) -> bool:
    """Call BEFORE the AI request. Returns False if the user is over budget."""
    user = db.query(User).filter(User.id == user_id).first()
    if user.plan != "free":
        return True

    if datetime.utcnow() > user.window_started_at + timedelta(hours=WINDOW_HOURS):
        user.token_count = 0
        user.window_started_at = datetime.utcnow()
        db.commit()

    return user.token_count < FREE_TIER_TOKEN_LIMIT


def record_usage(user_id, tokens_used: int, db: Session) -> int:
    """Call AFTER the AI request, with the real token count from the response. Returns the new total."""
    user = db.query(User).filter(User.id == user_id).first()
    user.token_count += tokens_used
    db.commit()
    return user.token_count