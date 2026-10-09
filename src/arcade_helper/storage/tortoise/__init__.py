"""Tortoise ORM storage for external game and integration data."""

from .account_store import TortoiseAccountStore
from .migration import migrate_tortoise_account_store_v2
from .models import (
    ChuniSong,
    ChuniSongAlias,
    GameProfile,
    LXNS_PLATFORM,
    MaiMap,
    MaiMapTreasure,
    MaiSong,
    MaiSongAlias,
    ProviderAccount,
    ProviderCredential,
    QQ_PLATFORM,
    User,
    UserAccount,
    ensure_user_by_qq,
    get_user_by_qq,
)
from .song_store import TortoiseSongStore
from .catalog_store import CatalogConflictError, TortoiseCatalogStore
from .catalog_migration import initialize_catalog_storage, migrate_catalog_identities, upgrade_catalog_schema

CATALOG_MODEL_MODULE = "arcade_helper.storage.tortoise.catalog_models"
ALIAS_MODEL_MODULE = "arcade_helper.storage.tortoise.alias_models"

MODEL_MODULE = "arcade_helper.storage.tortoise.models"

__all__ = [
    "upgrade_catalog_schema",
    "ALIAS_MODEL_MODULE",
    "CATALOG_MODEL_MODULE",
    "CatalogConflictError",
    "TortoiseCatalogStore",
    "initialize_catalog_storage",
    "migrate_catalog_identities",
    "ChuniSong",
    "ChuniSongAlias",
    "GameProfile",
    "LXNS_PLATFORM",
    "MODEL_MODULE",
    "MaiMap",
    "MaiMapTreasure",
    "MaiSong",
    "MaiSongAlias",
    "ProviderAccount",
    "ProviderCredential",
    "QQ_PLATFORM",
    "TortoiseAccountStore",
    "TortoiseSongStore",
    "User",
    "UserAccount",
    "ensure_user_by_qq",
    "get_user_by_qq",
    "migrate_tortoise_account_store_v2",
]

