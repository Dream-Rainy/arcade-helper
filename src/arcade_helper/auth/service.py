"""Authentication and OAuth flows for external providers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from arcade_helper.core.errors import AccountNotBoundError, OAuthStateMismatchError, TokenRefreshError
from arcade_helper.integrations.lxns.accounts import AccountStore
from arcade_helper.integrations.lxns.binding import LxnsProvider
from arcade_helper.integrations.lxns.oauth_client import OauthClient
from arcade_helper.users.identity import PlatformIdentity
from arcade_helper.users.service import UserBindResult, UserService

LXNS_PLATFORM = LxnsProvider.lxns.value


@dataclass(frozen=True)
class OAuthFlow:
    """OAuth bind URL and state returned by ``start_lxns_oauth``."""

    provider: str
    state: str
    bind_uri: str
    timeout_seconds: int


@dataclass(frozen=True)
class OAuthBindResult:
    """Result of completing an OAuth callback and storing provider tokens."""

    status: Literal["bound", "error"]
    message: str
    account_key: str = ""


class AuthService:
    """Owns OAuth state validation, token exchange, and token refresh."""

    def __init__(
        self,
        *,
        store: AccountStore,
        users: UserService,
        lxns_client,
        oauth: OauthClient | None,
        logger=None,
        ensure_started=None,
    ) -> None:
        self._store = store
        self._users = users
        self._lxns = lxns_client
        self._oauth = oauth
        self._logger = logger
        self._ensure_started = ensure_started or (lambda: None)
        self._refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}

    @property
    def lxns_oauth(self) -> OauthClient:
        """Configured LXNS OAuth client, or raise if OAuth is unavailable."""
        if self._oauth is None:
            raise OAuthStateMismatchError("LXNS OAuth is not configured")
        return self._oauth

    def start_lxns_oauth(self, identity: PlatformIdentity) -> OAuthFlow:
        """Create an LXNS OAuth state and authorization URL for one platform identity."""
        self._ensure_started()
        oauth = self.lxns_oauth
        state = oauth.add_wait_bind_user(identity.cache_key)
        return OAuthFlow(
            provider=LXNS_PLATFORM,
            state=state,
            bind_uri=oauth.get_bind_uri(state),
            timeout_seconds=oauth.state_ttl_seconds,
        )

    def get_lxns_bind_result(self, identity: PlatformIdentity) -> dict | None:
        """Return the latest in-memory OAuth bind result for an identity."""
        self._ensure_started()
        oauth = self.lxns_oauth
        return oauth.get_bind_result_by_user(identity.cache_key) or oauth.get_bind_result_by_user(identity.account_key)

    def get_lxns_pending_state(self, identity: PlatformIdentity) -> str | None:
        """Return the pending OAuth state for an identity, if one exists."""
        self._ensure_started()
        oauth = self.lxns_oauth
        oauth.cleanup_wait_bind_user()
        return next(
            (
                state
                for state, data in oauth.wait_bind_user.items()
                if data.get("user_id_hash") in {identity.cache_key, identity.account_key}
            ),
            None,
        )

    async def complete_lxns_oauth(
        self,
        *,
        identity: PlatformIdentity,
        code: str,
        state: str,
    ) -> OAuthBindResult:
        """Validate OAuth state, exchange the code, and bind LXNS tokens."""
        self._ensure_started()
        oauth = self.lxns_oauth
        try:
            self._validate_lxns_state(identity=identity, state=state)
            token_data = await oauth.get_token(code)
            result = await self.bind_lxns_token_data(identity=identity, token_data=token_data)

            oauth.remove_wait_bind_user(state)
            oauth.mark_bind_result(
                state=state,
                user_id_hash=identity.cache_key,
                status="bound",
                message="OAuth 绑定成功",
                account_key=result.lxns_account_key,
            )
            oauth.mark_bind_result(
                state=state,
                user_id_hash=identity.account_key,
                status="bound",
                message="OAuth 绑定成功",
                account_key=result.lxns_account_key,
            )
            return OAuthBindResult(status="bound", message="OAuth 绑定成功", account_key=result.lxns_account_key)
        except Exception as exc:  # noqa: BLE001
            oauth.mark_bind_result(
                state=state,
                user_id_hash=identity.cache_key,
                status="error",
                message=str(exc),
            )
            oauth.mark_bind_result(
                state=state,
                user_id_hash=identity.account_key,
                status="error",
                message=str(exc),
            )
            return OAuthBindResult(status="error", message=f"OAuth 绑定过程中出现错误：{exc}")

    async def bind_lxns_token_data(self, *, identity: PlatformIdentity, token_data: dict) -> UserBindResult:
        """Bind already-exchanged LXNS OAuth token data to a platform identity."""
        self._ensure_started()
        access_token = token_data["access_token"]
        maimai_response = await self._lxns.maimai.user_player(access_token)
        chuni_response = await self._lxns.chunithm.user_player(access_token)

        maimai_name, maimai_friend_code = _extract_name_and_code(maimai_response)
        chuni_name, chuni_friend_code = _extract_name_and_code(chuni_response)
        return await self._users.bind_lxns_oauth_tokens(
            identity=identity,
            token_data=token_data,
            account_name=maimai_name or chuni_name or None,
            maimai_name=maimai_name,
            maimai_friend_code=maimai_friend_code,
            chunithm_name=chuni_name,
            chunithm_friend_code=chuni_friend_code,
        )

    async def refresh_lxns_profile_for_identity(self, identity: PlatformIdentity) -> tuple[str, str]:
        """Refresh stored game profile data for the identity's default LXNS account."""
        self._ensure_started()
        profile = await self._users.require_default_lxns_game_profile(identity)
        return await self.refresh_lxns_profile_for_account(account_key=profile.account_key)

    async def refresh_lxns_profile_for_account(self, *, account_key: str) -> tuple[str, str]:
        """Refresh stored game profile data for one LXNS provider account."""
        self._ensure_started()
        token_data = await self.ensure_lxns_token_valid(account_key=account_key)
        account = await self._store.get_provider_account(provider=LXNS_PLATFORM, account_key=account_key)
        if account is None:
            raise AccountNotBoundError(f"LXNS account not found: {account_key}")

        maimai_response = await self._lxns.maimai.user_player(token_data["access_token"])
        chuni_response = await self._lxns.chunithm.user_player(token_data["access_token"])
        maimai_name, maimai_friend_code = _extract_name_and_code(maimai_response)
        chuni_name, chuni_friend_code = _extract_name_and_code(chuni_response)
        if not any([maimai_name, maimai_friend_code, chuni_name, chuni_friend_code]):
            raise AccountNotBoundError("无法获取用户信息，请检查OAuth2.0令牌是否有效")

        await self._store.upsert_game_profile(
            account_key=account_key,
            maimai_name=maimai_name or None,
            maimai_friend_code=maimai_friend_code or None,
            chunithm_name=chuni_name or None,
            chunithm_friend_code=chuni_friend_code or None,
        )
        return maimai_name, chuni_name

    async def ensure_lxns_token_valid(self, *, account_key: str) -> dict:
        """Return valid LXNS token data, refreshing and storing it when needed."""
        self._ensure_started()
        account = await self._store.get_provider_account(provider=LXNS_PLATFORM, account_key=account_key)
        if account is None:
            raise AccountNotBoundError(f"LXNS account not found: {account_key}")

        token_data = _token_data_from_account_json(account.account_json)
        if _token_is_valid(token_data):
            return token_data

        lock_key = (LXNS_PLATFORM, account_key)
        lock = self._refresh_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            latest = await self._store.get_provider_account(provider=LXNS_PLATFORM, account_key=account_key)
            if latest is None:
                raise AccountNotBoundError(f"LXNS account not found: {account_key}")

            latest_token_data = _token_data_from_account_json(latest.account_json)
            if _token_is_valid(latest_token_data):
                return latest_token_data

            refresh_token = latest_token_data.get("refresh_token", "")
            try:
                refreshed = await self.lxns_oauth.refresh_token(refresh_token)
            except Exception as exc:  # noqa: BLE001
                reread = await self._store.get_provider_account(provider=LXNS_PLATFORM, account_key=account_key)
                if reread is not None:
                    reread_token_data = _token_data_from_account_json(reread.account_json)
                    if _token_is_valid(reread_token_data):
                        return reread_token_data
                raise TokenRefreshError("无法刷新 access token，可能是 refresh token 已过期或无效") from exc

            updated_json = dict(latest.account_json)
            updated_json.update(
                {
                    "oauth2_access_token": refreshed["access_token"],
                    "oauth2_refresh_token": refreshed["refresh_token"],
                    "oauth2_token_expiry": refreshed["token_expiry"].isoformat(),
                    "oauth2_refresh_expiry": refreshed.get("refresh_expiry").isoformat()
                    if refreshed.get("refresh_expiry")
                    else None,
                }
            )
            ok = await self._store.update_provider_account_json_if_current(
                provider=LXNS_PLATFORM,
                account_key=account_key,
                account_json=updated_json,
                expected_refresh_token=refresh_token,
            )
            if not ok:
                reread = await self._store.get_provider_account(provider=LXNS_PLATFORM, account_key=account_key)
                if reread is not None:
                    reread_token_data = _token_data_from_account_json(reread.account_json)
                    if _token_is_valid(reread_token_data):
                        return reread_token_data
                raise TokenRefreshError("OAuth token refresh write conflicted")

            return _token_data_from_account_json(updated_json)

    def _validate_lxns_state(self, *, identity: PlatformIdentity, state: str) -> dict:
        oauth = self.lxns_oauth
        try:
            return oauth.validate_wait_bind_user(state, user_id_hash=identity.cache_key)
        except Exception:
            return oauth.validate_wait_bind_user(state, user_id_hash=identity.account_key)


def _extract_name_and_code(response: dict) -> tuple[str, str]:
    data = response.get("data") if isinstance(response, dict) else None
    payload = data if isinstance(data, dict) else response
    name = payload.get("name", "") if isinstance(payload, dict) else ""
    friend_code = payload.get("friend_code", "") if isinstance(payload, dict) else ""
    return name or "", friend_code or ""


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _token_data_from_account_json(account_json: dict) -> dict:
    legacy = account_json.get("oauth2", {}) if isinstance(account_json.get("oauth2"), dict) else {}
    access_token = account_json.get("oauth2_access_token") or legacy.get("access_token") or ""
    refresh_token = account_json.get("oauth2_refresh_token") or legacy.get("refresh_token") or ""
    token_expiry = _parse_datetime(account_json.get("oauth2_token_expiry") or legacy.get("token_expiry"))
    refresh_expiry = _parse_datetime(account_json.get("oauth2_refresh_expiry") or legacy.get("refresh_expiry"))
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expiry": token_expiry,
        "refresh_expiry": refresh_expiry,
    }


def _token_is_valid(token_data: dict) -> bool:
    if not token_data.get("access_token") or not token_data.get("refresh_token"):
        return False
    expiry = token_data.get("token_expiry")
    if not isinstance(expiry, datetime):
        return False
    now = datetime.now(expiry.tzinfo) if expiry.tzinfo is not None else datetime.now(UTC).replace(tzinfo=None)
    return expiry > now + timedelta(seconds=15)


__all__ = ["AuthService", "OAuthBindResult", "OAuthFlow"]
