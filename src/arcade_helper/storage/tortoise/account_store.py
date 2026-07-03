"""Tortoise-backed AccountStore implementation for LXNS integrations."""

from __future__ import annotations

from typing import Any

from arcade_helper.integrations.lxns.accounts import (
    StoredGameProfile,
    StoredLxnsAccount,
    StoredProviderAccount,
    StoredProviderCredential,
)
from arcade_helper.storage.dump import (
    AccountDumpRecord,
    AccountDumpUser,
    AccountStoreConflictPolicy,
    AccountStoreDump,
    AccountStoreImportResult,
    ProviderCredentialDumpRecord,
)

from .models import (
    GameProfile,
    LXNS_PLATFORM,
    ProviderAccount,
    ProviderCredential,
    QQ_PLATFORM,
    User,
    UserAccount,
)


class TortoiseAccountStore:
    async def ensure_user_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> int:
        user = await self._get_user_by_platform(platform, account_key)
        if user is not None:
            return int(user.id)

        user = await User.create(profile_json={})
        await UserAccount.create(
            user=user,
            platform=platform,
            account_key=account_key,
            account_name=account_name or f"{platform}_{account_key}",
            is_default=True,
            schema_version=1,
            account_json=account_json or {"platform": platform, "account_key": account_key},
        )
        return int(user.id)

    async def ensure_user_model_for_platform(
        self,
        *,
        platform: str,
        account_key: str,
        account_name: str | None = None,
        account_json: dict[str, Any] | None = None,
    ) -> User:
        await self.ensure_user_for_platform(
            platform=platform,
            account_key=account_key,
            account_name=account_name,
            account_json=account_json,
        )
        user = await self._get_user_by_platform(platform, account_key)
        if user is None:
            raise ValueError(f"无法创建平台用户: {platform}:{account_key}")
        return user

    async def get_platform_account_key_for_user(self, *, user_id: int, platform: str) -> str | None:
        account = await UserAccount.get_or_none(user_id=user_id, platform=platform, is_default=True)
        if account is None:
            account = await UserAccount.filter(user_id=user_id, platform=platform).order_by("id").first()
        return str(account.account_key) if account is not None else None

    async def ensure_user_by_qq(self, qq: str) -> int:
        return await self.ensure_user_for_platform(
            platform=QQ_PLATFORM,
            account_key=qq,
            account_name=f"QQ_{qq}",
            account_json={"qq": qq},
        )

    async def _get_user_by_platform(self, platform: str, account_key: str) -> User | None:
        link = await UserAccount.get_or_none(platform=platform, account_key=account_key).prefetch_related("user")
        return link.user if link else None

    async def _get_user_by_qq(self, qq: str) -> User | None:
        return await self._get_user_by_platform(QQ_PLATFORM, qq)

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
        await self.ensure_user_for_platform(
            platform=identity_platform,
            account_key=identity_account_key,
            account_json={"platform": identity_platform, "account_key": identity_account_key},
        )
        user = await self._get_user_by_platform(identity_platform, identity_account_key)
        if user is None:
            raise ValueError(f"无法创建平台用户: {identity_platform}:{identity_account_key}")

        has_default = await ProviderAccount.get_or_none(
            user=user,
            provider=provider,
            is_default=True,
        )

        obj = await ProviderAccount.get_or_none(provider=provider, account_key=account_key)
        if obj is None:
            obj = await ProviderAccount.create(
                user=user,
                provider=provider,
                account_key=account_key,
                account_name=account_name,
                is_default=has_default is None,
                schema_version=1,
                account_json=account_json,
            )
        else:
            obj.user = user
            obj.schema_version = 1
            obj.account_json = account_json
            obj.account_name = account_name
            if has_default is None:
                obj.is_default = True
            await obj.save()

        credential_method = _credential_method_from_json(account_json)
        if credential_method:
            await self.upsert_provider_credential(
                provider=provider,
                provider_account_key=account_key,
                credential_key=credential_method,
                credential_method=credential_method,
                credential_json=account_json,
                is_default=True,
            )
        return _provider_account_record(obj)

    async def upsert_lxns_account(
        self,
        *,
        qq: str,
        account_key: str,
        account_json: dict[str, Any],
        account_name: str,
    ) -> StoredLxnsAccount:
        obj = await self.upsert_provider_account(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
            account_key=account_key,
            account_json=account_json,
            account_name=account_name,
        )
        return StoredLxnsAccount(
            user_id=obj.user_id,
            qq=qq,
            account_key=obj.account_key,
            account_name=obj.account_name,
            schema_version=obj.schema_version,
            is_default=obj.is_default,
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
        user = await self._get_user_by_platform(identity_platform, identity_account_key)
        if user is None:
            return False
        return await ProviderAccount.filter(user=user, provider=provider).exists()

    async def list_provider_accounts(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> list[StoredProviderAccount]:
        user = await self._get_user_by_platform(identity_platform, identity_account_key)
        if user is None:
            return []

        rows = await ProviderAccount.filter(user=user, provider=provider).order_by("id")
        return [_provider_account_record(row) for row in rows]

    async def set_default_provider_account(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
        account_key: str,
    ) -> None:
        user = await self._get_user_by_platform(identity_platform, identity_account_key)
        if user is None:
            return

        account = await ProviderAccount.get_or_none(user=user, provider=provider, account_key=account_key)
        if account is None:
            return

        await ProviderAccount.filter(user=user, provider=provider, is_default=True).update(is_default=False)
        account.is_default = True
        await account.save()

    async def set_default_lxns_account(self, *, qq: str, account_key: str) -> None:
        await self.set_default_provider_account(
            identity_platform=QQ_PLATFORM,
            identity_account_key=qq,
            provider=LXNS_PLATFORM,
            account_key=account_key,
        )

    async def _default_provider_account_for_identity(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> ProviderAccount | None:
        user = await self._get_user_by_platform(identity_platform, identity_account_key)
        if user is None:
            return None

        default_acc = await ProviderAccount.get_or_none(user=user, provider=provider, is_default=True)
        if default_acc is not None:
            return default_acc

        provider_accounts = await ProviderAccount.filter(user=user, provider=provider).order_by("-id")
        return provider_accounts[0] if provider_accounts else None

    async def get_default_game_profile_for_identity(
        self,
        *,
        identity_platform: str,
        identity_account_key: str,
        provider: str,
    ) -> StoredGameProfile | None:
        default_acc = await self._default_provider_account_for_identity(
            identity_platform=identity_platform,
            identity_account_key=identity_account_key,
            provider=provider,
        )
        if default_acc is None:
            return None

        gp = await GameProfile.get_or_none(account=default_acc)
        if gp is None:
            return StoredGameProfile(
                account_key=str(default_acc.account_key),
                account_name=str(default_acc.account_name or ""),
                provider=str(default_acc.provider),
            )
        return _profile_record(default_acc, gp)

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
        account = await ProviderAccount.get_or_none(provider=provider, account_key=account_key)
        if account is None:
            return None

        gp = await GameProfile.get_or_none(account=account)
        if gp is None:
            return StoredGameProfile(
                account_key=str(account.account_key),
                account_name=str(account.account_name or ""),
                provider=str(account.provider),
            )
        return _profile_record(account, gp)

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
        account = await ProviderAccount.get(provider=provider, account_key=provider_account_key)
        credentials = ProviderCredential.filter(account=account, credential_method=credential_method)
        has_default = await credentials.filter(is_default=True).exists()
        obj = await ProviderCredential.get_or_none(account=account, credential_key=credential_key)
        if obj is None:
            obj = await ProviderCredential.create(
                account=account,
                credential_key=credential_key,
                credential_method=credential_method,
                credential_json=credential_json,
                is_default=bool(is_default) or not has_default,
                schema_version=1,
            )
        else:
            obj.credential_method = credential_method
            obj.credential_json = credential_json
            obj.schema_version = 1
            if is_default or not has_default:
                obj.is_default = True
            await obj.save()
        if obj.is_default:
            await ProviderCredential.filter(account=account, credential_method=credential_method).exclude(id=obj.id).update(
                is_default=False
            )
        return _provider_credential_record(account, obj)

    async def get_provider_credential(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_method: str | None = None,
        credential_key: str | None = None,
    ) -> StoredProviderCredential | None:
        account = await ProviderAccount.get_or_none(provider=provider, account_key=provider_account_key)
        if account is None:
            return None
        query = ProviderCredential.filter(account=account)
        if credential_key is not None:
            obj = await query.filter(credential_key=credential_key).first()
        elif credential_method is not None:
            obj = await query.filter(credential_method=credential_method, is_default=True).first()
            if obj is None:
                obj = await query.filter(credential_method=credential_method).order_by("id").first()
        else:
            obj = await query.filter(is_default=True).first()
            if obj is None:
                obj = await query.order_by("id").first()
        return _provider_credential_record(account, obj) if obj is not None else None

    async def update_provider_credential_json_if_current(
        self,
        *,
        provider: str,
        provider_account_key: str,
        credential_key: str,
        credential_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        account = await ProviderAccount.get_or_none(provider=provider, account_key=provider_account_key)
        if account is None:
            return False
        obj = await ProviderCredential.get_or_none(account=account, credential_key=credential_key)
        if obj is None:
            return False
        if expected_refresh_token is not None:
            stored = obj.credential_json or {}
            stored_refresh = stored.get("oauth2_refresh_token") or stored.get("oauth2", {}).get("refresh_token")
            if stored_refresh != expected_refresh_token:
                return False
        obj.credential_json = credential_json
        await obj.save()
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
        provider_account = await ProviderAccount.get(provider=provider, account_key=account_key)
        gp = await GameProfile.get_or_none(account=provider_account)
        if gp is None:
            gp = await GameProfile.create(account=provider_account, platform=provider)

        if maimai_name is not None:
            gp.maimai_name = maimai_name
        if maimai_friend_code is not None:
            gp.maimai_friend_code = maimai_friend_code
        if chunithm_name is not None:
            gp.chunithm_name = chunithm_name
        if chunithm_friend_code is not None:
            gp.chunithm_friend_code = chunithm_friend_code
        await gp.save()
        return _profile_record(provider_account, gp)

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
        user = await self._get_user_by_platform(identity_platform, identity_account_key)
        if user is None:
            return 0

        provider_accounts = await ProviderAccount.filter(user=user, provider=provider)
        deleted = 0
        for acc in provider_accounts:
            await acc.delete()
            deleted += 1
        return deleted

    async def get_provider_account(
        self,
        *,
        provider: str,
        account_key: str,
    ) -> StoredProviderAccount | None:
        obj = await ProviderAccount.get_or_none(provider=provider, account_key=account_key).prefetch_related("user")
        return _provider_account_record(obj) if obj is not None else None

    async def update_provider_account_json_if_current(
        self,
        *,
        provider: str,
        account_key: str,
        account_json: dict[str, Any],
        expected_refresh_token: str | None = None,
    ) -> bool:
        obj = await ProviderAccount.get_or_none(provider=provider, account_key=account_key)
        if obj is None:
            return False

        if expected_refresh_token is not None:
            stored = obj.account_json or {}
            stored_refresh = stored.get("oauth2_refresh_token") or stored.get("oauth2", {}).get("refresh_token")
            if stored_refresh != expected_refresh_token:
                return False

        obj.account_json = account_json
        credential = await ProviderCredential.get_or_none(
            account=obj,
            credential_method=_credential_method_from_json(account_json),
        )
        if credential is not None:
            credential.credential_json = account_json
            await credential.save()
        await obj.save()
        return True

    async def export_account_dump(self) -> AccountStoreDump:
        users = await User.all().order_by("id")
        accounts = await UserAccount.all().order_by("id")
        provider_accounts = await ProviderAccount.all().order_by("id")
        credentials = await ProviderCredential.all().prefetch_related("account").order_by("id")
        return AccountStoreDump(
            users=tuple(
                AccountDumpUser(
                    user_id=int(user.id),
                    profile_json=dict(user.profile_json or {}),
                )
                for user in users
            ),
            accounts=tuple(
                AccountDumpRecord(
                    user_id=int(account.user_id),
                    platform=str(account.platform),
                    account_key=str(account.account_key),
                    account_name=str(account.account_name or ""),
                    account_json=dict(account.account_json or {}),
                    schema_version=int(account.schema_version),
                    is_default=bool(account.is_default),
                )
                for account in accounts
            ),
            provider_accounts=tuple(
                AccountDumpRecord(
                    user_id=int(account.user_id),
                    platform=str(account.provider),
                    account_key=str(account.account_key),
                    account_name=str(account.account_name or ""),
                    account_json=dict(account.account_json or {}),
                    schema_version=int(account.schema_version),
                    is_default=bool(account.is_default),
                )
                for account in provider_accounts
            ),
            provider_credentials=tuple(
                ProviderCredentialDumpRecord(
                    user_id=int(credential.account.user_id),
                    provider=str(credential.account.provider),
                    provider_account_key=str(credential.account.account_key),
                    credential_key=str(credential.credential_key),
                    credential_method=str(credential.credential_method),
                    credential_json=dict(credential.credential_json or {}),
                    schema_version=int(credential.schema_version),
                    is_default=bool(credential.is_default),
                )
                for credential in credentials
            ),
        )

    async def import_account_dump(
        self,
        dump: AccountStoreDump,
        *,
        conflict_policy: AccountStoreConflictPolicy = "keep_target",
        dry_run: bool = False,
    ) -> AccountStoreImportResult:
        users_created = users_updated = accounts_created = accounts_updated = accounts_skipped = 0
        conflicts: list[str] = []

        for user_record in dump.users:
            user = await User.get_or_none(id=user_record.user_id)
            if user is None:
                users_created += 1
                if not dry_run:
                    await User.create(id=user_record.user_id, profile_json=dict(user_record.profile_json))
                continue

            if dict(user.profile_json or {}) != user_record.profile_json:
                users_updated += 1
                if not dry_run and conflict_policy == "overwrite":
                    user.profile_json = dict(user_record.profile_json)
                    await user.save()

        for account_record in dump.accounts:
            user = await User.get_or_none(id=account_record.user_id)
            if user is None:
                users_created += 1
                if not dry_run:
                    user = await User.create(id=account_record.user_id, profile_json={})

            account = await UserAccount.get_or_none(
                platform=account_record.platform,
                account_key=account_record.account_key,
            )
            if account is None:
                accounts_created += 1
                if not dry_run:
                    if user is None:
                        user = await User.get(id=account_record.user_id)
                    await UserAccount.create(
                        user=user,
                        platform=account_record.platform,
                        account_key=account_record.account_key,
                        account_name=account_record.account_name,
                        is_default=account_record.is_default,
                        schema_version=account_record.schema_version,
                        account_json=dict(account_record.account_json),
                    )
                continue

            if _account_matches_dump(account, account_record):
                accounts_skipped += 1
                continue

            conflicts.append(f"{account_record.platform}:{account_record.account_key}")
            if conflict_policy == "fail":
                raise ValueError(f"account import conflict: {account_record.platform}:{account_record.account_key}")
            if conflict_policy == "keep_target":
                accounts_skipped += 1
                continue

            accounts_updated += 1
            if not dry_run:
                if user is None:
                    user = await User.get(id=account_record.user_id)
                account.user = user
                account.account_name = account_record.account_name
                account.is_default = account_record.is_default
                account.schema_version = account_record.schema_version
                account.account_json = dict(account_record.account_json)
                await account.save()

        for account_record in dump.provider_accounts:
            user = await User.get_or_none(id=account_record.user_id)
            if user is None:
                users_created += 1
                if not dry_run:
                    user = await User.create(id=account_record.user_id, profile_json={})

            account = await ProviderAccount.get_or_none(
                provider=account_record.platform,
                account_key=account_record.account_key,
            )
            if account is None:
                accounts_created += 1
                if not dry_run:
                    if user is None:
                        user = await User.get(id=account_record.user_id)
                    await ProviderAccount.create(
                        user=user,
                        provider=account_record.platform,
                        account_key=account_record.account_key,
                        account_name=account_record.account_name,
                        is_default=account_record.is_default,
                        schema_version=account_record.schema_version,
                        account_json=dict(account_record.account_json),
                    )
                continue

            if _provider_account_matches_dump(account, account_record):
                accounts_skipped += 1
                continue

            conflicts.append(f"{account_record.platform}:{account_record.account_key}")
            if conflict_policy == "fail":
                raise ValueError(f"provider account import conflict: {account_record.platform}:{account_record.account_key}")
            if conflict_policy == "keep_target":
                accounts_skipped += 1
                continue

            accounts_updated += 1
            if not dry_run:
                if user is None:
                    user = await User.get(id=account_record.user_id)
                account.user = user
                account.account_name = account_record.account_name
                account.is_default = account_record.is_default
                account.schema_version = account_record.schema_version
                account.account_json = dict(account_record.account_json)
                await account.save()

        for credential_record in dump.provider_credentials:
            account = await ProviderAccount.get_or_none(
                provider=credential_record.provider,
                account_key=credential_record.provider_account_key,
            )
            if account is None:
                accounts_skipped += 1
                continue

            credential = await ProviderCredential.get_or_none(
                account=account,
                credential_key=credential_record.credential_key,
            )
            if credential is None:
                accounts_created += 1
                if not dry_run:
                    await ProviderCredential.create(
                        account=account,
                        credential_key=credential_record.credential_key,
                        credential_method=credential_record.credential_method,
                        credential_json=dict(credential_record.credential_json),
                        schema_version=credential_record.schema_version,
                        is_default=credential_record.is_default,
                    )
                continue

            if _provider_credential_matches_dump(credential, credential_record):
                accounts_skipped += 1
                continue

            conflicts.append(
                f"{credential_record.provider}:"
                f"{credential_record.provider_account_key}:"
                f"{credential_record.credential_key}"
            )
            if conflict_policy == "fail":
                raise ValueError(
                    "provider credential import conflict: "
                    f"{credential_record.provider}:"
                    f"{credential_record.provider_account_key}:"
                    f"{credential_record.credential_key}"
                )
            if conflict_policy == "keep_target":
                accounts_skipped += 1
                continue

            accounts_updated += 1
            if not dry_run:
                credential.credential_method = credential_record.credential_method
                credential.credential_json = dict(credential_record.credential_json)
                credential.schema_version = credential_record.schema_version
                credential.is_default = credential_record.is_default
                await credential.save()

        return AccountStoreImportResult(
            users_created=users_created,
            users_updated=users_updated,
            accounts_created=accounts_created,
            accounts_updated=accounts_updated,
            accounts_skipped=accounts_skipped,
            conflicts=tuple(conflicts),
            dry_run=dry_run,
        )


def _profile_record(account: ProviderAccount, profile: GameProfile) -> StoredGameProfile:
    return StoredGameProfile(
        account_key=str(account.account_key),
        account_name=str(account.account_name or ""),
        provider=str(account.provider),
        maimai_name=str(profile.maimai_name or ""),
        maimai_friend_code=str(profile.maimai_friend_code or ""),
        chunithm_name=str(profile.chunithm_name or ""),
        chunithm_friend_code=str(profile.chunithm_friend_code or ""),
    )


def _provider_account_record(account: ProviderAccount) -> StoredProviderAccount:
    return StoredProviderAccount(
        user_id=int(account.user_id),
        platform=str(account.provider),
        account_key=str(account.account_key),
        account_name=str(account.account_name or ""),
        account_json=dict(account.account_json or {}),
        schema_version=int(account.schema_version),
        is_default=bool(account.is_default),
    )


def _provider_credential_record(
    account: ProviderAccount,
    credential: ProviderCredential,
) -> StoredProviderCredential:
    return StoredProviderCredential(
        user_id=int(account.user_id),
        provider=str(account.provider),
        provider_account_key=str(account.account_key),
        credential_key=str(credential.credential_key),
        credential_method=str(credential.credential_method),
        credential_json=dict(credential.credential_json or {}),
        schema_version=int(credential.schema_version),
        is_default=bool(credential.is_default),
    )


def _account_matches_dump(account: UserAccount, dump: AccountDumpRecord) -> bool:
    return (
        int(account.user_id) == dump.user_id
        and str(account.platform) == dump.platform
        and str(account.account_key) == dump.account_key
        and str(account.account_name or "") == dump.account_name
        and dict(account.account_json or {}) == dump.account_json
        and int(account.schema_version) == dump.schema_version
        and bool(account.is_default) == dump.is_default
    )


def _provider_account_matches_dump(account: ProviderAccount, dump: AccountDumpRecord) -> bool:
    return (
        int(account.user_id) == dump.user_id
        and str(account.provider) == dump.platform
        and str(account.account_key) == dump.account_key
        and str(account.account_name or "") == dump.account_name
        and dict(account.account_json or {}) == dump.account_json
        and int(account.schema_version) == dump.schema_version
        and bool(account.is_default) == dump.is_default
    )


def _provider_credential_matches_dump(
    credential: ProviderCredential,
    dump: ProviderCredentialDumpRecord,
) -> bool:
    return (
        str(credential.credential_key) == dump.credential_key
        and str(credential.credential_method) == dump.credential_method
        and dict(credential.credential_json or {}) == dump.credential_json
        and int(credential.schema_version) == dump.schema_version
        and bool(credential.is_default) == dump.is_default
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


__all__ = ["TortoiseAccountStore"]

