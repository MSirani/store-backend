from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class RegisterWithRoleInput(RegisterInput):
    account_type: Literal["customer", "seller"] = "customer"


class LoginInput(BaseModel):
    email: EmailStr
    password: str


class ShippingStatusInput(BaseModel):
    status: Literal["shipped", "delivered"]


class ShopInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class ProductInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price_toman: int = Field(ge=0)
    stock: int = Field(ge=0)


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price_toman: int | None = Field(default=None, ge=0)
    active: bool | None = None


class StockAdjustment(BaseModel):
    delta: int = Field(ne=0)


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class CartItemInput(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(ge=1, le=100)


class CartQuantityInput(BaseModel):
    quantity: int = Field(ge=1, le=100)


class AddressInput(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    recipient_name: str = Field(min_length=1, max_length=150)
    phone: str = Field(min_length=5, max_length=30)
    city: str = Field(min_length=1, max_length=100)
    street: str = Field(min_length=1, max_length=500)
    postal_code: str = Field(min_length=1, max_length=30)


class CheckoutInput(BaseModel):
    address_id: int = Field(gt=0)
    method: Literal["wallet", "gateway", "mixed"] = "wallet"
    wallet_amount_toman: int = Field(default=0, ge=0)


class DevPaymentResult(BaseModel):
    success: bool


class AdminWalletCredit(BaseModel):
    amount_toman: int = Field(gt=0)
    event_key: str = Field(min_length=8, max_length=128)
