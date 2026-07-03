from __future__ import annotations

from typing import Any

import pytest

from arcade_helper.integrations.lxns.accounts import StoredGameProfile, StoredProviderAccount, StoredProviderCredential
from arcade_helper.users import PlatformIdentity
from arcade_helper.users.service import UserService


class FakeMaimai:
    async def player_by_qq(self, qq: str, headers: dict) -> dict:
        return {"success": True, "data": {"friend_code": "1234567890"}}

    async def player(self, friend_code: str, headers: dict) -> dict:
        return {"success": True, "data": {"name": "Mai Player", "friend_code": friend_code}}


class FakeLxns:
    maimai = FakeMaimai()


class FakeAccountStore:
    def __init__(self) -> None:
        self.users: dict[tuple[str, str], int] = {}
        self.accounts: dict[tuple[str, str], StoredProviderAccount] = {}
        self.credentials: dict[tuple[str, str, str], StoredProviderCredential] = {}
        self.profiles: dict[tuple[str, str], StoredGameProfile] = {}
        self.next_user_id = 1

    async def ensure_user_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> int:
        key = (platform, account_key)
        if key not in self.users:
            self.users[key] = self.next_user_id
            self.next_user_id += 1
        return self.users[key]

    async def ensure_user_model_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user_id = await self.ensure_user_for_platform(
            platform=platform,
            account_key=account_key,
            account_name=account_name,
            account_json=account_json,
        )
        return {"id": user_id}

    async def get_platform_account_key_for_user(self, *, user_id: int, platform: str) -> str | None:
        return next(
            (account_key for (stored_platform, account_key), stored_user_id in self.users.items()
             if stored_platform == platform and stored_user_id == user_id),
            None,
        )

    async def upsert_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
        account_key: str,
        account_json: dict[str, Any],
        account_name: str,
    ) -> StoredProviderAccount:
        user_id = await self.ensure_user_for_platform(
            platform=identity_platform,
            account_key=identity_account_key,
        )
        is_default = not any(acc.user_id == user_id and acc.platform == provider for acc in self.accounts.values())
        stored = StoredProviderAccount(
            user_id=user_id,
            platform=provider,
            account_key=account_key,
            account_name=account_name,
            account_json=account_json,
            is_default=is_default,
        )
        self.accounts[(provider, account_key)] = stored
        return stored

    async def upsert_provider_credential(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_key: str,
        credential_method: str,
        credential_json: dict[str, Any],
        is_default: bool = True,
    ) -> StoredProviderCredential:
        account = self.accounts[(provider, provider_account_key)]
        credential = StoredProviderCredential(
            user_id=account.user_id,
            provider=provider,
            provider_account_key=provider_account_key,
            credential_key=credential_key,
            credential_method=credential_method,
            credential_json=credential_json,
            is_default=is_default,
        )
        self.credentials[(provider, provider_account_key, credential_key)] = credential
        return credential

    async def get_provider_credential(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_method: str | None = None,
        credential_key: str | None = None,
    ) -> StoredProviderCredential | None:
        credentials = [
            credential
            for (stored_provider, stored_account_key, stored_credential_key), credential in self.credentials.items()
            if stored_provider == provider
            and stored_account_key == provider_account_key
            and (credential_key is None or stored_credential_key == credential_key)
            and (credential_method is None or credential.credential_method == credential_method)
        ]
        return next((credential for credential in credentials if credential.is_default), credentials[0] if credentials else None)

    async def has_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> bool:
        user_id = self.users.get((identity_platform, identity_account_key))
        return user_id is not None and any(
            acc.user_id == user_id and acc.platform == provider for acc in self.accounts.values()
        )

    async def list_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> list[StoredProviderAccount]:
        user_id = self.users.get((identity_platform, identity_account_key))
        return [
            acc
            for acc in self.accounts.values()
            if user_id is not None and acc.user_id == user_id and acc.platform == provider
        ]

    async def set_default_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
        account_key: str,
    ) -> None:
        self.accounts[(provider, account_key)] = StoredProviderAccount(
            **{**self.accounts[(provider, account_key)].__dict__, "is_default": True}
        )

    async def get_default_game_profile_for_identity(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> StoredGameProfile | None:
        accounts = await self.list_provider_accounts(
            identity_platform=identity_platform,
            identity_account_key=identity_account_key,
            provider=provider,
        )
        account = next((acc for acc in accounts if acc.is_default), accounts[0] if accounts else None)
        return self.profiles.get((provider, account.account_key)) if account else None

    async def upsert_game_profile(self, **kwargs: Any) -> StoredGameProfile:
        account_key = str(kwargs["account_key"])
        account = next(acc for acc in self.accounts.values() if acc.account_key == account_key)
        profile = StoredGameProfile(
            account_key=account_key,
            account_name=account.account_name,
            provider=account.platform,
            maimai_name=kwargs.get("maimai_name") or "",
            maimai_friend_code=kwargs.get("maimai_friend_code") or "",
            chunithm_name=kwargs.get("chunithm_name") or "",
            chunithm_friend_code=kwargs.get("chunithm_friend_code") or "",
        )
        self.profiles[(account.platform, account_key)] = profile
        return profile

    async def get_game_profile_by_provider_account(
        self,
        *,
        provider: str,
        account_key: str,
    ) -> StoredGameProfile | None:
        return self.profiles.get((provider, account_key))

    async def delete_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> int:
        accounts = await self.list_provider_accounts(
            identity_platform=identity_platform,
            identity_account_key=identity_account_key,
            provider=provider,
        )
        for account in accounts:
            self.accounts.pop((provider, account.account_key), None)
            self.profiles.pop((provider, account.account_key), None)
            for key in list(self.credentials):
                if key[0] == provider and key[1] == account.account_key:
                    self.credentials.pop(key, None)
        return len(accounts)

    async def get_provider_account(self, *, provider: str, account_key: str) -> StoredProviderAccount | None:
        return self.accounts.get((provider, account_key))

    async def update_provider_account_json_if_current(self, **kwargs: Any) -> bool:
        return True

    async def update_provider_credential_json_if_current(self, **kwargs: Any) -> bool:
        return True


@pytest.mark.asyncio
async def test_user_service_binds_and_lists_lxns_account():
    store = FakeAccountStore()
    service = UserService(store=store, lxns_client=FakeLxns())

    result = await service.bind_lxns_by_friend_code(
        identity=PlatformIdentity.qq("10001"),
        friend_code="1234567890",
    )

    assert result.status == "bound"
    assert result.lxns_account_key == "maimai:1234567890"

    accounts = await service.list_lxns_accounts(PlatformIdentity.qq("10001"))
    assert len(accounts) == 1
    assert accounts[0].maimai_friend_code == "1234567890"


@pytest.mark.asyncio
async def test_user_service_auto_bind_uses_provider_lookup():
    store = FakeAccountStore()
    service = UserService(store=store, lxns_client=FakeLxns())

    result = await service.ensure_lxns_bound_by_qq_lookup(
        identity=PlatformIdentity.qq("10002"),
        headers={"Authorization": "token"},
    )

    assert result.status == "bound"
    profile = await service.get_default_lxns_game_profile(PlatformIdentity.qq("10002"))
    assert profile is not None
    assert profile.maimai_name == "Mai Player"


@pytest.mark.asyncio
async def test_user_service_resolves_canonical_user_and_platform_account_key():
    store = FakeAccountStore()
    service = UserService(store=store, lxns_client=FakeLxns())

    user = await service.ensure_user_model_for_platform(PlatformIdentity.qq("10003"))
    account_key = await service.get_platform_account_key_for_user(user_id=user["id"], platform="qq")

    assert user == {"id": 1}
    assert account_key == "10003"


@pytest.mark.asyncio
async def test_user_service_oauth_credential_merges_with_friend_code_account():
    store = FakeAccountStore()
    service = UserService(store=store, lxns_client=FakeLxns())

    first = await service.bind_lxns_by_friend_code(
        identity=PlatformIdentity.qq("10004"),
        friend_code="1234567890",
    )
    second = await service.bind_lxns_oauth_tokens(
        identity=PlatformIdentity.qq("10004"),
        token_data={
            "access_token": "access",
            "refresh_token": "refresh",
            "token_expiry": "2099-01-01T00:00:00+00:00",
        },
        maimai_friend_code="1234567890",
    )

    assert first.lxns_account_key == second.lxns_account_key
    assert len(store.accounts) == 1
    assert store.credentials[("lxns", "maimai:1234567890", "oa:oauth")].credential_method == "oa"
