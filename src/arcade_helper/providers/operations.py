"""Provider operation metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProviderAccess = Literal["read", "write"]


@dataclass(frozen=True)
class ProviderOperation:
    """A single method-level provider capability."""

    key: str
    method: str
    game_code: str | None = None
    data_kind: str = ""
    access: ProviderAccess = "read"
    requires_account: bool = False
    fallback_allowed: bool = True
    stale_allowed: bool = False
    priority: int = 0


@dataclass(frozen=True)
class ProviderRoutePolicy:
    """Optional default routing rule for one operation."""

    operation: str
    game_code: str | None = None
    default_provider: str | None = None
    fallback_enabled: bool = True


__all__ = ["ProviderAccess", "ProviderOperation", "ProviderRoutePolicy"]
