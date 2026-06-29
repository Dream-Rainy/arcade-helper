from __future__ import annotations

from typing import Any

import pytest

from arcade_helper.integrations.lxns.oauth_client import OauthClient


def _oauth_client(response: dict[str, Any]) -> OauthClient:
    async def post_json(url: str, **kwargs: Any) -> dict[str, Any]:
        return response

    return OauthClient(
        http_post_json=post_json,
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://bot.example.test/callback",
    )


@pytest.mark.asyncio
async def test_oauth_client_derives_redirect_uri_from_relay_origin() -> None:
    captured: dict[str, Any] = {}

    async def post_json(url: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs["json_data"])
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 900,
        }

    client = OauthClient(
        http_post_json=post_json,
        client_id="client-id",
        client_secret="client-secret",
        relay_url="https://relay.example.test/api/events?token=secret",
    )

    await client.get_token("auth-code")

    assert captured["redirect_uri"] == "https://relay.example.test/callback"


@pytest.mark.asyncio
async def test_oauth_client_keeps_explicit_redirect_uri_with_relay_url() -> None:
    captured: dict[str, Any] = {}

    async def post_json(url: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs["json_data"])
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 900,
        }

    client = OauthClient(
        http_post_json=post_json,
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://bot.example.test/oauth/callback",
        relay_url="https://relay.example.test/api/events",
    )

    await client.get_token("auth-code")

    assert captured["redirect_uri"] == "https://bot.example.test/oauth/callback"


@pytest.mark.asyncio
async def test_oauth_client_reads_top_level_token_response() -> None:
    client = _oauth_client(
        {
            "access_token": "top-access",
            "refresh_token": "top-refresh",
            "token_type": "Bearer",
            "expires_in": 900,
            "scope": "read_player",
        }
    )

    result = await client.get_token("auth-code")

    assert result["access_token"] == "top-access"
    assert result["refresh_token"] == "top-refresh"
    assert result["expires_in"] == 900


@pytest.mark.asyncio
async def test_oauth_client_keeps_legacy_data_token_response_fallback() -> None:
    client = _oauth_client(
        {
            "data": {
                "access_token": "legacy-access",
                "refresh_token": "legacy-refresh",
                "expires_in": 900,
            }
        }
    )

    result = await client.get_token("auth-code")

    assert result["access_token"] == "legacy-access"
    assert result["refresh_token"] == "legacy-refresh"


@pytest.mark.asyncio
async def test_oauth_client_prefers_top_level_token_response() -> None:
    client = _oauth_client(
        {
            "access_token": "top-access",
            "refresh_token": "top-refresh",
            "expires_in": 900,
            "data": {
                "access_token": "legacy-access",
                "refresh_token": "legacy-refresh",
                "expires_in": 1,
            },
        }
    )

    result = await client.get_token("auth-code")

    assert result["access_token"] == "top-access"
    assert result["refresh_token"] == "top-refresh"
    assert result["expires_in"] == 900
