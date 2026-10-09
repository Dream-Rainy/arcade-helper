"""Persistent catalog identity with conservative, transactional imports."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from tortoise.expressions import F
from tortoise.transactions import in_transaction

from arcade_helper.catalog.importer import CatalogImportReport, CatalogRecord, content_key, prepare_arcade_catalog
from arcade_helper.core.errors import CatalogSyncError, StorageNotConfiguredError
from arcade_helper.core.song import SongData
from arcade_helper.aliases.models import alias_key
from arcade_helper.core.song_identity import normalize_width, parse_chart_title
from .catalog_models import AliasRevision, CatalogImport, GameSong, MusicWork, SongReference


class CatalogConflictError(CatalogSyncError):
    """The catalog changed since this import began; refetch before retrying."""


class TortoiseCatalogStore:
    """Opt-in stable catalog. All IDs returned here are internal GameSong IDs."""

    def __init__(self, *, connection_name: str = "default", enable_aliases: bool = False, auto_match: bool = True) -> None:
        self.connection_name = connection_name
        self.enable_aliases = enable_aliases
        self.auto_match = auto_match
        self._song_cache = {}
        self._cache_lock = asyncio.Lock()

    @property
    def _db(self):
        from tortoise import connections
        return connections.get(self.connection_name)

    async def get_revision(self) -> int:
        row = await AliasRevision.filter(id=1).using_db(self._db).first()
        if row is None:
            raise StorageNotConfiguredError("Run initialize_catalog_storage() before persistent catalog use")
        return row.revision

    async def import_arcade_catalog(
        self, game_code: str, payload: dict, *, provider: str = "arcade_songs",
        namespace: str = "default", expected_sync_version: int | None = None,
        dry_run: bool = False, allow_empty: bool = False,
        expected_alias_versions: dict | None = None,
    ) -> CatalogImportReport:
        records, invalid = prepare_arcade_catalog(payload)
        return await self.import_records(
            game_code, records, provider=provider, namespace=namespace, invalid=invalid,
            expected_sync_version=expected_sync_version, dry_run=dry_run, allow_empty=allow_empty,
            raw_payload=payload,
            expected_alias_versions=expected_alias_versions,
        )

    async def import_records(
        self, game_code: str, records: list[CatalogRecord], *, provider: str,
        namespace: str = "default", expected_sync_version: int | None = None,
        invalid: list[str] | None = None, dry_run: bool = False, allow_empty: bool = False,
        raw_payload: Any = None, complete: bool = True, expected_alias_versions: dict | None = None, alias_complete: bool = False,
    ) -> CatalogImportReport:
        """Publish certain rows; defer removals if any identity is unresolved.

        Pass the dataset sync version read BEFORE fetching to reject out-of-order network
        completions. Without it this is an explicit local import of supplied data.
        """
        game_code, provider, namespace = game_code.strip().lower(), provider.strip(), namespace.strip()
        for value, limit in ((game_code, 32), (provider, 32), (namespace, 64)):
            if not value or len(value) > limit:
                raise ValueError("Invalid catalog source name")
        for record in records:
            if not record.song.title or record.native_key == "":
                raise ValueError("Song title and native keys must not be empty")
            alias_key(record.song.title)
        source_key = content_key([provider, namespace, game_code])
        async with in_transaction(self.connection_name) as db:
            if not dry_run:
                # ponytail: serialize fact writes globally; freshness is checked per dataset below.
                locked = await AliasRevision.filter(id=1).using_db(db).update(revision=F("revision") + 1)
                if not locked:
                    raise StorageNotConfiguredError("Run initialize_catalog_storage() first")
            current_revision = (await AliasRevision.get(id=1).using_db(db)).revision
            revision = current_revision if dry_run else current_revision - 1
            previous = await CatalogImport.filter(source_key=source_key).using_db(db).only("id", "provider", "namespace", "game_code", "sync_version", "resolutions").first()
            if previous is not None and (previous.provider, previous.namespace, previous.game_code) != (provider, namespace, game_code):
                raise CatalogConflictError("Dataset key collision")
            sync_version = previous.sync_version if previous else 0
            if expected_sync_version is not None and sync_version != expected_sync_version:
                raise CatalogConflictError("Dataset changed during fetch; refetch and retry")
            report = CatalogImportReport(game_code, revision, dry_run=dry_run, invalid=list(invalid or []), sync_version=sync_version)
            refs = await SongReference.filter(
                provider=provider, namespace=namespace, game_code=game_code,
            ).using_db(db).only("id", "reference_key", "external_key", "key_kind", "identity_material", "present", "game_song_id")
            by_key = {ref.reference_key: ref for ref in refs}
            by_normalized_identity = defaultdict(list)
            for ref in refs:
                if ref.key_kind == "derived" and ref.present:
                    identity = dict(ref.identity_material)
                    title = normalize_width(identity["title"])
                    identity["title"] = title.strip() or title
                    by_normalized_identity[content_key(identity)].append(ref)
            grouped: dict[str, list[CatalogRecord]] = defaultdict(list)
            for record in records:
                grouped[content_key([provider, namespace, game_code, record.key_kind, record.external_key])].append(record)
            missing = [ref for ref in refs if ref.present and ref.reference_key not in grouped]
            affected: set[int] = set()
            changed = False
            grouped_items = list(grouped.items())
            for offset in range(0, len(grouped_items), 256):
                batch = grouped_items[offset:offset + 256]
                batch_ids = set()
                for key, group in batch:
                    ref = by_key.get(key)
                    if ref is not None:
                        batch_ids.add(ref.id)
                    elif group[0].key_kind == "derived":
                        matches = by_normalized_identity.get(content_key(group[0].identity), [])
                        if len(matches) == 1:
                            batch_ids.add(matches[0].id)
                previous_payloads = {r["id"]: r for r in await SongReference.filter(id__in=batch_ids).using_db(db).values("id", "payload", "raw_payload", "present")} if batch_ids else {}
                for key, group in batch:
                    record = group[0]
                    if previous and (previous.resolutions or {}).get(record.external_key, {}).get("decision") == "reject":
                        continue
                    ref = by_key.get(key)
                    identity = record.identity
                    width_matches = by_normalized_identity.get(content_key(identity), []) if ref is None and record.key_kind == "derived" else []
                    width_migration = len(width_matches) == 1
                    if width_migration:
                        ref = width_matches[0]
                    reason = None
                    if any(item.raw != record.raw or item.identity != identity for item in group[1:]):
                        reason = "duplicate_identity"
                    elif len(width_matches) > 1:
                        reason = "width_identity_collision"
                    elif ref is not None and not width_migration and (ref.external_key != record.external_key or ref.key_kind != record.key_kind):
                        raise CatalogConflictError("Source key collision")
                    elif ref is not None and not width_migration and record.key_kind == "derived" and ref.identity_material != identity:
                        raise CatalogConflictError("Derived identity collision")
                    elif ref is not None and any(
                        ref.identity_material.get(field) != identity[field]
                        for field in ("chart_variant", "music_variant")
                    ):
                        reason = "variant_changed"
                    elif record.key_kind == "derived" and (identity["artist"] is None or identity["chart_variant"] is None):
                        reason = "incomplete_identity"
                    elif ref is not None and not ref.present and record.key_kind == "derived":
                        reason = "retired_identity"
                    elif ref is None and record.key_kind == "derived" and any(
                        old.identity_material["title"] == identity["title"]
                        or (identity["artist"] and old.identity_material.get("artist") == identity["artist"])
                        for old in missing
                    ):
                        reason = "possible_rename"
                    if reason:
                        report.pending.append({"external_key": record.external_key, "title": record.song.title, "reason": reason})
                        continue
                    if width_migration:
                        # A unique width-only change keeps its existing GameSong ID and evidence anchors.
                        missing = [old for old in missing if old.id != ref.id]
                        if not dry_run:
                            ref.reference_key, ref.external_key = key, record.external_key
                            await ref.save(using_db=db, update_fields=["reference_key", "external_key"])
                    payload = record.song.model_dump(mode="json")
                    for field in ("id", "game_song_id", "work_id", "game_code"):
                        payload.pop(field, None)
                    payload["chart_variant"] = identity["chart_variant"]
                    if ref is None:
                        report.created += 1
                        changed = True
                        if dry_run:
                            continue
                        work = await MusicWork.create(
                            display_title=parse_chart_title(record.song.title, literal=identity["chart_variant"] == [])[0],
                            artist=record.song.artist, using_db=db,
                        )
                        song = await GameSong.create(
                            work=work, game_code=game_code, title=record.song.title,
                            artist=record.song.artist, chart_variant=identity["chart_variant"],
                            catalog_payload=payload, using_db=db,
                        )
                        ref = await SongReference.create(
                            game_song=song, provider=provider, namespace=namespace, game_code=game_code,
                            reference_key=key, external_key=record.external_key, key_kind=record.key_kind,
                            identity_material=identity, payload=payload, raw_payload=record.raw, using_db=db,
                        )
                    elif previous_payloads[ref.id]["payload"] != payload or ref.identity_material != identity or not ref.present:
                        report.updated += 1
                        changed = True
                        if not dry_run:
                            ref.payload, ref.identity_material, ref.present = payload, identity, True
                            ref.raw_payload = record.raw
                            await ref.save(using_db=db, update_fields=["payload", "identity_material", "present", "raw_payload"])
                    else:
                        report.unchanged += 1
                        if not dry_run and previous_payloads[ref.id]["raw_payload"] != record.raw:
                            ref.raw_payload = record.raw
                            await ref.save(using_db=db, update_fields=["raw_payload"])
                    if ref is not None:
                        report.song_ids[record.external_key] = ref.game_song_id
                        if ref.id not in previous_payloads or previous_payloads[ref.id]["payload"] != payload or not previous_payloads[ref.id]["present"]:
                            affected.add(ref.game_song_id)
            report.missing_deferred = bool(not complete or report.pending or report.invalid or (not records and not allow_empty))
            if not report.missing_deferred:
                report.withdrawn = len(missing)
                changed |= bool(missing)
                if not dry_run:
                    for ref in missing:
                        ref.present = False
                        await ref.save(using_db=db, update_fields=["present"])
                        affected.add(ref.game_song_id)
            if dry_run:
                return report
            affected_ids = sorted(affected)
            for offset in range(0, len(affected_ids), 256):
                batch_ids = affected_ids[offset:offset + 256]
                # Each source keeps its own observation; projection order is deterministic.
                selected = {}
                for row in await SongReference.filter(game_song_id__in=batch_ids, present=True).using_db(db).order_by(
                    "provider", "namespace", "reference_key",
                ).values("game_song_id", "payload", "identity_material"):
                    selected.setdefault(row["game_song_id"], row)
                await GameSong.filter(id__in=set(batch_ids) - selected.keys(), active=True).using_db(db).update(active=False, version=F("version") + 1)
                current_rows = await GameSong.filter(id__in=list(selected)).using_db(db).values("id", "title", "artist", "chart_variant", "active", "version", "work_id")
                for current in current_rows:
                    observation = selected[current["id"]]
                    payload, variant = observation["payload"], observation["identity_material"]["chart_variant"]
                    semantic_changed = (current["title"], current["artist"], current["chart_variant"], current["active"]) != (payload["title"], payload.get("artist", ""), variant, True)
                    await GameSong.filter(id=current["id"]).using_db(db).update(
                        title=payload["title"], artist=payload.get("artist", ""),
                        chart_variant=variant, catalog_payload=payload, active=True,
                        version=current["version"] + int(semantic_changed),
                    )
                    if current["title"] != payload["title"]:
                        work = await MusicWork.get(id=current["work_id"]).using_db(db).only("id", "display_title")
                        display_title = normalize_width(work.display_title)
                        if work.display_title != display_title:
                            work.display_title = display_title
                            await work.save(using_db=db, update_fields=["display_title"])
            if self.enable_aliases:
                from .alias_store import TortoiseAliasStore
                from arcade_helper.catalog.matcher import match_catalog
                from .alias_models import AliasEvent
                last_event = await AliasEvent.filter(actor_id__isnull=True).using_db(db).only("id").order_by("-id").first()
                await TortoiseAliasStore(self).ingest_catalog(
                    db, game_code, records, report.song_ids, provider=provider, namespace=namespace,
                    complete=(complete or alias_complete) and not report.pending and not report.invalid, expected_versions=expected_alias_versions,
                )
                if self.auto_match:
                    await match_catalog(db)
                latest_event = await AliasEvent.filter(actor_id__isnull=True).using_db(db).only("id").order_by("-id").first()
                changed |= (latest_event.id if latest_event else 0) != (last_event.id if last_event else 0)
            if changed:
                report.revision += 1
            else:
                await AliasRevision.filter(id=1).using_db(db).update(revision=revision)
            raw_payload = raw_payload if raw_payload is not None else [record.raw for record in records]
            report.sync_version += 1
            # Keep the last usable snapshot when a fetch is partial, invalid or unexpectedly empty.
            preserve_snapshot = not complete or bool(report.invalid or report.pending) or (not records and not allow_empty)
            snapshot_fields = {
                "provider": provider, "namespace": namespace, "game_code": game_code,
                "sync_version": report.sync_version,
                "report": asdict(report),
                "observed_payload": raw_payload,
            }
            if not preserve_snapshot:
                snapshot_fields.update(raw_payload=raw_payload, snapshot_hash=content_key(raw_payload))
            elif previous is None:
                snapshot_fields.update(raw_payload=None, snapshot_hash=None)
            # A preserved snapshot stays in the database without decoding/reencoding it.
            if previous:
                for field, value in snapshot_fields.items():
                    setattr(previous, field, value)
                previous.updated_at = datetime.now(timezone.utc)
                await previous.save(using_db=db, update_fields=[*snapshot_fields, "updated_at"])
            else:
                await CatalogImport.create(source_key=source_key, using_db=db, **snapshot_fields)
            return report

    async def get_sync_version(self, game_code: str, *, provider: str = "arcade_songs", namespace: str = "default") -> int:
        row = await CatalogImport.filter(source_key=content_key([
            provider.strip(), namespace.strip(), game_code.strip().lower(),
        ])).using_db(self._db).only("sync_version").first()
        return row.sync_version if row else 0

    async def get_import(self, game_code: str, *, provider: str = "arcade_songs", namespace: str = "default") -> dict | None:
        row = await CatalogImport.filter(
            source_key=content_key([provider.strip(), namespace.strip(), game_code.strip().lower()]),
        ).using_db(self._db).only("report").first()
        return row.report if row else None

    async def resolve_reference(
        self, game_code: str, external_key: str, *, provider: str, namespace: str = "default",
        key_kind: str = "native",
    ) -> int | None:
        row = await SongReference.filter(reference_key=content_key([
            provider.strip(), namespace.strip(), game_code.strip().lower(), key_kind, str(external_key),
        ])).using_db(self._db).only("game_song_id").first()
        return row.game_song_id if row else None

    @staticmethod
    def _song(row: GameSong) -> SongData:
        return SongData.model_validate({
            **row.catalog_payload, "id": row.id, "game_song_id": row.id,
            "work_id": str(row.work_id), "game_code": row.game_code,
        })

    async def _cached_songs(self, game_code: str) -> dict[int, SongData]:
        async with self._cache_lock:
            for _ in range(3):
                before = await self.get_revision()
                cached = self._song_cache.get(game_code)
                if cached is not None and cached[0] == before:
                    return cached[1]
                songs = {row.id: self._song(row) for row in await GameSong.filter(
                    game_code=game_code, active=True,
                ).using_db(self._db).order_by("id")}
                if self.enable_aliases:
                    from .alias_store import TortoiseAliasStore
                    aliases = await TortoiseAliasStore(self)._alias_texts(game_code)
                    for song_id, song in songs.items():
                        song.aliases = aliases.get(song_id, [])
                if await self.get_revision() == before:
                    self._song_cache[game_code] = (before, songs)
                    return songs
            raise CatalogConflictError("Catalog changed repeatedly while loading; retry")

    async def get_song_by_id(self, game_code: str, song_id: int) -> SongData | None:
        for _ in range(3):
            before = await self.get_revision()
            cached = self._song_cache.get(game_code)
            if cached is not None and cached[0] == before:
                song = cached[1].get(song_id)
                return song.model_copy(deep=True) if song else None
            row = await GameSong.filter(game_code=game_code, id=song_id, active=True).using_db(self._db).first()
            song = self._song(row) if row else None
            if song is not None and self.enable_aliases:
                from .alias_store import TortoiseAliasStore
                song.aliases = (await TortoiseAliasStore(self)._alias_texts(song_ids=[song_id])).get(song_id, [])
            if await self.get_revision() == before:
                return song
        raise CatalogConflictError("Catalog changed repeatedly while loading; retry")

    async def get_songs_by_ids(self, game_code: str, song_ids: list[int]) -> dict[int, SongData]:
        """Read full, active songs and effective aliases for just the requested IDs."""
        ids = list(dict.fromkeys(song_ids))
        if not ids:
            return {}
        for _ in range(3):
            db = self._db
            before = await self.get_revision()
            songs = {row.id: self._song(row) for row in await GameSong.filter(
                game_code=game_code, active=True, id__in=ids,
            ).using_db(db)}
            if self.enable_aliases and songs:
                from .alias_store import TortoiseAliasStore
                projection = await TortoiseAliasStore(self)._alias_texts(song_ids=list(songs))
                for sid, song in songs.items():
                    song.aliases = projection.get(sid, [])
            if await self.get_revision() == before and self._db is db:
                return {sid: songs[sid] for sid in ids if sid in songs}
        raise CatalogConflictError("Catalog changed repeatedly while loading; retry")

    async def load_all_songs(self, game_code: str) -> dict[int, SongData]:
        return {key: song.model_copy(deep=True) for key, song in (await self._cached_songs(game_code)).items()}

    async def native_aliases(self, game_code: str) -> dict[int, list[str]]:
        from .alias_store import TortoiseAliasStore
        if self.enable_aliases:
            aliases = await TortoiseAliasStore(self)._alias_texts(game_code)
        else:
            aliases = {sid: song.aliases for sid, song in (await self.load_search_songs(game_code)).items()}
        refs = await SongReference.filter(provider="catalog", namespace="native", game_code=game_code, present=True).using_db(self._db).values_list("game_song_id", "external_key")
        return {int(key): list(aliases[sid]) for sid, key in refs if sid in aliases and key.isdecimal()}

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        return dict(await GameSong.filter(game_code=game_code, active=True).using_db(self._db).values_list("id", "title"))

    async def load_search_songs(self, game_code: str) -> dict[int, SongData]:
        """Text-only metadata for alias-enabled catalogs; legacy JSON fallback."""
        if not self.enable_aliases:
            # Legacy imports keep aliases only in JSON until alias storage is enabled.
            return await self._cached_songs(game_code)
        rows = await GameSong.filter(game_code=game_code, active=True).using_db(self._db).values(
            "id", "title", "artist", "chart_variant"
        )
        songs = {r["id"]: SongData(**r) for r in rows}
        if self.enable_aliases:
            from .alias_store import TortoiseAliasStore
            projection = await TortoiseAliasStore(self)._alias_texts(game_code)
            for sid, song in songs.items():
                song.aliases = projection[sid]
        return songs

    async def query_title_exact(self, game_code: str, title: str) -> list[tuple[int, str]]:
        if self.enable_aliases:
            from .alias_store import TortoiseAliasStore
            return await TortoiseAliasStore(self).query_exact(game_code, title, kind="title")
        return await GameSong.filter(game_code=game_code, active=True, title__iexact=normalize_width(title)).using_db(
            self._db
        ).values_list("id", "title")

    async def load_alias_records(self, game_code: str) -> list[tuple[int, str]]:
        return [(song_id, alias) for song_id, song in (await self.load_search_songs(game_code)).items() for alias in song.aliases]

    async def query_alias_exact(self, game_code: str, alias_lower: str) -> list[tuple[int, str]]:
        if self.enable_aliases:
            from .alias_store import TortoiseAliasStore
            return await TortoiseAliasStore(self).query_exact(game_code, alias_lower)
        return [(song_id, alias) for song_id, alias in await self.load_alias_records(game_code) if alias.lower() == alias_lower]

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int) -> list[str]:
        song = await self.get_song_by_id(game_code, song_id)
        return song.aliases if song else []

    async def get_song_with_difficulty(self, game_code: str, song_id: int, song_type: str = "standard", level_index: int = 3) -> dict | None:
        song = await self.get_song_by_id(game_code, song_id)
        if song is None:
            return None
        sheets = song.difficulties.get(song_type, [])
        return {"song_data": song, "target_difficulty": sheets[level_index] if 0 <= level_index < len(sheets) else None}
