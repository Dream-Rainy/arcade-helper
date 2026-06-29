"""SSE relay client for LXNS OAuth callbacks."""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any, Protocol

import aiohttp

_SOCK_READ_TIMEOUT_SECONDS = 180
_CONNECT_TIMEOUT_SECONDS = 30
_INITIAL_BACKOFF_SECONDS = 1
_MAX_BACKOFF_SECONDS = 60
_IDLE_STOP_SECONDS = 60


class SseLogger(Protocol):
    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...


class NullSseLogger:
    """Logger used when no SSE logger is supplied."""

    def info(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass


def _parse_sse_frame(frame: str) -> tuple[str, str | None]:
    event_name = "message"
    data_lines: list[str] = []

    for raw_line in frame.splitlines():
        line = raw_line.rstrip("\n")
        if not line or line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]

        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)

    if not data_lines:
        return event_name, None
    return event_name, "\n".join(data_lines)


class SseRelayClient:
    """SSE 中继客户端：连接公网 Relay，监听 OAuth 回调事件。"""

    def __init__(
        self,
        *,
        relay_url: str = "",
        relay_token: str = "",
        idle_stop_seconds: int = _IDLE_STOP_SECONDS,
        logger: SseLogger | None = None,
    ) -> None:
        self.relay_url = relay_url
        self.relay_token = relay_token
        self.idle_stop_seconds = idle_stop_seconds
        self._logger = logger or NullSseLogger()
        self._pending: dict[str, asyncio.Future[str]] = {}
        self._task: asyncio.Task[None] | None = None
        self._idle_stop_task: asyncio.Task[None] | None = None
        self._running = False
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Whether the relay connection is currently open."""
        return self._connected

    def configure(self, *, relay_url: str, relay_token: str) -> None:
        """Update relay connection settings."""
        self.relay_url = relay_url
        self.relay_token = relay_token

    def register(self, state: str) -> asyncio.Future[str]:
        """注册一个 state 等待 Relay 推送授权码。返回 awaitable future。"""

        self._cancel_idle_stop()
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[state] = future
        return future

    async def register_for_oauth(self, state: str) -> asyncio.Future[str]:
        """Register a state and keep the relay connected only while OAuth is pending."""

        future = self.register(state)
        await self.start()
        return future

    def unregister(self, state: str) -> None:
        """Stop waiting for one OAuth state and schedule idle shutdown."""
        self._pending.pop(state, None)
        self._schedule_idle_stop()

    def _cancel_idle_stop(self) -> None:
        if self._idle_stop_task and not self._idle_stop_task.done():
            self._idle_stop_task.cancel()
        self._idle_stop_task = None

    def _schedule_idle_stop(self) -> None:
        if self._pending or not self._running:
            return
        if self._idle_stop_task and not self._idle_stop_task.done():
            return
        self._idle_stop_task = asyncio.create_task(self._stop_after_idle())

    async def _stop_after_idle(self) -> None:
        try:
            await asyncio.sleep(self.idle_stop_seconds)
            if not self._pending:
                await self.stop()
        except asyncio.CancelledError:
            pass

    def _event_received(self, data: dict[str, Any]) -> None:
        state = data.get("state")
        code = data.get("code")
        if state and code and state in self._pending:
            future = self._pending.pop(state)
            if not future.done():
                future.set_result(code)
            self._schedule_idle_stop()

    def _process_sse_frame(self, frame: str) -> None:
        event_name, payload = _parse_sse_frame(frame)
        if payload is None:
            return

        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            self._logger.warning(
                f"[sse_client] 无法解析 {event_name} 事件: {payload!r}; error={exc!r}"
            )
            return

        if not isinstance(data, dict):
            self._logger.warning(f"[sse_client] 忽略非对象 {event_name} 事件: {data!r}")
            return

        self._event_received(data)

    def _feed_sse_chunk(self, buffer: str, chunk: bytes) -> str:
        text = chunk.decode("utf-8", errors="replace")
        buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            self._process_sse_frame(frame)
        return buffer

    async def start(self) -> None:
        """Start the relay connection while there are pending OAuth states."""
        if not self._pending:
            self._logger.info("[sse_client] 没有等待中的 OAuth state，跳过启动")
            return

        if not self.relay_url or not self.relay_token:
            self._logger.warning("[sse_client] Relay URL 或 token 未配置，SSE 客户端不会启动")
            return

        self._cancel_idle_stop()
        if self._task and not self._task.done():
            self._logger.info("[sse_client] 已在运行，跳过重复启动")
            return

        self._running = True
        self._task = asyncio.create_task(self._connect_loop())
        self._logger.info(f"[sse_client] 已启动，连接 {self.relay_url}")

    async def stop(self) -> None:
        """Stop the relay connection and cancel pending state futures."""
        self._running = False
        self._connected = False
        current_task = asyncio.current_task()
        if (
            self._idle_stop_task
            and self._idle_stop_task is not current_task
            and not self._idle_stop_task.done()
        ):
            self._idle_stop_task.cancel()
        self._idle_stop_task = None
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for state, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
            del self._pending[state]
        self._logger.info("[sse_client] 已停止")

    async def _sleep_before_retry(self, backoff: int) -> None:
        jitter = random.uniform(0, min(backoff * 0.25, 3))
        await asyncio.sleep(backoff + jitter)

    async def _connect_loop(self) -> None:
        url = f"{self.relay_url.rstrip('/')}/events"
        backoff = _INITIAL_BACKOFF_SECONDS

        while self._running and self._pending:
            try:
                timeout = aiohttp.ClientTimeout(
                    total=None,
                    connect=_CONNECT_TIMEOUT_SECONDS,
                    sock_read=_SOCK_READ_TIMEOUT_SECONDS,
                )
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        url,
                        params={"token": self.relay_token},
                        headers={
                            "Accept": "text/event-stream",
                            "Cache-Control": "no-cache",
                        },
                    ) as resp:
                        if resp.status != 200:
                            self._connected = False
                            self._logger.warning(
                                f"[sse_client] Relay 返回 {resp.status}，{backoff}s 后重试"
                            )
                            await self._sleep_before_retry(backoff)
                            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                            continue

                        backoff = _INITIAL_BACKOFF_SECONDS
                        self._connected = True
                        self._logger.info("[sse_client] SSE 连接已建立")
                        buf = ""
                        async for line_bytes, _ in resp.content.iter_chunks():
                            if not self._running:
                                break
                            buf = self._feed_sse_chunk(buf, line_bytes)

                        self._connected = False
                        if self._running and self._pending:
                            self._logger.warning(f"[sse_client] SSE 流结束，{backoff}s 后重连")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._connected = False
                self._logger.warning(f"[sse_client] 连接断开，{backoff}s 后重连: {exc!r}")

            if self._running and self._pending:
                await self._sleep_before_retry(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)


__all__ = ["SseRelayClient"]

