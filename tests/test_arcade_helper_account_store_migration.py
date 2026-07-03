from __future__ import annotations

import pytest

from arcade_helper.storage import JsonAccountStore, migrate_account_store


@pytest.mark.asyncio
async def test_migrate_account_store_json_to_json(tmp_path):
    source = JsonAccountStore(tmp_path / "source.json")
    target = JsonAccountStore(tmp_path / "target.json")

    await source.upsert_provider_account(
        identity_platform="qq",
        identity_account_key="10001",
        provider="lxns",
        account_key="oauth:abc",
        account_name="Mai",
        account_json={
            "oauth2_access_token": "access",
            "oauth2_refresh_token": "refresh",
        },
    )
    await source.upsert_game_profile(account_key="oauth:abc", maimai_name="Can Refresh Later")

    preview = await migrate_account_store(source=source, target=target, dry_run=True)
    assert preview.import_result.dry_run is True
    assert preview.import_result.accounts_created == 3

    result = await migrate_account_store(source=source, target=target)
    assert len(result.dump.users) == 1
    assert result.import_result.accounts_created == 3

    migrated = await target.get_provider_account(provider="lxns", account_key="oauth:abc")
    assert migrated is not None
    credential = await target.get_provider_credential(
        provider="lxns",
        provider_account_key="oauth:abc",
        credential_method="oa",
    )
    assert credential is not None
    assert credential.credential_json["oauth2_refresh_token"] == "refresh"

    profile = await target.get_game_profile_by_provider_account(provider="lxns", account_key="oauth:abc")
    assert profile is not None
    assert profile.maimai_name == ""
