"""Transactional alias evidence, work association and human moderation."""

import asyncio
from collections import defaultdict
from copy import deepcopy
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from urllib.parse import urlparse

from tortoise.expressions import F
from tortoise.exceptions import DoesNotExist
from tortoise.transactions import in_transaction

from arcade_helper.aliases.models import (
    alias_key, AliasConflictError, AliasPermissionError, AliasCandidate, AliasResolution,
)
from arcade_helper.catalog.importer import content_key, prepare_arcade_catalog
from arcade_helper.catalog.matcher import merge_works, split_songs, match_catalog
from arcade_helper.core.song_identity import normalize_width, parse_chart_title
from .catalog_models import AliasRevision, CatalogImport, GameSong, MusicWork, SongReference
from .alias_models import AliasSource, AliasEvidence, AliasRule, AliasRequest, AliasEvent, AliasReceipt, WorkCandidate


_BATCH_SIZE = 256
_NOT_LOADED = object()


@dataclass(frozen=True, slots=True)
class _SongIdentity:
    id: int
    work_id: object
    game_code: str
    title: str
    chart_variant: list | None


class TortoiseAliasStore:
    def __init__(self, catalog_store, *, policy=None, daily_limit=20, pending_limit=5):
        self.catalog = catalog_store
        self.policy = policy
        self.daily_limit = daily_limit
        self.pending_limit = pending_limit
        self._identity_lock = asyncio.Lock()
        self._identity_cache = None
        self._read_revision = None
        self._resolution_cache = {}
        self._effective_cache = {}

    def _read_generation(self, revision):
        generation = (self.catalog._db, revision)
        if self._read_revision != generation:
            self._read_revision = generation
            self._resolution_cache.clear()
            self._effective_cache.clear()

    async def _identity_snapshot(self, revision):
        generation = (self.catalog._db, revision)
        async with self._identity_lock:
            if self._identity_cache is None or self._identity_cache[0] != generation:
                rows = [_SongIdentity(*row) for row in await GameSong.filter(active=True).using_db(self.catalog._db).order_by("id").values_list("id", "work_id", "game_code", "title", "chart_variant")]
                native_ids = dict(await SongReference.filter(provider="catalog", namespace="native", present=True).using_db(self.catalog._db).values_list("game_song_id", "external_key"))
                titles = {s.id: alias_key(s.title) for s in rows}
                self._identity_cache = (generation, rows, native_ids, titles)
            return self._identity_cache[1:]

    def allowed(self, actor_id, action, game=None):
        return type(actor_id) is int and actor_id > 0 and self.policy is not None and self.policy(actor_id, action, game) is True

    def authorize(self, actor_id, action, game=None):
        if not self.allowed(actor_id, action, game):
            raise AliasPermissionError(f"Permission required: {action}:{game or 'global'}")

    @asynccontextmanager
    async def write(self, *, facts=True, requests=False):
        async with in_transaction(self.catalog.connection_name) as db:
            # ponytail: pending requests share one lock; per-actor locks only if this short transaction contends.
            # Fact-changing reviews take locks in catalog -> requests order; imports only take the catalog lock.
            for lock_id in ([1] if facts else []) + ([2] if requests or not facts else []):
                if not await AliasRevision.filter(id=lock_id).using_db(db).update(revision=F("revision") + 1):
                    raise AliasConflictError("Catalog storage is not initialized")
            yield db

    async def event(self, db, action, *, actor_id=None, request_id=None, before=None, after=None, reason=""):
        await AliasEvent.create(using_db=db, actor_id=actor_id, request_id=request_id, action=action,
                                before=before, after=after, reason=reason)

    async def configure_source(self, source_key, *, actor_id, origin_game=None, enabled=True, trusted_import=False, kind="external"):
        self.authorize(actor_id, "admin")
        if not source_key or len(source_key) > 128 or kind not in {"external", "catalog", "legacy", "community"}:
            raise ValueError("Invalid external source")
        if kind == "community" and (source_key != f"community:{origin_game or 'global'}" or trusted_import):
            raise ValueError("Community source names are server-defined and cannot enable external import")
        async with self.write() as db:
            self.authorize(actor_id, "admin")
            row = await AliasSource.filter(source_key=source_key).using_db(db).first()
            if row and (row.source_key != source_key or row.origin_game != origin_game or row.kind != kind):
                raise AliasConflictError("Source origin and kind are immutable")
            source, _ = await AliasSource.update_or_create(source_key=source_key, using_db=db, defaults={
                "origin_game": origin_game, "kind": kind, "enabled": enabled, "trusted_import": trusted_import,
                "version": row.version + 1 if row else 1,
            })
            await self.event(db, "configure_source", actor_id=actor_id, after={"source": source.id, "enabled": enabled})
            return source.id

    async def source_versions(self, game_code):
        return dict(await AliasSource.filter(origin_game=game_code).using_db(self.catalog._db).values_list("source_key", "sync_version"))

    async def _source(self, db, key, game, *, kind="external", known=False):
        if not key or len(key) > 128:
            raise ValueError("Invalid source key")
        source, _ = await AliasSource.get_or_create(source_key=key, using_db=db, defaults={
            "origin_game": game, "kind": kind, "enabled": known, "trusted_import": known and kind != "community",
        })
        if source.source_key != key or source.origin_game != game or source.kind != kind:
            raise AliasConflictError("Source metadata mismatch")
        return source

    async def _evidence(self, db, source, anchor, text, *, record_key, scope, kind="alias", actor_id=None, request_id=None, reference_id=None, _existing=_NOT_LOADED, _unchanged_ids=None):
        text = normalize_width(text)
        normalized = alias_key(text)
        if kind == "title":
            scope = "game_song"
        if scope not in {"work", "game_song"} or kind not in {"alias", "title"}:
            raise ValueError("Invalid evidence scope/kind")
        key = content_key([source.id, str(record_key), anchor, normalized, scope, kind])
        row = await AliasEvidence.filter(evidence_key=key).using_db(db).first() if _existing is _NOT_LOADED else _existing
        if row and (row.source_id, row.source_record_key, row.anchor_id, row.alias_key, row.scope, row.kind) != (source.id, str(record_key), anchor, normalized, scope, kind):
            raise AliasConflictError("Evidence key collision")
        if row:
            if reference_id is not None and row.reference_id not in {None, reference_id}:
                raise AliasConflictError("Evidence already belongs to another source record")
            if row.reference_id is None and reference_id is not None:
                row.reference_id = reference_id
                await row.save(using_db=db, update_fields=["reference_id"])
            if row.raw_text != text or row.remote_state != "present":
                before = {"text": row.raw_text, "remote_state": row.remote_state}
                row.raw_text, row.remote_state, row.version = text, "present", row.version + 1
                row.last_seen_at = datetime.now(timezone.utc)
                await row.save(using_db=db, update_fields=["raw_text", "remote_state", "version", "last_seen_at"])
                await self.event(db, "observe_evidence", before=before, after={"evidence_id": row.id, "text": text})
            else:
                if _unchanged_ids is not None:
                    _unchanged_ids.append(row.id)
                else:
                    await row.save(using_db=db, update_fields=["last_seen_at"])
            return row
        row = await AliasEvidence.create(using_db=db, evidence_key=key, anchor_id=anchor, source=source,
                                         source_record_key=str(record_key), raw_text=text, alias_key=normalized,
                                         kind=kind, scope=scope, submitter_id=actor_id, request_id=request_id)
        if reference_id is not None:
            row.reference_id = reference_id
            await row.save(using_db=db, update_fields=["reference_id"])
        await self.event(db, "add_evidence", actor_id=actor_id, request_id=request_id, after={"evidence_id": row.id, "source": source.source_key})
        return row

    async def _sync_source(self, db, source, observations, *, complete=False, expected_version=None, allow_empty=False):
        if expected_version is not None and source.sync_version != expected_version:
            raise AliasConflictError("SOURCE_CHANGED: refetch the source")
        seen = set()
        legacy_sources = dict(await AliasSource.filter(source_key__startswith="legacy:").using_db(db).values_list("source_key", "id")) if source.trusted_import and source.kind == "external" else {}
        for offset in range(0, len(observations), _BATCH_SIZE):
            batch = observations[offset:offset + _BATCH_SIZE]
            anchors = dict(await GameSong.filter(id__in={o["anchor_id"] for o in batch}).using_db(db).values_list("id", "game_code"))
            keys = [content_key([source.id, str(o["record_key"]), o["anchor_id"], alias_key(o["text"]),
                                 "game_song" if o.get("kind", "alias") == "title" else o.get("scope", "work"), o.get("kind", "alias")]) for o in batch]
            existing = {row.evidence_key: row for row in await AliasEvidence.filter(evidence_key__in=keys).using_db(db).only(
                "id", "evidence_key", "source_id", "source_record_key", "anchor_id", "alias_key", "scope", "kind", "reference_id", "raw_text", "remote_state", "version")}
            legacy = defaultdict(list)
            if legacy_sources:
                for row in await AliasEvidence.filter(source_id__in=list(legacy_sources.values()), anchor_id__in=list(anchors),
                    alias_key__in={alias_key(o["text"]) for o in batch}, moderation_state="accepted").using_db(db).only("id", "source_id", "anchor_id", "alias_key", "version"):
                    legacy[(row.source_id, row.anchor_id, row.alias_key)].append(row)
            unchanged = []
            for observation, key in zip(batch, keys):
                anchor = observation["anchor_id"]
                if anchor not in anchors:
                    raise DoesNotExist(f"GameSong {anchor} does not exist")
                if source.origin_game is not None and source.origin_game != anchors[anchor]:
                    raise ValueError("Source game must match its anchor")
                evidence = await self._evidence(db, source, anchor, observation["text"],
                    record_key=observation["record_key"], scope=observation.get("scope", "work"),
                    kind=observation.get("kind", "alias"), reference_id=observation.get("reference_id"),
                    _existing=existing.get(key), _unchanged_ids=unchanged)
                existing[key] = evidence
                seen.add(evidence.id)
                if evidence.kind == "alias":
                    for old in legacy.pop((legacy_sources.get(f"legacy:{anchors[anchor]}"), anchor, evidence.alias_key), []):
                        old.moderation_state, old.version = "superseded", old.version + 1
                        old.last_seen_at = datetime.now(timezone.utc)
                        await old.save(using_db=db, update_fields=["moderation_state", "version", "last_seen_at"])
                        await self.event(db, "supersede_legacy", after={"evidence_id": old.id, "replacement": evidence.id})
            if unchanged:
                await AliasEvidence.filter(id__in=unchanged).using_db(db).update(last_seen_at=datetime.now(timezone.utc))
        if complete and (seen or allow_empty):
            for old in await AliasEvidence.filter(source=source, remote_state="present").exclude(id__in=seen).using_db(db).only("id", "version"):
                if old.id not in seen:
                    old.remote_state, old.version = "withdrawn", old.version + 1
                    old.last_seen_at = datetime.now(timezone.utc)
                    await old.save(using_db=db, update_fields=["remote_state", "version", "last_seen_at"])
                    await self.event(db, "withdraw_source_evidence", after={"evidence_id": old.id})
        source.sync_version += 1
        await source.save(using_db=db, update_fields=["sync_version"])

    async def sync_source(self, source_key, observations, *, actor_id, expected_sync_version, complete=False, allow_empty=False):
        """Trusted host import only; submission APIs cannot select an external source."""
        self.authorize(actor_id, "admin")
        async with self.write() as db:
            self.authorize(actor_id, "admin")
            source = await AliasSource.get(source_key=source_key).using_db(db)
            if not source.trusted_import or source.kind == "community":
                raise AliasPermissionError("Source is not a trusted external importer")
            await self._sync_source(db, source, observations, complete=complete,
                                    expected_version=expected_sync_version, allow_empty=allow_empty)
            return source.sync_version

    async def ingest_catalog(self, db, game_code, records, song_ids, *, provider, namespace, complete, expected_versions=None):
        groups = defaultdict(list)
        completed = set()
        metadata = {}
        reference_keys = [content_key([provider, namespace, game_code, r.key_kind, r.external_key]) for r in records if r.external_key in song_ids]
        reference_ids = {}
        for offset in range(0, len(reference_keys), _BATCH_SIZE):
            reference_ids.update(await SongReference.filter(reference_key__in=reference_keys[offset:offset + _BATCH_SIZE]).using_db(db).values_list("reference_key", "id"))
        for record in records:
            anchor = song_ids.get(record.external_key)
            if anchor is None:
                continue
            reference_id = reference_ids[content_key([provider, namespace, game_code, record.key_kind, record.external_key])]
            title_key = f"catalog:{provider}:{namespace}:{game_code}"
            groups[title_key].append({"anchor_id": anchor, "text": record.song.title, "record_key": record.external_key,
                                      "scope": "game_song", "kind": "title", "reference_id": reference_id})
            metadata[title_key] = ("catalog", provider in {"catalog", "arcade_songs"})
            completed.add(title_key)
            for observation in record.song.alias_observations:
                groups[observation.source_key].append({"anchor_id": anchor, "text": observation.text,
                    "record_key": observation.record_key or content_key([record.external_key, alias_key(observation.text)]),
                    "scope": "game_song" if record.identity["chart_variant"] and observation.scope == "work" else observation.scope,
                    "kind": observation.kind, "reference_id": reference_id})
                metadata[observation.source_key] = ("external", observation.source_key in {
                    f"lxns:{game_code}", f"dxrating:{game_code}", f"yuzuchan:{game_code}",
                })
            completed.update(record.song.alias_complete_sources)
            if not record.song.alias_observations and not record.song.alias_complete_sources:
                legacy = f"legacy:{game_code}"
                metadata[legacy] = ("legacy", True)
                for text in record.song.aliases:
                    if text != record.song.title:
                        groups[legacy].append({"anchor_id": anchor, "text": text,
                            "record_key": content_key([anchor, alias_key(text)]),
                            "scope": "game_song" if record.identity["chart_variant"] else "work"})
        for key in sorted(set(groups) | completed):
            kind, known = metadata.get(key, ("external", key in {f"lxns:{game_code}", f"dxrating:{game_code}", f"yuzuchan:{game_code}"}))
            source = await self._source(db, key, game_code, kind=kind, known=known)
            await self._sync_source(db, source, groups[key], complete=complete and key in completed,
                                    expected_version=(expected_versions or {}).get(key, 0) if expected_versions is not None else None)

    async def projection(self, game_code=None, *, include_inactive=False, _song_ids=None, _alias_keys=None):
        return await self._project_aliases(game_code, include_inactive=include_inactive, song_ids=_song_ids, alias_keys=_alias_keys)

    async def _alias_texts(self, game_code=None, *, song_ids=None):
        """Effective alias text only, sharing scope and block rules with provenance."""
        result = await self._project_aliases(game_code, song_ids=song_ids, texts=True)
        return {sid: list(dict.fromkeys(values)) for sid, values in result.items()}

    async def _project_aliases(self, game_code=None, *, include_inactive=False, song_ids=None, alias_keys=None, texts=False):
        db = self.catalog._db
        query = GameSong.filter(**({"game_code": game_code} if game_code else {}), active=True)
        if song_ids is not None:
            query = query.filter(id__in=song_ids)
        targets = await query.using_db(db).values_list("id", "work_id")
        if not targets:
            return {}
        works = {work for _, work in targets}
        rules = set(await AliasRule.filter(work_id__in=works, blocked=True).using_db(db).values_list("work_id", "alias_key"))
        evidence_query = AliasEvidence.filter(anchor__work_id__in=works)
        if alias_keys is not None:
            evidence_query = evidence_query.filter(alias_key__in=alias_keys)
        fields = ["id", "anchor_id", "anchor__work_id", "scope", "alias_key", "raw_text"]
        if not include_inactive:
            evidence_query = evidence_query.filter(source__enabled=True, remote_state="present", moderation_state="accepted")
        if texts:
            evidence_query = evidence_query.filter(kind="alias")
        else:
            fields += ["kind", "request_id", "remote_state", "moderation_state", "version", "source__source_key", "source__origin_game", "source__kind", "source__enabled"]
        evidence = await evidence_query.using_db(db).order_by("id").values(*fields)
        by_work = defaultdict(list)
        for row in evidence:
            by_work[row["anchor__work_id"]].append(row)
        result = {sid: [] for sid, _ in targets}
        for sid, work in targets:
            for row in by_work[work]:
                if row["scope"] == "game_song" and row["anchor_id"] != sid:
                    continue
                effective = (not include_inactive or (row["source__enabled"] and row["remote_state"] == "present" and row["moderation_state"] == "accepted")) and (work, row["alias_key"]) not in rules
                if effective or include_inactive:
                    if texts:
                        result[sid].append(row["raw_text"])
                    else:
                        source_kind = row["source__kind"]
                        result[sid].append({"id": row["id"], "text": row["raw_text"], "alias_key": row["alias_key"],
                            "kind": row["kind"], "scope": row["scope"], "anchor_id": row["anchor_id"],
                            "source_key": row["source__source_key"], "origin_game": row["source__origin_game"],
                            "source_kind": source_kind, "request_id": row["request_id"],
                            "acceptance": "human_review" if source_kind == "community" and row["request_id"] else "legacy_import" if source_kind == "legacy" else "external_import",
                            "remote_state": row["remote_state"], "moderation_state": row["moderation_state"],
                            "effective": effective, "version": row["version"]})
        return result

    async def query_exact(self, game_code, text, *, kind="alias"):
        """Read only matching evidence, preserving work scope and moderation."""
        normalized = alias_key(text)
        works = await AliasEvidence.filter(alias_key=normalized, kind=kind).using_db(
            self.catalog._db
        ).values_list("anchor__work_id", flat=True)
        targets = await GameSong.filter(game_code=game_code, active=True, work_id__in=works).using_db(
            self.catalog._db
        ).values_list("id", flat=True)
        projection = await self.projection(_song_ids=targets, _alias_keys=[normalized])
        return list({song_id: e["text"] for song_id, evidence in projection.items()
                     for e in evidence if e["kind"] == kind}.items())

    async def get_effective_aliases(self, game_song_id, *, include_inactive=False, actor_id=None):
        if include_inactive:
            self.authorize(actor_id, "admin")
        key = (game_song_id, include_inactive)
        for _ in range(3):
            before = await self.catalog.get_revision()
            self._read_generation(before)
            if key in self._effective_cache:
                return deepcopy(self._effective_cache[key])
            await GameSong.get(id=game_song_id).only("id").using_db(self.catalog._db)
            result = (await self.projection(include_inactive=include_inactive, _song_ids=[game_song_id])).get(game_song_id, [])
            if await self.catalog.get_revision() == before:
                if len(self._effective_cache) >= 256:
                    self._effective_cache.pop(next(iter(self._effective_cache)))
                self._effective_cache[key] = result
                return deepcopy(result)
        raise AliasConflictError("Catalog changed repeatedly during alias lookup")

    async def _search(self, query, game_code=None, *, aliases_only=False):
        from arcade_helper.search import SongSearchService
        normalized = alias_key(str(query))
        key = (str(query), game_code, aliases_only)
        for _ in range(3):
            before = await self.catalog.get_revision()
            self._read_generation(before)
            if key in self._resolution_cache:
                return self._resolution_cache[key]
            rows, native_ids, titles = await self._identity_snapshot(before)
            candidates = []
            markers = parse_chart_title(str(query))[1]
            eligible = [s for s in rows if (not game_code or s.game_code == game_code) and (
                not markers or tuple(s.chart_variant or ()) == markers
                or (s.chart_variant == [] and titles[s.id] == normalized)
            )]
            matching_works = set(await AliasEvidence.filter(alias_key=normalized).using_db(self.catalog._db).values_list("anchor__work_id", flat=True))
            projection = await self.projection(_song_ids=[s.id for s in eligible if s.work_id in matching_works], _alias_keys=[normalized])
            for song in eligible:
                evidence = [e for e in projection.get(song.id, []) if e["alias_key"] == normalized and (not aliases_only or e["kind"] == "alias")]
                title_hit = not aliases_only and titles[song.id] == normalized
                id_hit = not aliases_only and str(query) in {str(song.id), native_ids.get(song.id)}
                if evidence or title_hit or id_hit:
                    games = tuple(sorted({e["origin_game"] for e in evidence if e["kind"] == "alias" and e["origin_game"]}))
                    candidates.append(AliasCandidate(song.id, str(song.work_id), song.game_code, song.title,
                        evidence[0]["text"] if evidence else song.title, "exact_alias" if evidence and any(e["kind"] == "alias" for e in evidence) else "exact_id" if id_hit else "exact_title", 100,
                        games, tuple(e["id"] for e in evidence), shared=bool(games and song.game_code not in games),
                        scope="work" if any(e["scope"] == "work" for e in evidence) else "game_song",
                        chart_variant=tuple(song.chart_variant) if song.chart_variant is not None else None))
            exact = bool(candidates)
            if not candidates and not aliases_only:
                search = getattr(self, "_search_service", None)
                if search is None:
                    search = self._search_service = SongSearchService(self.catalog)
                permitted = {s.id for s in eligible}
                songs_by_id = {s.id: s for s in eligible}
                matches = []
                for game in sorted({s.game_code for s in eligible}):
                    for match in await search.search_song(query, game_code=game):
                        if match.song_id not in permitted:
                            continue
                        matches.append((game, match))
                projection = await self.projection(_song_ids={match.song_id for _, match in matches},
                                                   _alias_keys={alias_key(match.matched_text) for _, match in matches})
                for game, match in matches:
                    song = songs_by_id[match.song_id]
                    evidence = [e for e in projection[song.id] if e["kind"] == "alias" and e["alias_key"] == alias_key(match.matched_text)]
                    games = tuple(sorted({e["origin_game"] for e in evidence if e["origin_game"]}))
                    candidates.append(AliasCandidate(song.id, str(song.work_id), game, song.title, match.matched_text,
                        match.match_type.value, match.match_score, games, tuple(e["id"] for e in evidence),
                        shared=bool(games and game not in games), chart_variant=tuple(song.chart_variant) if song.chart_variant is not None else None))
            preferences = {}
            for work in {c.work_id for c in candidates}:
                available = {s.game_code for s in rows if str(s.work_id) == work}
                origins = {g for c in candidates if c.work_id == work for g in c.source_games} & available
                choices = origins or available
                preferences[work] = next(iter(choices)) if len(choices) == 1 else None
            candidates = [replace(c, preferred_game=preferences[c.work_id]) for c in candidates]
            candidates.sort(key=lambda c: (c.game_code != c.preferred_game, -c.score, c.game_code, c.game_song_id))
            inferred = next(iter(preferences.values())) if exact and len(preferences) == 1 else None
            if await self.catalog.get_revision() == before:
                result = AliasResolution("explicit" if game_code else "inferred" if inferred else "ambiguous" if candidates else "unknown",
                    game_code or inferred, "explicit_game" if game_code else "alias_origin_or_only_game" if inferred else "candidates_require_choice",
                    tuple(candidates))
                if exact:
                    if len(self._resolution_cache) >= 256:
                        self._resolution_cache.pop(next(iter(self._resolution_cache)))
                    self._resolution_cache[key] = result
                return result
        raise AliasConflictError("Catalog changed repeatedly during resolution")

    async def search_all(self, query, *, game_code=None):
        return await self._search(query, game_code)

    async def resolve_alias(self, query, *, game_code=None):
        return await self._search(query, game_code, aliases_only=True)

    def _review_requirements(self, operation, payload, source_game=None):
        if operation in {"MAP_SOURCE_RECORD", "RELINK_GAME_SONGS"}:
            return [("admin", None)]
        if operation in {"BLOCK_ALIAS", "UNBLOCK_ALIAS"}:
            return [("global", None)]
        if operation in {"RETRACT_EVIDENCE", "REPLACE_ALIAS"}:
            result = [("review" if source_game else "global", source_game)]
            if operation == "RETRACT_EVIDENCE":
                return result
        else:
            result = []
        game = payload.get("origin_game")
        return result + [("review" if game else "global", game)]

    async def _targets(self, db, operation, payload):
        versions = {}
        if operation in {"ADD_ALIAS", "REPLACE_ALIAS"}:
            key = f"community:{payload.get('origin_game') or 'global'}"
            source = await AliasSource.filter(source_key=key).using_db(db).first()
            versions[f"source_key:{key}"] = source.version if source else 0
        song_ids = list(payload.get("song_ids", []))
        if payload.get("game_song_id"):
            song_ids.append(payload["game_song_id"])
        source_game = None
        if payload.get("evidence_id"):
            row = await AliasEvidence.get(id=payload["evidence_id"]).using_db(db)
            source = await AliasSource.get(id=row.source_id).using_db(db)
            versions[f"evidence:{row.id}"] = row.version
            versions[f"source:{source.id}"] = source.version
            source_game = source.origin_game
            song_ids.append(row.anchor_id)
        if payload.get("target_id"):
            song_ids.append(payload["target_id"])
        if operation == "MAP_SOURCE_RECORD":
            batch, record = await self._mapping_record(db, payload)
            reference = await SongReference.filter(reference_key=content_key([
                payload["provider"], payload["namespace"], payload["game_code"], record.key_kind, record.external_key,
            ])).using_db(db).first()
            if reference:
                song_ids.append(reference.game_song_id)
        for song in await GameSong.filter(id__in=song_ids).only("id", "work_id", "version").using_db(db):
            versions[f"song:{song.id}"] = song.version
            work = await MusicWork.get(id=song.work_id).only("id", "version").using_db(db)
            versions[f"work:{work.id}"] = work.version
            if "text" in payload:
                key = alias_key(payload["text"])
                rule = await AliasRule.filter(rule_key=content_key([str(work.id), key])).using_db(db).first()
                versions[f"rule:{work.id}:{key}"] = rule.version if rule else 0
        if len({int(key.split(":")[1]) for key in versions if key.startswith("song:")}) != len(set(song_ids)):
            raise ValueError("Unknown song target")
        if operation == "MAP_SOURCE_RECORD":
            key = content_key([payload["provider"], payload["namespace"], payload["game_code"]])
            row = await CatalogImport.get(source_key=key).using_db(db)
            versions[f"import:{key}"] = row.sync_version
        return versions, source_game

    async def _payload(self, db, operation, payload):
        allowed = {
            "ADD_ALIAS": {"game_song_id", "text", "scope", "origin_game"},
            "RETRACT_EVIDENCE": {"evidence_id"},
            "REPLACE_ALIAS": {"evidence_id", "game_song_id", "text", "scope", "origin_game"},
            "BLOCK_ALIAS": {"game_song_id", "text"}, "UNBLOCK_ALIAS": {"game_song_id", "text"},
            "RELINK_GAME_SONGS": {"song_ids", "mode"},
            "MAP_SOURCE_RECORD": {"game_code", "provider", "namespace", "external_key", "decision", "target_id"},
        }
        if operation not in allowed or not isinstance(payload, dict) or set(payload) - allowed[operation] - {"evidence_url", "previous_request_id"}:
            raise ValueError("Invalid operation/payload fields")
        payload = dict(payload)
        if "evidence_url" in payload:
            url = urlparse(payload["evidence_url"])
            if len(payload["evidence_url"]) > 2048 or url.scheme not in {"http", "https"} or not url.netloc:
                raise ValueError("Invalid evidence URL")
        if operation in {"ADD_ALIAS", "REPLACE_ALIAS", "BLOCK_ALIAS", "UNBLOCK_ALIAS"}:
            alias_key(payload["text"])
            payload["text"] = normalize_width(payload["text"])
            song = await GameSong.get(id=payload["game_song_id"]).only("id", "active", "game_code").using_db(db)
            if not song.active:
                raise ValueError("Choose an active song")
            if operation in {"ADD_ALIAS", "REPLACE_ALIAS"}:
                payload.setdefault("origin_game", song.game_code)
                if payload["origin_game"] not in {None, song.game_code}:
                    raise ValueError("Origin game must match the selected entry")
                payload.setdefault("scope", "work")
                if payload["scope"] not in {"work", "game_song"}:
                    raise ValueError("Invalid scope")
        if operation == "RELINK_GAME_SONGS":
            ids = payload.get("song_ids", [])
            if not ids or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids) or payload.get("mode") not in {"merge", "split"}:
                raise ValueError("Invalid work mapping")
            payload["song_ids"] = sorted(ids)
        if operation == "MAP_SOURCE_RECORD":
            payload.setdefault("namespace", "default")
            if payload.get("decision") not in {"bind", "new", "reject"} or (payload["decision"] == "bind" and not payload.get("target_id")):
                raise ValueError("Invalid source mapping")
            await self._mapping_record(db, payload)
        return payload

    async def submit(self, *, actor_id, operation, payload, idempotency_key, reason=""):
        self.authorize(actor_id, "submit", payload.get("origin_game") if isinstance(payload, dict) else None)
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > 128 or len(reason) > 2000:
            raise ValueError("Invalid idempotency key or reason")
        async with self.write(facts=False) as db:
            self.authorize(actor_id, "submit", payload.get("origin_game"))
            data = await self._payload(db, operation, payload)
            fingerprint = content_key([operation, data, reason])
            receipt_key = content_key([actor_id, idempotency_key])
            receipt = await AliasReceipt.filter(receipt_key=receipt_key).using_db(db).first()
            if receipt:
                if (receipt.actor_id, receipt.idempotency_key, receipt.payload_hash) != (actor_id, idempotency_key, fingerprint):
                    raise AliasConflictError("IDEMPOTENCY_CONFLICT")
                return receipt.request_id
            versions, _ = await self._targets(db, operation, data)
            work_ids = {}
            if data.get("scope") == "work":
                work_id = await GameSong.filter(id=data["game_song_id"]).using_db(db).values_list("work_id", flat=True)
                work_ids = dict(await GameSong.filter(work_id=work_id[0]).using_db(db).values_list("id", "work_id"))
            def semantics(value):
                result = {k: v for k, v in value.items() if k not in {"evidence_url", "previous_request_id"}}
                if "text" in result:
                    result["text"] = alias_key(result["text"])
                if result.get("scope") == "work" and result.get("game_song_id") in work_ids:
                    result["work_id"] = str(work_ids[result.pop("game_song_id")])
                return result
            semantic = semantics(data)
            pending_key = content_key([operation, semantic])
            existing = await AliasRequest.filter(pending_key=pending_key).using_db(db).first()
            if existing is None and work_ids:
                # Old pending keys used an entry ID; reuse their requests and receipts without rewriting history.
                for pending in await AliasRequest.filter(status="pending", operation=operation).using_db(db).order_by("id"):
                    if semantics(pending.payload) == semantic:
                        existing = pending
                        break
            if existing is None:
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                if await AliasRequest.filter(actor_id=actor_id, created_at__gte=today).using_db(db).count() >= self.daily_limit:
                    raise AliasConflictError("DAILY_LIMIT")
                if await AliasRequest.filter(actor_id=actor_id, status="pending").using_db(db).count() >= self.pending_limit:
                    raise AliasConflictError("PENDING_LIMIT")
                existing = await AliasRequest.create(using_db=db, actor_id=actor_id, operation=operation, payload=data,
                    target_versions=versions, idempotency_hash=receipt_key, idempotency_key=idempotency_key,
                    pending_key=pending_key, reason=reason)
                await self.event(db, "submit", actor_id=actor_id, request_id=existing.id, after={"operation": operation})
            await AliasReceipt.create(using_db=db, receipt_key=receipt_key, actor_id=actor_id,
                                      idempotency_key=idempotency_key, payload_hash=fingerprint, request=existing)
            return existing.id

    async def _can_review(self, row, actor_id, db, *, co_submitted=None):
        if actor_id == row.actor_id:
            return False
        # Co-submitters of the same pending operation cannot approve it either.
        if (row.id in co_submitted if co_submitted is not None else await AliasReceipt.filter(request_id=row.id, actor_id=actor_id).using_db(db).exists()):
            return False
        source_game = None
        if row.payload.get("evidence_id"):
            evidence = await AliasEvidence.get(id=row.payload["evidence_id"]).only("id", "source_id").using_db(db)
            source = await AliasSource.get(id=evidence.source_id).only("id", "origin_game").using_db(db)
            source_game = source.origin_game
        return all(self.allowed(actor_id, action, game) for action, game in self._review_requirements(row.operation, row.payload, source_game))

    async def get_request(self, request_id, *, actor_id):
        if type(actor_id) is not int or actor_id <= 0:
            raise AliasPermissionError("An authenticated internal user ID is required")
        db = self.catalog._db
        row = await AliasRequest.get(id=request_id).using_db(db)
        can_review = await self._can_review(row, actor_id, db)
        owner = actor_id == row.actor_id or await AliasReceipt.filter(request_id=row.id, actor_id=actor_id).using_db(db).exists()
        if not owner and not can_review:
            raise AliasPermissionError("Request is private")
        current, _ = await self._targets(db, row.operation, row.payload)
        songs = await GameSong.filter(id__in=[int(k.split(":")[1]) for k in current if k.startswith("song:")]).only("id", "work_id").using_db(db)
        affected = await GameSong.filter(work_id__in=[s.work_id for s in songs], active=True).only("id", "game_code").using_db(db)
        collisions = []
        if "text" in row.payload:
            collisions = [asdict(c) for c in (await self.resolve_alias(row.payload["text"])).candidates]
        private = actor_id == row.actor_id or can_review
        visible_payload = dict(row.payload)
        if not private:
            visible_payload.pop("evidence_url", None)
            visible_payload.pop("previous_request_id", None)
        decision = await AliasEvent.filter(request_id=row.id, action__in=["approved", "rejected", "superseded"]).using_db(db).order_by("-id").first()
        return {"id": row.id, "actor_id": row.actor_id if private else None, "operation": row.operation, "payload": visible_payload,
                "status": row.status, "reason": row.reason if private else "", "decision_reason": decision.reason if decision else None,
                "version": row.version, "target_versions": row.target_versions,
                "current_target_versions": current, "can_review": can_review and row.status == "pending",
                "review_blocked_reason": "SELF_REVIEW_OR_PERMISSION" if not can_review else "ALREADY_DECIDED" if row.status != "pending" else None,
                "affected_games": sorted({s.game_code for s in affected}), "same_alias_candidates": collisions}

    async def list_requests(self, *, actor_id, status="pending", after_id=0, limit=50, operation=None, game_code=None, include_details=True):
        if type(actor_id) is not int or actor_id <= 0:
            raise AliasPermissionError("An authenticated internal user ID is required")
        limit = max(1, min(limit, 100))
        query = AliasRequest.all()
        if status:
            query = query.filter(status=status)
        if operation:
            query = query.filter(operation=operation)
        result = []
        cursor = after_id
        db = self.catalog._db
        while len(result) <= limit:
            rows = await query.filter(id__gt=cursor).using_db(db).order_by("id").limit(limit + 1)
            if not rows:
                break
            co_submitted = set(await AliasReceipt.filter(actor_id=actor_id, request_id__in=[r.id for r in rows]).using_db(db).values_list("request_id", flat=True))
            ids = {i for row in rows for i in [row.payload.get("game_song_id"), row.payload.get("target_id"), *row.payload.get("song_ids", [])] if i}
            anchors = dict(await AliasEvidence.filter(id__in=[r.payload["evidence_id"] for r in rows if r.payload.get("evidence_id")]).using_db(db).values_list("id", "anchor_id"))
            songs = {s.id: s for s in await GameSong.filter(id__in=ids | set(anchors.values())).only("id", "work_id", "game_code", "title").using_db(db)}
            games = defaultdict(set)
            for work, game in await GameSong.filter(work_id__in={s.work_id for s in songs.values()}, active=True).using_db(db).values_list("work_id", "game_code"):
                games[work].add(game)
            for row in rows:
                cursor = row.id
                can_review = await self._can_review(row, actor_id, db, co_submitted=co_submitted)
                if row.actor_id != actor_id and row.id not in co_submitted and not can_review:
                    continue
                target_ids = [row.payload.get("game_song_id"), row.payload.get("target_id"), anchors.get(row.payload.get("evidence_id")), *row.payload.get("song_ids", [])]
                targets = [songs[i] for i in dict.fromkeys(target_ids) if i in songs]
                affected_games = sorted({game for s in targets for game in games[s.work_id]})
                if game_code is not None and game_code not in affected_games:
                    continue
                if include_details:
                    detail = await self.get_request(row.id, actor_id=actor_id)
                else:
                    payload = dict(row.payload)
                    if row.actor_id != actor_id and not can_review:
                        payload.pop("evidence_url", None)
                        payload.pop("previous_request_id", None)
                    detail = {"id": row.id, "version": row.version, "operation": row.operation,
                              "status": row.status, "payload": payload, "affected_games": affected_games,
                              "songs": [{"game_code": s.game_code, "title": s.title} for s in targets]}
                result.append(detail)
                if len(result) > limit:
                    break
            if len(rows) < limit + 1:
                break
        has_more = len(result) > limit
        return {"items": result[:limit], "next_cursor": result[limit - 1]["id"] if has_more else cursor, "has_more": has_more}

    async def withdraw(self, request_id, *, actor_id, expected_version):
        if type(actor_id) is not int or actor_id <= 0:
            raise AliasPermissionError("An authenticated internal user ID is required")
        async with self.write(facts=False) as db:
            row = await AliasRequest.get(id=request_id).using_db(db)
            if actor_id != row.actor_id:
                raise AliasPermissionError("Only the original submitter can withdraw")
            if row.status != "pending" or row.version != expected_version:
                raise AliasConflictError("ALREADY_DECIDED_OR_CHANGED")
            row.status, row.pending_key, row.decided_at, row.version = "withdrawn", None, datetime.now(timezone.utc), row.version + 1
            await row.save(using_db=db)
            await self.event(db, "withdraw_request", actor_id=actor_id, request_id=row.id)

    async def supersede(self, request_id, *, actor_id, expected_version, reason):
        if not reason.strip():
            raise ValueError("A reason is required")
        async with self.write(facts=False) as db:
            row = await AliasRequest.get(id=request_id).using_db(db)
            if not await self._can_review(row, actor_id, db):
                raise AliasPermissionError("SELF_REVIEW_OR_PERMISSION")
            if row.status != "pending" or row.version != expected_version:
                raise AliasConflictError("ALREADY_DECIDED_OR_CHANGED")
            row.status, row.pending_key, row.decided_at, row.version = "superseded", None, datetime.now(timezone.utc), row.version + 1
            await row.save(using_db=db)
            await self.event(db, "superseded", actor_id=actor_id, request_id=row.id, reason=reason)

    async def review(self, request_id, *, actor_id, approve, expected_version, reason, expected_target_versions=None):
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000 or type(approve) is not bool:
            raise ValueError("A review decision and reason are required")
        async with self.write(facts=approve, requests=True) as db:
            row = await AliasRequest.get(id=request_id).using_db(db)
            if not await self._can_review(row, actor_id, db):
                raise AliasPermissionError("SELF_REVIEW_OR_PERMISSION")
            if row.status != "pending" or row.version != expected_version:
                raise AliasConflictError("ALREADY_DECIDED_OR_CHANGED")
            versions, _ = await self._targets(db, row.operation, row.payload)
            if approve and versions != (expected_target_versions if expected_target_versions is not None else row.target_versions):
                raise AliasConflictError("TARGET_CHANGED: refresh and explicitly confirm current targets")
            if approve:
                await self._payload(db, row.operation, row.payload)
                await self._apply(db, row, actor_id, reason)
            row.status, row.pending_key, row.decided_at, row.version = "approved" if approve else "rejected", None, datetime.now(timezone.utc), row.version + 1
            await row.save(using_db=db)
            await self.event(db, row.status, actor_id=actor_id, request_id=row.id, before=row.target_versions, after=versions, reason=reason)
            return row.status

    async def _apply(self, db, row, actor_id, reason):
        data, operation = row.payload, row.operation
        if operation in {"RETRACT_EVIDENCE", "REPLACE_ALIAS"}:
            evidence = await AliasEvidence.get(id=data["evidence_id"]).using_db(db)
            evidence.moderation_state, evidence.version = "revoked", evidence.version + 1
            await evidence.save(using_db=db)
            await self.event(db, "revoke_evidence", actor_id=actor_id, request_id=row.id, after={"evidence_id": evidence.id}, reason=reason)
        if operation in {"ADD_ALIAS", "REPLACE_ALIAS"}:
            source = await self._source(db, f"community:{data['origin_game'] or 'global'}", data["origin_game"], kind="community", known=True)
            await self._evidence(db, source, data["game_song_id"], data["text"], record_key=f"request:{row.id}", scope=data["scope"], actor_id=row.actor_id, request_id=row.id)
        elif operation in {"BLOCK_ALIAS", "UNBLOCK_ALIAS"}:
            song = await GameSong.get(id=data["game_song_id"]).using_db(db)
            key = alias_key(data["text"])
            digest = content_key([str(song.work_id), key])
            old = await AliasRule.filter(rule_key=digest).using_db(db).first()
            if old and (old.work_id, old.alias_key) != (song.work_id, key):
                raise AliasConflictError("Rule key collision")
            await AliasRule.update_or_create(rule_key=digest, using_db=db, defaults={"work_id": song.work_id,
                "alias_key": key, "blocked": operation == "BLOCK_ALIAS", "reason": reason, "version": old.version + 1 if old else 1})
        elif operation == "RELINK_GAME_SONGS":
            songs = await GameSong.filter(id__in=data["song_ids"]).using_db(db).order_by("id")
            if data["mode"] == "split":
                await split_songs(db, data["song_ids"], actor_id=actor_id, request_id=row.id, reason=reason)
            else:
                target = songs[0].work_id
                for work in sorted({s.work_id for s in songs} - {target}, key=str):
                    await merge_works(db, work, target, actor_id=actor_id, request_id=row.id, reason=reason)
        elif operation == "MAP_SOURCE_RECORD":
            await self._map_record(db, data, actor_id, row.id, reason)

    async def _mapping_record(self, db, payload):
        key = content_key([payload["provider"], payload["namespace"], payload["game_code"]])
        batch = await CatalogImport.get(source_key=key).using_db(db)
        records, _ = prepare_arcade_catalog(batch.observed_payload)
        found = [r for r in records if r.external_key == payload["external_key"]]
        if len(found) != 1:
            raise AliasConflictError("Source record is missing or ambiguous in the latest snapshot")
        return batch, found[0]

    async def _map_record(self, db, data, actor_id, request_id, reason):
        batch, record = await self._mapping_record(db, data)
        decision = data["decision"]
        if decision != "reject":
            identity = record.identity
            payload = record.song.model_dump(mode="json")
            payload.pop("id", None)
            if decision == "new":
                work = await MusicWork.create(display_title=record.song.title, artist=record.song.artist, using_db=db)
                target = await GameSong.create(work=work, game_code=data["game_code"], title=record.song.title,
                    artist=record.song.artist, chart_variant=identity["chart_variant"], catalog_payload=payload, using_db=db)
            else:
                target = await GameSong.get(id=data["target_id"]).using_db(db)
                if target.game_code != data["game_code"]:
                    raise ValueError("Mapping cannot cross games")
            ref_key = content_key([data["provider"], data["namespace"], data["game_code"], record.key_kind, record.external_key])
            ref = await SongReference.filter(reference_key=ref_key).using_db(db).first()
            if ref and (ref.external_key, ref.key_kind) != (record.external_key, record.key_kind):
                raise AliasConflictError("Source key collision")
            if ref and ref.game_song_id != target.id:
                old_anchor = ref.game_song_id
                for evidence in await AliasEvidence.filter(reference_id=ref.id).using_db(db):
                    evidence.anchor_id = target.id
                    evidence.evidence_key = content_key([evidence.source_id, evidence.source_record_key, target.id,
                                                        evidence.alias_key, evidence.scope, evidence.kind])
                    evidence.version += 1
                    await evidence.save(using_db=db)
                if not await SongReference.filter(game_song_id=old_anchor, present=True).exclude(id=ref.id).using_db(db).exists():
                    await GameSong.filter(id=old_anchor).using_db(db).update(active=False, version=F("version") + 1)
            old_refs = await SongReference.filter(provider=data["provider"], namespace=data["namespace"], game_song=target).using_db(db)
            for old in old_refs:
                if old.reference_key != ref_key:
                    old.present = False
                    await old.save(using_db=db, update_fields=["present"])
            await SongReference.update_or_create(reference_key=ref_key, using_db=db, defaults={
                "game_song": target, "provider": data["provider"], "namespace": data["namespace"], "game_code": data["game_code"],
                "external_key": record.external_key, "key_kind": record.key_kind, "identity_material": identity,
                "payload": payload, "raw_payload": record.raw, "present": True,
            })
            await GameSong.filter(id=target.id).using_db(db).update(title=record.song.title, artist=record.song.artist,
                chart_variant=identity["chart_variant"], catalog_payload=payload, active=True, version=F("version") + 1)
            title_source = await AliasSource.filter(source_key=f"catalog:{data['provider']}:{data['namespace']}:{data['game_code']}").using_db(db).first()
            if title_source:
                for evidence in await AliasEvidence.filter(source=title_source, anchor=target, kind="title", remote_state="present").using_db(db):
                    if evidence.source_record_key != record.external_key:
                        evidence.remote_state, evidence.version = "withdrawn", evidence.version + 1
                        await evidence.save(using_db=db)
                        await self.event(db, "retire_mapped_title", actor_id=actor_id, request_id=request_id, after={"evidence_id": evidence.id})
            await self.ingest_catalog(db, data["game_code"], [record], {record.external_key: target.id},
                provider=data["provider"], namespace=data["namespace"], complete=False)
        else:
            ref = await SongReference.filter(reference_key=content_key([
                data["provider"], data["namespace"], data["game_code"], record.key_kind, record.external_key,
            ])).using_db(db).first()
            if ref:
                ref.present = False
                await ref.save(using_db=db, update_fields=["present"])
                await AliasEvidence.filter(reference=ref).using_db(db).update(remote_state="withdrawn", version=F("version") + 1)
                remaining = await SongReference.filter(game_song_id=ref.game_song_id, present=True).using_db(db).order_by("provider", "namespace", "reference_key").first()
                if remaining:
                    await GameSong.filter(id=ref.game_song_id).using_db(db).update(title=remaining.payload["title"],
                        artist=remaining.payload.get("artist", ""), chart_variant=remaining.identity_material["chart_variant"],
                        catalog_payload=remaining.payload, version=F("version") + 1)
                else:
                    await GameSong.filter(id=ref.game_song_id).using_db(db).update(active=False, version=F("version") + 1)
        batch.resolutions = {**(batch.resolutions or {}), record.external_key: {"decision": decision}}
        batch.sync_version += 1
        await batch.save(using_db=db, update_fields=["resolutions", "sync_version"])
        await self.event(db, "map_source_record", actor_id=actor_id, request_id=request_id, after=data, reason=reason)

    async def list_candidates(self, *, actor_id, after_id=0, limit=50):
        self.authorize(actor_id, "admin")
        return await WorkCandidate.filter(status="pending", id__gt=after_id).using_db(self.catalog._db).order_by("id").limit(min(max(limit, 1), 100)).values()

    async def decide_candidate(self, candidate_id, *, actor_id, accept, expected_version, reason):
        self.authorize(actor_id, "admin")
        if not reason.strip():
            raise ValueError("A reason is required")
        async with self.write() as db:
            self.authorize(actor_id, "admin")
            candidate = await WorkCandidate.get(id=candidate_id).using_db(db)
            if candidate.status != "pending" or candidate.version != expected_version:
                raise AliasConflictError("CANDIDATE_CHANGED")
            songs = await GameSong.filter(id__in=[candidate.left_id, candidate.right_id]).using_db(db).order_by("id")
            if {str(s.id): s.version for s in songs} != candidate.target_versions:
                raise AliasConflictError("TARGET_CHANGED")
            if accept:
                await merge_works(db, songs[1].work_id, songs[0].work_id, actor_id=actor_id, reason=reason)
            candidate.status, candidate.version = "accepted" if accept else "rejected", candidate.version + 1
            await candidate.save(using_db=db)
            await self.event(db, "decide_candidate", actor_id=actor_id, after={"id": candidate.id, "status": candidate.status}, reason=reason)

    async def associate(self, *, actor_id):
        self.authorize(actor_id, "admin")
        async with self.write() as db:
            self.authorize(actor_id, "admin")
            return await match_catalog(db)

    async def events(self, *, actor_id, after_id=0, limit=50):
        if type(actor_id) is not int or actor_id <= 0:
            raise AliasPermissionError("An authenticated internal user ID is required")
        rows = await AliasEvent.filter(id__gt=after_id).using_db(self.catalog._db).order_by("id").limit(min(max(limit, 1), 100))
        result = []
        for row in rows:
            permitted = self.allowed(actor_id, "admin")
            if not permitted and row.request_id:
                request = await AliasRequest.get(id=row.request_id).using_db(self.catalog._db)
                permitted = actor_id == request.actor_id or await self._can_review(request, actor_id, self.catalog._db)
            if permitted:
                result.append({"id": row.id, "request_id": row.request_id, "actor_id": row.actor_id, "action": row.action,
                    "before": row.before, "after": row.after, "reason": row.reason, "created_at": row.created_at.isoformat()})
        return {"items": result, "next_cursor": rows[-1].id if rows else after_id}
