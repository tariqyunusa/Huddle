import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import User

WINDOW_HOURS = int(os.environ.get("USAGE_WINDOW_HOURS", "5"))

PLAN_TOKEN_LIMITS = {
    "free": int(os.environ.get("FREE_TIER_TOKEN_LIMIT", "15000")),
    "standard": int(os.environ.get("STANDARD_TIER_TOKEN_LIMIT", "150000")),
    "pro": int(os.environ.get("PRO_TIER_TOKEN_LIMIT", "400000")),
}


def check_usage_allowed(user_id, db: Session) -> bool:
    """Call BEFORE the AI request. Returns False if the user is over budget."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False

    limit = PLAN_TOKEN_LIMITS.get(user.plan, PLAN_TOKEN_LIMITS["free"])

    if datetime.utcnow() > user.window_started_at + timedelta(hours=WINDOW_HOURS):
        user.token_count = 0
        user.window_started_at = datetime.utcnow()
        db.commit()

    return user.token_count < limit


def record_usage(user_id, tokens_used: int, db: Session) -> int:
    """Call AFTER the AI request, with the real token count from the response. Returns the new total."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return 0
    user.token_count += tokens_used
    db.commit()
    return user.token_count