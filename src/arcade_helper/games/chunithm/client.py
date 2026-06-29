"""High-level CHUNITHM data client."""

from __future__ import annotations

from typing import Any

from arcade_helper.core.context import CatalogContext
from arcade_helper.integrations.lxns.constants import (
    chunithm_alias_list_url,
    chunithm_collection_list_url,
    chunithm_collection_url,
    chunithm_player_best_url,
    chunithm_player_bests_url,
    chunithm_player_collection_url,
    chunithm_player_heatmap_url,
    chunithm_player_by_qq_url,
    chunithm_player_recents_url,
    chunithm_player_score_history_url,
    chunithm_player_scores_url,
    chunithm_player_song_bests_url,
    chunithm_player_trend_url,
    chunithm_player_url,
    chunithm_song_url,
    chunithm_song_list_url,
)
from arcade_helper.integrations.lxns.user_api import LxnsUserApi

from .data_fetcher import fetch_chunithm_catalog
from .schemas import ChuniSongData


class ChunithmClient:
    """Convenience API for CHUNITHM external data."""

    def __init__(self, context: CatalogContext):
        self.context = context
        self.user_api = LxnsUserApi(context)

    def _headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        return dict(self.context.headers if headers is None else headers)

    async def songs(self, *, notes: bool = True, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch the CHUNITHM song list from LXNS."""
        return await self.context.get_json(
            chunithm_song_list_url(notes=notes, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def aliases(self, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch CHUNITHM song aliases from LXNS."""
        return await self.context.get_json(
            chunithm_alias_list_url(base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def song(
        self,
        song_id: int,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one CHUNITHM song by id."""
        return await self.context.get_json(
            chunithm_song_url(song_id, version=version, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def collection_list(
        self,
        collection_type: str,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch CHUNITHM collections by collection type."""
        return await self.context.get_json(
            chunithm_collection_list_url(collection_type, version=version, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def collection(
        self,
        collection_type: str,
        collection_id: int,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one CHUNITHM collection item."""
        return await self.context.get_json(
            chunithm_collection_url(
                collection_type,
                collection_id,
                version=version,
                base_url=self.context.base_url,
            ),
            headers=self._headers(headers),
        )

    async def player(self, friend_code: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch a CHUNITHM player profile by friend code."""
        return await self.context.get_json(
            chunithm_player_url(friend_code, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def player_by_qq(self, qq: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch a CHUNITHM player profile by linked QQ number."""
        return await self.context.get_json(
            chunithm_player_by_qq_url(qq, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def best(
        self,
        friend_code: str,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one CHUNITHM best score by friend code and optional chart filters."""
        return await self.context.get_json(
            chunithm_player_best_url(
                friend_code,
                song_id=song_id,
                song_name=song_name,
                level_index=level_index,
                base_url=self.context.base_url,
            ),
            headers=self._headers(headers),
        )

    async def bests(self, friend_code: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch CHUNITHM rating bests by friend code."""
        return await self.context.get_json(
            chunithm_player_bests_url(friend_code, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def song_bests(
        self,
        friend_code: str,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch CHUNITHM scores for one song by friend code."""
        return await self.context.get_json(
            chunithm_player_song_bests_url(
                friend_code,
                song_id=song_id,
                song_name=song_name,
                base_url=self.context.base_url,
            ),
            headers=self._headers(headers),
        )

    async def recents(self, friend_code: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch recent CHUNITHM plays by friend code."""
        return await self.context.get_json(
            chunithm_player_recents_url(friend_code, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def scores(self, friend_code: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch full CHUNITHM scores by friend code."""
        return await self.context.get_json(
            chunithm_player_scores_url(friend_code, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def heatmap(self, friend_code: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch CHUNITHM play heatmap data by friend code."""
        return await self.context.get_json(
            chunithm_player_heatmap_url(friend_code, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def trend(
        self,
        friend_code: str,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch CHUNITHM rating trend data by friend code."""
        return await self.context.get_json(
            chunithm_player_trend_url(friend_code, version=version, base_url=self.context.base_url),
            headers=self._headers(headers),
        )

    async def score_history(
        self,
        friend_code: str,
        *,
        song_id: int | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch CHUNITHM score history for a chart by friend code."""
        return await self.context.get_json(
            chunithm_player_score_history_url(
                friend_code,
                song_id=song_id,
                level_index=level_index,
                base_url=self.context.base_url,
            ),
            headers=self._headers(headers),
        )

    async def player_collection(
        self,
        friend_code: str,
        collection_type: str,
        collection_id: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one CHUNITHM player collection item by friend code."""
        return await self.context.get_json(
            chunithm_player_collection_url(
                friend_code,
                collection_type,
                collection_id,
                base_url=self.context.base_url,
            ),
            headers=self._headers(headers),
        )

    async def user_player(self, access_token: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Alias for ``oauth_player``."""
        return await self.oauth_player(access_token, headers=headers)

    async def user_scores(self, access_token: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Alias for ``oauth_scores``."""
        return await self.oauth_scores(access_token, headers=headers)

    async def oauth_player(self, access_token: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch the OAuth user's CHUNITHM player profile."""
        return (
            await self.user_api.oauth_call(access_token, "GET", "/api/v0/user/chunithm/player", headers=headers)
        ).data

    async def oauth_scores(self, access_token: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch the OAuth user's full CHUNITHM scores."""
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/scores",
                headers=headers,
            )
        ).data

    async def oauth_bests(
        self,
        access_token: str,
        *,
        song_id: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch the OAuth user's CHUNITHM rating bests."""
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/bests",
                headers=headers,
                params={"song_id": song_id} if song_id is not None else None,
            )
        ).data

    async def oauth_heatmap(self, access_token: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """Fetch the OAuth user's CHUNITHM heatmap."""
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/heatmap",
                headers=headers,
            )
        ).data

    async def oauth_trend(
        self,
        access_token: str,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch the OAuth user's CHUNITHM rating trend."""
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/trend",
                headers=headers,
                params={"version": version} if version is not None else None,
            )
        ).data

    async def oauth_score_history(
        self,
        access_token: str,
        *,
        song_id: int | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch the OAuth user's CHUNITHM score history."""
        params = {"song_id": song_id, "level_index": level_index}
        query = {key: value for key, value in params.items() if value is not None}
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/score/history",
                headers=headers,
                params=query or None,
            )
        ).data

    async def oauth_collections(
        self,
        access_token: str,
        collection_type: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch the OAuth user's CHUNITHM collections by type."""
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/{collection_type}",
                path_params={"collection_type": collection_type},
                headers=headers,
            )
        ).data

    async def oauth_collection(
        self,
        access_token: str,
        collection_type: str,
        collection_id: int,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch one OAuth user CHUNITHM collection item."""
        return (
            await self.user_api.oauth_call(
                access_token,
                "GET",
                "/api/v0/user/chunithm/player/{collection_type}/{collection_id}",
                path_params={"collection_type": collection_type, "collection_id": collection_id},
                headers=headers,
            )
        ).data

    async def catalog(self) -> dict[int, ChuniSongData]:
        """Fetch and merge the CHUNITHM catalog through the catalog fetcher."""
        return await fetch_chunithm_catalog(self.context)


__all__ = ["ChunithmClient"]
