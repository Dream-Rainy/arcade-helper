"""Standard-library local storage adapters."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from arcade_helper.integrations.lxns.accounts import (
    StoredGameProfile,
    StoredLxnsAccount,
    StoredProviderAccount,
    StoredProviderCredential,
)
from arcade_helper.integrations.lxns.binding import LxnsProvider

from .dump import (
    ACCOUNT_STORE_DUMP_SCHEMA_VERSION,
    AccountDumpRecord,
    AccountDumpUser,
    AccountStoreConflictPolicy,
    AccountStoreDump,
    AccountStoreImportResult,
    ProviderCredentialDumpRecord,
)

QQ_PLATFORM = "qq"
LXNS_PLATFORM = LxnsProvider.lxns.value
LOCAL_ACCOUNT_STORE_SCHEMA_VERSION = 1


class JsonAccountStore:
    """JSON-backed account store for small deployments and local scripts."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()

    async def ensure_user_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> int:
        async with self._lock:
            data = self._load()
            account = self._find_account(data, platform, account_key)
            if account is not None:
                return int(account["user_id"])

            user_id = self._next_user_id(data)
            data["users"].append({"id": user_id, "profile_json": {}})
            data["accounts"].append(
                {
                    "user_id": user_id,
                    "platform": platform,
                    "account_key": account_key,
                    "account_name": account_name or f"{platform}_{account_key}",
                    "is_default": True,
                    "schema_version": 1,
                    "account_json": account_json or {"platform": platform, "account_key": account_key},
                }
            )
            self._save(data)
            return user_id

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
        async with self._lock:
            data = self._load()
            account = self._default_account_for_user(data, user_id=user_id, platform=platform)
            if account is None:
                account = next(
                    (
                        item
                        for item in data["accounts"]
                        if int(item["user_id"]) == user_id and item["platform"] == platform
                    ),
                    None,
                )
            return str(account["account_key"]) if account is not None else None

    async def ensure_user_by_qq(self, qq: str) -> int:
        return await self.ensure_user_for_platform(
            platform=QQ_PLATFORM,
            account_key=qq,
            account_name=f"QQ_{qq}",
            account_json={"qq": qq},
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
        async with self._lock:
            data = self._load()
            user_id = self._ensure_user_for_platform_unlocked(
                data,
                platform=identity_platform,
                account_key=identity_account_key,
                account_name=f"{identity_platform}_{identity_account_key}",
                account_json={"platform": identity_platform, "account_key": identity_account_key},
            )
            has_default = any(
                int(item["user_id"]) == user_id and item["provider"] == provider and bool(item.get("is_default"))
                for item in data["provider_accounts"]
            )
            account = self._find_provider_account(data, provider, account_key)
            if account is None:
                account = {
                    "user_id": user_id,
                    "provider": provider,
                    "account_key": account_key,
                    "account_name": account_name,
                    "is_default": not has_default,
                    "schema_version": 1,
                    "account_json": account_json,
                }
                data["provider_accounts"].append(account)
            else:
                account.update(
                    {
                        "user_id": user_id,
                        "account_name": account_name,
                        "schema_version": 1,
                        "account_json": account_json,
                    }
                )
                if not has_default:
                    account["is_default"] = True
            credential_method = _credential_method_from_json(account_json)
            if credential_method:
                self._upsert_provider_credential_unlocked(
                    data,
                    provider=provider,
                    provider_account_key=account_key,
                    credential_key=credential_method,
                    credential_method=credential_method,
                    credential_json=account_json,
                    is_default=True,
                )
            self._save(data)
            return _provider_account_from_json(account)

    async def upsert_lxns_account(
        self,
        *,
        qq: str,
        account_key: str,
        account_json: dict[str, Any],
        account_name: str,
    ) -> StoredLxnsAccount:
        account = await self.upsert_provider_account(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
            account_key=account_key,
            account_json=account_json,
            account_name=account_name,
        )
        return StoredLxnsAccount(
            user_id=account.user_id,
            qq=qq,
            account_key=account.account_key,
            account_name=account.account_name,
            schema_version=account.schema_version,
            is_default=account.is_default,
        )

    async def has_lxns_account(self, qq: str) -> bool:
        return await self.has_provider_account(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
        )

    async def has_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> bool:
        async with self._lock:
            data = self._load()
            user_id = self._user_id_for_platform(data, identity_platform, identity_account_key)
            return user_id is not None and any(
                int(item["user_id"]) == user_id and item["provider"] == provider for item in data["provider_accounts"]
            )

    async def list_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> list[StoredProviderAccount]:
        async with self._lock:
            data = self._load()
            user_id = self._user_id_for_platform(data, identity_platform, identity_account_key)
            if user_id is None:
                return []
            return [
                _provider_account_from_json(item)
                for item in data["provider_accounts"]
                if int(item["user_id"]) == user_id and item["provider"] == provider
            ]

    async def set_default_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
        account_key: str,
    ) -> None:
        async with self._lock:
            data = self._load()
            user_id = self._user_id_for_platform(data, identity_platform, identity_account_key)
            if user_id is None:
                return
            target = self._find_provider_account(data, provider, account_key)
            if target is None or int(target["user_id"]) != user_id:
                return
            for item in data["provider_accounts"]:
                if int(item["user_id"]) == user_id and item["provider"] == provider:
                    item["is_default"] = item["account_key"] == account_key
            self._save(data)

    async def set_default_lxns_account(self, *, qq: str, account_key: str) -> None:
        await self.set_default_provider_account(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
            account_key=account_key,
        )

    async def get_default_game_profile_for_identity(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> StoredGameProfile | None:
        async with self._lock:
            data = self._load()
            user_id = self._user_id_for_platform(data, identity_platform, identity_account_key)
            if user_id is None:
                return None
            account = self._default_provider_account_for_user(data, user_id=user_id, provider=provider)
            if account is None:
                account = next(
                    (
                        item
                        for item in data["provider_accounts"]
                        if int(item["user_id"]) == user_id and item["provider"] == provider
                    ),
                    None,
                )
            if account is None:
                return None
            return self._profile_record(data, account)

    async def get_default_game_profile(self, qq: str) -> StoredGameProfile | None:
        return await self.get_default_game_profile_for_identity(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
        )

    async def get_game_profile_by_provider_account(
        self,
        *,
        provider: str,
        account_key: str,
    ) -> StoredGameProfile | None:
        async with self._lock:
            data = self._load()
            account = self._find_provider_account(data, provider, account_key)
            return self._profile_record(data, account) if account is not None else None

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
        async with self._lock:
            data = self._load()
            credential = self._upsert_provider_credential_unlocked(
                data,
                provider=provider,
                provider_account_key=provider_account_key,
                credential_key=credential_key,
                credential_method=credential_method,
                credential_json=credential_json,
                is_default=is_default,
            )
            self._save(data)
            return _provider_credential_from_json(credential)

    async def get_provider_credential(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_method: str | None = None,
        credential_key: str | None = None,
    ) -> StoredProviderCredential | None:
        async with self._lock:
            data = self._load()
            credential = self._find_provider_credential(
                data,
                provider=provider,
                provider_account_key=provider_account_key,
                credential_method=credential_method,
                credential_key=credential_key,
            )
            return _provider_credential_from_json(credential) if credential is not None else None

    async def update_provider_credential_json_if_current(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_key: str,
        credential_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        async with self._lock:
            data = self._load()
            credential = self._find_provider_credential(
                data,
                provider=provider,
                provider_account_key=provider_account_key,
                credential_key=credential_key,
            )
            if credential is None:
                return False
            if expected_refresh_token is not None:
                stored = dict(credential.get("credential_json") or {})
                stored_refresh = stored.get("oauth2_refresh_token") or stored.get("oauth2", {}).get("refresh_token")
                if stored_refresh != expected_refresh_token:
                    return False
            credential["credential_json"] = credential_json
            self._save(data)
            return True

    async def upsert_game_profile(
        self,
        *,
        provider: str = LXNS_PLATFORM,
        account_key: str,
        maimai_name: str | None = None,
        maimai_friend_code: str | None = None,
        chunithm_name: str | None = None,
        chunithm_friend_code: str | None = None,
    ) -> StoredGameProfile:
        async with self._lock:
            data = self._load()
            account = self._find_provider_account(data, provider, account_key)
            if account is None:
                raise ValueError(f"provider account not found: {account_key}")

            profiles = data.setdefault("game_profiles", {})
            profile_key = _profile_key(provider, account_key)
            profile = dict(profiles.get(profile_key) or profiles.get(account_key) or {})
            if maimai_name is not None:
                profile["maimai_name"] = maimai_name
            if maimai_friend_code is not None:
                profile["maimai_friend_code"] = maimai_friend_code
            if chunithm_name is not None:
                profile["chunithm_name"] = chunithm_name
            if chunithm_friend_code is not None:
                profile["chunithm_friend_code"] = chunithm_friend_code
            profiles[profile_key] = profile
            self._save(data)
            return self._profile_record(data, account)

    async def delete_lxns_accounts(self, qq: str) -> int:
        return await self.delete_provider_accounts(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
        )

    async def delete_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> int:
        async with self._lock:
            data = self._load()
            user_id = self._user_id_for_platform(data, identity_platform, identity_account_key)
            if user_id is None:
                return 0
            deleted_keys = [
                item["account_key"]
                for item in data["provider_accounts"]
                if int(item["user_id"]) == user_id and item["provider"] == provider
            ]
            data["provider_accounts"] = [
                item
                for item in data["provider_accounts"]
                if not (int(item["user_id"]) == user_id and item["provider"] == provider)
            ]
            data["provider_credentials"] = [
                item
                for item in data["provider_credentials"]
                if not (
                    item["provider"] == provider
                    and item["provider_account_key"] in deleted_keys
                )
            ]
            for account_key in deleted_keys:
                data.setdefault("game_profiles", {}).pop(_profile_key(provider, account_key), None)
                data.setdefault("game_profiles", {}).pop(account_key, None)
            self._save(data)
            return len(deleted_keys)

    async def get_provider_account(
        self,
        *,
        provider: str,
        account_key: str,
    ) -> StoredProviderAccount | None:
        async with self._lock:
            data = self._load()
            account = self._find_provider_account(data, provider, account_key)
            return _provider_account_from_json(account) if account is not None else None

    async def update_provider_account_json_if_current(
        self,
        *,
        provider: str,
        account_key: str,
        account_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        async with self._lock:
            data = self._load()
            account = self._find_provider_account(data, provider, account_key)
            if account is None:
                return False
            if expected_refresh_token is not None:
                stored = dict(account.get("account_json") or {})
                stored_refresh = stored.get("oauth2_refresh_token") or stored.get("oauth2", {}).get("refresh_token")
                if stored_refresh != expected_refresh_token:
                    return False
            account["account_json"] = account_json
            credential = self._find_provider_credential(
                data,
                provider=provider,
                provider_account_key=account_key,
                credential_method=_credential_method_from_json(account_json),
            )
            if credential is not None:
                credential["credential_json"] = account_json
            self._save(data)
            return True

    async def export_account_dump(self) -> AccountStoreDump:
        async with self._lock:
            data = self._load()
            return AccountStoreDump(
                users=tuple(
                    AccountDumpUser(
                        user_id=int(item["id"]),
                        profile_json=dict(item.get("profile_json") or {}),
                    )
                    for item in data["users"]
                ),
                accounts=tuple(
                    AccountDumpRecord.from_json(item)
                    for item in data["accounts"]
                    if (item["platform"], item["account_key"])
                    not in {(row["provider"], row["account_key"]) for row in data["provider_accounts"]}
                ),
                provider_accounts=tuple(
                    AccountDumpRecord(
                        user_id=int(item["user_id"]),
                        platform=str(item["provider"]),
                        account_key=str(item["account_key"]),
                        account_name=str(item.get("account_name") or ""),
                        account_json=dict(item.get("account_json") or {}),
                        schema_version=int(item.get("schema_version") or 1),
                        is_default=bool(item.get("is_default", False)),
                    )
                    for item in data["provider_accounts"]
                ),
                provider_credentials=tuple(
                    ProviderCredentialDumpRecord(
                        user_id=int(item["user_id"]),
                        provider=str(item["provider"]),
                        provider_account_key=str(item["provider_account_key"]),
                        credential_key=str(item["credential_key"]),
                        credential_method=str(item["credential_method"]),
                        credential_json=dict(item.get("credential_json") or {}),
                        schema_version=int(item.get("schema_version") or 1),
                        is_default=bool(item.get("is_default", False)),
                    )
                    for item in data["provider_credentials"]
                ),
            )

    async def import_account_dump(
        self,
        dump: AccountStoreDump,
        *,
        conflict_policy: AccountStoreConflictPolicy = "keep_target",
        dry_run: bool = False,
    ) -> AccountStoreImportResult:
        async with self._lock:
            data = self._load()
            return self._import_dump_unlocked(data, dump, conflict_policy=conflict_policy, dry_run=dry_run)

    def _import_dump_unlocked(
        self,
        data: dict[str, Any],
        dump: AccountStoreDump,
        *,
        conflict_policy: AccountStoreConflictPolicy,
        dry_run: bool,
    ) -> AccountStoreImportResult:
        users_created = users_updated = accounts_created = accounts_updated = accounts_skipped = 0
        conflicts: list[str] = []
        users_by_id = {int(item["id"]): item for item in data["users"]}
        accounts_by_key = {(item["platform"], item["account_key"]): item for item in data["accounts"]}

        for user in dump.users:
            existing = users_by_id.get(user.user_id)
            if existing is None:
                users_created += 1
                if not dry_run:
                    data["users"].append({"id": user.user_id, "profile_json": dict(user.profile_json)})
            elif existing.get("profile_json") != user.profile_json:
                users_updated += 1
                if not dry_run and conflict_policy == "overwrite":
                    existing["profile_json"] = dict(user.profile_json)

        for account in dump.accounts:
            key = (account.platform, account.account_key)
            existing = accounts_by_key.get(key)
            incoming = account.to_json()
            if existing is None:
                accounts_created += 1
                if not dry_run:
                    data["accounts"].append(incoming)
                continue

            if existing == incoming:
                accounts_skipped += 1
                continue

            conflicts.append(f"{account.platform}:{account.account_key}")
            if conflict_policy == "fail":
                raise ValueError(f"account import conflict: {account.platform}:{account.account_key}")
            if conflict_policy == "keep_target":
                accounts_skipped += 1
                continue
            accounts_updated += 1
            if not dry_run:
                existing.update(incoming)

        provider_accounts_by_key = {
            (item["provider"], item["account_key"]): item for item in data["provider_accounts"]
        }
        for account in dump.provider_accounts:
            key = (account.platform, account.account_key)
            existing = provider_accounts_by_key.get(key)
            incoming = {
                "user_id": account.user_id,
                "provider": account.platform,
                "account_key": account.account_key,
                "account_name": account.account_name,
                "account_json": dict(account.account_json),
                "schema_version": account.schema_version,
                "is_default": account.is_default,
            }
            if existing is None:
                accounts_created += 1
                if not dry_run:
                    data["provider_accounts"].append(incoming)
                continue
            if existing == incoming:
                accounts_skipped += 1
                continue
            conflicts.append(f"{account.platform}:{account.account_key}")
            if conflict_policy == "fail":
                raise ValueError(f"provider account import conflict: {account.platform}:{account.account_key}")
            if conflict_policy == "keep_target":
                accounts_skipped += 1
                continue
            accounts_updated += 1
            if not dry_run:
                existing.update(incoming)

        credentials_by_key = {
            (item["provider"], item["provider_account_key"], item["credential_key"]): item
            for item in data["provider_credentials"]
        }
        for credential in dump.provider_credentials:
            key = (credential.provider, credential.provider_account_key, credential.credential_key)
            existing = credentials_by_key.get(key)
            incoming = credential.to_json()
            if existing is None:
                accounts_created += 1
                if not dry_run:
                    data["provider_credentials"].append(incoming)
                continue
            if existing == incoming:
                accounts_skipped += 1
                continue
            conflicts.append(f"{credential.provider}:{credential.provider_account_key}:{credential.credential_key}")
            if conflict_policy == "fail":
                raise ValueError(
                    "provider credential import conflict: "
                    f"{credential.provider}:{credential.provider_account_key}:{credential.credential_key}"
                )
            if conflict_policy == "keep_target":
                accounts_skipped += 1
                continue
            accounts_updated += 1
            if not dry_run:
                existing.update(incoming)

        if not dry_run:
            self._save(data)
        return AccountStoreImportResult(
            users_created=users_created,
            users_updated=users_updated,
            accounts_created=accounts_created,
            accounts_updated=accounts_updated,
            accounts_skipped=accounts_skipped,
            conflicts=tuple(conflicts),
            dry_run=dry_run,
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": LOCAL_ACCOUNT_STORE_SCHEMA_VERSION,
                "users": [],
                "accounts": [],
                "provider_accounts": [],
                "provider_credentials": [],
                "game_profiles": {},
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        schema_version = int(data.get("schema_version") or 1)
        if schema_version != LOCAL_ACCOUNT_STORE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported local account store schema version: {schema_version}")
        data.setdefault("users", [])
        data.setdefault("accounts", [])
        data.setdefault("provider_accounts", [])
        data.setdefault("provider_credentials", [])
        data.setdefault("game_profiles", {})
        self._migrate_legacy_provider_accounts_unlocked(data)
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["schema_version"] = LOCAL_ACCOUNT_STORE_SCHEMA_VERSION
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _next_user_id(data: dict[str, Any]) -> int:
        return max((int(item["id"]) for item in data["users"]), default=0) + 1

    @staticmethod
    def _find_account(data: dict[str, Any], platform: str, account_key: str) -> dict[str, Any] | None:
        return next(
            (item for item in data["accounts"] if item["platform"] == platform and item["account_key"] == account_key),
            None,
        )

    @staticmethod
    def _find_provider_account(data: dict[str, Any], provider: str, account_key: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in data["provider_accounts"]
                if item["provider"] == provider and item["account_key"] == account_key
            ),
            None,
        )

    @staticmethod
    def _find_provider_credential(
        data: dict[str, Any],
        *,
        provider: str,
        provider_account_key: str,
        credential_method: str | None = None,
        credential_key: str | None = None,
    ) -> dict[str, Any] | None:
        credentials = [
            item
            for item in data["provider_credentials"]
            if item["provider"] == provider and item["provider_account_key"] == provider_account_key
        ]
        if credential_key is not None:
            return next((item for item in credentials if item["credential_key"] == credential_key), None)
        if credential_method is not None:
            method_credentials = [item for item in credentials if item["credential_method"] == credential_method]
            return next((item for item in method_credentials if bool(item.get("is_default"))), None) or (
                method_credentials[0] if method_credentials else None
            )
        return next((item for item in credentials if bool(item.get("is_default"))), None) or (
            credentials[0] if credentials else None
        )

    def _user_id_for_platform(self, data: dict[str, Any], platform: str, account_key: str) -> int | None:
        account = self._find_account(data, platform, account_key)
        return int(account["user_id"]) if account is not None else None

    def _ensure_user_for_platform_unlocked(
        self,
        data: dict[str, Any],
        *,
        platform: str,
        account_key: str,
        account_name: str,
        account_json: dict[str, Any],
    ) -> int:
        existing = self._find_account(data, platform, account_key)
        if existing is not None:
            return int(existing["user_id"])
        user_id = self._next_user_id(data)
        data["users"].append({"id": user_id, "profile_json": {}})
        data["accounts"].append(
            {
                "user_id": user_id,
                "platform": platform,
                "account_key": account_key,
                "account_name": account_name,
                "is_default": True,
                "schema_version": 1,
                "account_json": account_json,
            }
        )
        return user_id

    def _migrate_legacy_provider_accounts_unlocked(self, data: dict[str, Any]) -> None:
        provider_keys = {(item["provider"], item["account_key"]) for item in data["provider_accounts"]}
        credential_keys = {
            (item["provider"], item["provider_account_key"], item["credential_key"])
            for item in data["provider_credentials"]
        }
        for account in data["accounts"]:
            if account.get("platform") != LXNS_PLATFORM:
                continue
            provider_key = (LXNS_PLATFORM, account["account_key"])
            if provider_key not in provider_keys:
                data["provider_accounts"].append(
                    {
                        "user_id": int(account["user_id"]),
                        "provider": LXNS_PLATFORM,
                        "account_key": account["account_key"],
                        "account_name": account.get("account_name") or "",
                        "account_json": dict(account.get("account_json") or {}),
                        "schema_version": int(account.get("schema_version") or 1),
                        "is_default": bool(account.get("is_default", False)),
                    }
                )
                provider_keys.add(provider_key)
            method = _credential_method_from_json(dict(account.get("account_json") or {}))
            credential_key = method or str(account["account_key"])
            key = (LXNS_PLATFORM, account["account_key"], credential_key)
            if method and key not in credential_keys:
                data["provider_credentials"].append(
                    {
                        "user_id": int(account["user_id"]),
                        "provider": LXNS_PLATFORM,
                        "provider_account_key": account["account_key"],
                        "credential_key": credential_key,
                        "credential_method": method,
                        "credential_json": dict(account.get("account_json") or {}),
                        "schema_version": int(account.get("schema_version") or 1),
                        "is_default": True,
                    }
                )
                credential_keys.add(key)

    @staticmethod
    def _default_account_for_user(data: dict[str, Any], *, user_id: int, platform: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in data["accounts"]
                if int(item["user_id"]) == user_id and item["platform"] == platform and bool(item.get("is_default"))
            ),
            None,
        )

    @staticmethod
    def _default_provider_account_for_user(data: dict[str, Any], *, user_id: int, provider: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in data["provider_accounts"]
                if int(item["user_id"]) == user_id and item["provider"] == provider and bool(item.get("is_default"))
            ),
            None,
        )

    def _upsert_provider_credential_unlocked(
        self,
        data: dict[str, Any],
        *,
        provider: str,
        provider_account_key: str,
        credential_key: str,
        credential_method: str,
        credential_json: dict[str, Any],
        is_default: bool,
    ) -> dict[str, Any]:
        account = self._find_provider_account(data, provider, provider_account_key)
        if account is None:
            raise ValueError(f"provider account not found: {provider_account_key}")
        credentials = [
            item
            for item in data["provider_credentials"]
            if item["provider"] == provider and item["provider_account_key"] == provider_account_key
        ]
        credential = next((item for item in credentials if item["credential_key"] == credential_key), None)
        has_default = any(item["credential_method"] == credential_method and bool(item.get("is_default")) for item in credentials)
        if credential is None:
            credential = {
                "user_id": int(account["user_id"]),
                "provider": provider,
                "provider_account_key": provider_account_key,
                "credential_key": credential_key,
                "credential_method": credential_method,
                "credential_json": credential_json,
                "schema_version": 1,
                "is_default": bool(is_default) or not has_default,
            }
            data["provider_credentials"].append(credential)
        else:
            credential.update(
                {
                    "user_id": int(account["user_id"]),
                    "credential_method": credential_method,
                    "credential_json": credential_json,
                    "schema_version": 1,
                }
            )
            if is_default or not has_default:
                credential["is_default"] = True
        if credential.get("is_default"):
            for item in credentials:
                if item is not credential and item["credential_method"] == credential_method:
                    item["is_default"] = False
        return credential

    def _profile_record(self, data: dict[str, Any], account: dict[str, Any]) -> StoredGameProfile:
        provider = str(account.get("provider") or account.get("platform") or "")
        account_key = str(account["account_key"])
        profiles = data.setdefault("game_profiles", {})
        profile = dict(profiles.get(_profile_key(provider, account_key)) or profiles.get(account_key) or {})
        return StoredGameProfile(
            account_key=account_key,
            account_name=str(account.get("account_name") or ""),
            provider=provider,
            maimai_name=str(profile.get("maimai_name") or ""),
            maimai_friend_code=str(profile.get("maimai_friend_code") or ""),
            chunithm_name=str(profile.get("chunithm_name") or ""),
            chunithm_friend_code=str(profile.get("chunithm_friend_code") or ""),
        )


def _provider_account_from_json(account: dict[str, Any]) -> StoredProviderAccount:
    return StoredProviderAccount(
        user_id=int(account["user_id"]),
        platform=str(account.get("provider") or account.get("platform") or ""),
        account_key=str(account["account_key"]),
        account_name=str(account.get("account_name") or ""),
        account_json=dict(account.get("account_json") or {}),
        schema_version=int(account.get("schema_version") or 1),
        is_default=bool(account.get("is_default", False)),
    )


def _provider_credential_from_json(credential: dict[str, Any]) -> StoredProviderCredential:
    return StoredProviderCredential(
        user_id=int(credential["user_id"]),
        provider=str(credential["provider"]),
        provider_account_key=str(credential["provider_account_key"]),
        credential_key=str(credential["credential_key"]),
        credential_method=str(credential["credential_method"]),
        credential_json=dict(credential.get("credential_json") or {}),
        schema_version=int(credential.get("schema_version") or 1),
        is_default=bool(credential.get("is_default", False)),
    )


def _credential_method_from_json(payload: dict[str, Any]) -> str:
    method = payload.get("method")
    if method:
        return str(method)
    if payload.get("oauth2_access_token") or payload.get("oauth2_refresh_token") or payload.get("oauth2"):
        return "oa"
    if payload.get("unique_code"):
        return "unique_code"
    if payload.get("api_key"):
        return "api_key"
    return ""


def _profile_key(provider: str, account_key: str) -> str:
    return f"{provider}:{account_key}"


__all__ = ["JsonAccountStore", "LOCAL_ACCOUNT_STORE_SCHEMA_VERSION"]
