"""Account storage protocol for LXNS binding flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StoredLxnsAccount:
    """Legacy LXNS account record stored for a QQ user."""

    user_id: int
    qq: str
    account_key: str
    account_name: str
    schema_version: int = 1
    is_default: bool = False


@dataclass(frozen=True)
class StoredGameProfile:
    """Stored game profile fields attached to a provider account."""

    account_key: str
    account_name: str
    maimai_name: str = ""
    maimai_friend_code: str = ""
    chunithm_name: str = ""
    chunithm_friend_code: str = ""
    provider: str = "lxns"


@dataclass(frozen=True)
class StoredProviderAccount:
    """Provider account record linked to a platform identity."""

    user_id: int
    platform: str
    account_key: str
    account_name: str
    account_json: dict[str, Any]
    schema_version: int = 1
    is_default: bool = False


@dataclass(frozen=True)
class StoredProviderCredential:
    """One access method for a provider account, such as OAuth or friend code."""

    user_id: int
    provider: str
    provider_account_key: str
    credential_key: str
    credential_method: str
    credential_json: dict[str, Any]
    schema_version: int = 1
    is_default: bool = False


class AccountStore(Protocol):
    """Storage boundary used by user, auth, and player services."""

    async def ensure_user_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> int:
        """Ensure a canonical user exists for a platform identity."""

    async def ensure_user_model_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> Any:
        """Ensure a canonical user exists and return the concrete store model."""

    async def get_platform_account_key_for_user(self, *, user_id: int, platform: str) -> str | None:
        """Return one platform account key linked to a canonical user."""

    async def ensure_user_by_qq(self, qq: str) -> int:
        """Ensure a local user exists for QQ and return its user id."""

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
        """Create or update a provider account linked to a platform identity."""

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
        """Create or update one credential attached to a provider account."""

    async def get_provider_credential(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_method: str | None = None,
        credential_key: str | None = None,
    ) -> StoredProviderCredential | None:
        """Return one credential for a provider account."""

    async def update_provider_credential_json_if_current(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_key: str,
        credential_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        """Update credential JSON if the stored refresh token still matches."""

    async def upsert_lxns_account(
        self,
        *,
        qq: str,
        account_key: str,
        account_json: dict[str, Any],
        account_name: str,
    ) -> StoredLxnsAccount:
        """Create or update a LXNS account linked to the QQ user."""

    async def has_lxns_account(self, qq: str) -> bool:
        """Return whether the QQ user already has any LXNS account."""

    async def has_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> bool:
        """Return whether the platform identity has any provider account."""

    async def list_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> list[StoredProviderAccount]:
        """Return provider accounts linked to the platform identity."""

    async def set_default_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
        account_key: str,
    ) -> None:
        """Mark one provider account as default for the platform identity."""

    async def set_default_lxns_account(self, *, qq: str, account_key: str) -> None:
        """Mark one LXNS account as default for the QQ user."""

    async def get_default_game_profile_for_identity(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> StoredGameProfile | None:
        """Return the default provider game profile for the platform identity."""

    async def get_default_game_profile(self, qq: str) -> StoredGameProfile | None:
        """Return the default LXNS game profile for the QQ user."""

    async def get_game_profile_by_provider_account(
        self,
        *,
        provider: str,
        account_key: str,
    ) -> StoredGameProfile | None:
        """Return a game profile by provider account key."""

    async def upsert_game_profile(
        self,
        *,
        provider: str = "lxns",
        account_key: str,
        maimai_name: str | None = None,
        maimai_friend_code: str | None = None,
        chunithm_name: str | None = None,
        chunithm_friend_code: str | None = None,
    ) -> StoredGameProfile:
        """Create or update the game profile attached to a LXNS account."""

    async def delete_lxns_accounts(self, qq: str) -> int:
        """Delete all LXNS accounts for the QQ user and return the count."""

    async def delete_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> int:
        """Delete all provider accounts for the platform identity and return the count."""

    async def get_provider_account(
        self,
        *,
        provider: str,
        account_key: str,
    ) -> StoredProviderAccount | None:
        """Return one provider account by provider/account key."""

    async def update_provider_account_json_if_current(
        self,
        *,
        provider: str,
        account_key: str,
        account_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        """Update provider account JSON if the stored refresh token still matches."""


__all__ = [
    "AccountStore",
    "StoredGameProfile",
    "StoredLxnsAccount",
    "StoredProviderAccount",
    "StoredProviderCredential",
]
