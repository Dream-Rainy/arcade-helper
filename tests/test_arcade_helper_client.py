from __future__ import annotations

from typing import Any

import pytest

from arcade_helper import ArcadeHelperClient, ClientClosedError, ClientNotStartedError
from arcade_helper.storage import JsonAccountStore


@pytest.mark.asyncio
async def test_arcade_helper_client_wraps_maimai_player_api_without_nonebot() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"Authorization": "dev-token"},
    )
    await client.lifecycle.start()

    result = await client.maimai.bests("1234567890")

    assert result == {"ok": True}
    assert calls == [
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/bests",
            {"headers": {"Authorization": "dev-token"}},
        )
    ]


@pytest.mark.asyncio
async def test_arcade_helper_client_user_player_uses_oauth_bearer_header() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"X-Base": "1"},
    )
    await client.lifecycle.start()

    await client.chunithm.user_player("access-token")

    assert calls == [
        (
            "https://lxns.example.test/api/v0/user/chunithm/player",
            {"headers": {"X-Base": "1", "Authorization": "Bearer access-token"}},
        )
    ]


@pytest.mark.asyncio
async def test_arcade_helper_client_user_api_splits_oauth_and_personal_tokens() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"X-Base": "1"},
    )
    await client.lifecycle.start()

    oauth = await client.lxns.user.oauth_call(
        "access-token",
        "GET",
        "/api/v0/user/maimai/player/{collection_type}/{collection_id}",
        path_params={"collection_type": "frames", "collection_id": 321},
    )
    personal = await client.lxns.user.personal_call(
        "user-token",
        "GET",
        "/api/v0/user/chunithm/player/scores",
    )

    assert oauth.data == {"ok": True}
    assert personal.data == {"ok": True}
    assert calls == [
        (
            "https://lxns.example.test/api/v0/user/maimai/player/frames/321",
            {"headers": {"X-Base": "1", "Authorization": "Bearer access-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/user/chunithm/player/scores",
            {"headers": {"X-Base": "1", "X-User-Token": "user-token"}},
        ),
    ]


@pytest.mark.asyncio
async def test_arcade_helper_client_oauth_player_helpers_use_user_scoped_paths() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(http_get_json=get_json, lxns_base_url="https://lxns.example.test")
    await client.lifecycle.start()

    await client.maimai.oauth_bests("access-token", song_id=123, song_type="dx")
    await client.chunithm.oauth_trend("access-token", version=23000)

    assert calls == [
        (
            "https://lxns.example.test/api/v0/user/maimai/player/bests",
            {"headers": {"Authorization": "Bearer access-token"}, "params": {"song_id": 123, "song_type": "dx"}},
        ),
        (
            "https://lxns.example.test/api/v0/user/chunithm/player/trend",
            {"headers": {"Authorization": "Bearer access-token"}, "params": {"version": 23000}},
        ),
    ]


@pytest.mark.asyncio
async def test_arcade_helper_client_wraps_maimai_get_endpoints() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"Authorization": "dev-token"},
    )
    await client.lifecycle.start()

    await client.maimai.song(123, version=25000)
    await client.maimai.collection_list("trophy", version=25000, required=True)
    await client.maimai.collection("icon", 456, version=25000)
    await client.maimai.best("1234567890", song_name="Test Song", level_index=4, song_type="dx")
    await client.maimai.ap_bests("1234567890")
    await client.maimai.song_bests("1234567890", song_id=789, song_type="standard")
    await client.maimai.scores("1234567890")
    await client.maimai.heatmap("1234567890")
    await client.maimai.trend("1234567890", version=25000)
    await client.maimai.score_history("1234567890", song_id=789, song_type="dx", level_index=3)
    await client.maimai.player_collection("1234567890", "frame", 321)
    await client.maimai.user_scores("access-token")

    assert calls == [
        (
            "https://lxns.example.test/api/v0/maimai/song/123?version=25000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/trophy/list?version=25000&required=true",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/icon/456?version=25000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/best?"
            "song_name=Test+Song&level_index=4&song_type=dx",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/bests/ap",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/bests?song_id=789&song_type=standard",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/scores",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/heatmap",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/trend?version=25000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/score/history?"
            "song_id=789&song_type=dx&level_index=3",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/maimai/player/1234567890/frame/321",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/user/maimai/player/scores",
            {"headers": {"Authorization": "Bearer access-token"}},
        ),
    ]


@pytest.mark.asyncio
async def test_arcade_helper_client_wraps_chunithm_get_endpoints() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"Authorization": "dev-token"},
    )
    await client.lifecycle.start()

    await client.chunithm.player("1234567890")
    await client.chunithm.song(123, version=23000)
    await client.chunithm.collection_list("character", version=23000)
    await client.chunithm.collection("icon", 456, version=23000)
    await client.chunithm.best("1234567890", song_name="Test Song", level_index=4)
    await client.chunithm.bests("1234567890")
    await client.chunithm.song_bests("1234567890", song_id=789)
    await client.chunithm.recents("1234567890")
    await client.chunithm.scores("1234567890")
    await client.chunithm.heatmap("1234567890")
    await client.chunithm.trend("1234567890", version=23000)
    await client.chunithm.score_history("1234567890", song_id=789, level_index=3)
    await client.chunithm.player_collection("1234567890", "plate", 321)
    await client.chunithm.user_scores("access-token")

    assert calls == [
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/song/123?version=23000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/character/list?version=23000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/icon/456?version=23000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/best?"
            "song_name=Test+Song&level_index=4",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/bests",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/bests?song_id=789",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/recents",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/scores",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/heatmap",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/trend?version=23000",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/score/history?"
            "song_id=789&level_index=3",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/chunithm/player/1234567890/plate/321",
            {"headers": {"Authorization": "dev-token"}},
        ),
        (
            "https://lxns.example.test/api/v0/user/chunithm/player/scores",
            {"headers": {"Authorization": "Bearer access-token"}},
        ),
    ]


@pytest.mark.asyncio
async def test_arcade_helper_client_builds_oauth_client_from_post_json() -> None:
    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def post_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "data": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 900,
            }
        }

    client = ArcadeHelperClient(
        http_get_json=get_json,
        http_post_json=post_json,
        lxns_base_url="https://lxns.example.test",
        lxns_client_id="client-id",
        lxns_client_secret="client-secret",
        lxns_oauth_redirect_uri="https://bot.example.test/callback",
    )

    assert client.lxns.oauth is not None
    assert "https://lxns.example.test/oauth/authorize" in client.lxns.oauth.get_bind_uri("state")


@pytest.mark.asyncio
async def test_arcade_helper_client_oauth_http_calls_require_lifecycle_start() -> None:
    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def post_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "data": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 900,
            }
        }

    client = ArcadeHelperClient(
        http_get_json=get_json,
        http_post_json=post_json,
        lxns_oauth_redirect_uri="https://bot.example.test/callback",
    )

    assert client.lxns.oauth is not None
    with pytest.raises(ClientNotStartedError):
        await client.lxns.oauth.get_token("auth-code")


@pytest.mark.asyncio
async def test_arcade_helper_client_requires_lifecycle_start_for_http_calls() -> None:
    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    client = ArcadeHelperClient(http_get_json=get_json)

    with pytest.raises(ClientNotStartedError):
        await client.maimai.bests("1234567890")


@pytest.mark.asyncio
async def test_arcade_helper_client_requires_lifecycle_start_for_user_services() -> None:
    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    client = ArcadeHelperClient(http_get_json=get_json)

    with pytest.raises(ClientNotStartedError):
        await client.users.from_platform("qq", "123456")


@pytest.mark.asyncio
async def test_arcade_helper_client_rejects_calls_after_stop() -> None:
    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    client = ArcadeHelperClient(http_get_json=get_json)
    await client.lifecycle.start()
    await client.lifecycle.stop()

    with pytest.raises(ClientClosedError):
        await client.maimai.bests("1234567890")


@pytest.mark.asyncio
async def test_arcade_helper_client_uses_local_account_store_when_configured(tmp_path) -> None:
    async def get_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return {}

    client = ArcadeHelperClient(http_get_json=get_json, local_data_dir=tmp_path)
    await client.lifecycle.start()

    assert isinstance(client.accounts, JsonAccountStore)
    user = await client.users.from_platform("qq", "10001")

    assert user.user_id == 1
    assert (tmp_path / "accounts.json").exists()

