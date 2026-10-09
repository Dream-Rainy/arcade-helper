"""Prepare source records without assigning ephemeral song IDs to identities."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from arcade_helper.core.song import SongData
from arcade_helper.core.song_identity import normalize_width, parse_chart_title, song_identity_material
from arcade_helper.aliases.models import alias_key
from arcade_helper.games.arcade_songs import normalize_arcade_song


def content_key(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CatalogRecord:
    song: SongData
    raw: dict
    native_key: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "song", self.song.model_copy(update={
            "title": normalize_width(self.song.title),
            "aliases": [normalize_width(text) for text in self.song.aliases],
            "alias_observations": [observation.model_copy(update={"text": normalize_width(observation.text)})
                                   for observation in self.song.alias_observations],
        }))

    @property
    def identity(self) -> dict:
        variant = (
            self.song.chart_variant if "chart_variant" in self.song.model_fields_set
            else parse_chart_title(self.song.title)[1]
        )
        return song_identity_material(
            self.song.title, self.song.artist, variant,
            getattr(self.song, "music_variant", None),
        )

    @property
    def external_key(self) -> str:
        return self.native_key if self.native_key is not None else content_key(self.identity)

    @property
    def key_kind(self) -> str:
        return "native" if self.native_key is not None else "derived"


@dataclass
class CatalogImportReport:
    game_code: str
    revision: int
    dry_run: bool = False
    sync_version: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    withdrawn: int = 0
    pending: list[dict] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    missing_deferred: bool = False
    song_ids: dict[str, int] = field(default_factory=dict)


def prepare_native_catalog(game_code: str, songs: dict[int, SongData]) -> tuple[list[CatalogRecord], list[str]]:
    """Share native backfill/sync validation without altering source snapshots."""
    threshold = {"maimai": 10_000_000, "chunithm": 100_000}[game_code]
    records, invalid = [], []
    for song_id, song in songs.items():
        if not 0 < song_id < threshold:
            continue
        record = CatalogRecord(song, song.model_dump(mode="json"), str(song_id))
        try:
            alias_key(record.song.title)
        except ValueError as exc:
            invalid.append(f"{game_code}:{song_id}: title {song.title!r}: {exc}")
            continue
        aliases, observations = [], []
        for field, values, target in (("alias", record.song.aliases, aliases),
                                      ("observation", record.song.alias_observations, observations)):
            for index, value in enumerate(values, 1):
                text = value if field == "alias" else value.text
                try:
                    alias_key(text)
                except ValueError as exc:
                    invalid.append(f"{game_code}:{song_id}: {field}[{index}] {text!r}: {exc}")
                    continue
                target.append(value)
        records.append(CatalogRecord(record.song.model_copy(update={"aliases": aliases,
                                     "alias_observations": observations}), record.raw, record.native_key))
    return records, invalid


def prepare_arcade_catalog(payload: dict) -> tuple[list[CatalogRecord], list[str]]:
    """Preserve malformed row diagnostics so a partial fetch cannot delete data.

    No generic arcade-songs field is assumed to be a stable native ID.
    Adapters with a verified native key can construct CatalogRecord directly.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("songs"), list):
        raise ValueError("Catalog payload must contain a songs list")
    records: list[CatalogRecord] = []
    invalid: list[str] = []
    for index, raw in enumerate(payload["songs"]):
        try:
            if not isinstance(raw, dict) or not isinstance(raw.get("title"), str) or not raw["title"]:
                raise ValueError("missing title")
            sheets = raw.get("sheets", [])
            if not isinstance(sheets, list) or any(not isinstance(sheet, dict) for sheet in sheets):
                raise ValueError("invalid sheets list")
            song = normalize_arcade_song(raw, song_id=0)
            alias_key(song.title)
            records.append(CatalogRecord(song=song, raw=raw))
        except (TypeError, ValueError) as exc:
            invalid.append(f"songs[{index}]: {exc}")
    return records, invalid
