from typing import Literal
from pydantic import Field
from ..schemas import Strict, Anchor


class AccountInput(Strict):
    displayName: str = Field(default="", max_length=100)
    language: Literal["zh-CN", "en"] = "zh-CN"
    avatarId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ActionKey(Strict):
    requestKey: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9:_-]+$")


class DefaultModelsInput(ActionKey):
    expectedVersion: int = Field(ge=0)
    textProfileId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    imageEnabled: bool = False


class GrantInput(ActionKey):
    credits: int = Field(default=0, ge=0, le=10000000)
    memberDays: int = Field(default=0, ge=0, le=3660)
    dailyCredits: int = Field(default=0, ge=0, le=100000)
    reason: str = Field(min_length=2, max_length=120)


class UserAdminInput(ActionKey):
    status: Literal["active", "disabled"]
    role: Literal["USER", "ADMIN"]
    memo: str = Field(default="", max_length=1000)


class InviteInput(ActionKey):
    label: str = Field(min_length=1, max_length=100)
    maxUses: int = Field(default=10, ge=1, le=10000)
    validDays: int = Field(default=30, ge=1, le=365)
    reward: int = Field(default=0, ge=0, le=100000)
    admission: bool = True


class CardInput(ActionKey):
    credits: int = Field(default=0, ge=0, le=10000000)
    memberDays: int = Field(default=0, ge=0, le=3660)
    validDays: int = Field(default=30, ge=1, le=365)


class RedeemInput(Strict):
    code: str = Field(min_length=8, max_length=100)


class ProductInput(ActionKey):
    sku: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["credits", "member"]
    amountMinor: int = Field(gt=0, le=100000000)
    currency: Literal["CNY", "USD", "EUR", "HKD"]
    credits: int = Field(default=0, ge=0, le=10000000)
    memberDays: int = Field(default=0, ge=0, le=3660)
    dailyCredits: int = Field(default=0, ge=0, le=100000)
    active: bool = True


class PolicyInput(ActionKey):
    inviteRequired: bool = False
    billingEnabled: bool = False
    prices: dict[str, int] = Field(default_factory=dict, max_length=60)
    signinCredits: int = Field(default=0, ge=0, le=100000)


class OrderInput(ActionKey):
    productId: str = Field(pattern=r"^[a-f0-9]{32}$")


class SearchInput(Strict):
    query: str = Field(min_length=1, max_length=100)
    scope: Literal["author", "read", "player"] = "author"
    anchor: Anchor | None = None
    worldId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    lineId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class TelemetryEvent(Strict):
    eventId: str = Field(min_length=8, max_length=64)
    eventVersion: Literal[2]
    appVersion: str = Field(max_length=32)
    ownerUserId: str | None = Field(default=None, max_length=32)
    type: Literal["page_view", "click", "error", "api"]
    name: str = Field(max_length=120)
    path: str = Field(default="", max_length=200)
    target: str | None = Field(default=None, max_length=100)
    metadata: dict | None = None
    durationMs: int | None = Field(default=None, ge=0, le=3600000)
    statusCode: int | None = Field(default=None, ge=0, le=599)
    anonId: str = Field(max_length=64)
    sessionId: str = Field(max_length=64)
    deviceType: Literal["mobile", "desktop"]
    ts: int


class TelemetryInput(Strict):
    eventVersion: Literal[2]
    expectedUserId: str | None
    events: list[TelemetryEvent] = Field(min_length=1, max_length=50)
