"""Maimai player domain handles and LXNS response models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from arcade_helper.core.result import DataResult
from arcade_helper.users import PlatformIdentity


class _MaimaiModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @property
    def raw(self) -> dict[str, Any]:
        """Backward-compatible raw-ish view of the parsed payload."""
        return self.model_dump(mode="python", exclude_none=True)


class MaimaiCollectionRequiredSong(_MaimaiModel):
    id: int | None = None
    title: str | None = None
    type: str | None = None
    completed: bool | None = None
    completed_difficulties: list[int] | None = None


class MaimaiCollectionRequired(_MaimaiModel):
    difficulties: list[int] | None = None
    rate: str | None = None
    fc: str | None = None
    fs: str | None = None
    songs: list[MaimaiCollectionRequiredSong] | None = None
    completed: bool | None = None


class MaimaiCollection(_MaimaiModel):
    """maimai Collection."""

    id: int | None = Field(None, validation_alias=AliasChoices("id", "collection_id"))
    name: str | None = None
    color: str | None = None
    description: str | None = None
    genre: str | None = None
    required: list[MaimaiCollectionRequired] | None = None
    collection_type: str = ""
    collection_id: int | None = None


class MaimaiProfile(_MaimaiModel):
    """maimai Player."""

    name: str | None = None
    rating: int | None = None
    friend_code: int | str | None = None
    course_rank: int | None = None
    class_rank: int | None = None
    star: int | None = None
    trophy: MaimaiCollection | None = None
    icon: MaimaiCollection | None = None
    name_plate: MaimaiCollection | None = None
    frame: MaimaiCollection | None = None
    upload_time: str | None = None


class MaimaiSimpleScore(_MaimaiModel):
    """maimai SimpleScore."""

    id: int | None = Field(None, validation_alias=AliasChoices("id", "song_id", "songId"))
    song_name: str | None = Field(None, validation_alias=AliasChoices("song_name", "songName", "title"))
    level: str | None = None
    level_index: int | None = Field(None, validation_alias=AliasChoices("level_index", "levelIndex"))
    fc: str | None = None
    fs: str | None = None
    rate: str | None = None
    type: str | None = None


class MaimaiScore(MaimaiSimpleScore):
    """maimai Score."""

    achievements: float | None = Field(None, validation_alias=AliasChoices("achievements", "achievement"))
    dx_score: int | None = Field(None, validation_alias=AliasChoices("dx_score", "dxScore"))
    dx_star: int | None = Field(None, validation_alias=AliasChoices("dx_star", "dxStar"))
    dx_rating: float | None = Field(None, validation_alias=AliasChoices("dx_rating", "dxRating"))
    play_time: str | None = Field(None, validation_alias=AliasChoices("play_time", "playTime", "playedAt"))
    upload_time: str | None = Field(None, validation_alias=AliasChoices("upload_time", "uploadTime"))
    last_played_time: str | None = Field(None, validation_alias=AliasChoices("last_played_time", "lastPlayedTime"))


class MaimaiBest(MaimaiScore):
    """Single maimai Score returned by the best endpoint."""


class MaimaiBests(_MaimaiModel):
    """maimai Best 50 payload."""

    standard_total: int | None = None
    dx_total: int | None = None
    standard: list[MaimaiScore] = Field(default_factory=list)
    dx: list[MaimaiScore] = Field(default_factory=list)
    standard_selections: list[MaimaiScore] = Field(default_factory=list)
    dx_selections: list[MaimaiScore] = Field(default_factory=list)


class MaimaiApBests(MaimaiBests):
    """maimai AP/AP+ best payload."""

    scores: list[MaimaiScore] = Field(default_factory=list)


class MaimaiSongBests(_MaimaiModel):
    """maimai scores for one song."""

    scores: list[MaimaiScore] = Field(default_factory=list)

    @property
    def raw(self) -> list[dict[str, Any]] | dict[str, Any]:
        if self.scores:
            return [score.raw for score in self.scores]
        return super().raw


class MaimaiRecents(MaimaiSongBests):
    """maimai Recent 50 payload."""


class MaimaiScores(_MaimaiModel):
    """maimai SimpleScore list."""

    scores: list[MaimaiSimpleScore] = Field(default_factory=list)

    @property
    def raw(self) -> list[dict[str, Any]] | dict[str, Any]:
        if self.scores:
            return [score.raw for score in self.scores]
        return super().raw


class MaimaiHeatmap(_MaimaiModel):
    """maimai play heatmap payload."""

    records: Any = None

    @property
    def raw(self) -> Any:
        return self.records if self.records is not None else super().raw


class MaimaiRatingTrend(_MaimaiModel):
    """maimai RatingTrend."""

    total: int | None = None
    standard: int | None = None
    dx: int | None = None
    date: str | None = None


class MaimaiTrend(_MaimaiModel):
    """maimai rating trend payload."""

    trend: list[MaimaiRatingTrend] = Field(default_factory=list)

    @property
    def raw(self) -> list[dict[str, Any]] | dict[str, Any]:
        if self.trend:
            return [item.raw for item in self.trend]
        return super().raw


class MaimaiScoreHistory(MaimaiSongBests):
    """maimai score history payload."""


@dataclass(frozen=True)
class MaimaiPlayer:
    """Lightweight maimai player handle for one command/service flow."""

    service: Any
    identity: PlatformIdentity
    account_key: str
    friend_code: str
    provider: str = "lxns"

    async def profile(self, *, headers: dict[str, str] | None = None) -> DataResult[MaimaiProfile]:
        """Read the player profile, preferring the OAuth user API when available."""
        return await self.service.maimai_profile(self, headers=headers)

    async def bests(self, *, headers: dict[str, str] | None = None) -> DataResult[MaimaiBests]:
        """Read rating bests from the provider bests endpoint."""
        return await self.service.maimai_bests(self, headers=headers)

    async def best(
        self,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        level_index: int | str | None = None,
        song_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiBest]:
        """Read one best score, using OAuth full scores for local filtering when available."""
        return await self.service.maimai_best(
            self,
            song_id=song_id,
            song_name=song_name,
            level_index=level_index,
            song_type=song_type,
            headers=headers,
        )

    async def ap_bests(self, *, headers: dict[str, str] | None = None) -> DataResult[MaimaiApBests]:
        """Read AP/AP+ scores, deriving from OAuth full scores when available."""
        return await self.service.maimai_ap_bests(self, headers=headers)

    async def song_bests(
        self,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        song_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiSongBests]:
        """Read scores for one song, deriving from OAuth full scores when available."""
        return await self.service.maimai_song_bests(
            self,
            song_id=song_id,
            song_name=song_name,
            song_type=song_type,
            headers=headers,
        )

    async def recents(self, *, headers: dict[str, str] | None = None) -> DataResult[MaimaiRecents]:
        """Read recent plays from the provider endpoint, with a stale OAuth fallback if needed."""
        return await self.service.maimai_recents(self, headers=headers)

    async def scores(self, *, headers: dict[str, str] | None = None) -> DataResult[MaimaiScores]:
        """Read full scores, preferring the OAuth user API when available."""
        return await self.service.maimai_scores(self, headers=headers)

    async def heatmap(self, *, headers: dict[str, str] | None = None) -> DataResult[MaimaiHeatmap]:
        """Read play heatmap data from the provider heatmap endpoint."""
        return await self.service.maimai_heatmap(self, headers=headers)

    async def trend(
        self,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiTrend]:
        """Read rating trend data from the provider trend endpoint."""
        return await self.service.maimai_trend(self, version=version, headers=headers)

    async def score_history(
        self,
        *,
        song_id: int | None = None,
        song_type: str | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiScoreHistory]:
        """Read score history for a song/chart from the provider history endpoint."""
        return await self.service.maimai_score_history(
            self,
            song_id=song_id,
            song_type=song_type,
            level_index=level_index,
            headers=headers,
        )

    async def collection(
        self,
        collection_type: str,
        collection_id: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiCollection]:
        """Read a player collection item such as a frame or plate."""
        return await self.service.maimai_collection(
            self,
            collection_type=collection_type,
            collection_id=collection_id,
            headers=headers,
        )


__all__ = [
    "MaimaiApBests",
    "MaimaiBest",
    "MaimaiBests",
    "MaimaiCollection",
    "MaimaiCollectionRequired",
    "MaimaiCollectionRequiredSong",
    "MaimaiHeatmap",
    "MaimaiPlayer",
    "MaimaiProfile",
    "MaimaiRatingTrend",
    "MaimaiRecents",
    "MaimaiScore",
    "MaimaiScoreHistory",
    "MaimaiScores",
    "MaimaiSimpleScore",
    "MaimaiSongBests",
    "MaimaiTrend",
]
