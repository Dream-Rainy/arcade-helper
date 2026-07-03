"""Player services backed by provider ``call_api``."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, TypeVar

from arcade_helper.core.errors import AccountNotBoundError
from arcade_helper.core.result import DataFreshness, DataResult
from arcade_helper.providers import ProviderApiResponse
from arcade_helper.providers.registry import ProviderRegistry
from arcade_helper.users import PlatformIdentity
from arcade_helper.users.service import UserService

from .chunithm import (
    ChunithmBest,
    ChunithmBests,
    ChunithmCollection,
    ChunithmHeatmap,
    ChunithmPlayer,
    ChunithmProfile,
    ChunithmRatingTrend,
    ChunithmRecents,
    ChunithmScore,
    ChunithmScoreHistory,
    ChunithmScores,
    ChunithmSimpleScore,
    ChunithmSongBests,
    ChunithmTrend,
)
from .maimai import (
    MaimaiApBests,
    MaimaiBest,
    MaimaiBests,
    MaimaiCollection,
    MaimaiHeatmap,
    MaimaiPlayer,
    MaimaiProfile,
    MaimaiRatingTrend,
    MaimaiRecents,
    MaimaiScore,
    MaimaiScoreHistory,
    MaimaiScores,
    MaimaiSimpleScore,
    MaimaiSongBests,
    MaimaiTrend,
)

T = TypeVar("T")
TokenResolver = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class MaimaiPlayerDirectory:
    service: "PlayerService"

    async def default_player(self, identity: PlatformIdentity) -> MaimaiPlayer:
        """Resolve the default maimai player for a platform identity."""
        return await self.service.default_maimai_player(identity)


@dataclass(frozen=True)
class ChunithmPlayerDirectory:
    service: "PlayerService"

    async def default_player(self, identity: PlatformIdentity) -> ChunithmPlayer:
        """Resolve the default CHUNITHM player for a platform identity."""
        return await self.service.default_chunithm_player(identity)


class PlayerService:
    """Resolve player handles and read player data through providers."""

    def __init__(
        self,
        *,
        providers: ProviderRegistry,
        users: UserService,
        logger: Any = None,
        ensure_started=None,
        ensure_account_token: TokenResolver | None = None,
    ) -> None:
        self._providers = providers
        self._users = users
        self._logger = logger
        self._ensure_started = ensure_started or (lambda: None)
        self._ensure_account_token = ensure_account_token
        self.maimai = MaimaiPlayerDirectory(self)
        self.chunithm = ChunithmPlayerDirectory(self)

    async def default_maimai_player(self, identity: PlatformIdentity) -> MaimaiPlayer:
        self._ensure_started()
        profile = await self._users.require_default_lxns_game_profile(identity)
        if not profile.maimai_friend_code:
            raise AccountNotBoundError(f"No maimai friend code for {identity.cache_key}")
        return MaimaiPlayer(
            service=self,
            identity=identity,
            account_key=profile.account_key,
            friend_code=profile.maimai_friend_code,
            provider="lxns",
        )

    async def default_chunithm_player(self, identity: PlatformIdentity) -> ChunithmPlayer:
        self._ensure_started()
        profile = await self._users.require_default_lxns_game_profile(identity)
        if not profile.chunithm_friend_code:
            raise AccountNotBoundError(f"No chunithm friend code for {identity.cache_key}")
        return ChunithmPlayer(
            service=self,
            identity=identity,
            account_key=profile.account_key,
            friend_code=profile.chunithm_friend_code,
            provider="lxns",
        )

    async def maimai_profile(
        self,
        player: MaimaiPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiProfile]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.profile.by_friend_code",
            endpoint="/api/v0/maimai/player/{friend_code}",
            headers=headers,
        )
        return self._result(
            _parse_model(MaimaiProfile, response.data),
            operation="maimai.player.profile.by_friend_code",
            provider=response.provider,
        )

    async def maimai_bests(
        self,
        player: MaimaiPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiBests]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.bests",
            endpoint="/api/v0/maimai/player/{friend_code}/bests",
            headers=headers,
        )
        return self._result(
            _parse_model(MaimaiBests, response.data),
            operation="maimai.player.bests",
            provider=response.provider,
        )

    async def maimai_best(
        self,
        player: MaimaiPlayer,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        level_index: int | str | None = None,
        song_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiBest]:
        response = await self._call_oauth_scores_api(
            player,
            game_code="maimai",
            operation="maimai.player.best",
            headers=headers,
        )
        if response is not None:
            return self._result(
                _parse_model(
                    MaimaiBest,
                    _best_score_from_response(
                        response.data,
                        song_id=song_id,
                        song_name=song_name,
                        level_index=level_index,
                        song_type=song_type,
                        score_fields=("achievements", "achievement", "dx_score", "dxScore", "score", "rating", "ra"),
                    ),
                ),
                operation="maimai.player.best",
                provider=response.provider,
            )

        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.best",
            endpoint="/api/v0/maimai/player/{friend_code}/best",
            params={
                "song_id": song_id,
                "song_name": song_name,
                "level_index": level_index,
                "song_type": song_type,
            },
            headers=headers,
        )
        return self._result(
            _parse_model(MaimaiBest, response.data),
            operation="maimai.player.best",
            provider=response.provider,
        )

    async def chunithm_profile(
        self,
        player: ChunithmPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmProfile]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.profile.by_friend_code",
            endpoint="/api/v0/chunithm/player/{friend_code}",
            headers=headers,
        )
        return self._result(
            _parse_model(ChunithmProfile, response.data),
            operation="chunithm.player.profile.by_friend_code",
            provider=response.provider,
        )

    async def chunithm_bests(
        self,
        player: ChunithmPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmBests]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.bests",
            endpoint="/api/v0/chunithm/player/{friend_code}/bests",
            headers=headers,
        )
        return self._result(
            _parse_model(ChunithmBests, response.data),
            operation="chunithm.player.bests",
            provider=response.provider,
        )

    async def chunithm_best(
        self,
        player: ChunithmPlayer,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmBest]:
        response = await self._call_oauth_scores_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.best",
            headers=headers,
        )
        if response is not None:
            return self._result(
                _parse_model(
                    ChunithmBest,
                    _best_score_from_response(
                        response.data,
                        song_id=song_id,
                        song_name=song_name,
                        level_index=level_index,
                        song_type=None,
                        score_fields=("score", "rating", "ra"),
                    ),
                ),
                operation="chunithm.player.best",
                provider=response.provider,
            )

        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.best",
            endpoint="/api/v0/chunithm/player/{friend_code}/best",
            params={"song_id": song_id, "song_name": song_name, "level_index": level_index},
            headers=headers,
        )
        return self._result(
            _parse_model(ChunithmBest, response.data),
            operation="chunithm.player.best",
            provider=response.provider,
        )

    async def chunithm_song_bests(
        self,
        player: ChunithmPlayer,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmSongBests]:
        response = await self._call_oauth_scores_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.song_bests",
            headers=headers,
        )
        if response is not None:
            song_scores = _song_scores_from_response(
                response.data,
                song_id=song_id,
                song_name=song_name,
                song_type=None,
                score_fields=("score", "rating", "ra"),
            )
            if song_scores:
                return self._result(
                    _parse_list_model(ChunithmSongBests, ChunithmScore, song_scores, field="scores"),
                    operation="chunithm.player.song_bests",
                    provider=response.provider,
                )

        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.song_bests",
            endpoint="/api/v0/chunithm/player/{friend_code}/bests",
            params={"song_id": song_id, "song_name": song_name},
            headers=headers,
        )
        return self._result(
            _parse_list_model(ChunithmSongBests, ChunithmScore, response.data, field="scores"),
            operation="chunithm.player.song_bests",
            provider=response.provider,
        )

    async def chunithm_recents(
        self,
        player: ChunithmPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmRecents]:
        response = await self._call_recents_api(
            player,
            game_code="chunithm",
            endpoint="/api/v0/chunithm/player/{friend_code}/recents",
            headers=headers,
        )
        return self._result(
            _parse_list_model(ChunithmRecents, ChunithmScore, response.data, field="scores"),
            operation="chunithm.player.recents",
            provider=response.provider,
            stale=response.stale,
        )

    async def chunithm_scores(
        self,
        player: ChunithmPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmScores]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.scores",
            endpoint="/api/v0/chunithm/player/{friend_code}/scores",
            headers=headers,
        )
        return self._result(
            _parse_list_model(ChunithmScores, ChunithmSimpleScore, response.data, field="scores"),
            operation="chunithm.player.scores",
            provider=response.provider,
        )

    async def chunithm_heatmap(
        self,
        player: ChunithmPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmHeatmap]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.heatmap",
            endpoint="/api/v0/chunithm/player/{friend_code}/heatmap",
            headers=headers,
        )
        return self._result(
            _parse_records_model(ChunithmHeatmap, response.data),
            operation="chunithm.player.heatmap",
            provider=response.provider,
        )

    async def chunithm_trend(
        self,
        player: ChunithmPlayer,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmTrend]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.trend",
            endpoint="/api/v0/chunithm/player/{friend_code}/trend",
            params={"version": version},
            headers=headers,
        )
        return self._result(
            _parse_list_model(ChunithmTrend, ChunithmRatingTrend, response.data, field="trend"),
            operation="chunithm.player.trend",
            provider=response.provider,
        )

    async def chunithm_score_history(
        self,
        player: ChunithmPlayer,
        *,
        song_id: int | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmScoreHistory]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.score_history",
            endpoint="/api/v0/chunithm/player/{friend_code}/score/history",
            params={"song_id": song_id, "level_index": level_index},
            headers=headers,
        )
        return self._result(
            _parse_list_model(ChunithmScoreHistory, ChunithmScore, response.data, field="scores"),
            operation="chunithm.player.score_history",
            provider=response.provider,
        )

    async def chunithm_collection(
        self,
        player: ChunithmPlayer,
        *,
        collection_type: str,
        collection_id: int,
        headers: dict[str, str] | None = None,
    ) -> DataResult[ChunithmCollection]:
        response = await self._call_player_api(
            player,
            game_code="chunithm",
            operation="chunithm.player.collection",
            endpoint="/api/v0/chunithm/player/{friend_code}/{collection_type}/{collection_id}",
            path_params={"collection_type": collection_type, "collection_id": collection_id},
            headers=headers,
        )
        return self._result(
            _parse_model(
                ChunithmCollection,
                response.data,
                collection_type=collection_type,
                collection_id=collection_id,
            ),
            operation="chunithm.player.collection",
            provider=response.provider,
        )

    async def maimai_ap_bests(
        self,
        player: MaimaiPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiApBests]:
        response = await self._call_oauth_scores_api(
            player,
            game_code="maimai",
            operation="maimai.player.ap_bests",
            headers=headers,
        )
        if response is not None:
            ap_scores = _maimai_ap_scores_from_response(response.data)
            if ap_scores:
                return self._result(
                    _parse_list_model(MaimaiApBests, MaimaiScore, ap_scores, field="scores"),
                    operation="maimai.player.ap_bests",
                    provider=response.provider,
                )

        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.ap_bests",
            endpoint="/api/v0/maimai/player/{friend_code}/bests/ap",
            headers=headers,
        )
        return self._result(
            _parse_model(MaimaiApBests, response.data),
            operation="maimai.player.ap_bests",
            provider=response.provider,
        )

    async def maimai_song_bests(
        self,
        player: MaimaiPlayer,
        *,
        song_id: int | None = None,
        song_name: str | None = None,
        song_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiSongBests]:
        response = await self._call_oauth_scores_api(
            player,
            game_code="maimai",
            operation="maimai.player.song_bests",
            headers=headers,
        )
        if response is not None:
            song_scores = _song_scores_from_response(
                response.data,
                song_id=song_id,
                song_name=song_name,
                song_type=song_type,
                score_fields=("achievements", "achievement", "dx_score", "dxScore", "score", "rating", "ra"),
            )
            if song_scores:
                return self._result(
                    _parse_list_model(MaimaiSongBests, MaimaiScore, song_scores, field="scores"),
                    operation="maimai.player.song_bests",
                    provider=response.provider,
                )

        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.song_bests",
            endpoint="/api/v0/maimai/player/{friend_code}/bests",
            params={"song_id": song_id, "song_name": song_name, "song_type": song_type},
            headers=headers,
        )
        return self._result(
            _parse_list_model(MaimaiSongBests, MaimaiScore, response.data, field="scores"),
            operation="maimai.player.song_bests",
            provider=response.provider,
        )

    async def maimai_recents(
        self,
        player: MaimaiPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiRecents]:
        response = await self._call_recents_api(
            player,
            game_code="maimai",
            endpoint="/api/v0/maimai/player/{friend_code}/recents",
            headers=headers,
        )
        return self._result(
            _parse_list_model(MaimaiRecents, MaimaiScore, response.data, field="scores"),
            operation="maimai.player.recents",
            provider=response.provider,
            stale=response.stale,
        )

    async def maimai_scores(
        self,
        player: MaimaiPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiScores]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.scores",
            endpoint="/api/v0/maimai/player/{friend_code}/scores",
            headers=headers,
        )
        return self._result(
            _parse_list_model(MaimaiScores, MaimaiSimpleScore, response.data, field="scores"),
            operation="maimai.player.scores",
            provider=response.provider,
        )

    async def maimai_heatmap(
        self,
        player: MaimaiPlayer,
        *,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiHeatmap]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.heatmap",
            endpoint="/api/v0/maimai/player/{friend_code}/heatmap",
            headers=headers,
        )
        return self._result(
            _parse_records_model(MaimaiHeatmap, response.data),
            operation="maimai.player.heatmap",
            provider=response.provider,
        )

    async def maimai_trend(
        self,
        player: MaimaiPlayer,
        *,
        version: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiTrend]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.trend",
            endpoint="/api/v0/maimai/player/{friend_code}/trend",
            params={"version": version},
            headers=headers,
        )
        return self._result(
            _parse_list_model(MaimaiTrend, MaimaiRatingTrend, response.data, field="trend"),
            operation="maimai.player.trend",
            provider=response.provider,
        )

    async def maimai_score_history(
        self,
        player: MaimaiPlayer,
        *,
        song_id: int | None = None,
        song_type: str | None = None,
        level_index: int | str | None = None,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiScoreHistory]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.score_history",
            endpoint="/api/v0/maimai/player/{friend_code}/score/history",
            params={"song_id": song_id, "song_type": song_type, "level_index": level_index},
            headers=headers,
        )
        return self._result(
            _parse_list_model(MaimaiScoreHistory, MaimaiScore, response.data, field="scores"),
            operation="maimai.player.score_history",
            provider=response.provider,
        )

    async def maimai_collection(
        self,
        player: MaimaiPlayer,
        *,
        collection_type: str,
        collection_id: int,
        headers: dict[str, str] | None = None,
    ) -> DataResult[MaimaiCollection]:
        response = await self._call_maimai_player_api(
            player,
            operation="maimai.player.collection",
            endpoint="/api/v0/maimai/player/{friend_code}/{collection_type}/{collection_id}",
            path_params={"collection_type": collection_type, "collection_id": collection_id},
            headers=headers,
        )
        return self._result(
            _parse_model(
                MaimaiCollection,
                response.data,
                collection_type=collection_type,
                collection_id=collection_id,
            ),
            operation="maimai.player.collection",
            provider=response.provider,
        )

    async def _call_maimai_player_api(
        self,
        player: MaimaiPlayer,
        *,
        operation: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        path_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None,
    ) -> ProviderApiResponse:
        return await self._call_player_api(
            player,
            game_code="maimai",
            operation=operation,
            endpoint=endpoint,
            params=params,
            path_params=path_params,
            headers=headers,
        )

    async def _call_player_api(
        self,
        player: MaimaiPlayer | ChunithmPlayer,
        *,
        game_code: str,
        operation: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        path_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None,
    ) -> ProviderApiResponse:
        self._ensure_started()
        registered = self._providers.require(
            operation,
            game_code=game_code,
            account_preference=player.provider,
        )
        query = {key: value for key, value in (params or {}).items() if value is not None}
        oauth_endpoint = _oauth_player_endpoint(game_code, endpoint)
        if oauth_endpoint and self._ensure_account_token is not None:
            access_token = await self._account_access_token(player.account_key)
            if access_token:
                api_kwargs = {
                    "path_params": path_params or {},
                    "headers": {**(headers or {}), "Authorization": f"Bearer {access_token}"},
                    "allow_auth_override": True,
                }
                if query:
                    api_kwargs["params"] = query
                return await registered.provider.call_api("GET", oauth_endpoint, **api_kwargs)

        api_kwargs = {
            "path_params": {"friend_code": player.friend_code, **(path_params or {})},
            "headers": headers,
        }
        if query:
            api_kwargs["params"] = query
        return await registered.provider.call_api("GET", endpoint, **api_kwargs)

    async def _call_oauth_scores_api(
        self,
        player: MaimaiPlayer | ChunithmPlayer,
        *,
        game_code: str,
        operation: str,
        headers: dict[str, str] | None,
    ) -> ProviderApiResponse | None:
        self._ensure_started()
        if self._ensure_account_token is None:
            return None
        access_token = await self._account_access_token(player.account_key)
        if not access_token:
            return None
        registered = self._providers.require(
            operation,
            game_code=game_code,
            account_preference=player.provider,
        )
        return await registered.provider.call_api(
            "GET",
            f"/api/v0/user/{game_code}/player/scores",
            path_params={},
            headers={**(headers or {}), "Authorization": f"Bearer {access_token}"},
            allow_auth_override=True,
        )

    async def _call_recents_api(
        self,
        player: MaimaiPlayer | ChunithmPlayer,
        *,
        game_code: str,
        endpoint: str,
        headers: dict[str, str] | None,
    ) -> "_RecentsResponse":
        operation = f"{game_code}.player.recents"
        try:
            response = await self._call_player_api(
                player,
                game_code=game_code,
                operation=operation,
                endpoint=endpoint,
                headers=headers,
            )
            return _RecentsResponse(data=response.data, provider=response.provider, stale=False)
        except Exception as exc:
            oauth_response = await self._call_oauth_scores_api(
                player,
                game_code=game_code,
                operation=operation,
                headers=headers,
            )
            if oauth_response is None:
                raise
            recents = _recents_from_response(oauth_response.data)
            if recents is None:
                raise exc
            warnings.warn(
                f"{operation} fell back to locally derived recents from full scores; result may be incomplete.",
                RuntimeWarning,
                stacklevel=2,
            )
            return _RecentsResponse(data=recents, provider=oauth_response.provider, stale=True)

    async def _account_access_token(self, account_key: str) -> str:
        if self._ensure_account_token is None:
            return ""
        try:
            token_data = await self._ensure_account_token(account_key)
        except Exception:
            return ""
        return str(token_data.get("access_token", ""))

    def _result(self, data: T, *, operation: str, provider: str, stale: bool = False) -> DataResult[T]:
        return DataResult(
            data=data,
            freshness=DataFreshness(
                provider=provider,
                operation=operation,
                stale=stale,
                fetched_at=datetime.now(UTC),
            ),
        )


def _oauth_player_endpoint(game_code: str, endpoint: str) -> str | None:
    prefix = f"/api/v0/{game_code}/player/{{friend_code}}"
    if not endpoint.startswith(prefix):
        return None
    suffix = endpoint[len(prefix) :]
    if suffix in {"", "/bests", "/scores", "/heatmap", "/trend", "/score/history"}:
        return f"/api/v0/user/{game_code}/player{suffix}"
    if suffix == "/{collection_type}/{collection_id}":
        return f"/api/v0/user/{game_code}/player/{'{collection_type}'}/{'{collection_id}'}"
    return None


def _response_payload(raw: Any) -> Any:
    if isinstance(raw, dict) and "data" in raw and (len(raw) == 1 or "success" in raw or "code" in raw):
        return raw["data"]
    return raw


def _parse_model(model: Any, raw: Any, **updates: Any):
    payload = _response_payload(raw)
    parsed = model.model_validate({} if payload is None else payload)
    return parsed.model_copy(update=updates) if updates else parsed


def _parse_list_model(model: Any, item_model: Any, raw: Any, *, field: str):
    payload = _response_payload(raw)
    items = _payload_list(payload, field)
    if items is None:
        return model.model_validate({} if payload is None else payload)
    return model.model_validate({field: [item_model.model_validate(item) for item in items]})


def _parse_records_model(model: Any, raw: Any):
    payload = _response_payload(raw)
    if isinstance(payload, dict):
        return model.model_validate(payload)
    return model.model_validate({"records": payload})


def _payload_list(payload: Any, field: str) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        items = payload.get(field)
        if isinstance(items, list):
            return items
        scores = payload.get("scores")
        if isinstance(scores, list):
            return scores
    return None


@dataclass(frozen=True)
class _RecentsResponse:
    data: Any
    provider: str
    stale: bool


def _best_score_from_response(
    raw: Any,
    *,
    song_id: int | None,
    song_name: str | None,
    level_index: int | str | None,
    song_type: str | None,
    score_fields: tuple[str, ...],
) -> Any:
    scores = [
        score
        for score in _iter_score_dicts(raw)
        if _score_matches(
            score,
            song_id=song_id,
            song_name=song_name,
            level_index=level_index,
            song_type=song_type,
        )
    ]
    return max(scores, key=lambda score: _score_value(score, score_fields), default=None)


def _maimai_ap_scores_from_response(raw: Any) -> list[dict[str, Any]]:
    scores = [score for score in _iter_score_dicts(raw) if _is_maimai_ap_score(score)]
    return sorted(
        scores,
        key=lambda score: _score_value(score, ("achievements", "achievement", "dx_score", "dxScore", "score")),
        reverse=True,
    )


def _song_scores_from_response(
    raw: Any,
    *,
    song_id: int | None,
    song_name: str | None,
    song_type: str | None,
    score_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    scores = [
        score
        for score in _iter_score_dicts(raw)
        if _score_matches(
            score,
            song_id=song_id,
            song_name=song_name,
            level_index=None,
            song_type=song_type,
        )
    ]
    return sorted(scores, key=lambda score: _score_value(score, score_fields), reverse=True)


def _is_maimai_ap_score(score: dict[str, Any]) -> bool:
    values = _score_values(score, ("fc", "full_combo", "fullCombo", "combo", "combo_status", "comboStatus"))
    return any(value.lower().replace("_", "").replace(" ", "") in {"ap", "app", "allperfect"} for value in values)


def _recents_from_response(raw: Any) -> list[dict[str, Any]] | None:
    recent_scores = [
        (played_at, score)
        for score in _iter_score_dicts(raw)
        if (played_at := _score_played_at(score)) is not None
    ]
    if not recent_scores:
        return None
    recent_scores.sort(key=lambda item: item[0], reverse=True)
    return [score for _, score in recent_scores]


def _score_played_at(score: dict[str, Any]) -> datetime | None:
    for value in _score_values(
        score,
        (
            "played_at",
            "playedAt",
            "play_time",
            "playTime",
            "timestamp",
            "created_at",
            "createdAt",
            "updated_at",
            "updatedAt",
        ),
    ):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: str) -> datetime | None:
    try:
        timestamp = float(value)
    except ValueError:
        timestamp = None
    if timestamp is not None:
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iter_score_dicts(raw: Any):
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(raw, dict):
        return
    for value in raw.values():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item
        elif isinstance(value, dict):
            yield from _iter_score_dicts(value)


def _score_matches(
    score: dict[str, Any],
    *,
    song_id: int | None,
    song_name: str | None,
    level_index: int | str | None,
    song_type: str | None,
) -> bool:
    if song_id is not None and str(song_id) not in _score_values(score, ("id", "song_id", "songId", "music_id")):
        return False
    if song_name is not None and song_name not in _score_values(score, ("title", "song_name", "songName", "name")):
        return False
    if level_index is not None and str(level_index) not in _score_values(score, ("level_index", "levelIndex")):
        return False
    if song_type is not None and song_type not in _score_values(score, ("type", "song_type", "songType")):
        return False
    return True


def _score_values(score: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    values = {str(score[key]) for key in keys if key in score and score[key] is not None}
    for nested_key in ("song", "music"):
        nested = score.get(nested_key)
        if isinstance(nested, dict):
            values.update(str(nested[key]) for key in keys if key in nested and nested[key] is not None)
    return values


def _score_value(score: dict[str, Any], fields: tuple[str, ...]) -> float:
    for field in fields:
        value = score.get(field)
        if value is None:
            continue
        try:
            return float(str(value).rstrip("%"))
        except (TypeError, ValueError):
            continue
    return 0.0


__all__ = ["ChunithmPlayerDirectory", "MaimaiPlayerDirectory", "PlayerService"]
