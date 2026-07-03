"""Tortoise models for external account and game catalog data."""

from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


QQ_PLATFORM = "qq"
LXNS_PLATFORM = "lxns"


class User(Model):
    """主用户表：仅存全局唯一 user id + 可序列化 profile"""

    id = fields.IntField(primary_key=True)
    profile_json = fields.JSONField(default=dict)

    class Meta(Model.Meta):
        table = "users"


class UserAccount(Model):
    """用户账号表：一个用户可绑定多个平台/多个账号；账号信息 JSON 化"""

    id = fields.IntField(primary_key=True)

    user: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User",
        related_name="accounts",
        on_delete=fields.CASCADE,
        db_index=True,
    )

    platform = fields.CharField(max_length=32, db_index=True)
    account_name = fields.CharField(max_length=64, null=True, default="User Account")
    account_key = fields.CharField(max_length=128)
    is_default = fields.BooleanField(default=False, db_index=True)
    schema_version = fields.IntField(default=1)
    account_json = fields.JSONField(default=dict)

    class Meta(Model.Meta):
        table = "user_accounts"
        unique_together = (("platform", "account_key"),)
        indexes = (
            ("user_id", "platform"),
            ("user_id", "platform", "is_default"),
            ("platform", "account_key"),
        )


class ProviderAccount(Model):
    """External provider account owned by one canonical user."""

    id = fields.IntField(primary_key=True)

    user: fields.ForeignKeyRelation[User] = fields.ForeignKeyField(
        "models.User",
        related_name="provider_accounts",
        on_delete=fields.CASCADE,
        db_index=True,
    )

    provider = fields.CharField(max_length=32, db_index=True)
    account_key = fields.CharField(max_length=128)
    account_name = fields.CharField(max_length=64, null=True, default="Provider Account")
    is_default = fields.BooleanField(default=False, db_index=True)
    schema_version = fields.IntField(default=1)
    account_json = fields.JSONField(default=dict)

    class Meta(Model.Meta):
        table = "provider_accounts"
        unique_together = (("provider", "account_key"),)
        indexes = (
            ("user_id", "provider"),
            ("user_id", "provider", "is_default"),
            ("provider", "account_key"),
        )


class ProviderCredential(Model):
    """One binding or access method for a provider account."""

    id = fields.IntField(primary_key=True)

    account: fields.ForeignKeyRelation[ProviderAccount] = fields.ForeignKeyField(
        "models.ProviderAccount",
        related_name="credentials",
        on_delete=fields.CASCADE,
        db_index=True,
    )

    credential_key = fields.CharField(max_length=128)
    credential_method = fields.CharField(max_length=32, db_index=True)
    credential_json = fields.JSONField(default=dict)
    is_default = fields.BooleanField(default=False, db_index=True)
    schema_version = fields.IntField(default=1)

    class Meta(Model.Meta):
        table = "provider_credentials"
        unique_together = (("account_id", "credential_key"),)
        indexes = (
            ("account_id", "credential_method"),
            ("account_id", "credential_method", "is_default"),
        )


class GameProfile(Model):
    """游戏侧资料单独存表，并与某个 ProviderAccount 绑定。"""

    id = fields.IntField(primary_key=True)

    account: fields.OneToOneRelation[ProviderAccount] = fields.OneToOneField(
        "models.ProviderAccount",
        related_name="game_profile",
        on_delete=fields.CASCADE,
        db_index=True,
    )

    platform = fields.CharField(max_length=32, db_index=True)

    maimai_name = fields.CharField(max_length=64, null=True)
    maimai_friend_code = fields.CharField(max_length=32, null=True, db_index=True)

    chunithm_name = fields.CharField(max_length=64, null=True)
    chunithm_friend_code = fields.CharField(max_length=32, null=True, db_index=True)

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "game_profiles"


async def get_user_by_qq(qq: str) -> User | None:
    """通过 QQ 号定位用户（返回 User 或 None）。"""

    link = await UserAccount.get_or_none(platform=QQ_PLATFORM, account_key=qq).prefetch_related("user")
    return link.user if link else None


async def ensure_user_by_qq(qq: str) -> User:
    """通过 QQ 号定位/创建用户（不存在则创建 User 与 QQ 绑定记录）。"""

    user = await get_user_by_qq(qq)
    if user is not None:
        return user

    user = await User.create(profile_json={})
    await UserAccount.create(
        user=user,
        platform=QQ_PLATFORM,
        account_key=qq,
        account_name=f"QQ_{qq}",
        is_default=True,
        schema_version=1,
        account_json={"qq": qq},
    )
    return user


class MaiSong(Model):
    """Maimai 乐曲主表：存储乐曲基础信息"""

    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=256, db_index=True)
    artist = fields.CharField(max_length=256, null=True)
    category = fields.CharField(max_length=64, null=True, db_index=True)
    bpm = fields.IntField(null=True)
    version = fields.CharField(max_length=32, null=True, db_index=True)
    rights = fields.TextField(null=True)
    mai_map = fields.CharField(max_length=256, null=True, db_index=True)

    release_date = fields.CharField(max_length=32, null=True)
    image_name = fields.CharField(max_length=512, null=True)
    is_new = fields.BooleanField(default=False)
    is_locked = fields.BooleanField(default=False)
    comment = fields.TextField(null=True)

    difficulties = fields.JSONField(default=dict)
    collections = fields.JSONField(default=list)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "mai_songs"


class MaiMap(Model):
    """Maimai 地图数据表：存储从 Map XML 解析的地图信息"""

    id = fields.IntField(primary_key=True)
    data_name = fields.CharField(max_length=128)
    map_name = fields.CharField(max_length=256, db_index=True)
    is_collabo = fields.BooleanField(default=False)
    is_infinity = fields.BooleanField(default=False)
    island_id = fields.IntField(null=True)
    island_name = fields.CharField(max_length=256, null=True)
    color_id = fields.IntField(null=True)
    color_name = fields.CharField(max_length=256, null=True)
    bonus_music_id = fields.IntField(null=True, db_index=True)
    bonus_music_name = fields.CharField(max_length=256, null=True)
    bonus_music_magnification = fields.IntField(null=True)
    open_event_id = fields.IntField(null=True)
    open_event_name = fields.CharField(max_length=256, null=True)
    net_open_name_id = fields.IntField(null=True)
    net_open_name = fields.CharField(max_length=256, null=True)
    treasures = fields.JSONField(default=list)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "mai_maps"


class MaiMapTreasure(Model):
    """Maimai 地图奖励数据表：存储从 MapTreasure XML 解析的奖励信息"""

    id = fields.IntField(primary_key=True)
    data_name = fields.CharField(max_length=128)
    treasure_name = fields.CharField(max_length=256, db_index=True)
    treasure_type = fields.CharField(max_length=64, db_index=True)
    character_id = fields.IntField(null=True)
    character_name = fields.CharField(max_length=256, null=True)
    music_id = fields.IntField(null=True, db_index=True)
    music_name = fields.CharField(max_length=256, null=True)
    numeric = fields.IntField(null=True)
    name_plate_id = fields.IntField(null=True)
    name_plate_name = fields.CharField(max_length=256, null=True)
    frame_id = fields.IntField(null=True)
    frame_name = fields.CharField(max_length=256, null=True)
    title_id = fields.IntField(null=True)
    title_name = fields.CharField(max_length=256, null=True)
    icon_id = fields.IntField(null=True)
    icon_name = fields.CharField(max_length=256, null=True)
    challenge_id = fields.IntField(null=True)
    challenge_name = fields.CharField(max_length=256, null=True)
    gate_id = fields.IntField(null=True)
    gate_name = fields.CharField(max_length=256, null=True)
    key_id = fields.IntField(null=True)
    key_name = fields.CharField(max_length=256, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "mai_map_treasures"


class MaiSongAlias(Model):
    """Maimai 乐曲别名表：一首乐曲可以有多个别名"""

    id = fields.IntField(primary_key=True)
    song: fields.ForeignKeyRelation[MaiSong] = fields.ForeignKeyField(
        "models.MaiSong",
        related_name="aliases",
        on_delete=fields.CASCADE,
        db_index=True,
    )
    alias = fields.CharField(max_length=256, db_index=True)
    priority = fields.IntField(default=0)

    class Meta(Model.Meta):
        table = "mai_song_aliases"
        unique_together = (("song_id", "alias"),)


class ChuniSong(Model):
    """Chunithm 乐曲主表：存储乐曲基础信息"""

    id = fields.IntField(primary_key=True)
    title = fields.CharField(max_length=256, db_index=True)
    artist = fields.CharField(max_length=256, null=True)
    genre = fields.CharField(max_length=64, null=True, db_index=True)
    bpm = fields.IntField(null=True)
    version = fields.IntField(null=True, db_index=True)
    rights = fields.TextField(null=True)
    image_name = fields.CharField(max_length=512, null=True)
    difficulties = fields.JSONField(default=dict)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "chuni_songs"


class ChuniSongAlias(Model):
    """Chunithm 乐曲别名表：一首乐曲可以有多个别名"""

    id = fields.IntField(primary_key=True)
    song: fields.ForeignKeyRelation[ChuniSong] = fields.ForeignKeyField(
        "models.ChuniSong",
        related_name="aliases",
        on_delete=fields.CASCADE,
        db_index=True,
    )
    alias = fields.CharField(max_length=256, db_index=True)
    priority = fields.IntField(default=0)

    class Meta(Model.Meta):
        table = "chuni_song_aliases"
        unique_together = (("song_id", "alias"),)


__all__ = [
    "ChuniSong",
    "ChuniSongAlias",
    "GameProfile",
    "LXNS_PLATFORM",
    "MaiMap",
    "MaiMapTreasure",
    "MaiSong",
    "MaiSongAlias",
    "ProviderAccount",
    "ProviderCredential",
    "QQ_PLATFORM",
    "User",
    "UserAccount",
    "ensure_user_by_qq",
    "get_user_by_qq",
]

