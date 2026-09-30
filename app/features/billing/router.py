# app/features/billing/router.py
import os
import requests
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.users.dependencies import get_verified_user
from app.features.users.models import User

router = APIRouter()

PAYSTACK_SECRET_KEY = os.environ["PAYSTACK_SECRET_KEY"]
PLAN_CODES = {
    "standard": os.environ["PAYSTACK_PLAN_STANDARD"],
    "pro": os.environ["PAYSTACK_PLAN_PRO"],
}

@router.post("/billing/subscribe")
def subscribe(plan: str, db: Session = Depends(get_db), current_user: User = Depends(get_verified_user)):
    if plan not in PLAN_CODES:
        raise HTTPException(status_code=400, detail="Invalid plan")

    response = requests.post(
        "https://api.paystack.co/transaction/initialize",
        headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
        json={
            "email": current_user.email,
            "amount": "1",  # overridden by the plan's amount — Paystack requires a value here anyway
            "plan": PLAN_CODES[plan],
            "callback_url": os.environ["FRONTEND_URL"] + "/billing/callback",
        },
    )
    data = response.json()
    if not data.get("status"):
        raise HTTPException(status_code=502, detail="Could not start subscription")

    return {"authorization_url": data["data"]["authorization_url"]}

@router.post("/webhooks/paystack")
async def paystack_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    expected = hmac.new(PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = await request.json()
    event_type = event.get("event")
    data = event.get("data", {})

    if event_type == "charge.success" and data.get("plan"):
        email = data["customer"]["email"]
        plan_code = data["plan"]["plan_code"]
        plan_name = next((k for k, v in PLAN_CODES.items() if v == plan_code), None)
        if plan_name:
            user = db.query(User).filter(User.email == email).first()
            if user:
                user.plan = plan_name
                user.paystack_customer_code = data["customer"]["customer_code"]
                user.paystack_subscription_code = data.get("subscription_code") or data.get("plan_object", {}).get("subscription_code")
                db.commit()

    elif event_type in ("subscription.disable", "subscription.not_renew"):
        sub_code = data.get("subscription_code")
        user = db.query(User).filter(User.paystack_subscription_code == sub_code).first()
        if user:
            user.plan = "free"
            db.commit()

    return {"status": "ok"}