import hashlib
import hmac
import json
import os
import time
from uuid import UUID

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.users.dependencies import get_verified_user
from app.features.users.models import BachsWebhookEvent, User

router = APIRouter()

BACHS_API_KEY = os.environ["BACHS_API_KEY"]
BACHS_WEBHOOK_SECRET = os.environ["BACHS_WEBHOOK_SECRET"]
BACHS_API_BASE_URL = os.environ.get("BACHS_API_BASE_URL") or (
    "https://sandbox-api.bachs.io"
    if BACHS_API_KEY.startswith("sk_sandbox_")
    else "https://api.bachs.io"
)
PLAN_PRODUCTS = {
    "standard": os.environ["BACHS_PRODUCT_STANDARD"],
    "pro": os.environ["BACHS_PRODUCT_PRO"],
}


@router.post("/billing/subscribe")
def subscribe(
    plan: str,
    current_user: User = Depends(get_verified_user),
):
    product_id = PLAN_PRODUCTS.get(plan)
    if not product_id:
        raise HTTPException(status_code=400, detail="Invalid plan")

    frontend_url = os.environ["FRONTEND_URL"].rstrip("/")
    try:
        response = requests.post(
            f"{BACHS_API_BASE_URL.rstrip('/')}/v1/checkout-sessions",
            headers={"Authorization": f"Bearer {BACHS_API_KEY}"},
            json={
                "product_cart": [{"product_id": product_id, "quantity": 1}],
                "customer": {
                    "email": current_user.email,
                    "name": current_user.display_name,
                },
                "success_url": f"{frontend_url}/billing/callback",
                "cancel_url": f"{frontend_url}/billing/callback?cancelled=1",
                "metadata": {"user_id": str(current_user.id), "plan": plan},
            },
            timeout=15,
        )
        response.raise_for_status()
        checkout_url = response.json().get("checkout_url")
    except (requests.RequestException, ValueError):
        raise HTTPException(status_code=502, detail="Could not start subscription")

    if not checkout_url:
        raise HTTPException(status_code=502, detail="Checkout provider returned no payment link")
    return {"checkout_url": checkout_url}


def _valid_bachs_signature(body: bytes, headers) -> bool:
    timestamp = headers.get("x-bachs-timestamp", "")
    v2_header = headers.get("x-bachs-signature-v2", "")
    candidates: list[str] = []

    # V2 carries its own timestamp and supports multiple signatures during key rotation.
    v2_timestamp = None
    for part in v2_header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        if key == "t":
            v2_timestamp = value
        elif key == "v1":
            candidates.append(value)

    timestamp = v2_timestamp or timestamp
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (TypeError, ValueError):
        return False

    signed_payload = timestamp.encode() + b"." + body
    expected = hmac.new(BACHS_WEBHOOK_SECRET.encode(), signed_payload, hashlib.sha256).hexdigest()
    if candidates:
        return any(hmac.compare_digest(expected, candidate) for candidate in candidates)

    # V1 is retained for endpoints configured before Bachs signature v2 rollout.
    v1 = headers.get("x-bachs-signature", "")
    return bool(v1) and hmac.compare_digest(expected, v1)


def _subscription_plan(data: dict) -> str | None:
    metadata = data.get("metadata") or {}
    plan = metadata.get("plan")
    if plan in PLAN_PRODUCTS:
        return plan
    product = data.get("product") or {}
    product_id = product.get("product_id") or product.get("id") or data.get("product_id")
    return next((name for name, configured_id in PLAN_PRODUCTS.items() if configured_id == product_id), None)


@router.post("/webhooks/bachs")
async def bachs_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    if not _valid_bachs_signature(body, request.headers):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = json.loads(body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid event body")

    event_id = event.get("id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event ID")
    if db.query(BachsWebhookEvent).filter(BachsWebhookEvent.event_id == event_id).first():
        return {"status": "ok"}

    event_type = event.get("type") or event.get("event_type")
    data = event.get("data") or {}
    subscription = data.get("subscription") or data
    customer = subscription.get("customer") or data.get("customer") or {}
    metadata = subscription.get("metadata") or data.get("metadata") or {}
    subscription_id = subscription.get("subscription_id") or subscription.get("id")

    user = None
    user_id = metadata.get("user_id")
    if user_id:
        try:
            user = db.query(User).filter(User.id == UUID(str(user_id))).first()
        except ValueError:
            user = None
    if user is None and subscription_id:
        user = db.query(User).filter(User.bachs_subscription_id == subscription_id).first()
    if user is None:
        email = customer.get("email")
        if email:
            user = db.query(User).filter(User.email == email).first()

    if user and event_type in ("customer.subscription.created", "customer.subscription.updated"):
        status = subscription.get("status") or data.get("status")
        plan = _subscription_plan(subscription)
        if status in ("active", "trialing") and plan:
            user.plan = plan
            user.bachs_subscription_id = subscription_id
            user.bachs_customer_id = customer.get("customer_id") or customer.get("id") or user.bachs_customer_id
        elif status in ("canceled", "unpaid") and subscription_id == user.bachs_subscription_id:
            user.plan = "free"
    elif user and event_type == "customer.subscription.deleted":
        if subscription_id == user.bachs_subscription_id:
            user.plan = "free"
            user.bachs_subscription_id = None

    db.add(BachsWebhookEvent(event_id=str(event_id)))
    db.commit()
    return {"status": "ok"}
