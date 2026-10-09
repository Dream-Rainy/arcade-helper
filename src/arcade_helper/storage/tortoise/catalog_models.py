"""Opt-in catalog models; register alongside MODEL_MODULE under the models app."""

from uuid import uuid4
from tortoise import fields
from tortoise.models import Model


class MusicWork(Model):
    """Shared music identity; imports start with one work per game entry."""

    id = fields.UUIDField(primary_key=True, default=uuid4)
    display_title = fields.TextField()
    artist = fields.TextField(default="")
    version = fields.IntField(default=1)
    redirect_to = fields.UUIDField(null=True)

    class Meta:
        table = "music_works"


class GameSong(Model):
    """Stable entry independent of source order and legacy per-game tables."""

    id = fields.IntField(primary_key=True)
    work = fields.ForeignKeyField("models.MusicWork", on_delete=fields.RESTRICT)
    game_code = fields.CharField(max_length=32, db_index=True)
    title = fields.TextField()
    artist = fields.TextField(default="")
    chart_variant = fields.JSONField(null=True)
    catalog_payload = fields.JSONField(default=dict)
    active = fields.BooleanField(default=True)
    version = fields.IntField(default=1)
    edition_key = fields.CharField(max_length=64, default="")

    class Meta:
        table = "game_songs"


class SongReference(Model):
    """One source's stable native or derived key and latest observation."""

    id = fields.IntField(primary_key=True)
    game_song = fields.ForeignKeyField("models.GameSong", on_delete=fields.RESTRICT)
    provider = fields.CharField(max_length=32)
    namespace = fields.CharField(max_length=64)
    game_code = fields.CharField(max_length=32)
    reference_key = fields.CharField(max_length=64, unique=True)
    external_key = fields.TextField()
    key_kind = fields.CharField(max_length=16)
    identity_material = fields.JSONField()
    payload = fields.JSONField()
    raw_payload = fields.JSONField()
    present = fields.BooleanField(default=True)

    class Meta:
        table = "song_references"
        indexes = (("provider", "namespace", "game_code"),)


class CatalogImport(Model):
    """Latest snapshot/report per dataset, not an import workflow history."""

    id = fields.IntField(primary_key=True)
    source_key = fields.CharField(max_length=64, unique=True)
    provider = fields.CharField(max_length=32)
    namespace = fields.CharField(max_length=64)
    game_code = fields.CharField(max_length=32)
    sync_version = fields.BigIntField(default=0)
    snapshot_hash = fields.CharField(max_length=64, null=True)
    raw_payload = fields.JSONField(null=True)
    report = fields.JSONField()
    observed_payload = fields.JSONField(null=True)
    resolutions = fields.JSONField(default=dict)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "catalog_imports"


class AliasRevision(Model):
    """Shared catalog/search generation, also used by later alias writes."""

    id = fields.IntField(primary_key=True)
    revision = fields.BigIntField(default=0)

    class Meta:
        table = "alias_revision"
