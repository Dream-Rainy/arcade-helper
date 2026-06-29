"""Portable account store dump models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


ACCOUNT_STORE_DUMP_SCHEMA_VERSION = 1

AccountStoreConflictPolicy = Literal["keep_target", "overwrite", "fail"]


@dataclass(frozen=True)
class AccountDumpUser:
    """Canonical user record for account-store migration."""

    user_id: int
    profile_json: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "profile_json": dict(self.profile_json),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AccountDumpUser":
        return cls(
            user_id=int(data["user_id"]),
            profile_json=dict(data.get("profile_json") or {}),
        )


@dataclass(frozen=True)
class AccountDumpRecord:
    """Platform or provider account linked to a canonical user."""

    user_id: int
    platform: str
    account_key: str
    account_name: str = ""
    account_json: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    is_default: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "platform": self.platform,
            "account_key": self.account_key,
            "account_name": self.account_name,
            "account_json": dict(self.account_json),
            "schema_version": self.schema_version,
            "is_default": self.is_default,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AccountDumpRecord":
        return cls(
            user_id=int(data["user_id"]),
            platform=str(data["platform"]),
            account_key=str(data["account_key"]),
            account_name=str(data.get("account_name") or ""),
            account_json=dict(data.get("account_json") or {}),
            schema_version=int(data.get("schema_version") or 1),
            is_default=bool(data.get("is_default", False)),
        )


@dataclass(frozen=True)
class AccountStoreDump:
    """Portable account binding and OAuth token dump.

    The dump intentionally excludes game profiles, OAuth state, song catalogs,
    aliases, maps, and provider request caches.
    """

    schema_version: int = ACCOUNT_STORE_DUMP_SCHEMA_VERSION
    users: tuple[AccountDumpUser, ...] = ()
    accounts: tuple[AccountDumpRecord, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "users": [user.to_json() for user in self.users],
            "accounts": [account.to_json() for account in self.accounts],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AccountStoreDump":
        schema_version = int(data.get("schema_version") or 1)
        if schema_version != ACCOUNT_STORE_DUMP_SCHEMA_VERSION:
            raise ValueError(f"Unsupported account store dump schema version: {schema_version}")
        return cls(
            schema_version=schema_version,
            users=tuple(AccountDumpUser.from_json(item) for item in data.get("users", [])),
            accounts=tuple(AccountDumpRecord.from_json(item) for item in data.get("accounts", [])),
        )


@dataclass(frozen=True)
class AccountStoreImportResult:
    users_created: int = 0
    users_updated: int = 0
    accounts_created: int = 0
    accounts_updated: int = 0
    accounts_skipped: int = 0
    conflicts: tuple[str, ...] = ()
    dry_run: bool = False


class DumpableAccountStore(Protocol):
    async def export_account_dump(self) -> AccountStoreDump:
        """Export portable account binding and OAuth token data."""

    async def import_account_dump(
        self,
        dump: AccountStoreDump,
        *,
        conflict_policy: AccountStoreConflictPolicy = "keep_target",
        dry_run: bool = False,
    ) -> AccountStoreImportResult:
        """Import portable account binding and OAuth token data."""


__all__ = [
    "ACCOUNT_STORE_DUMP_SCHEMA_VERSION",
    "AccountDumpRecord",
    "AccountDumpUser",
    "AccountStoreConflictPolicy",
    "AccountStoreDump",
    "AccountStoreImportResult",
    "DumpableAccountStore",
]
