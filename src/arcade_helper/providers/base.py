"""Base provider protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ProviderApiResponse:
    """Raw response returned by a provider-scoped HTTP API call."""

    provider: str
    method: str
    url: str
    data: Any


class Provider(Protocol):
    """Minimal provider shape used by the registry."""

    name: str
    priority: int

    async def call_api(
        self,
        method: Literal["GET", "POST"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        """Call a provider-relative HTTP endpoint."""


__all__ = ["Provider", "ProviderApiResponse"]
