from fastapi import APIRouter, Header, Request

from api.dependencies import CurrentUser, DbDep
from api.schemas.billing import CheckoutRequest, CheckoutResponse
from api.services import billing_service

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout(req: CheckoutRequest, user: CurrentUser, session: DbDep) -> CheckoutResponse:
    url = await billing_service.create_checkout_session(session, user, req.price_id)
    return CheckoutResponse(url=url)


@router.post("/webhook", status_code=200)
async def stripe_webhook(
    request: Request,
    session: DbDep,
    stripe_signature: str = Header(..., alias="Stripe-Signature"),
) -> dict:
    payload = await request.body()
    await billing_service.handle_webhook(session, payload, stripe_signature)
    return {"received": True}
