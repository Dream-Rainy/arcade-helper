from __future__ import annotations

import asyncio

import pytest

from arcade_helper.integrations.lxns.sse import SseRelayClient


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


class _IdleRelayClient(SseRelayClient):
    async def _connect_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1)


@pytest.mark.asyncio
async def test_sse_relay_client_resolves_registered_state() -> None:
    client = SseRelayClient()

    future = client.register("state-1")
    client._event_received({"state": "state-1", "code": "oauth-code"})

    assert await future == "oauth-code"


@pytest.mark.asyncio
async def test_sse_relay_client_unregister_removes_pending_state() -> None:
    client = SseRelayClient()

    future = client.register("state-2")
    client.unregister("state-2")
    client._event_received({"state": "state-2", "code": "oauth-code"})

    assert not future.done()


@pytest.mark.asyncio
async def test_sse_relay_client_ignores_heartbeat_comments() -> None:
    client = SseRelayClient()
    future = client.register("state-3")

    client._process_sse_frame(": connected")
    client._process_sse_frame(": heartbeat")

    assert not future.done()


@pytest.mark.asyncio
async def test_sse_relay_client_parses_event_frame() -> None:
    client = SseRelayClient()
    future = client.register("state-4")

    client._process_sse_frame(
        'event: oauth\n'
        'data: {"state": "state-4", "code": "oauth-code"}'
    )

    assert await future == "oauth-code"


@pytest.mark.asyncio
async def test_sse_relay_client_parses_split_chunks() -> None:
    client = SseRelayClient()
    future = client.register("state-5")

    buffer = client._feed_sse_chunk("", b"event: oauth\ndata: {\"state\": \"sta")
    assert not future.done()

    buffer = client._feed_sse_chunk(buffer, b"te-5\", \"code\": \"oauth-code\"}\n\n")

    assert buffer == ""
    assert await future == "oauth-code"


def test_sse_relay_client_reports_invalid_json() -> None:
    logger = _Logger()
    client = SseRelayClient(logger=logger)

    client._process_sse_frame("event: oauth\ndata: not-json")

    assert logger.warnings
    assert "JSONDecodeError" in logger.warnings[0]


def test_sse_relay_client_connection_state_is_explicit() -> None:
    client = SseRelayClient()

    assert client.is_connected is False
    client._running = True
    assert client.is_connected is False
    client._connected = True
    assert client.is_connected is True


@pytest.mark.asyncio
async def test_sse_relay_client_reuses_connection_until_oauth_idle() -> None:
    client = _IdleRelayClient(
        relay_url="https://relay.example",
        relay_token="token",
        idle_stop_seconds=0,
    )

    first = await client.register_for_oauth("state-6")
    task = client._task
    second = await client.register_for_oauth("state-7")

    assert task is not None
    assert client._task is task
    assert client._running is True

    client.unregister("state-6")
    await asyncio.sleep(0)
    assert client._running is True
    assert not first.done()

    client._event_received({"state": "state-7", "code": "oauth-code"})
    assert await second == "oauth-code"
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert client._running is False
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()
