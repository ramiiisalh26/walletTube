"""
Stripe billing service — replaces Java's BillingService.java.
Stripe customer ID is stored in user_subscriptions (per schema.sql).
"""
import logging

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import User, UserSubscription

logger = logging.getLogger(__name__)


def _stripe() -> None:
    stripe.api_key = settings.stripe_secret_key


async def _get_or_create_stripe_customer(session: AsyncSession, user: User) -> str:
    """Return existing Stripe customer ID or create a new one."""
    result = await session.execute(
        select(UserSubscription.stripe_customer_id)
        .where(
            UserSubscription.user_id == user.id,
            UserSubscription.stripe_customer_id.isnot(None),
        )
        .limit(1)
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    _stripe()
    customer = stripe.Customer.create(email=user.email, name=user.name)
    # Persist so future checkouts reuse the same customer
    await session.execute(
        text("""
            INSERT INTO user_subscriptions (user_id, plan_id, stripe_customer_id, status)
            VALUES (:uid, :plan_id, :cid, 'active')
            ON CONFLICT DO NOTHING
        """),
        {"uid": user.id, "plan_id": user.plan_id, "cid": customer["id"]},
    )
    await session.commit()
    return customer["id"]


async def create_checkout_session(session: AsyncSession, user: User, price_id: str) -> str:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing not configured")

    try:
        customer_id = await _get_or_create_stripe_customer(session, user)
        _stripe()
        checkout = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            success_url=settings.stripe_success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=settings.stripe_cancel_url,
            line_items=[{"price": price_id, "quantity": 1}],
        )
        return checkout["url"]
    except stripe.StripeError as exc:
        logger.error("Stripe checkout failed for user %s: %s", user.id, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Billing error: {exc}")


def _resolve_plan_slug(price_id: str) -> str:
    if price_id == settings.stripe_pro_price_id:
        return "pro"
    if price_id == settings.stripe_team_price_id:
        return "team"
    return "free"


async def handle_webhook(session: AsyncSession, payload: bytes, sig_header: str) -> None:
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Webhook secret not configured")

    _stripe()
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except stripe.SignatureVerificationError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe signature")

    event_type = event["type"]
    data_obj = event["data"]["object"]

    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        sub_id = data_obj["id"]
        customer_id = data_obj["customer"]
        stripe_status = data_obj["status"]
        price_id = data_obj["items"]["data"][0]["price"]["id"]
        slug = _resolve_plan_slug(price_id)
        active = stripe_status == "active"

        if active:
            await session.execute(
                text("""
                    UPDATE users u
                    SET plan_id = p.id
                    FROM subscription_plans p
                    JOIN user_subscriptions us ON us.user_id = u.id
                    WHERE p.slug = :slug
                      AND us.stripe_customer_id = :cid
                """),
                {"slug": slug, "cid": customer_id},
            )
            await session.execute(
                text("""
                    UPDATE user_subscriptions
                    SET stripe_subscription_id = :sid,
                        status = 'active',
                        updated_at = NOW()
                    WHERE stripe_customer_id = :cid
                """),
                {"sid": sub_id, "cid": customer_id},
            )
        else:
            await _downgrade_to_free(session, customer_id)

    elif event_type == "customer.subscription.deleted":
        customer_id = data_obj["customer"]
        await _downgrade_to_free(session, customer_id)

    else:
        logger.debug("Unhandled Stripe event: %s", event_type)
        return

    await session.commit()


async def _downgrade_to_free(session: AsyncSession, stripe_customer_id: str) -> None:
    await session.execute(
        text("""
            UPDATE users u
            SET plan_id = p.id
            FROM subscription_plans p
            JOIN user_subscriptions us ON us.user_id = u.id
            WHERE p.slug = 'free'
              AND us.stripe_customer_id = :cid
        """),
        {"cid": stripe_customer_id},
    )
    await session.execute(
        text("""
            UPDATE user_subscriptions
            SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
            WHERE stripe_customer_id = :cid
        """),
        {"cid": stripe_customer_id},
    )
    logger.info("Downgraded to free for Stripe customer %s", stripe_customer_id)
