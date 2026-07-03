"""Tortoise account-store schema migrations."""

from __future__ import annotations

import json
from typing import Any

from tortoise import Tortoise
from tortoise.exceptions import IntegrityError

from .models import GameProfile, LXNS_PLATFORM, ProviderAccount, ProviderCredential, UserAccount


async def migrate_tortoise_account_store_v2() -> int:
    """Migrate legacy LXNS user-account rows into provider accounts/credentials."""

    await Tortoise.generate_schemas(safe=True)
    # ponytail: this migrates data; rebuild old game_profiles FK constraints per deployed DB dialect if enforced.
    profiles = {int(row["account_id"]): row for row in await GameProfile.all().values()}
    migrated = 0

    for account in await UserAccount.filter(platform=LXNS_PLATFORM).order_by("id"):
        profile = profiles.get(int(account.id), {})
        provider_key = _provider_account_key(account, profile)
        provider_account = await _ensure_provider_account(account, provider_key)
        if bool(account.is_default) and not provider_account.is_default:
            provider_account.is_default = True
            await provider_account.save()

        method = _credential_method(account)
        if method:
            await _upsert_credential(
                provider_account,
                credential_key=str(account.account_key),
                method=method,
                credential_json=_json(account.account_json),
                schema_version=int(account.schema_version),
            )

        if profile:
            await _move_profile(profile, provider_account)
        migrated += 1

    return migrated


async def _ensure_provider_account(account: UserAccount, provider_key: str) -> ProviderAccount:
    existing = await ProviderAccount.get_or_none(provider=LXNS_PLATFORM, account_key=provider_key)
    if existing is not None:
        if not existing.account_name and account.account_name:
            existing.account_name = account.account_name
            await existing.save()
        return existing
    try:
        return await ProviderAccount.create(
            id=int(account.id),
            user_id=int(account.user_id),
            provider=LXNS_PLATFORM,
            account_key=provider_key,
            account_name=account.account_name,
            is_default=bool(account.is_default),
            schema_version=int(account.schema_version),
            account_json=_json(account.account_json),
        )
    except IntegrityError:
        return await ProviderAccount.create(
            user_id=int(account.user_id),
            provider=LXNS_PLATFORM,
            account_key=provider_key,
            account_name=account.account_name,
            is_default=bool(account.is_default),
            schema_version=int(account.schema_version),
            account_json=_json(account.account_json),
        )


async def _upsert_credential(
    account: ProviderAccount,
    *,
    credential_key: str,
    method: str,
    credential_json: dict,
    schema_version: int,
) -> None:
    credential = await ProviderCredential.get_or_none(account=account, credential_key=credential_key)
    if credential is None:
        await ProviderCredential.create(
            account=account,
            credential_key=credential_key,
            credential_method=method,
            credential_json=credential_json,
            is_default=True,
            schema_version=schema_version,
        )
        return
    credential.credential_method = method
    credential.credential_json = credential_json
    credential.is_default = True
    credential.schema_version = schema_version
    await credential.save()


async def _move_profile(profile: dict, account: ProviderAccount) -> None:
    existing = await GameProfile.get_or_none(account_id=account.id)
    if existing is None:
        await GameProfile.filter(id=profile["id"]).update(account_id=account.id, platform=LXNS_PLATFORM)
        return

    changed = False
    for field in ("maimai_name", "maimai_friend_code", "chunithm_name", "chunithm_friend_code"):
        if not getattr(existing, field) and profile.get(field):
            setattr(existing, field, profile[field])
            changed = True
    if changed:
        await existing.save()
    if int(profile["id"]) != int(existing.id):
        await GameProfile.filter(id=profile["id"]).delete()


def _provider_account_key(account: UserAccount, profile: dict[str, Any]) -> str:
    if profile.get("maimai_friend_code"):
        return f"maimai:{profile['maimai_friend_code']}"
    if profile.get("chunithm_friend_code"):
        return f"chunithm:{profile['chunithm_friend_code']}"
    payload = _json(account.account_json)
    if payload.get("unique_code"):
        return f"maimai:{payload['unique_code']}"
    return str(account.account_key)


def _credential_method(account: UserAccount) -> str:
    payload = _json(account.account_json)
    if payload.get("method"):
        return str(payload["method"])
    key = str(account.account_key)
    if key.startswith("unique_code:"):
        return "unique_code"
    if key.startswith("oa:"):
        return "oa"
    if key.startswith("api_key:"):
        return "api_key"
    return ""


def _json(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


__all__ = ["migrate_tortoise_account_store_v2"]
