from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from arcade_helper.auth import AuthService
from arcade_helper.integrations.lxns.accounts import StoredProviderAccount, StoredProviderCredential
from arcade_helper.users.service import UserService


class FakeOAuth:
    def __init__(self) -> None:
        self.refresh_count = 0

    async def refresh_token(self, refresh_token: str, *, code_verifier: str | None = None) -> dict:
        self.refresh_count += 1
        await asyncio.sleep(0)
        return {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "token_expiry": datetime.now(UTC) + timedelta(minutes=15),
            "refresh_expiry": datetime.now(UTC) + timedelta(days=30),
        }


class FakeStore:
    def __init__(self) -> None:
        self.account = StoredProviderAccount(
            user_id=1,
            platform="lxns",
            account_key="maimai:1234567890",
            account_name="OAuth User",
            account_json={},
            is_default=True,
        )
        self.credential = StoredProviderCredential(
            user_id=1,
            provider="lxns",
            provider_account_key="maimai:1234567890",
            credential_key="oa:oauth",
            credential_method="oa",
            credential_json={
                "oauth2_access_token": "old_access",
                "oauth2_refresh_token": "old_refresh",
                "oauth2_token_expiry": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                "oauth2_refresh_expiry": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
            is_default=True,
        )

    async def get_provider_account(self, *, provider: str, account_key: str) -> StoredProviderAccount | None:
        if provider == self.account.platform and account_key == self.account.account_key:
            return self.account
        return None

    async def get_provider_credential(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_method: str | None = None,
        credential_key: str | None = None,
    ) -> StoredProviderCredential | None:
        if provider != self.credential.provider or provider_account_key != self.credential.provider_account_key:
            return None
        if credential_method is not None and credential_method != self.credential.credential_method:
            return None
        if credential_key is not None and credential_key != self.credential.credential_key:
            return None
        return self.credential

    async def update_provider_account_json_if_current(
        self,
        *,
        provider: str,
        account_key: str,
        account_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        if expected_refresh_token != self.credential.credential_json["oauth2_refresh_token"]:
            return False
        self.account = StoredProviderAccount(
            user_id=self.account.user_id,
            platform=self.account.platform,
            account_key=self.account.account_key,
            account_name=self.account.account_name,
            account_json=account_json,
            is_default=True,
        )
        return True

    async def update_provider_credential_json_if_current(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_key: str,
        credential_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        if expected_refresh_token != self.credential.credential_json["oauth2_refresh_token"]:
            return False
        self.credential = StoredProviderCredential(
            user_id=self.credential.user_id,
            provider=provider,
            provider_account_key=provider_account_key,
            credential_key=credential_key,
            credential_method=self.credential.credential_method,
            credential_json=credential_json,
            is_default=True,
        )
        return True


@pytest.mark.asyncio
async def test_auth_service_serializes_concurrent_token_refresh():
    store = FakeStore()
    oauth = FakeOAuth()
    users = UserService(store=store, lxns_client=object())
    service = AuthService(store=store, users=users, lxns_client=object(), oauth=oauth)

    first, second = await asyncio.gather(
        service.ensure_lxns_token_valid(account_key="maimai:1234567890"),
        service.ensure_lxns_token_valid(account_key="maimai:1234567890"),
    )

    assert first["access_token"] == "new_access"
    assert second["access_token"] == "new_access"
    assert oauth.refresh_count == 1
