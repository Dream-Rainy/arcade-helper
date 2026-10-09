from __future__ import annotations

import json

import pytest
from tortoise import Tortoise

from arcade_helper.storage.tortoise import migrate_tortoise_account_store_v2
from arcade_helper.storage.tortoise.models import GameProfile, ProviderAccount, ProviderCredential


@pytest.mark.asyncio
async def test_alias_index_migration_is_additive_repeatable_and_used_by_exact_lookup(tmp_path):
    from arcade_helper.storage.tortoise import CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE, upgrade_catalog_schema
    await Tortoise.init(db_url=f"sqlite://{tmp_path / 'catalog.sqlite3'}",
                        modules={'models': [CATALOG_MODEL_MODULE, ALIAS_MODEL_MODULE]})
    try:
        await Tortoise.generate_schemas()
        db = Tortoise.get_connection('default')
        planned = await upgrade_catalog_schema(dry_run=True)
        assert len(planned) == 1 and 'CREATE INDEX' in planned[0]
        assert await upgrade_catalog_schema(dry_run=False) == planned
        assert await upgrade_catalog_schema(dry_run=False) == []
        plan = await db.execute_query_dict("EXPLAIN QUERY PLAN SELECT id FROM alias_evidence WHERE alias_key=? AND kind=?", ['flower', 'alias'])
        assert any('idx_alias_evidence_key_kind' in row['detail'] for row in plan)
    finally:
        await Tortoise.close_connections()


@pytest.mark.asyncio
async def test_tortoise_account_store_v2_migration_merges_lxns_credentials(tmp_path):
    await Tortoise.init(
        db_url=f"sqlite://{tmp_path / 'accounts.sqlite3'}",
        modules={"models": ["arcade_helper.storage.tortoise.models"]},
    )
    try:
        conn = Tortoise.get_connection("default")
        await conn.execute_script(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                profile_json JSON NOT NULL DEFAULT '{}'
            );
            CREATE TABLE user_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                platform VARCHAR(32) NOT NULL,
                account_name VARCHAR(64),
                account_key VARCHAR(128) NOT NULL,
                is_default INT NOT NULL DEFAULT 0,
                schema_version INT NOT NULL DEFAULT 1,
                account_json JSON NOT NULL DEFAULT '{}',
                UNIQUE(platform, account_key)
            );
            CREATE TABLE game_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                account_id INT NOT NULL UNIQUE REFERENCES user_accounts(id) ON DELETE CASCADE,
                platform VARCHAR(32) NOT NULL,
                maimai_name VARCHAR(64),
                maimai_friend_code VARCHAR(32),
                chunithm_name VARCHAR(64),
                chunithm_friend_code VARCHAR(32),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await conn.execute_query("INSERT INTO users (id, profile_json) VALUES (1, '{}')")
        await conn.execute_query(
            """
            INSERT INTO user_accounts
                (id, user_id, platform, account_name, account_key, is_default, schema_version, account_json)
            VALUES
                (1, 1, 'qq', 'QQ_10001', '10001', 1, 1, ?),
                (2, 1, 'lxns', 'Mai', 'unique_code:1234567890', 1, 1, ?),
                (3, 1, 'lxns', 'OAuth', 'oa:oauth', 0, 1, ?)
            """,
            [
                json.dumps({"qq": "10001"}),
                json.dumps({"method": "unique_code", "unique_code": "1234567890"}),
                json.dumps({"method": "oa", "oauth2_refresh_token": "refresh"}),
            ],
        )
        await conn.execute_query(
            """
            INSERT INTO game_profiles
                (id, account_id, platform, maimai_name, maimai_friend_code)
            VALUES
                (1, 2, 'lxns', 'Mai', '1234567890'),
                (2, 3, 'lxns', NULL, '1234567890')
            """
        )

        migrated = await migrate_tortoise_account_store_v2()

        account = await ProviderAccount.get(provider="lxns", account_key="maimai:1234567890")
        credentials = await ProviderCredential.filter(account=account).order_by("credential_key")
        profile = await GameProfile.get(account=account)

        assert migrated == 2
        assert account.user_id == 1
        assert [item.credential_key for item in credentials] == ["oa:oauth", "unique_code:1234567890"]
        assert credentials[0].credential_json["oauth2_refresh_token"] == "refresh"
        assert profile.maimai_name == "Mai"
        assert await GameProfile.all().count() == 1
    finally:
        await Tortoise.close_connections()
