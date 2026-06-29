"""LXNS provider operation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, urljoin

from arcade_helper.providers.base import ProviderApiResponse
from arcade_helper.providers.operations import ProviderOperation

from .client import LxnsClient


@dataclass
class LxnsProvider:
    """Provider descriptor for LXNS-backed operations."""

    client: LxnsClient
    name: str = "lxns"
    priority: int = 100

    async def call_api(
        self,
        method: Literal["GET", "POST"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        """Call an LXNS provider-relative HTTP endpoint."""
        endpoint = _format_endpoint(
            endpoint,
            path_params=kwargs.pop("path_params", None),
        )
        url = _build_provider_url(self.client.context.base_url, endpoint)
        headers = _merge_headers(
            base_headers=self.client.context.headers,
            extra_headers=kwargs.pop("headers", None),
            allow_auth_override=bool(kwargs.pop("allow_auth_override", False)),
        )
        json_data = kwargs.pop("json_data", None)
        if json_data is None and "json" in kwargs:
            json_data = kwargs.pop("json")

        method = method.upper()
        if method == "GET":
            data = await self.client.context.get_json(
                url,
                headers=headers,
                **kwargs,
            )
        elif method == "POST":
            data = await self.client.context.post_json(
                url,
                headers=headers,
                json_data=json_data,
                **kwargs,
            )
        else:
            raise ValueError(f"Unsupported LXNS API method: {method}")

        return ProviderApiResponse(provider=self.name, method=method, url=url, data=data)

    @property
    def operations(self) -> tuple[ProviderOperation, ...]:
        """Provider operation metadata exposed to ``ProviderRegistry``."""
        return (
            ProviderOperation(
                key="maimai.catalog.songs",
                method="maimai.catalog",
                game_code="maimai",
                data_kind="song_catalog",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.catalog.aliases",
                method="maimai.aliases",
                game_code="maimai",
                data_kind="song_aliases",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.catalog.song",
                method="maimai.song",
                game_code="maimai",
                data_kind="song",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.catalog.collections",
                method="maimai.song_collections",
                game_code="maimai",
                data_kind="song_collections",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.catalog.collection_list",
                method="maimai.collection_list",
                game_code="maimai",
                data_kind="collections",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.catalog.collection",
                method="maimai.collection",
                game_code="maimai",
                data_kind="collection",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.catalog.maps",
                method="maimai.catalog",
                game_code="maimai",
                data_kind="map_catalog",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="maimai.player.profile.by_friend_code",
                method="maimai.player",
                game_code="maimai",
                data_kind="player_profile",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.profile.by_qq",
                method="maimai.player_by_qq",
                game_code="maimai",
                data_kind="player_profile",
            ),
            ProviderOperation(
                key="maimai.player.bests",
                method="maimai.bests",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.best",
                method="maimai.best",
                game_code="maimai",
                data_kind="player_score",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.ap_bests",
                method="maimai.ap_bests",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.song_bests",
                method="maimai.song_bests",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.recents",
                method="maimai.recents",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.scores",
                method="maimai.scores",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.heatmap",
                method="maimai.heatmap",
                game_code="maimai",
                data_kind="player_history",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.trend",
                method="maimai.trend",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.score_history",
                method="maimai.score_history",
                game_code="maimai",
                data_kind="player_history",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.player.collection",
                method="maimai.player_collection",
                game_code="maimai",
                data_kind="player_collection",
                requires_account=True,
            ),
            ProviderOperation(
                key="maimai.user.scores",
                method="maimai.user_scores",
                game_code="maimai",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.catalog.songs",
                method="chunithm.catalog",
                game_code="chunithm",
                data_kind="song_catalog",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="chunithm.catalog.aliases",
                method="chunithm.aliases",
                game_code="chunithm",
                data_kind="song_aliases",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="chunithm.catalog.song",
                method="chunithm.song",
                game_code="chunithm",
                data_kind="song",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="chunithm.catalog.collection_list",
                method="chunithm.collection_list",
                game_code="chunithm",
                data_kind="collections",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="chunithm.catalog.collection",
                method="chunithm.collection",
                game_code="chunithm",
                data_kind="collection",
                stale_allowed=True,
            ),
            ProviderOperation(
                key="chunithm.player.profile.by_friend_code",
                method="chunithm.player",
                game_code="chunithm",
                data_kind="player_profile",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.profile.by_qq",
                method="chunithm.player_by_qq",
                game_code="chunithm",
                data_kind="player_profile",
            ),
            ProviderOperation(
                key="chunithm.player.best",
                method="chunithm.best",
                game_code="chunithm",
                data_kind="player_score",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.bests",
                method="chunithm.bests",
                game_code="chunithm",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.song_bests",
                method="chunithm.song_bests",
                game_code="chunithm",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.recents",
                method="chunithm.recents",
                game_code="chunithm",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.scores",
                method="chunithm.scores",
                game_code="chunithm",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.heatmap",
                method="chunithm.heatmap",
                game_code="chunithm",
                data_kind="player_history",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.trend",
                method="chunithm.trend",
                game_code="chunithm",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.score_history",
                method="chunithm.score_history",
                game_code="chunithm",
                data_kind="player_history",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.player.collection",
                method="chunithm.player_collection",
                game_code="chunithm",
                data_kind="player_collection",
                requires_account=True,
            ),
            ProviderOperation(
                key="chunithm.user.scores",
                method="chunithm.user_scores",
                game_code="chunithm",
                data_kind="player_scores",
                requires_account=True,
            ),
            ProviderOperation(
                key="auth.oauth.build_url",
                method="oauth.get_bind_uri",
                data_kind="oauth",
                access="read",
                fallback_allowed=False,
            ),
            ProviderOperation(
                key="auth.oauth.exchange_code",
                method="oauth.exchange_code",
                data_kind="oauth",
                access="write",
                fallback_allowed=False,
            ),
            ProviderOperation(
                key="auth.oauth.refresh_token",
                method="oauth.refresh_token",
                data_kind="oauth",
                access="write",
                fallback_allowed=False,
            ),
            ProviderOperation(
                key="auth.sse.register_state",
                method="sse.register_for_oauth",
                data_kind="oauth_relay",
                fallback_allowed=False,
            ),
        )


def _build_provider_url(base_url: str, endpoint: str) -> str:
    if "://" in endpoint or endpoint.startswith("//"):
        raise ValueError("provider call_api endpoint must be a relative path")

    base = (base_url or "https://maimai.lxns.net").rstrip("/") + "/"
    relative = endpoint.lstrip("/")
    return urljoin(base, relative)


def _format_endpoint(endpoint: str, *, path_params: dict[str, Any] | None) -> str:
    if not path_params:
        return endpoint
    quoted = {key: quote(str(value), safe="") for key, value in path_params.items()}
    return endpoint.format_map(quoted)


def _merge_headers(
    *,
    base_headers: dict[str, str],
    extra_headers: dict[str, str] | None,
    allow_auth_override: bool,
) -> dict[str, str]:
    headers = dict(base_headers)
    if not extra_headers:
        return headers

    base_auth_keys = {key.lower() for key in headers}
    for key, value in extra_headers.items():
        if (
            key.lower() == "authorization"
            and "authorization" in base_auth_keys
            and not allow_auth_override
            and headers.get(key) != value
        ):
            raise ValueError("Authorization header override is not allowed")
        headers[key] = value
    return headers


__all__ = ["LxnsProvider"]
