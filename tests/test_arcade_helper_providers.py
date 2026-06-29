from __future__ import annotations

from dataclasses import dataclass

import pytest

from arcade_helper import ArcadeHelperClient, NoProviderAvailableError
from arcade_helper.providers import ProviderOperation, ProviderRegistry, ProviderRoutePolicy


@dataclass
class _Provider:
    name: str
    priority: int = 0


def test_provider_registry_sorts_by_account_preference_then_priority() -> None:
    registry = ProviderRegistry()
    lxns = _Provider("lxns", priority=100)
    divingfish = _Provider("divingfish", priority=80)
    op = ProviderOperation(
        key="maimai.player.bests",
        method="get_bests",
        game_code="maimai",
    )

    registry.register(lxns, [op])
    registry.register(divingfish, [op])

    candidates = registry.candidates(
        "maimai.player.bests",
        game_code="maimai",
        account_preference="divingfish",
    )

    assert [entry.provider.name for entry in candidates] == ["divingfish", "lxns"]


def test_provider_registry_uses_route_policy_default_provider() -> None:
    registry = ProviderRegistry()
    lxns = _Provider("lxns", priority=100)
    divingfish = _Provider("divingfish", priority=80)
    op = ProviderOperation(
        key="maimai.player.profile.by_friend_code",
        method="player",
        game_code="maimai",
    )

    registry.register(lxns, [op])
    registry.register(divingfish, [op])
    registry.set_policy(
        ProviderRoutePolicy(
            operation="maimai.player.profile.by_friend_code",
            game_code="maimai",
            default_provider="divingfish",
        )
    )

    selected = registry.require("maimai.player.profile.by_friend_code", game_code="maimai")

    assert selected.provider.name == "divingfish"


def test_provider_registry_raises_when_no_provider_matches() -> None:
    registry = ProviderRegistry()

    with pytest.raises(NoProviderAvailableError):
        registry.require("maimai.player.bests", game_code="maimai")


def test_arcade_helper_client_registers_lxns_operations() -> None:
    async def get_json(*_args, **_kwargs):
        return {}

    client = ArcadeHelperClient(http_get_json=get_json)
    operations = {entry.operation.key for entry in client.providers.operations()}

    assert "maimai.player.bests" in operations
    assert "maimai.player.score_history" in operations
    assert "maimai.catalog.songs" in operations
    assert "maimai.catalog.collection_list" in operations
    assert "chunithm.catalog.songs" in operations
    assert "chunithm.player.recents" in operations
    assert "chunithm.user.scores" in operations
    assert "auth.oauth.refresh_token" in operations


@pytest.mark.asyncio
async def test_provider_registry_call_api_routes_to_named_provider() -> None:
    calls: list[tuple[str, dict]] = []

    async def get_json(url: str, **kwargs):
        calls.append((url, kwargs))
        return {"ok": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"Authorization": "token"},
    )
    await client.lifecycle.start()

    result = await client.providers.call_api(
        "lxns",
        "GET",
        "/api/v0/maimai/player/{friend_code}/bests",
        path_params={"friend_code": "12 34"},
        headers={"X-Debug": "1"},
        params={"limit": "50"},
    )

    assert result.provider == "lxns"
    assert result.method == "GET"
    assert result.url == "https://lxns.example.test/api/v0/maimai/player/12%2034/bests"
    assert result.data == {"ok": True}
    assert calls == [
        (
            "https://lxns.example.test/api/v0/maimai/player/12%2034/bests",
            {
                "headers": {"Authorization": "token", "X-Debug": "1"},
                "params": {"limit": "50"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_lxns_provider_call_api_posts_json_payload() -> None:
    calls: list[tuple[str, dict]] = []

    async def get_json(url: str, **kwargs):
        return {}

    async def post_json(url: str, **kwargs):
        calls.append((url, kwargs))
        return {"created": True}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        http_post_json=post_json,
        lxns_base_url="https://lxns.example.test",
    )
    await client.lifecycle.start()

    result = await client.lxns_provider.call_api(
        "POST",
        "api/v0/example",
        json={"hello": "world"},
    )

    assert result.data == {"created": True}
    assert calls == [
        (
            "https://lxns.example.test/api/v0/example",
            {"headers": {}, "json_data": {"hello": "world"}},
        )
    ]


@pytest.mark.asyncio
async def test_lxns_provider_call_api_rejects_absolute_url_and_auth_override() -> None:
    async def get_json(url: str, **kwargs):
        return {}

    client = ArcadeHelperClient(
        http_get_json=get_json,
        lxns_base_url="https://lxns.example.test",
        headers={"Authorization": "token"},
    )
    await client.lifecycle.start()

    with pytest.raises(ValueError, match="relative path"):
        await client.lxns_provider.call_api("GET", "https://evil.example.test/api")

    with pytest.raises(ValueError, match="Authorization"):
        await client.lxns_provider.call_api(
            "GET",
            "/api/v0/example",
            headers={"Authorization": "other-token"},
        )
