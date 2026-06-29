"""Generic arcade-songs catalog provider.

This module normalizes zetaraku/arcade-songs catalogs for games without a
dedicated client. Player data and game-specific APIs belong to dedicated
clients such as ``client.maimai`` and ``client.chunithm``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import Any, Literal
from urllib.parse import urljoin

from pydantic import Field

from arcade_helper.core.context import CatalogContext
from arcade_helper.core.song import SongData, SongSheet
from arcade_helper.providers.base import ProviderApiResponse
from arcade_helper.providers.operations import ProviderOperation

ARCADE_SONGS_RAW_BASE_URL = "https://raw.githubusercontent.com/zetaraku/arcade-songs/master/data/"
ARCADE_SONGS_SITES_URL = f"{ARCADE_SONGS_RAW_BASE_URL}sites.json"
ARCADE_SONGS_CACHE_TTL_SECONDS = 86_400


class ArcadeSongSheet(SongSheet):
    """Generic arcade-songs chart model for non-specialized game catalogs."""


class ArcadeSongData(SongData[ArcadeSongSheet]):
    """Generic arcade-songs song model for games without a dedicated schema."""

    difficulties: dict[str, list[ArcadeSongSheet]] = Field(default_factory=dict)


async def fetch_arcade_songs_sites(
    context: CatalogContext,
    *,
    cache_dir: Path | None = None,
    cache_ttl_seconds: int = ARCADE_SONGS_CACHE_TTL_SECONDS,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch arcade-songs site metadata."""
    cache_path = _cache_path(cache_dir, "sites")
    if not force_refresh:
        cached = _read_cache(cache_path, cache_ttl_seconds)
        if isinstance(cached, list):
            return cached

    payload = await context.get_json(ARCADE_SONGS_SITES_URL, force_refresh=True)
    if isinstance(payload, str):
        payload = json.loads(payload)
    sites = payload if isinstance(payload, list) else []
    _write_cache(cache_path, sites)
    return sites


async def fetch_arcade_songs_payload(
    context: CatalogContext,
    game_code: str,
    *,
    cache_dir: Path | None = None,
    cache_ttl_seconds: int = ARCADE_SONGS_CACHE_TTL_SECONDS,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch one arcade-songs ``data.json`` payload by game code."""
    cache_path = _cache_path(cache_dir, game_code)
    if not force_refresh:
        cached = _read_cache(cache_path, cache_ttl_seconds)
        if isinstance(cached, dict):
            return cached

    sites = await fetch_arcade_songs_sites(
        context,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )
    for site in sites:
        if isinstance(site, dict) and site.get("gameCode") == game_code:
            base_url = str(site.get("dataSourceUrl") or "").rstrip("/")
            if base_url:
                payload = await context.get_json(f"{base_url}/data.json", force_refresh=True)
                if isinstance(payload, str):
                    payload = json.loads(payload)
                data = payload if isinstance(payload, dict) else {}
                _write_cache(cache_path, data)
                return data
    raise ValueError(f"arcade-songs sites.json 中未找到 {game_code} 的 dataSourceUrl")


async def fetch_arcade_songs_catalog(
    context: CatalogContext,
    game_code: str,
    *,
    cache_dir: Path | None = None,
    cache_ttl_seconds: int = ARCADE_SONGS_CACHE_TTL_SECONDS,
    force_refresh: bool = False,
) -> dict[int, ArcadeSongData]:
    """Fetch and normalize one arcade-songs catalog."""
    payload = await fetch_arcade_songs_payload(
        context,
        game_code,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        force_refresh=force_refresh,
    )
    return normalize_arcade_songs_catalog(payload)


def normalize_arcade_songs_catalog(payload: dict[str, Any]) -> dict[int, ArcadeSongData]:
    """Normalize arcade-songs ``songs`` into common ``SongData`` objects."""
    songs: dict[int, ArcadeSongData] = {}
    for index, raw_song in enumerate(payload.get("songs") or [], start=1):
        if not isinstance(raw_song, dict):
            continue
        title = str(raw_song.get("title") or "").strip()
        if not title:
            continue
        song = ArcadeSongData.model_validate(
            {
                "id": index,
                "title": title,
                "artist": raw_song.get("artist") or "",
                "bpm": raw_song.get("bpm") or 0,
                "image_name": raw_song.get("imageName") or "",
                "difficulties": _group_sheets(raw_song.get("sheets") or []),
                "aliases": [title],
                "category": raw_song.get("category"),
                "comment": raw_song.get("comment") or "",
                "is_locked": bool(raw_song.get("isLocked", False)),
                "is_new": bool(raw_song.get("isNew", False)),
                "release_date": raw_song.get("releaseDate") or "",
                "version": raw_song.get("version"),
            }
        )
        songs[song.id] = song
    return songs


@dataclass
class ArcadeSongsProvider:
    """Provider for generic arcade-songs catalogs behind ``client.catalog``."""

    context: CatalogContext
    cache_dir: Path | None = None
    cache_ttl_seconds: int = ARCADE_SONGS_CACHE_TTL_SECONDS
    name: str = "arcade_songs"
    priority: int = 10

    async def catalog(self, game_code: str, *, force_refresh: bool = False) -> dict[int, ArcadeSongData]:
        """Fetch one generic arcade-songs catalog."""
        return await fetch_arcade_songs_catalog(
            self.context,
            game_code,
            cache_dir=self.cache_dir,
            cache_ttl_seconds=self.cache_ttl_seconds,
            force_refresh=force_refresh,
        )

    async def sites(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Fetch arcade-songs site metadata."""
        return await fetch_arcade_songs_sites(
            self.context,
            cache_dir=self.cache_dir,
            cache_ttl_seconds=self.cache_ttl_seconds,
            force_refresh=force_refresh,
        )

    async def call_api(
        self,
        method: Literal["GET", "POST"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        """Call a raw arcade-songs data endpoint."""
        method = method.upper()
        if method != "GET":
            raise ValueError("arcade-songs provider only supports GET")
        if "://" in endpoint or endpoint.startswith("//"):
            raise ValueError("provider call_api endpoint must be a relative path")
        url = urljoin(ARCADE_SONGS_RAW_BASE_URL, endpoint.lstrip("/"))
        data = await self.context.get_json(url, **kwargs)
        return ProviderApiResponse(provider=self.name, method=method, url=url, data=data)

    @property
    def operations(self) -> tuple[ProviderOperation, ...]:
        """Provider operation metadata exposed to ``ProviderRegistry``."""
        return (
            ProviderOperation(
                key="arcade_songs.catalog.sites",
                method="sites",
                data_kind="catalog_sites",
                stale_allowed=True,
                priority=-10,
            ),
            ProviderOperation(
                key="arcade_songs.catalog.songs",
                method="catalog",
                data_kind="song_catalog",
                stale_allowed=True,
                priority=-10,
            ),
        )


def _group_sheets(raw_sheets: list[Any]) -> dict[str, list[ArcadeSongSheet]]:
    grouped: dict[str, list[ArcadeSongSheet]] = {}
    for raw_sheet in raw_sheets:
        if not isinstance(raw_sheet, dict):
            continue
        sheet_type = str(raw_sheet.get("type") or "standard").strip().lower()
        if sheet_type == "std":
            sheet_type = "standard"
        sheet = ArcadeSongSheet.model_validate(
            {
                **raw_sheet,
                "type": sheet_type,
                "difficulty": str(raw_sheet.get("difficulty") or ""),
                "level": str(raw_sheet.get("level") or ""),
            }
        )
        grouped.setdefault(sheet_type, []).append(sheet)
    return grouped


def _cache_path(cache_dir: Path | None, cache_key: str) -> Path | None:
    if cache_dir is None:
        return None
    safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in cache_key)
    return cache_dir / f"{safe_key}.json"


def _read_cache(path: Path | None, ttl_seconds: int) -> Any | None:
    if path is None or not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time() - float(cached.get("cached_at", 0)) > ttl_seconds:
        return None
    return cached.get("payload")


def _write_cache(path: Path | None, payload: Any) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"cached_at": time(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


__all__ = [
    "ARCADE_SONGS_SITES_URL",
    "ARCADE_SONGS_CACHE_TTL_SECONDS",
    "ArcadeSongData",
    "ArcadeSongSheet",
    "ArcadeSongsProvider",
    "fetch_arcade_songs_catalog",
    "fetch_arcade_songs_payload",
    "fetch_arcade_songs_sites",
    "normalize_arcade_songs_catalog",
]
