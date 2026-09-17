import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import User

FREE_TIER_TOKEN_LIMIT = int(os.environ.get("FREE_TIER_TOKEN_LIMIT", "15000"))
WINDOW_HOURS = int(os.environ.get("USAGE_WINDOW_HOURS", "5"))


def check_usage_allowed(user: User, db: Session) -> bool:
    """"Call BEFORE the AI request. Returns False if the user is over budget."""
    if user.plan != "free":
        return True
    
    if datetime.utcnow() > user.window_started_at + timedelta(hours=WINDOW_HOURS):
        user.token_count = 0
        user.window_started_at = datetime.utcnow()
        db.commit()
        
    return user.token_count < FREE_TIER_TOKEN_LIMIT


def record_usage(user: User, tokens_used: int, db: Session):
    """Call AFTER the AI request, with the real token count from the response."""
    user.token_count += tokens_used
    db.commit()


# def check_and_increment_usage(user: User, db: Session) -> bool:
#     """
#     Returns True if the user is allowed to send a message (and increments their count).
#     Returns False if they've hit their limit.
#     Paid users are never limited.
#     """
#     if user.plan != "free":
#         return True

#     if datetime.utcnow() > user.window_started_at + timedelta(hours=WINDOW_HOURS):
#         user.token_count = 0
#         user.window_started_at = datetime.utcnow()

#     if user.message_count >= FREE_TIER_MONTHLY_LIMIT:
#         db.commit()  # persist the reset if one happened, even though we're denying
#         return False

#     user.message_count += 1
#     db.commit()
#     return True