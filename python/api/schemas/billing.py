from pydantic import BaseModel


class CheckoutRequest(BaseModel):
    price_id: str


class CheckoutResponse(BaseModel):
    url: str
