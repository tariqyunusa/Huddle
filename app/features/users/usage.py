import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .models import User

FREE_TIER_MONTHLY_LIMIT = int(os.environ.get("FREE_TIER_MONTHLY_LIMIT", "50"))


def check_and_increment_usage(user: User, db: Session) -> bool:
    """
    Returns True if the user is allowed to send a message (and increments their count).
    Returns False if they've hit their limit.
    Paid users are never limited.
    """
    if user.plan != "free":
        return True

    if datetime.utcnow() > user.usage_reset_at:
        user.message_count = 0
        user.usage_reset_at = datetime.utcnow() + timedelta(days=30)

    if user.message_count >= FREE_TIER_MONTHLY_LIMIT:
        db.commit()  # persist the reset if one happened, even though we're denying
        return False

    user.message_count += 1
    db.commit()
    return True