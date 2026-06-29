"""Tortoise-backed song catalog queries."""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Any

from arcade_helper.core.song import SongData
from arcade_helper.games.chunithm.data_fetcher import build_chuni_jacket_image_name
from arcade_helper.games.chunithm.schemas import ChuniSongData
from arcade_helper.games.maimai.data_fetcher import build_maimai_jacket_image_name
from arcade_helper.games.maimai.schemas import MaiSongData, MapData, MapTreasureData

from .models import ChuniSong, ChuniSongAlias, MaiMap, MaiMapTreasure, MaiSong, MaiSongAlias


@dataclass(frozen=True)
class SongStoreSpec:
    song_model: type
    alias_model: type
    temp_id_threshold: int
    supports_song_with_difficulty: bool = False
    dedupe_aliases_for_display: bool = False


def _maimai_song_from_row(row: Any, aliases: list[str]) -> MaiSongData:
    return MaiSongData.from_dict({
        "id": row.id,
        "title": row.title,
        "artist": row.artist,
        "category": row.category,
        "bpm": row.bpm,
        "version": row.version,
        "rights": row.rights,
        "mai_map": row.mai_map,
        "release_date": row.release_date,
        "is_new": row.is_new,
        "is_locked": row.is_locked,
        "comment": row.comment,
        "difficulties": row.difficulties or {},
        "collections": row.collections or [],
        "aliases": aliases,
        "image_name": row.image_name or build_maimai_jacket_image_name(row.id),
    })


def _chunithm_song_from_row(row: Any, aliases: list[str]) -> ChuniSongData:
    return ChuniSongData.model_validate({
        "id": row.id,
        "title": row.title,
        "artist": row.artist or "",
        "genre": row.genre or "",
        "bpm": row.bpm or 0,
        "version": row.version,
        "rights": row.rights,
        "difficulties": row.difficulties or {},
        "image_name": row.image_name or build_chuni_jacket_image_name(row.id),
        "release_date": "",
        "is_new": False,
        "is_locked": False,
        "comment": "",
        "aliases": aliases,
    })


class TortoiseSongStore:
    """Read song catalog data from Tortoise models."""

    _specs: dict[str, SongStoreSpec] = {
        "maimai": SongStoreSpec(
            song_model=MaiSong,
            alias_model=MaiSongAlias,
            temp_id_threshold=10000000,
            supports_song_with_difficulty=True,
        ),
        "chunithm": SongStoreSpec(
            song_model=ChuniSong,
            alias_model=ChuniSongAlias,
            temp_id_threshold=100000,
            dedupe_aliases_for_display=True,
        ),
    }

    def __init__(self, logger: Any | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

    def _spec(self, game_code: str) -> SongStoreSpec:
        try:
            return self._specs[game_code]
        except KeyError as e:
            raise ValueError(f"Unsupported game_code: {game_code}") from e

    def _song_from_row(self, game_code: str, row: Any, aliases: list[str]) -> SongData:
        if game_code == "maimai":
            return _maimai_song_from_row(row, aliases)
        if game_code == "chunithm":
            return _chunithm_song_from_row(row, aliases)
        raise ValueError(f"Unsupported game_code: {game_code}")

    def _song_to_defaults(self, game_code: str, song: SongData) -> dict[str, Any]:
        diffs = getattr(song, "difficulties", None) or {}
        if game_code == "maimai":
            return {
                "title": song.title,
                "artist": getattr(song, "artist", None),
                "category": getattr(song, "category", None),
                "bpm": getattr(song, "bpm", None),
                "version": getattr(song, "version", None),
                "rights": getattr(song, "rights", None),
                "mai_map": getattr(song, "mai_map", None),
                "release_date": getattr(song, "release_date", None),
                "image_name": getattr(song, "image_name", "") or "",
                "is_new": getattr(song, "is_new", False),
                "is_locked": getattr(song, "is_locked", False),
                "comment": getattr(song, "comment", None),
                "difficulties": {
                    t: [s.model_dump(mode="json", by_alias=True, exclude_none=True) for s in sheets]
                    for t, sheets in diffs.items()
                },
            }
        if game_code == "chunithm":
            genre = getattr(song, "genre", "") or ""
            return {
                "title": song.title or "",
                "artist": getattr(song, "artist", None),
                "genre": (genre[:64] if isinstance(genre, str) and genre else None),
                "bpm": getattr(song, "bpm", None),
                "version": getattr(song, "version", None),
                "rights": getattr(song, "rights", None),
                "image_name": getattr(song, "image_name", "") or "",
                "difficulties": {
                    t: [s.model_dump(mode="json", by_alias=True, exclude_none=True) for s in sheets]
                    for t, sheets in diffs.items()
                },
            }
        raise ValueError(f"Unsupported game_code: {game_code}")

    async def sync_songs(self, game_code: str, song_data: dict[int, SongData]) -> None:
        spec = self._spec(game_code)
        self.logger.info(f"[{game_code}] 开始同步乐曲数据到数据库，共 {len(song_data)} 首")

        try:
            deleted_aliases = await spec.alias_model.filter(song_id__gte=spec.temp_id_threshold).delete()
            deleted_songs = await spec.song_model.filter(id__gte=spec.temp_id_threshold).delete()
            if deleted_aliases or deleted_songs:
                self.logger.info(
                    f"[{game_code}] 已清理历史临时乐曲数据："
                    f"删除 {deleted_songs} 首临时歌曲，{deleted_aliases} 条别名"
                )
        except Exception as e:
            self.logger.warning(f"[{game_code}] 清理历史临时乐曲数据失败，将继续尝试同步: {e}")

        synced_count = 0
        alias_count = 0

        for song_id, song in song_data.items():
            defaults = self._song_to_defaults(game_code, song)
            db_song, _ = await spec.song_model.update_or_create(id=song_id, defaults=defaults)
            synced_count += 1

            aliases_to_sync: list[str] = getattr(song, "aliases", None) or []
            seen: set[str] = set()
            unique_aliases: list[str] = []
            for alias in aliases_to_sync:
                if alias and alias.lower() not in seen:
                    seen.add(alias.lower())
                    unique_aliases.append(alias)

            if unique_aliases:
                existing_aliases = set(
                    str(a).lower()
                    for a in await spec.alias_model.filter(song_id=song_id).values_list(
                        "alias",
                        flat=True,
                    )
                )
                for priority, alias in enumerate(unique_aliases):
                    if alias.lower() not in existing_aliases:
                        await spec.alias_model.create(
                            song=db_song,
                            alias=alias,
                            priority=priority,
                        )
                        alias_count += 1

        self.logger.info(f"[{game_code}] 乐曲数据同步完成：{synced_count} 首乐曲，新增 {alias_count} 条别名")

    async def sync_maimai_collections(self, song_id: int, collections: list[dict[str, Any]]) -> None:
        self.logger.debug(f"同步乐曲 {song_id} 的收藏信息到数据库")
        try:
            await MaiSong.filter(id=song_id).update(collections=collections)
            self.logger.debug(f"成功同步乐曲 {song_id} 的 {len(collections)} 项收藏信息")
        except Exception as e:
            traceback.print_exc()
            self.logger.warning(f"同步乐曲 {song_id} 收藏信息失败: {e}")

    async def sync_maimai_maps(self, map_data: dict[int, MapData]) -> None:
        self.logger.info(f"开始同步 maimai 地图数据到数据库，共 {len(map_data)} 个地图")
        synced_count = 0

        for map_id, map_model in map_data.items():
            try:
                await MaiMap.update_or_create(
                    id=map_id,
                    defaults={
                        "data_name": map_model.data_name,
                        "map_name": map_model.map_name,
                        "is_collabo": map_model.is_collabo,
                        "is_infinity": map_model.is_infinity,
                        "island_id": map_model.island_id,
                        "island_name": map_model.island_name,
                        "color_id": map_model.color_id,
                        "color_name": map_model.color_name,
                        "bonus_music_id": map_model.bonus_music_id,
                        "bonus_music_name": map_model.bonus_music_name,
                        "bonus_music_magnification": map_model.bonus_music_magnification,
                        "open_event_id": map_model.open_event_id,
                        "open_event_name": map_model.open_event_name,
                        "net_open_name_id": map_model.net_open_name_id,
                        "net_open_name": map_model.net_open_name,
                        "treasures": [t.model_dump(by_alias=True) for t in map_model.treasures],
                    },
                )
                synced_count += 1
            except Exception as e:
                self.logger.warning(f"同步地图 {map_id} 失败: {e}")
                traceback.print_exc()

        self.logger.info(f"maimai 地图数据同步完成：{synced_count} 个地图")

    async def sync_maimai_map_treasures(self, treasure_data: dict[int, MapTreasureData]) -> None:
        self.logger.info(f"开始同步 maimai 地图奖励数据到数据库，共 {len(treasure_data)} 个奖励")
        synced_count = 0

        for treasure_id, treasure_model in treasure_data.items():
            try:
                await MaiMapTreasure.update_or_create(
                    id=treasure_id,
                    defaults={
                        "data_name": treasure_model.data_name,
                        "treasure_name": treasure_model.treasure_name,
                        "treasure_type": treasure_model.treasure_type,
                        "character_id": treasure_model.character_id,
                        "character_name": treasure_model.character_name,
                        "music_id": treasure_model.music_id,
                        "music_name": treasure_model.music_name,
                        "numeric": treasure_model.numeric,
                        "name_plate_id": treasure_model.name_plate_id,
                        "name_plate_name": treasure_model.name_plate_name,
                        "frame_id": treasure_model.frame_id,
                        "frame_name": treasure_model.frame_name,
                        "title_id": treasure_model.title_id,
                        "title_name": treasure_model.title_name,
                        "icon_id": treasure_model.icon_id,
                        "icon_name": treasure_model.icon_name,
                        "challenge_id": treasure_model.challenge_id,
                        "challenge_name": treasure_model.challenge_name,
                        "gate_id": treasure_model.gate_id,
                        "gate_name": treasure_model.gate_name,
                        "key_id": treasure_model.key_id,
                        "key_name": treasure_model.key_name,
                    },
                )
                synced_count += 1
            except Exception as e:
                self.logger.warning(f"同步奖励 {treasure_id} 失败: {e}")
                traceback.print_exc()

        self.logger.info(f"maimai 地图奖励数据同步完成：{synced_count} 个奖励")

    async def get_song_by_id(self, game_code: str, song_id: int) -> SongData | None:
        spec = self._spec(game_code)
        try:
            song = await spec.song_model.get_or_none(id=song_id)
            if song is None:
                return None
            aliases = await spec.alias_model.filter(song_id=song_id).order_by(
                "priority",
                "id",
            ).values_list("alias", flat=True)
            return self._song_from_row(game_code, song, [str(a) for a in aliases if a])
        except Exception as e:
            self.logger.error(f"[{game_code}] 从数据库加载乐曲 {song_id} 失败: {e}")
            traceback.print_exc()
            return None

    async def load_all_songs(self, game_code: str) -> dict[int, SongData]:
        spec = self._spec(game_code)
        self.logger.info(f"[{game_code}] 从数据库加载乐曲历史数据...")
        try:
            songs = await spec.song_model.all()
            if not songs:
                self.logger.warning(f"[{game_code}] 数据库中没有乐曲数据")
                return {}

            song_ids = [song.id for song in songs]
            aliases_by_song_id: dict[int, list[str]] = {sid: [] for sid in song_ids}
            alias_rows = (
                await spec.alias_model.filter(song_id__in=song_ids)
                .order_by("song_id", "priority", "id")
                .values("song_id", "alias")
            )
            for row in alias_rows:
                sid = row.get("song_id")
                alias = row.get("alias")
                if sid is not None and alias:
                    aliases_by_song_id.setdefault(sid, []).append(str(alias))

            return {
                song.id: self._song_from_row(game_code, song, aliases_by_song_id.get(song.id, []))
                for song in songs
            }
        except Exception as e:
            self.logger.error(f"[{game_code}] 从数据库加载乐曲数据失败: {e}")
            traceback.print_exc()
            return {}

    async def load_song_index(self, game_code: str) -> dict[int, str]:
        spec = self._spec(game_code)
        try:
            rows = await spec.song_model.all().values("id", "title")
            return {
                int(row["id"]): str(row.get("title") or "")
                for row in rows
                if row.get("id") is not None
            }
        except Exception as e:
            self.logger.error(f"[{game_code}] 从数据库加载乐曲索引失败: {e}")
            traceback.print_exc()
            return {}

    async def query_alias_exact(self, game_code: str, alias_lower: str) -> list[tuple[int, str]]:
        spec = self._spec(game_code)
        matched_aliases = await spec.alias_model.filter(alias__iexact=alias_lower).select_related("song")
        return [
            (alias_record.song.id, str(alias_record.alias))  # type: ignore[attr-defined]
            for alias_record in matched_aliases
        ]

    async def load_alias_records(self, game_code: str) -> list[tuple[int, str]]:
        spec = self._spec(game_code)
        all_aliases = await spec.alias_model.all().values("id", "song_id", "alias")
        records: list[tuple[int, str]] = []
        for alias_record in all_aliases:
            alias = alias_record.get("alias")
            if alias:
                records.append((alias_record["song_id"], str(alias)))
        return records

    async def get_song_aliases_for_song_id(self, game_code: str, song_id: int) -> list[str]:
        spec = self._spec(game_code)
        db_aliases = await spec.alias_model.filter(song_id=song_id).order_by(
            "priority",
            "id",
        ).values_list("alias", flat=True)

        aliases: list[str] = []
        seen: set[str] = set()
        for alias in db_aliases:
            if not alias:
                continue
            text = str(alias)
            key = text.lower()
            if spec.dedupe_aliases_for_display and key in seen:
                continue
            seen.add(key)
            aliases.append(text)
        return aliases

    async def get_song_with_difficulty(
        self,
        game_code: str,
        song_id: int,
        song_type: str = "standard",
        level_index: int = 3,
    ) -> dict | None:
        spec = self._spec(game_code)
        if not spec.supports_song_with_difficulty:
            return None

        song_data = await self.get_song_by_id(game_code, song_id)
        if not song_data:
            return None

        type_difficulties = (song_data.difficulties or {}).get(song_type, [])
        target_difficulty = (
            type_difficulties[level_index]
            if level_index < len(type_difficulties)
            else None
        )
        return {
            "song_data": song_data,
            "target_difficulty": target_difficulty,
        }


__all__ = ["SongStoreSpec", "TortoiseSongStore"]
