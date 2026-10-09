import hashlib
import hmac
import json
import logging
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
logger = logging.getLogger(__name__)

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
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        request_id = (
            exc.response.headers.get("x-request-id") or exc.response.headers.get("request-id")
            if exc.response is not None
            else None
        )
        try:
            body = exc.response.json() if exc.response is not None else {}
        except ValueError:
            body = {}
        error_code = body.get("error_code") or body.get("code")
        provider_message = body.get("detail") or body.get("message")
        logger.warning(
            "Bachs checkout creation failed: status=%s error_code=%s request_id=%s detail=%s",
            status,
            error_code,
            request_id,
            str(provider_message)[:300] if provider_message else None,
        )
        if status in (401, 403):
            detail = "Bachs rejected checkout. Check that the configured API key is valid for this environment and has checkout access."
        elif status == 404:
            detail = "Bachs could not find the configured product in the selected API environment."
        elif status == 422:
            detail = "Bachs rejected the checkout details. Check the configured product and checkout settings."
        elif status == 400 and provider_message:
            detail = f"Bachs rejected checkout: {str(provider_message)[:300]}"
        else:
            detail = f"Bachs could not start checkout (HTTP {status or 'error'}). Try again shortly."
        raise HTTPException(status_code=502, detail=detail) from exc
    except ValueError as exc:
        logger.warning("Bachs returned invalid checkout data")
        raise HTTPException(status_code=502, detail="Bachs returned invalid checkout data.") from exc
    except requests.RequestException as exc:
        logger.warning("Bachs checkout connection failed: %s: %s", type(exc).__name__, exc)
        raise HTTPException(
            status_code=502,
            detail="Could not connect to Bachs to start checkout. Try again shortly.",
        ) from exc

    if not checkout_url:
        raise HTTPException(status_code=502, detail="Checkout provider returned no payment link")
    return {"checkout_url": checkout_url}


@router.get("/billing/plans")
def get_billing_plans(current_user: User = Depends(get_verified_user)):
    products = {}
    for plan, product_id in PLAN_PRODUCTS.items():
        try:
            response = requests.get(
                f"{BACHS_API_BASE_URL.rstrip('/')}/v1/products/{product_id}",
                headers={"Authorization": f"Bearer {BACHS_API_KEY}"},
                timeout=10,
            )
            response.raise_for_status()
            product = response.json()
            price = product.get("price") or {}
            amount = price.get("amount")
            currency = price.get("currency")
            if amount is None or not currency:
                raise HTTPException(
                    status_code=502,
                    detail=f"The configured Bachs {plan} product has no fixed price.",
                )
            cycle = product.get("billing_cycle") or {}
            products[plan] = {
                "amount": str(amount),
                "currency": currency,
                "interval": cycle.get("interval"),
                "frequency": cycle.get("frequency", 1),
            }
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            try:
                error_code = (exc.response.json() or {}).get("error_code") if exc.response is not None else None
            except ValueError:
                error_code = None
            logger.warning(
                "Bachs product lookup failed for plan %s: status=%s error_code=%s",
                plan,
                status,
                error_code,
            )
            if status in (401, 403):
                detail = "Bachs rejected the product lookup. Check the API key and its products:read permission."
            elif status == 404:
                detail = f"Bachs could not find the configured {plan} product ID in the selected API environment."
            else:
                detail = f"Bachs could not load the configured {plan} product (HTTP {status or 'error'})."
            raise HTTPException(status_code=502, detail=detail)
        except requests.RequestException as exc:
            logger.warning("Bachs product lookup failed for plan %s: %s: %s", plan, type(exc).__name__, exc)
            raise HTTPException(
                status_code=502,
                detail="Could not connect to Bachs to load plan prices. Try again shortly.",
            )
        except ValueError:
            logger.warning("Bachs returned invalid product data for plan %s", plan)
            raise HTTPException(status_code=502, detail="Bachs returned invalid product data.")
    return {"plans": products}


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
