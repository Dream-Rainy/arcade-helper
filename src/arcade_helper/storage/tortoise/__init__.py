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

MODEL_MODULE = "arcade_helper.storage.tortoise.models"

__all__ = [
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

