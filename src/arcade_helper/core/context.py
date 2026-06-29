"""Context objects for external data fetching.

The data package does not keep global bot state. Callers pass a context to
catalog/client entry points so HTTP, config, and logging stay injectable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

HttpGetJson = Callable[..., Awaitable[Any]]
HttpPostJson = Callable[..., Awaitable[Any]]
LifecycleGuard = Callable[[], None]


@dataclass
class CatalogContext:
    http_get_json: HttpGetJson
    http_post_json: HttpPostJson | None = None
    ensure_started: LifecycleGuard | None = None
    ingame_data_base_dir: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    base_url: str = ""
    logger: Any = field(default_factory=lambda: logging.getLogger(__name__))

    def _ensure_started(self) -> None:
        if self.ensure_started is not None:
            self.ensure_started()

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        self._ensure_started()
        return await self.http_get_json(url, **kwargs)

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        self._ensure_started()
        if self.http_post_json is None:
            raise RuntimeError("http_post_json is not configured")
        return await self.http_post_json(url, **kwargs)

    def ingame_path(self, game: str, sub_dir: str) -> str:
        return f"{self.ingame_data_base_dir}/{game}/{sub_dir}"

__all__ = ["CatalogContext", "HttpGetJson", "HttpPostJson", "LifecycleGuard"]
