from __future__ import annotations

import json

import pytest

from arcade_helper.storage import AccountDumpRecord, AccountDumpUser, AccountStoreDump, JsonAccountStore


@pytest.mark.asyncio
async def test_json_account_store_binds_defaults_and_updates_token(tmp_path):
    store = JsonAccountStore(tmp_path / "accounts.json")

    account = await store.upsert_provider_account(
        identity_platform="qq",
        identity_account_key="10001",
        provider="lxns",
        account_key="oauth:abc",
        account_name="Mai",
        account_json={
            "oauth2_access_token": "old-access",
            "oauth2_refresh_token": "old-refresh",
        },
    )

    assert account.user_id == 1
    assert account.is_default is True
    assert await store.has_provider_account(
        identity_platform="qq",
        identity_account_key="10001",
        provider="lxns",
    )

    profile = await store.upsert_game_profile(
        account_key="oauth:abc",
        maimai_name="Mai",
        maimai_friend_code="1234567890",
    )
    assert profile.maimai_friend_code == "1234567890"

    ok = await store.update_provider_account_json_if_current(
        provider="lxns",
        account_key="oauth:abc",
        account_json={
            "oauth2_access_token": "new-access",
            "oauth2_refresh_token": "new-refresh",
        },
        expected_refresh_token="old-refresh",
    )
    assert ok is True

    conflict = await store.update_provider_account_json_if_current(
        provider="lxns",
        account_key="oauth:abc",
        account_json={"oauth2_refresh_token": "lost"},
        expected_refresh_token="old-refresh",
    )
    assert conflict is False

    stored = await store.get_provider_account(provider="lxns", account_key="oauth:abc")
    assert stored is not None
    assert stored.account_json["oauth2_access_token"] == "new-access"

    raw = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1


@pytest.mark.asyncio
async def test_json_account_store_game_profiles_are_provider_scoped(tmp_path):
    store = JsonAccountStore(tmp_path / "accounts.json")
    for provider, name in [("lxns", "LXNS"), ("other", "Other")]:
        await store.upsert_provider_account(
            identity_platform="qq",
            identity_account_key="10001",
            provider=provider,
            account_key="same",
            account_name=name,
            account_json={"provider": provider},
        )
        await store.upsert_game_profile(provider=provider, account_key="same", maimai_name=name)

    lxns = await store.get_game_profile_by_provider_account(provider="lxns", account_key="same")
    other = await store.get_game_profile_by_provider_account(provider="other", account_key="same")

    assert lxns is not None
    assert other is not None
    assert lxns.maimai_name == "LXNS"
    assert other.maimai_name == "Other"


@pytest.mark.asyncio
async def test_json_account_store_dump_excludes_game_profiles(tmp_path):
    store = JsonAccountStore(tmp_path / "accounts.json")
    await store.upsert_provider_account(
        identity_platform="qq",
        identity_account_key="10001",
        provider="lxns",
        account_key="oauth:abc",
        account_name="Mai",
        account_json={"oauth2_refresh_token": "refresh"},
    )
    await store.upsert_game_profile(account_key="oauth:abc", maimai_name="Not Migrated")

    dump = await store.export_account_dump()

    assert len(dump.users) == 1
    assert {account.platform for account in dump.accounts} == {"qq"}
    assert {account.platform for account in dump.provider_accounts} == {"lxns"}
    assert {credential.credential_method for credential in dump.provider_credentials} == {"oa"}
    assert "game_profiles" not in dump.to_json()


@pytest.mark.asyncio
async def test_json_account_store_import_conflict_policies(tmp_path):
    source_dump = AccountStoreDump(
        users=(AccountDumpUser(user_id=1),),
        accounts=(
            AccountDumpRecord(
                user_id=1,
                platform="qq",
                account_key="10001",
                account_name="QQ_10001",
                account_json={"qq": "10001"},
                is_default=True,
            ),
        ),
    )
    target = JsonAccountStore(tmp_path / "target.json")
    await target.import_account_dump(source_dump)

    changed = AccountStoreDump(
        users=(AccountDumpUser(user_id=1),),
        accounts=(
            AccountDumpRecord(
                user_id=1,
                platform="qq",
                account_key="10001",
                account_name="Changed",
                account_json={"qq": "changed"},
                is_default=True,
            ),
        ),
    )

    dry_run = await target.import_account_dump(changed, conflict_policy="overwrite", dry_run=True)
    assert dry_run.dry_run is True
    assert dry_run.accounts_updated == 1

    kept = await target.import_account_dump(changed, conflict_policy="keep_target")
    assert kept.accounts_skipped == 1
    account = await target.get_platform_account_key_for_user(user_id=1, platform="qq")
    assert account == "10001"

    overwritten = await target.import_account_dump(changed, conflict_policy="overwrite")
    assert overwritten.accounts_updated == 1
    dump = await target.export_account_dump()
    assert dump.accounts[0].account_name == "Changed"
