"""CHUNITHM player domain handles and raw data wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from arcade_helper.core.result import DataResult
from arcade_helper.users import PlatformIdentity


@dataclass(frozen=True)
class ChunithmProfile:
    """Raw CHUNITHM player profile result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmBests:
    """Raw CHUNITHM rating bests result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmBest:
    """Raw single CHUNITHM best score result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmSongBests:
    """Raw CHUNITHM song score list result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmRecents:
    """Raw CHUNITHM recent plays result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmScores:
    """Raw CHUNITHM full scores result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmHeatmap:
    """Raw CHUNITHM play heatmap result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmTrend:
    """Raw CHUNITHM rating trend result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmScoreHistory:
    """Raw CHUNITHM score history result."""

    friend_code: str
    raw: Any
    provider: str = "lxns"


@dataclass(frozen=True)
class ChunithmCollection:
    """Raw CHUNITHM player collection item result."""

    friend_code: str
    collection_type: str
    collection_id: int
    raw: Any
    provider: str = "lxns"


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
    "ChunithmCollection",
    "ChunithmHeatmap",
    "ChunithmPlayer",
    "ChunithmProfile",
    "ChunithmRecents",
    "ChunithmScoreHistory",
    "ChunithmScores",
    "ChunithmSongBests",
    "ChunithmTrend",
]
