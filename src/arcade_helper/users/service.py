"""User and account services for external game data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from arcade_helper.core.errors import DefaultAccountNotFoundError
from arcade_helper.integrations.lxns.accounts import (
    AccountStore,
    StoredGameProfile,
    StoredProviderAccount,
)
from arcade_helper.integrations.lxns.binding import (
    LxnsBindRequest,
    LxnsOAuthCredential,
    LxnsProvider,
    LxnsUniqueCodeCredential,
    build_account_key,
    build_oauth_fallback_account_key,
    build_provider_account_key,
    serialize_account_json,
)

from .identity import PlatformIdentity

LXNS_PLATFORM = LxnsProvider.lxns.value


@dataclass(frozen=True)
class ExternalUser:
    """Canonical external-data user handle."""

    user_id: int
    identity: PlatformIdentity


@dataclass(frozen=True)
class UserBindResult:
    """Result of binding a platform identity to an LXNS account."""

    status: Literal["bound", "already_bound", "not_found", "error"]
    message: str
    lxns_account_key: str = ""
    user_id: int | None = None


@dataclass(frozen=True)
class UserUnbindResult:
    """Result of removing LXNS accounts from a platform identity."""

    status: Literal["unbound", "not_bound", "error"]
    message: str
    deleted_count: int = 0


@dataclass(frozen=True)
class LinkedAccountSummary:
    """Display-friendly summary of a linked provider account."""

    account_key: str
    account_name: str
    provider: str
    is_default: bool = False
    maimai_friend_code: str = ""
    maimai_name: str = ""
    chunithm_friend_code: str = ""
    chunithm_name: str = ""


class UserService:
    """Owns platform-user resolution and provider account operations."""

    def __init__(self, *, store: AccountStore, lxns_client, logger=None, ensure_started=None) -> None:
        self._store = store
        self._lxns = lxns_client
        self._logger = logger
        self._ensure_started = ensure_started or (lambda: None)

    async def from_platform(self, platform: str, account_key: str, account_name: str | None = None) -> ExternalUser:
        """Ensure and return the canonical user for a platform account."""
        self._ensure_started()
        identity = PlatformIdentity(platform=platform, account_key=account_key, account_name=account_name)
        user_id = await self._store.ensure_user_for_platform(
            platform=identity.platform,
            account_key=identity.account_key,
            account_name=identity.account_name,
            account_json={"platform": identity.platform, "account_key": identity.account_key},
        )
        return ExternalUser(user_id=user_id, identity=identity)

    async def ensure_user_model_for_platform(self, identity: PlatformIdentity):
        """Ensure the backing store user row/model for a platform identity."""
        self._ensure_started()
        return await self._store.ensure_user_model_for_platform(
            platform=identity.platform,
            account_key=identity.account_key,
            account_name=identity.account_name,
            account_json={"platform": identity.platform, "account_key": identity.account_key},
        )

    async def get_platform_account_key_for_user(self, *, user_id: int, platform: str) -> str | None:
        """Resolve a platform account key from an internal user id."""
        self._ensure_started()
        return await self._store.get_platform_account_key_for_user(user_id=user_id, platform=platform)

    async def bind_lxns_by_friend_code(
        self,
        *,
        identity: PlatformIdentity,
        friend_code: str,
        account_name: str | None = None,
    ) -> UserBindResult:
        """Bind an LXNS account using a maimai friend code."""
        self._ensure_started()
        req = LxnsBindRequest(
            qq=identity.account_key,
            credential=LxnsUniqueCodeCredential(unique_code=friend_code),
        )
        credential_key = build_account_key(req)
        provider_account_key = build_provider_account_key(maimai_friend_code=friend_code)
        stored = await self._store.upsert_provider_account(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
            account_key=provider_account_key,
            account_json={"provider": LXNS_PLATFORM, "maimai_friend_code": friend_code},
            account_name=account_name or f"lxns_user_{identity.account_key}",
        )
        await self._store.upsert_provider_credential(
            provider=LXNS_PLATFORM,
            provider_account_key=stored.account_key,
            credential_key=credential_key,
            credential_method=req.credential.method.value,
            credential_json=serialize_account_json(req),
            is_default=True,
        )
        await self._store.upsert_game_profile(
            provider=LXNS_PLATFORM,
            account_key=stored.account_key,
            maimai_friend_code=friend_code,
        )
        return UserBindResult(
            status="bound",
            message="\n绑定成功！现在可以使用所有功能了~",
            lxns_account_key=stored.account_key,
            user_id=stored.user_id,
        )

    async def has_lxns_account(self, identity: PlatformIdentity) -> bool:
        """Return whether the identity has any linked LXNS account."""
        self._ensure_started()
        return await self._store.has_provider_account(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
        )

    async def get_default_lxns_game_profile(self, identity: PlatformIdentity) -> StoredGameProfile | None:
        """Return the default LXNS game profile for an identity, if bound."""
        self._ensure_started()
        return await self._store.get_default_game_profile_for_identity(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
        )

    async def require_default_lxns_game_profile(self, identity: PlatformIdentity) -> StoredGameProfile:
        """Return the default LXNS game profile or raise when none is bound."""
        self._ensure_started()
        profile = await self.get_default_lxns_game_profile(identity)
        if profile is None:
            raise DefaultAccountNotFoundError(f"No default LXNS account for {identity.cache_key}")
        return profile

    async def list_lxns_accounts(self, identity: PlatformIdentity) -> list[LinkedAccountSummary]:
        """List LXNS accounts linked to a platform identity."""
        self._ensure_started()
        accounts = await self._store.list_provider_accounts(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
        )
        summaries: list[LinkedAccountSummary] = []
        default_profile = await self.get_default_lxns_game_profile(identity)
        for account in accounts:
            profile = await self._profile_for_account(account)
            summaries.append(
                LinkedAccountSummary(
                    account_key=account.account_key,
                    account_name=account.account_name,
                    provider=account.platform,
                    is_default=account.is_default,
                    maimai_friend_code=profile.maimai_friend_code if profile else "",
                    maimai_name=profile.maimai_name if profile else "",
                    chunithm_friend_code=profile.chunithm_friend_code if profile else "",
                    chunithm_name=profile.chunithm_name if profile else "",
                )
            )

        if default_profile is not None:
            summaries = [
                summary
                if summary.account_key != default_profile.account_key
                else LinkedAccountSummary(
                    account_key=summary.account_key,
                    account_name=summary.account_name,
                    provider=summary.provider,
                    is_default=True,
                    maimai_friend_code=summary.maimai_friend_code,
                    maimai_name=summary.maimai_name,
                    chunithm_friend_code=summary.chunithm_friend_code,
                    chunithm_name=summary.chunithm_name,
                )
                for summary in summaries
            ]
        return summaries

    async def set_default_lxns_account(self, *, identity: PlatformIdentity, account_key: str) -> None:
        """Mark one linked LXNS account as the identity's default account."""
        self._ensure_started()
        await self._store.set_default_provider_account(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
            account_key=account_key,
        )

    async def unbind_lxns(self, identity: PlatformIdentity) -> UserUnbindResult:
        """Remove all LXNS accounts linked to a platform identity."""
        self._ensure_started()
        if not await self.has_lxns_account(identity):
            return UserUnbindResult(status="not_bound", message="未找到该用户")

        deleted = await self._store.delete_provider_accounts(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
        )
        if deleted <= 0:
            return UserUnbindResult(status="not_bound", message="未绑定")

        return UserUnbindResult(status="unbound", message="解绑成功", deleted_count=deleted)

    async def ensure_lxns_bound_by_qq_lookup(
        self,
        *,
        identity: PlatformIdentity,
        headers: dict,
    ) -> UserBindResult:
        """Bind by QQ lookup only when the identity is not already bound."""
        self._ensure_started()
        if await self.has_lxns_account(identity):
            return UserBindResult(status="already_bound", message="用户已绑定")
        return await self.bind_lxns_by_qq_lookup(identity=identity, headers=headers)

    async def bind_lxns_by_qq_lookup(
        self,
        *,
        identity: PlatformIdentity,
        headers: dict,
    ) -> UserBindResult:
        """Find an LXNS maimai profile by QQ and bind its friend code."""
        self._ensure_started()
        if identity.platform != "qq":
            return UserBindResult(status="error", message="当前自动绑定只支持 QQ 身份")

        qq_response = await self._lxns.maimai.player_by_qq(identity.account_key, headers=headers)
        if not qq_response.get("success"):
            return UserBindResult(
                status="not_found",
                message="在 LXNS 中未找到您的信息，请确保您已在 LXNS 网站上注册并绑定了 QQ 号。",
            )

        qq_data = qq_response.get("data", {})
        friend_code = str(qq_data.get("friend_code", ""))
        if not friend_code:
            return UserBindResult(
                status="error",
                message="从 LXNS 获取的数据不完整（缺少 friend_code），请稍后再试。",
            )

        player_response = await self._lxns.maimai.player(friend_code, headers=headers)
        if not player_response.get("success"):
            return UserBindResult(status="error", message="无法获取玩家详细信息，请稍后再试。")

        player_data = player_response.get("data", {})
        player_name = str(player_data.get("name", "") or "")
        result = await self.bind_lxns_by_friend_code(
            identity=identity,
            friend_code=friend_code,
            account_name=player_name or None,
        )
        await self._store.upsert_game_profile(
            provider=LXNS_PLATFORM,
            account_key=result.lxns_account_key,
            maimai_name=player_name,
            maimai_friend_code=friend_code,
        )
        return UserBindResult(
            status="bound",
            message=f"自动绑定成功！欢迎 {player_name}~",
            lxns_account_key=result.lxns_account_key,
            user_id=result.user_id,
        )

    async def bind_lxns_oauth_tokens(
        self,
        *,
        identity: PlatformIdentity,
        token_data: dict,
        account_name: str | None = None,
        maimai_name: str = "",
        maimai_friend_code: str = "",
        chunithm_name: str = "",
        chunithm_friend_code: str = "",
    ) -> UserBindResult:
        """Bind an LXNS account using OAuth token data and game profile fields."""
        self._ensure_started()
        credential = LxnsOAuthCredential(
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            token_expiry=token_data["token_expiry"],
            refresh_expiry=token_data.get("refresh_expiry"),
        )
        req = LxnsBindRequest(qq=identity.account_key, credential=credential)
        provider_account_key = build_provider_account_key(
            maimai_friend_code=maimai_friend_code,
            chunithm_friend_code=chunithm_friend_code,
            fallback=build_oauth_fallback_account_key(str(token_data["refresh_token"])),
        )
        stored = await self._store.upsert_provider_account(
            identity_platform=identity.platform,
            identity_account_key=identity.account_key,
            provider=LXNS_PLATFORM,
            account_key=provider_account_key,
            account_json={
                "provider": LXNS_PLATFORM,
                "maimai_friend_code": maimai_friend_code,
                "chunithm_friend_code": chunithm_friend_code,
            },
            account_name=account_name or maimai_name or chunithm_name or f"lxns_user_{identity.account_key}",
        )
        await self._store.upsert_provider_credential(
            provider=LXNS_PLATFORM,
            provider_account_key=stored.account_key,
            credential_key=build_account_key(req),
            credential_method=credential.method.value,
            credential_json=serialize_account_json(req),
            is_default=True,
        )
        await self._store.upsert_game_profile(
            provider=LXNS_PLATFORM,
            account_key=stored.account_key,
            maimai_name=maimai_name or None,
            maimai_friend_code=maimai_friend_code or None,
            chunithm_name=chunithm_name or None,
            chunithm_friend_code=chunithm_friend_code or None,
        )
        return UserBindResult(
            status="bound",
            message="OAuth 绑定成功",
            lxns_account_key=stored.account_key,
            user_id=stored.user_id,
        )

    async def _profile_for_account(self, account: StoredProviderAccount) -> StoredGameProfile | None:
        return await self._store.get_game_profile_by_provider_account(
            provider=account.platform,
            account_key=account.account_key,
        )


__all__ = [
    "ExternalUser",
    "LinkedAccountSummary",
    "UserBindResult",
    "UserService",
    "UserUnbindResult",
]
