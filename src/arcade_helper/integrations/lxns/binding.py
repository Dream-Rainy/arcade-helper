"""LXNS account binding models and store-based workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr

from .accounts import AccountStore


class LxnsBindMethod(str, Enum):
    """LXNS external account binding method."""

    unique_code = "unique_code"
    api_key = "api_key"
    oa = "oa"


class LxnsProvider(str, Enum):
    """External source identifier."""

    lxns = "lxns"


class IdentityProvider(str, Enum):
    """Internal identity source identifier."""

    qq = "qq"


class LxnsUniqueCodeCredential(BaseModel):
    method: Literal[LxnsBindMethod.unique_code] = LxnsBindMethod.unique_code
    unique_code: str = Field(min_length=4, max_length=128)


class LxnsApiKeyCredential(BaseModel):
    method: Literal[LxnsBindMethod.api_key] = LxnsBindMethod.api_key
    api_key: SecretStr = Field(description="Sensitive field; masked when printed")


class LxnsOAuthCredential(BaseModel):
    method: Literal[LxnsBindMethod.oa] = LxnsBindMethod.oa

    access_token: SecretStr
    refresh_token: SecretStr

    token_expiry: datetime = Field(description="Access token expiry")
    refresh_expiry: datetime | None = Field(default=None, description="Refresh token expiry")


LxnsCredential = LxnsUniqueCodeCredential | LxnsApiKeyCredential | LxnsOAuthCredential


class LxnsBindRequest(BaseModel):
    """Bind request independent from any concrete storage backend."""

    qq: str = Field(description="QQ id used to locate the internal user")
    credential: LxnsCredential = Field(discriminator="method")
    operator: str | None = None


class LxnsBindResult(BaseModel):
    user_id: int
    qq: str
    platform: Literal[LxnsProvider.lxns] = LxnsProvider.lxns
    account_name: str = Field(description="Stored account display name")
    account_key: str = Field(description="Stored account key")
    schema_version: int = 1
    stored_at: datetime


class LxnsAccountKeyType(str, Enum):
    unique_code = "unique_code"
    api_key = "api_key"
    oa = "oa"


class LxnsAccountKey(BaseModel):
    type: LxnsAccountKeyType
    value: str

    def to_string(self) -> str:
        return f"{self.type.value}:{self.value}"


class LxnsAccountJsonV1(BaseModel):
    provider: Literal[LxnsProvider.lxns] = LxnsProvider.lxns
    method: LxnsBindMethod

    unique_code: str | None = None
    api_key: SecretStr | None = None

    oauth2_access_token: SecretStr | None = None
    oauth2_refresh_token: SecretStr | None = None
    oauth2_token_expiry: datetime | None = None
    oauth2_refresh_expiry: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    extra: dict[str, Any] = Field(default_factory=dict)


class GameProfileV1(BaseModel):
    maimai_name: str | None = None
    maimai_friend_code: str | None = None
    chunithm_name: str | None = None
    chunithm_friend_code: str | None = None

    updated_at: datetime | None = None


class LxnsExternalSnapshot(BaseModel):
    user_id: str
    fetched_at: datetime
    data_type: str
    data: dict[str, Any]
    source: Literal[LxnsProvider.lxns] = LxnsProvider.lxns


def _hash_api_key(raw: str) -> str:
    return sha256(raw.encode("utf-8")).hexdigest()


def build_account_key(req: LxnsBindRequest) -> str:
    cred = req.credential

    if isinstance(cred, LxnsUniqueCodeCredential):
        return LxnsAccountKey(type=LxnsAccountKeyType.unique_code, value=cred.unique_code).to_string()

    if isinstance(cred, LxnsOAuthCredential):
        return LxnsAccountKey(type=LxnsAccountKeyType.oa, value="oauth").to_string()

    if isinstance(cred, LxnsApiKeyCredential):
        h = _hash_api_key(cred.api_key.get_secret_value())
        return LxnsAccountKey(type=LxnsAccountKeyType.api_key, value=f"sha256:{h}").to_string()

    raise TypeError(f"Unsupported credential: {type(cred)!r}")


def build_account_json(req: LxnsBindRequest) -> LxnsAccountJsonV1:
    cred = req.credential
    now = datetime.now(UTC)

    if isinstance(cred, LxnsUniqueCodeCredential):
        return LxnsAccountJsonV1(method=cred.method, unique_code=cred.unique_code, last_updated_at=now)

    if isinstance(cred, LxnsApiKeyCredential):
        return LxnsAccountJsonV1(method=cred.method, api_key=cred.api_key, last_updated_at=now)

    if isinstance(cred, LxnsOAuthCredential):
        return LxnsAccountJsonV1(
            method=cred.method,
            oauth2_access_token=cred.access_token,
            oauth2_refresh_token=cred.refresh_token,
            oauth2_token_expiry=cred.token_expiry,
            oauth2_refresh_expiry=cred.refresh_expiry,
            last_updated_at=now,
        )

    raise TypeError(f"Unsupported credential: {type(cred)!r}")


def serialize_account_json(req: LxnsBindRequest) -> dict[str, Any]:
    account_json = build_account_json(req)
    payload = account_json.model_dump(mode="json")
    cred = req.credential

    if isinstance(cred, LxnsApiKeyCredential):
        payload["api_key"] = cred.api_key.get_secret_value()

    if isinstance(cred, LxnsOAuthCredential):
        payload["oauth2_access_token"] = cred.access_token.get_secret_value()
        payload["oauth2_refresh_token"] = cred.refresh_token.get_secret_value()

    return payload


async def bind_upsert(
    req: LxnsBindRequest,
    store: AccountStore,
    account_name: str | None = None,
) -> LxnsBindResult:
    account_key = build_account_key(req)
    account_json = serialize_account_json(req)
    stored = await store.upsert_lxns_account(
        qq=req.qq,
        account_key=account_key,
        account_json=account_json,
        account_name=account_name or f"lxns_user_{req.qq}",
    )
    return LxnsBindResult(
        user_id=stored.user_id,
        qq=req.qq,
        platform=LxnsProvider.lxns,
        account_key=stored.account_key,
        account_name=stored.account_name,
        schema_version=stored.schema_version,
        stored_at=datetime.now(UTC),
    )


__all__ = [
    "GameProfileV1",
    "IdentityProvider",
    "LxnsAccountJsonV1",
    "LxnsAccountKey",
    "LxnsAccountKeyType",
    "LxnsApiKeyCredential",
    "LxnsBindMethod",
    "LxnsBindRequest",
    "LxnsBindResult",
    "LxnsCredential",
    "LxnsExternalSnapshot",
    "LxnsOAuthCredential",
    "LxnsProvider",
    "LxnsUniqueCodeCredential",
    "bind_upsert",
    "build_account_json",
    "build_account_key",
    "serialize_account_json",
]
