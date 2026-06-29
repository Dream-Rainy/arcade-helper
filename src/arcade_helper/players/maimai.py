"""Maimai player domain handles and raw data wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arcade_helper.core.result import DataResult
from arcade_helper.users import PlatformIdentity


@dataclass(frozen=True)
class MaimaiProfile:
    """Raw maimai player profile result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiBests:
    """Raw maimai rating bests result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiBest:
    """Raw single maimai best score result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiApBests:
    """Raw maimai AP/AP+ scores result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiSongBests:
    """Raw maimai song score list result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiRecents:
    """Raw maimai recent plays result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiScores:
    """Raw maimai full scores result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiHeatmap:
    """Raw maimai play heatmap result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiTrend:
    """Raw maimai rating trend result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiScoreHistory:
    """Raw maimai score history result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class MaimaiCollection:
    """Raw maimai player collection item result."""

    friend_code: str
    collection_type: str
    collection_id: int
    raw: Any
    provider: str = "lxns"


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
    "MaimaiHeatmap",
    "MaimaiPlayer",
    "MaimaiProfile",
    "MaimaiRecents",
    "MaimaiScoreHistory",
    "MaimaiScores",
    "MaimaiSongBests",
    "MaimaiTrend",
]
