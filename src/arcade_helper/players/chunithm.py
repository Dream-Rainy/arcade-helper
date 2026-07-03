"""CHUNITHM player domain handles and LXNS response models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from arcade_helper.core.result import DataResult
from arcade_helper.users import PlatformIdentity


class _ChunithmModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @property
    def raw(self) -> dict[str, Any]:
        """Backward-compatible raw-ish view of the parsed payload."""
        return self.model_dump(mode="python", exclude_none=True)


class ChunithmClassEmblem(_ChunithmModel):
    base: int | None = None
    medal: int | None = None


class ChunithmCollectionRequiredSong(_ChunithmModel):
    id: int | None = None
    title: str | None = None
    type: str | None = None
    completed: bool | None = None
    completed_difficulties: list[int] | None = None


class ChunithmCollectionRequired(_ChunithmModel):
    difficulties: list[int] | None = None
    rank: str | None = None
    full_combo: str | None = None
    full_chain: str | None = None
    songs: list[ChunithmCollectionRequiredSong] | None = None
    completed: bool | None = None


class ChunithmCollection(_ChunithmModel):
    """CHUNITHM Collection."""

    id: int | None = Field(None, validation_alias=AliasChoices("id", "collection_id"))
    name: str | None = None
    color: str | None = None
    level: int | None = None
    required: list[ChunithmCollectionRequired] | None = None
    collection_type: str = ""
    collection_id: int | None = None


class ChunithmProfile(_ChunithmModel):
    """CHUNITHM Player."""

    name: str | None = None
    level: int | None = None
    rating: float | None = None
    rating_possession: str | None = None
    friend_code: int | str | None = None
    class_emblem: ChunithmClassEmblem | None = None
    reborn_count: int | None = None
    over_power: float | None = None
    over_power_progress: float | None = None
    currency: int | None = None
    total_currency: int | None = None
    total_play_count: int | None = None
    trophy: ChunithmCollection | None = None
    character: ChunithmCollection | None = None
    name_plate: ChunithmCollection | None = None
    map_icon: ChunithmCollection | None = None
    upload_time: str | None = None


class ChunithmSimpleScore(_ChunithmModel):
    """CHUNITHM SimpleScore."""

    id: int | None = Field(None, validation_alias=AliasChoices("id", "song_id", "songId"))
    song_name: str | None = Field(None, validation_alias=AliasChoices("song_name", "songName", "title"))
    level: str | None = None
    level_index: int | None = Field(None, validation_alias=AliasChoices("level_index", "levelIndex"))
    clear: str | None = None
    full_combo: str | None = None
    full_chain: str | None = None
    rank: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_song(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not isinstance(data.get("song"), dict):
            return data
        song = data["song"]
        flattened = dict(data)
        flattened.setdefault("id", song.get("id"))
        flattened.setdefault("song_name", song.get("song_name") or song.get("title"))
        return flattened


class ChunithmScore(ChunithmSimpleScore):
    """CHUNITHM Score."""

    score: int | None = None
    rating: float | None = None
    over_power: float | None = None
    play_time: str | None = Field(None, validation_alias=AliasChoices("play_time", "playTime", "playedAt"))
    upload_time: str | None = Field(None, validation_alias=AliasChoices("upload_time", "uploadTime"))
    last_played_time: str | None = Field(None, validation_alias=AliasChoices("last_played_time", "lastPlayedTime"))


class ChunithmBest(ChunithmScore):
    """Single CHUNITHM Score returned by the best endpoint."""


class ChunithmBests(_ChunithmModel):
    """CHUNITHM rating composition payload."""

    rating: float | None = None
    bests_rating: float | None = None
    selections_rating: float | None = None
    recents_rating: float | None = None
    new_bests_rating: float | None = None
    bests: list[ChunithmScore] = Field(default_factory=list)
    selections: list[ChunithmScore] = Field(default_factory=list)
    recents: list[ChunithmScore] = Field(default_factory=list)
    new_bests: list[ChunithmScore] = Field(default_factory=list)


class ChunithmSongBests(_ChunithmModel):
    """CHUNITHM scores for one song."""

    scores: list[ChunithmScore] = Field(default_factory=list)

    @property
    def raw(self) -> list[dict[str, Any]] | dict[str, Any]:
        if self.scores:
            return [score.raw for score in self.scores]
        return super().raw


class ChunithmRecents(ChunithmSongBests):
    """CHUNITHM Recent 50 payload."""


class ChunithmScores(_ChunithmModel):
    """CHUNITHM SimpleScore list."""

    scores: list[ChunithmSimpleScore] = Field(default_factory=list)

    @property
    def raw(self) -> list[dict[str, Any]] | dict[str, Any]:
        if self.scores:
            return [score.raw for score in self.scores]
        return super().raw


class ChunithmHeatmap(_ChunithmModel):
    """CHUNITHM play heatmap payload."""

    records: Any = None

    @property
    def raw(self) -> Any:
        return self.records if self.records is not None else super().raw


class ChunithmRatingTrend(_ChunithmModel):
    """CHUNITHM RatingTrend."""

    rating: float | None = None
    bests_rating: float | None = None
    selections_rating: float | None = None
    recents_rating: float | None = None
    new_bests_rating: float | None = None
    date: str | None = None


class ChunithmTrend(_ChunithmModel):
    """CHUNITHM rating trend payload."""

    trend: list[ChunithmRatingTrend] = Field(default_factory=list)

    @property
    def raw(self) -> list[dict[str, Any]] | dict[str, Any]:
        if self.trend:
            return [item.raw for item in self.trend]
        return super().raw


class ChunithmScoreHistory(ChunithmSongBests):
    """CHUNITHM score history payload."""


@dataclass(frozen=True)
class ChunithmPlayer:
    """Lightweight CHUNITHM player handle for one command/service flow."""

    service: Any
    identity: PlatformIdentity
    account_key: str
    friend_code: str
    provider: str = "lxns"

    async def profile(self, *, headers: dict[str, str] | None = None) -> DataResult[ChunithmProfile]:
        """Read the player profile, preferring the OAuth user API when available."""
        return await self.service.chunithm_profile(self, headers=headers)

    async def bests(self, *, headers: dict[str, str] | None = None) -> DataResult[ChunithmBests]:
        """Read rating bests from the provider bests endpoint."""
        return await self.service.chunithm_bests(self, headers=headers)

    async def best(
        self,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmBest]:
        """Read one best score, using OAuth full scores for local filtering when available."""
        return await self.service.chunithm_best(
            self,
            song_id=song_id,
            song_name=song_name,
            level_index=level_index,
            headers=headers,
        )

    async def song_bests(
        self,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmSongBests]:
        """Read scores for one song, deriving from OAuth full scores when available."""
        return await self.service.chunithm_song_bests(
            self,
            song_id=song_id,
            song_name=song_name,
            headers=headers,
        )

    async def recents(self, *, headers: dict[str, str] | None = None) -> DataResult[ChunithmRecents]:
        """Read recent plays from the provider endpoint, with a stale OAuth fallback if needed."""
        return await self.service.chunithm_recents(self, headers=headers)

    async def scores(self, *, headers: dict[str, str] | None = None) -> DataResult[ChunithmScores]:
        """Read full scores, preferring the OAuth user API when available."""
        return await self.service.chunithm_scores(self, headers=headers)

    async def heatmap(self, *, headers: dict[str, str] | None = None) -> DataResult[ChunithmHeatmap]:
        """Read play heatmap data from the provider heatmap endpoint."""
        return await self.service.chunithm_heatmap(self, headers=headers)

    async def trend(
        self,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmTrend]:
        """Read rating trend data from the provider trend endpoint."""
        return await self.service.chunithm_trend(self, version=version, headers=headers)

    async def score_history(
        self,
        *,
        song_id: int | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmScoreHistory]:
        """Read score history for a song/chart from the provider history endpoint."""
        return await self.service.chunithm_score_history(
            self,
            song_id=song_id,
            level_index=level_index,
            headers=headers,
        )

    async def collection(
        self,
        collection_type: str,
        collection_id: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmCollection]:
        """Read a player collection item such as a plate or title."""
        return await self.service.chunithm_collection(
            self,
            collection_type=collection_type,
            collection_id=collection_id,
            headers=headers,
        )


__all__ = [
    "ChunithmBest",
    "ChunithmBests",
    "ChunithmClassEmblem",
    "ChunithmCollection",
    "ChunithmCollectionRequired",
    "ChunithmCollectionRequiredSong",
    "ChunithmHeatmap",
    "ChunithmPlayer",
    "ChunithmProfile",
    "ChunithmRatingTrend",
    "ChunithmRecents",
    "ChunithmScore",
    "ChunithmScoreHistory",
    "ChunithmScores",
    "ChunithmSimpleScore",
    "ChunithmSongBests",
    "ChunithmTrend",
]
