from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pytest

from arcade_helper.core.errors import AccountNotBoundError, DefaultAccountNotFoundError
from arcade_helper.integrations.lxns.accounts import StoredGameProfile
from arcade_helper.players import PlayerService
from arcade_helper.providers import ProviderApiResponse, ProviderOperation, ProviderRegistry
from arcade_helper.users import PlatformIdentity


@dataclass
class _Call:
    method: str
    endpoint: str
    kwargs: dict[str, Any]


class _Provider:
    name = "lxns"
    priority = 100

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[_Call] = []
        self.responses = responses or {}

    async def call_api(
        self,
        method: Literal["GET", "POST"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        self.calls.append(_Call(method=method, endpoint=endpoint, kwargs=kwargs))
        data = self.responses.get(endpoint, {"code": 200, "endpoint": endpoint})
        if isinstance(data, Exception):
            raise data
        return ProviderApiResponse(
            provider=self.name,
            method=method,
            url=f"https://lxns.example.test{endpoint}",
            data=data,
        )


class _Users:
    def __init__(self, profile: StoredGameProfile | None) -> None:
        self.profile = profile

    async def require_default_lxns_game_profile(self, identity: PlatformIdentity) -> StoredGameProfile:
        if self.profile is None:
            raise DefaultAccountNotFoundError(f"No default account for {identity.cache_key}")
        return self.profile


def _registry(provider: _Provider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        provider,
        [
            ProviderOperation(
                key="maimai.player.profile.by_friend_code",
                method="profile",
                game_code="maimai",
            ),
            ProviderOperation(key="maimai.player.bests", method="bests", game_code="maimai"),
            ProviderOperation(key="maimai.player.best", method="best", game_code="maimai"),
            ProviderOperation(key="maimai.player.ap_bests", method="ap_bests", game_code="maimai"),
            ProviderOperation(key="maimai.player.song_bests", method="song_bests", game_code="maimai"),
            ProviderOperation(key="maimai.player.recents", method="recents", game_code="maimai"),
            ProviderOperation(key="maimai.player.scores", method="scores", game_code="maimai"),
            ProviderOperation(key="maimai.player.heatmap", method="heatmap", game_code="maimai"),
            ProviderOperation(key="maimai.player.trend", method="trend", game_code="maimai"),
            ProviderOperation(key="maimai.player.score_history", method="score_history", game_code="maimai"),
            ProviderOperation(key="maimai.player.collection", method="collection", game_code="maimai"),
            ProviderOperation(
                key="chunithm.player.profile.by_friend_code",
                method="profile",
                game_code="chunithm",
            ),
            ProviderOperation(key="chunithm.player.bests", method="bests", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.best", method="best", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.song_bests", method="song_bests", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.recents", method="recents", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.scores", method="scores", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.heatmap", method="heatmap", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.trend", method="trend", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.score_history", method="score_history", game_code="chunithm"),
            ProviderOperation(key="chunithm.player.collection", method="collection", game_code="chunithm"),
        ],
    )
    return registry


@pytest.mark.asyncio
async def test_default_maimai_player_resolves_from_default_lxns_profile() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
    )

    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    assert player.account_key == "lxns:10001"
    assert player.friend_code == "10001"
    assert player.provider == "lxns"


@pytest.mark.asyncio
async def test_default_maimai_player_requires_friend_code() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(StoredGameProfile(account_key="lxns:10001", account_name="tester")),
    )

    with pytest.raises(AccountNotBoundError):
        await service.maimai.default_player(PlatformIdentity.qq("123456"))


@pytest.mark.asyncio
async def test_default_chunithm_player_resolves_from_default_lxns_profile() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                chunithm_friend_code="20002",
            )
        ),
    )

    player = await service.chunithm.default_player(PlatformIdentity.qq("123456"))

    assert player.account_key == "lxns:10001"
    assert player.friend_code == "20002"
    assert player.provider == "lxns"


@pytest.mark.asyncio
async def test_default_chunithm_player_requires_friend_code() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(StoredGameProfile(account_key="lxns:10001", account_name="tester")),
    )

    with pytest.raises(AccountNotBoundError):
        await service.chunithm.default_player(PlatformIdentity.qq("123456"))


@pytest.mark.asyncio
async def test_default_maimai_player_propagates_missing_default_account() -> None:
    provider = _Provider()
    service = PlayerService(providers=_registry(provider), users=_Users(None))

    with pytest.raises(DefaultAccountNotFoundError):
        await service.maimai.default_player(PlatformIdentity.qq("123456"))


@pytest.mark.asyncio
async def test_maimai_player_methods_call_provider_api_and_return_freshness() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    profile = await player.profile(headers={"X-Test": "1"})
    bests = await player.bests(headers={"X-Test": "1"})
    recents = await player.recents(headers={"X-Test": "1"})
    trend = await player.trend(headers={"X-Test": "1"})

    assert profile.data.raw["endpoint"] == "/api/v0/maimai/player/{friend_code}"
    assert bests.freshness.operation == "maimai.player.bests"
    assert recents.data.provider == "lxns"
    assert trend.freshness.stale is False
    assert [
        (call.method, call.endpoint, call.kwargs["path_params"], call.kwargs["headers"])
        for call in provider.calls
    ] == [
        ("GET", "/api/v0/maimai/player/{friend_code}", {"friend_code": "10001"}, {"X-Test": "1"}),
        ("GET", "/api/v0/maimai/player/{friend_code}/bests", {"friend_code": "10001"}, {"X-Test": "1"}),
        ("GET", "/api/v0/maimai/player/{friend_code}/recents", {"friend_code": "10001"}, {"X-Test": "1"}),
        ("GET", "/api/v0/maimai/player/{friend_code}/trend", {"friend_code": "10001"}, {"X-Test": "1"}),
    ]


@pytest.mark.asyncio
async def test_maimai_player_extra_methods_call_provider_api_with_params() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    best = await player.best(song_name="Test Song", level_index=4, song_type="dx", headers={"X-Test": "1"})
    ap_bests = await player.ap_bests(headers={"X-Test": "1"})
    song_bests = await player.song_bests(song_id=123, song_type="standard", headers={"X-Test": "1"})
    scores = await player.scores(headers={"X-Test": "1"})
    heatmap = await player.heatmap(headers={"X-Test": "1"})
    trend = await player.trend(version=25000, headers={"X-Test": "1"})
    history = await player.score_history(song_id=123, song_type="dx", level_index=3, headers={"X-Test": "1"})
    collection = await player.collection("frame", 456, headers={"X-Test": "1"})

    assert best.freshness.operation == "maimai.player.best"
    assert ap_bests.data.provider == "lxns"
    assert song_bests.data.raw["endpoint"] == "/api/v0/maimai/player/{friend_code}/bests"
    assert scores.freshness.stale is False
    assert heatmap.freshness.operation == "maimai.player.heatmap"
    assert trend.freshness.operation == "maimai.player.trend"
    assert history.data.raw["endpoint"] == "/api/v0/maimai/player/{friend_code}/score/history"
    assert collection.data.collection_type == "frame"
    assert collection.data.collection_id == 456
    assert [
        (
            call.method,
            call.endpoint,
            call.kwargs["path_params"],
            call.kwargs.get("params"),
            call.kwargs["headers"],
        )
        for call in provider.calls
    ] == [
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/best",
            {"friend_code": "10001"},
            {"song_name": "Test Song", "level_index": 4, "song_type": "dx"},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/bests/ap",
            {"friend_code": "10001"},
            None,
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/bests",
            {"friend_code": "10001"},
            {"song_id": 123, "song_type": "standard"},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/scores",
            {"friend_code": "10001"},
            None,
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/heatmap",
            {"friend_code": "10001"},
            None,
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/trend",
            {"friend_code": "10001"},
            {"version": 25000},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/score/history",
            {"friend_code": "10001"},
            {"song_id": 123, "song_type": "dx", "level_index": 3},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/maimai/player/{friend_code}/{collection_type}/{collection_id}",
            {"friend_code": "10001", "collection_type": "frame", "collection_id": 456},
            None,
            {"X-Test": "1"},
        ),
    ]


@pytest.mark.asyncio
async def test_maimai_player_prefers_oauth_user_api_when_token_exists() -> None:
    provider = _Provider()

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    bests = await player.song_bests(song_id=123, song_type="dx", headers={"X-Test": "1"})
    trend = await player.trend(version=25000, headers={"X-Test": "1"})
    collection = await player.collection("frame", 456, headers={"X-Test": "1"})

    assert bests.freshness.operation == "maimai.player.song_bests"
    assert trend.data.provider == "lxns"
    assert collection.data.collection_id == 456
    assert [
        (
            call.endpoint,
            call.kwargs["path_params"],
            call.kwargs.get("params"),
            call.kwargs["headers"],
            call.kwargs.get("allow_auth_override"),
        )
        for call in provider.calls
    ] == [
        (
            "/api/v0/user/maimai/player/scores",
            {},
            None,
            {"X-Test": "1", "Authorization": "Bearer access-token"},
            True,
        ),
        (
            "/api/v0/user/maimai/player/bests",
            {},
            {"song_id": 123, "song_type": "dx"},
            {"X-Test": "1", "Authorization": "Bearer access-token"},
            True,
        ),
        (
            "/api/v0/user/maimai/player/trend",
            {},
            {"version": 25000},
            {"X-Test": "1", "Authorization": "Bearer access-token"},
            True,
        ),
        (
            "/api/v0/user/maimai/player/{collection_type}/{collection_id}",
            {"collection_type": "frame", "collection_id": 456},
            None,
            {"X-Test": "1", "Authorization": "Bearer access-token"},
            True,
        ),
    ]


@pytest.mark.asyncio
async def test_maimai_best_prefers_oauth_scores_and_filters_locally() -> None:
    provider = _Provider(
        {
            "/api/v0/user/maimai/player/scores": {
                "data": [
                    {"song_id": 123, "title": "Test Song", "level_index": 4, "type": "dx", "achievements": 99.9},
                    {"song_id": 123, "title": "Test Song", "level_index": 4, "type": "dx", "achievements": 100.1},
                    {"song_id": 123, "title": "Test Song", "level_index": 4, "type": "standard", "achievements": 101.0},
                    {"song_id": 456, "title": "Other Song", "level_index": 4, "type": "dx", "achievements": 100.5},
                ]
            }
        }
    )

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    best = await player.best(song_id=123, level_index="4", song_type="dx", headers={"X-Test": "1"})

    assert best.data.raw == {
        "song_id": 123,
        "title": "Test Song",
        "level_index": 4,
        "type": "dx",
        "achievements": 100.1,
    }
    assert [
        (
            call.endpoint,
            call.kwargs["path_params"],
            call.kwargs.get("params"),
            call.kwargs["headers"],
            call.kwargs.get("allow_auth_override"),
        )
        for call in provider.calls
    ] == [
        (
            "/api/v0/user/maimai/player/scores",
            {},
            None,
            {"X-Test": "1", "Authorization": "Bearer access-token"},
            True,
        )
    ]


@pytest.mark.asyncio
async def test_maimai_song_bests_prefers_oauth_scores_and_filters_locally() -> None:
    provider = _Provider(
        {
            "/api/v0/user/maimai/player/scores": {
                "data": [
                    {"song_id": 123, "title": "Test Song", "type": "dx", "achievements": 99.9},
                    {"song_id": 123, "title": "Test Song", "type": "dx", "achievements": 100.2},
                    {"song_id": 123, "title": "Test Song", "type": "standard", "achievements": 101.0},
                    {"song_id": 456, "title": "Other Song", "type": "dx", "achievements": 100.5},
                ]
            }
        }
    )

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    song_bests = await player.song_bests(song_id=123, song_type="dx", headers={"X-Test": "1"})

    assert song_bests.data.raw == [
        {"song_id": 123, "title": "Test Song", "type": "dx", "achievements": 100.2},
        {"song_id": 123, "title": "Test Song", "type": "dx", "achievements": 99.9},
    ]
    assert [call.endpoint for call in provider.calls] == ["/api/v0/user/maimai/player/scores"]


@pytest.mark.asyncio
async def test_maimai_ap_bests_prefers_oauth_scores_and_filters_locally() -> None:
    provider = _Provider(
        {
            "/api/v0/user/maimai/player/scores": {
                "data": [
                    {"song_id": 1, "fc": "fc", "achievements": 101.0},
                    {"song_id": 2, "fc": "ap", "achievements": 100.5},
                    {"song_id": 3, "fc": "app", "achievements": 100.9},
                ]
            }
        }
    )

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    ap_bests = await player.ap_bests(headers={"X-Test": "1"})

    assert ap_bests.data.raw == [
        {"song_id": 3, "fc": "app", "achievements": 100.9},
        {"song_id": 2, "fc": "ap", "achievements": 100.5},
    ]
    assert [call.endpoint for call in provider.calls] == ["/api/v0/user/maimai/player/scores"]


@pytest.mark.asyncio
async def test_maimai_recents_uses_friend_code_api_even_with_oauth_token() -> None:
    provider = _Provider()

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    recents = await player.recents(headers={"X-Test": "1"})

    assert recents.freshness.stale is False
    assert [
        (call.endpoint, call.kwargs["path_params"], call.kwargs["headers"], call.kwargs.get("allow_auth_override"))
        for call in provider.calls
    ] == [
        (
            "/api/v0/maimai/player/{friend_code}/recents",
            {"friend_code": "10001"},
            {"X-Test": "1"},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_maimai_recents_falls_back_to_oauth_scores_with_warning() -> None:
    provider = _Provider(
        {
            "/api/v0/maimai/player/{friend_code}/recents": RuntimeError("developer api unavailable"),
            "/api/v0/user/maimai/player/scores": {
                "data": [
                    {"song_id": 1, "playedAt": "2026-01-01T10:00:00Z"},
                    {"song_id": 2, "playedAt": "2026-01-02T10:00:00Z"},
                ]
            },
        }
    )

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    with pytest.warns(RuntimeWarning, match="locally derived recents"):
        recents = await player.recents(headers={"X-Test": "1"})

    assert recents.freshness.stale is True
    assert recents.data.raw == [
        {"song_id": 2, "playedAt": "2026-01-02T10:00:00Z"},
        {"song_id": 1, "playedAt": "2026-01-01T10:00:00Z"},
    ]
    assert [call.endpoint for call in provider.calls] == [
        "/api/v0/maimai/player/{friend_code}/recents",
        "/api/v0/user/maimai/player/scores",
    ]


@pytest.mark.asyncio
async def test_maimai_player_falls_back_to_friend_code_api_when_oauth_token_unavailable() -> None:
    provider = _Provider()

    async def ensure_token(account_key: str) -> dict[str, Any]:
        raise RuntimeError(f"no token for {account_key}")

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                maimai_friend_code="10001",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.maimai.default_player(PlatformIdentity.qq("123456"))

    await player.scores(headers={"X-Test": "1"})

    assert [
        (call.endpoint, call.kwargs["path_params"], call.kwargs.get("allow_auth_override"))
        for call in provider.calls
    ] == [
        (
            "/api/v0/maimai/player/{friend_code}/scores",
            {"friend_code": "10001"},
            None,
        )
    ]


@pytest.mark.asyncio
async def test_chunithm_player_methods_call_provider_api_with_params() -> None:
    provider = _Provider()
    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                chunithm_friend_code="20002",
            )
        ),
    )
    player = await service.chunithm.default_player(PlatformIdentity.qq("123456"))

    profile = await player.profile(headers={"X-Test": "1"})
    best = await player.best(song_name="Test Song", level_index=4, headers={"X-Test": "1"})
    song_bests = await player.song_bests(song_id=123, headers={"X-Test": "1"})
    recents = await player.recents(headers={"X-Test": "1"})
    trend = await player.trend(version=25000, headers={"X-Test": "1"})
    history = await player.score_history(song_id=123, level_index=3, headers={"X-Test": "1"})
    collection = await player.collection("plate", 456, headers={"X-Test": "1"})

    assert profile.freshness.operation == "chunithm.player.profile.by_friend_code"
    assert best.freshness.operation == "chunithm.player.best"
    assert song_bests.data.raw["endpoint"] == "/api/v0/chunithm/player/{friend_code}/bests"
    assert recents.freshness.stale is False
    assert trend.freshness.stale is False
    assert history.data.raw["endpoint"] == "/api/v0/chunithm/player/{friend_code}/score/history"
    assert collection.data.collection_type == "plate"
    assert [
        (
            call.method,
            call.endpoint,
            call.kwargs["path_params"],
            call.kwargs.get("params"),
            call.kwargs["headers"],
        )
        for call in provider.calls
    ] == [
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}",
            {"friend_code": "20002"},
            None,
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}/best",
            {"friend_code": "20002"},
            {"song_name": "Test Song", "level_index": 4},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}/bests",
            {"friend_code": "20002"},
            {"song_id": 123},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}/recents",
            {"friend_code": "20002"},
            None,
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}/trend",
            {"friend_code": "20002"},
            {"version": 25000},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}/score/history",
            {"friend_code": "20002"},
            {"song_id": 123, "level_index": 3},
            {"X-Test": "1"},
        ),
        (
            "GET",
            "/api/v0/chunithm/player/{friend_code}/{collection_type}/{collection_id}",
            {"friend_code": "20002", "collection_type": "plate", "collection_id": 456},
            None,
            {"X-Test": "1"},
        ),
    ]


@pytest.mark.asyncio
async def test_chunithm_best_prefers_oauth_scores_and_filters_locally() -> None:
    provider = _Provider(
        {
            "/api/v0/user/chunithm/player/scores": {
                "data": {
                    "scores": [
                        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 3, "score": 1007000},
                        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 3, "score": 1009000},
                        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 4, "score": 1010000},
                    ]
                }
            }
        }
    )

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                chunithm_friend_code="20002",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.chunithm.default_player(PlatformIdentity.qq("123456"))

    best = await player.best(song_id=55, level_index=3, headers={"X-Test": "1"})

    assert best.data.raw == {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 3, "score": 1009000}
    assert [
        (
            call.endpoint,
            call.kwargs["path_params"],
            call.kwargs.get("params"),
            call.kwargs["headers"],
            call.kwargs.get("allow_auth_override"),
        )
        for call in provider.calls
    ] == [
        (
            "/api/v0/user/chunithm/player/scores",
            {},
            None,
            {"X-Test": "1", "Authorization": "Bearer access-token"},
            True,
        )
    ]


@pytest.mark.asyncio
async def test_chunithm_song_bests_prefers_oauth_scores_and_filters_locally() -> None:
    provider = _Provider(
        {
            "/api/v0/user/chunithm/player/scores": {
                "data": {
                    "scores": [
                        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 2, "score": 1007000},
                        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 3, "score": 1009000},
                        {"song": {"id": 66, "title": "Other Song"}, "levelIndex": 3, "score": 1010000},
                    ]
                }
            }
        }
    )

    async def ensure_token(account_key: str) -> dict[str, Any]:
        assert account_key == "lxns:10001"
        return {"access_token": "access-token"}

    service = PlayerService(
        providers=_registry(provider),
        users=_Users(
            StoredGameProfile(
                account_key="lxns:10001",
                account_name="tester",
                chunithm_friend_code="20002",
            )
        ),
        ensure_account_token=ensure_token,
    )
    player = await service.chunithm.default_player(PlatformIdentity.qq("123456"))

    song_bests = await player.song_bests(song_id=55, headers={"X-Test": "1"})

    assert song_bests.data.raw == [
        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 3, "score": 1009000},
        {"song": {"id": 55, "title": "Test Song"}, "levelIndex": 2, "score": 1007000},
    ]
    assert [call.endpoint for call in provider.calls] == ["/api/v0/user/chunithm/player/scores"]
