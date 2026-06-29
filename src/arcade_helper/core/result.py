"""Result wrappers for external data reads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class DataFreshness:
    """Metadata describing how visible external data was obtained."""

    provider: str
    """Provider that produced the data."""
    operation: str
    """Provider operation key used to read the data."""
    stale: bool = False
    """Whether the result is a fallback, cache, or best-effort approximation."""
    fetched_at: datetime | None = None
    """Time when this wrapper was produced."""
    snapshot_at: datetime | None = None
    """Provider-reported data snapshot time, when available."""
    fallback_chain: list[str] = field(default_factory=list)
    """Providers or strategies attempted before the final result."""
    warnings: list[str] = field(default_factory=list)
    """Non-fatal warnings callers may show or log."""


@dataclass(frozen=True)
class DataResult(Generic[T]):
    """External data plus provider/freshness metadata."""

    data: T
    """Domain object or raw payload returned to the caller."""
    freshness: DataFreshness
    """Provider and freshness metadata for the data."""


__all__ = ["DataFreshness", "DataResult"]
