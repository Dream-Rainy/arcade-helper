"""Opt-in association, evidence and moderation tables."""

from tortoise import fields
from tortoise.models import Model


class WorkCandidate(Model):
    id = fields.IntField(primary_key=True)
    pair_key = fields.CharField(max_length=64, unique=True)
    left = fields.ForeignKeyField("models.GameSong", related_name="left_candidates", on_delete=fields.RESTRICT)
    right = fields.ForeignKeyField("models.GameSong", related_name="right_candidates", on_delete=fields.RESTRICT)
    reason = fields.CharField(max_length=32)
    status = fields.CharField(max_length=16, default="pending", db_index=True)
    target_versions = fields.JSONField()
    version = fields.IntField(default=1)
    class Meta(Model.Meta):
        table = "work_candidates"


class AliasSource(Model):
    id = fields.IntField(primary_key=True)
    source_key = fields.CharField(max_length=128, unique=True)
    kind = fields.CharField(max_length=16)
    origin_game = fields.CharField(max_length=32, null=True)
    enabled = fields.BooleanField(default=False)
    trusted_import = fields.BooleanField(default=False)
    sync_version = fields.IntField(default=0)
    version = fields.IntField(default=1)
    class Meta(Model.Meta):
        table = "alias_sources"


class AliasEvidence(Model):
    id = fields.IntField(primary_key=True)
    evidence_key = fields.CharField(max_length=64, unique=True)
    anchor = fields.ForeignKeyField("models.GameSong", on_delete=fields.RESTRICT)
    source = fields.ForeignKeyField("models.AliasSource", on_delete=fields.RESTRICT)
    reference = fields.ForeignKeyField("models.SongReference", null=True, on_delete=fields.RESTRICT)
    source_record_key = fields.TextField()
    kind = fields.CharField(max_length=16)
    scope = fields.CharField(max_length=16)
    raw_text = fields.TextField()
    alias_key = fields.TextField()
    remote_state = fields.CharField(max_length=16, default="present")
    moderation_state = fields.CharField(max_length=16, default="accepted")
    submitter_id = fields.IntField(null=True)
    request_id = fields.IntField(null=True)
    version = fields.IntField(default=1)
    first_seen_at = fields.DatetimeField(auto_now_add=True)
    last_seen_at = fields.DatetimeField(auto_now=True)
    class Meta(Model.Meta):
        table = "alias_evidence"


class AliasRule(Model):
    id = fields.IntField(primary_key=True)
    rule_key = fields.CharField(max_length=64, unique=True)
    work = fields.ForeignKeyField("models.MusicWork", on_delete=fields.RESTRICT)
    alias_key = fields.TextField()
    blocked = fields.BooleanField(default=True)
    reason = fields.TextField()
    version = fields.IntField(default=1)
    class Meta(Model.Meta):
        table = "alias_rules"


class AliasRequest(Model):
    id = fields.IntField(primary_key=True)
    actor_id = fields.IntField(db_index=True)
    operation = fields.CharField(max_length=32)
    payload = fields.JSONField()
    target_versions = fields.JSONField()
    status = fields.CharField(max_length=16, default="pending", db_index=True)
    idempotency_hash = fields.CharField(max_length=64, unique=True)
    idempotency_key = fields.TextField()
    pending_key = fields.CharField(max_length=64, unique=True, null=True)
    reason = fields.TextField(default="")
    created_at = fields.DatetimeField(auto_now_add=True)
    decided_at = fields.DatetimeField(null=True)
    version = fields.IntField(default=1)
    class Meta(Model.Meta):
        table = "alias_requests"


class AliasEvent(Model):
    id = fields.IntField(primary_key=True)
    request_id = fields.IntField(null=True, db_index=True)
    actor_id = fields.IntField(null=True)
    action = fields.CharField(max_length=48)
    before = fields.JSONField(null=True)
    after = fields.JSONField(null=True)
    reason = fields.TextField(default="")
    created_at = fields.DatetimeField(auto_now_add=True)
    class Meta(Model.Meta):
        table = "alias_events"


class AliasReceipt(Model):
    id = fields.IntField(primary_key=True)
    receipt_key = fields.CharField(max_length=64, unique=True)
    actor_id = fields.IntField()
    idempotency_key = fields.TextField()
    payload_hash = fields.CharField(max_length=64)
    request = fields.ForeignKeyField("models.AliasRequest", on_delete=fields.RESTRICT)
    class Meta(Model.Meta):
        table = "alias_receipts"
