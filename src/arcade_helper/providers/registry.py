"""Provider registry and deterministic operation routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from arcade_helper.core.errors import NoProviderAvailableError

from .base import Provider, ProviderApiResponse
from .operations import ProviderOperation, ProviderRoutePolicy


@dataclass(frozen=True)
class RegisteredProvider:
    """A provider plus the operation it registered."""

    provider: Provider
    operation: ProviderOperation
    registration_order: int


class ProviderRegistry:
    """Registry for method-level provider operations."""

    def __init__(self) -> None:
        self._entries: list[RegisteredProvider] = []
        self._policies: dict[tuple[str, str | None], ProviderRoutePolicy] = {}
        self._next_order = 0

    def register(
        self,
        provider: Provider,
        operations: list[ProviderOperation] | tuple[ProviderOperation, ...],
    ) -> None:
        """Register a provider for a set of method-level operations."""
        for operation in operations:
            self._entries.append(
                RegisteredProvider(
                    provider=provider,
                    operation=operation,
                    registration_order=self._next_order,
                )
            )
            self._next_order += 1

    def set_policy(self, policy: ProviderRoutePolicy) -> None:
        """Set the default provider and fallback behavior for an operation."""
        self._policies[(policy.operation, policy.game_code)] = policy

    def get_policy(self, operation: str, game_code: str | None = None) -> ProviderRoutePolicy | None:
        """Return the most specific route policy for an operation."""
        return (
            self._policies.get((operation, game_code))
            or self._policies.get((operation, None))
        )

    def operations(self) -> list[RegisteredProvider]:
        """Return all registered provider-operation pairs."""
        return list(self._entries)

    def provider(self, name: str) -> Provider:
        """Return a provider by name or raise if it is not registered."""
        for entry in self._entries:
            if entry.provider.name == name:
                return entry.provider
        raise NoProviderAvailableError(f"No provider registered with name {name!r}")

    async def call_api(
        self,
        provider: str,
        method: Literal["GET", "POST"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        """Call a provider-scoped HTTP endpoint through its low-level API."""
        return await self.provider(provider).call_api(method, endpoint, **kwargs)

    def candidates(
        self,
        operation: str,
        *,
        game_code: str | None = None,
        account_preference: str | None = None,
        default_provider: str | None = None,
    ) -> list[RegisteredProvider]:
        """Return ordered providers that can satisfy an operation."""
        policy = self.get_policy(operation, game_code)
        default_name = default_provider
        if default_name is None and policy is not None:
            default_name = policy.default_provider

        entries = [
            entry
            for entry in self._entries
            if entry.operation.key == operation
            and (
                game_code is None
                or entry.operation.game_code is None
                or entry.operation.game_code == game_code
            )
        ]

        def sort_key(entry: RegisteredProvider) -> tuple[int, int, int, int]:
            provider_name = entry.provider.name
            account_rank = 0 if account_preference and provider_name == account_preference else 1
            default_rank = 0 if default_name and provider_name == default_name else 1
            provider_priority = int(getattr(entry.provider, "priority", 0))
            operation_priority = int(entry.operation.priority)
            return (
                account_rank,
                default_rank,
                -(provider_priority + operation_priority),
                entry.registration_order,
            )

        return sorted(entries, key=sort_key)

    def require(
        self,
        operation: str,
        *,
        game_code: str | None = None,
        account_preference: str | None = None,
        default_provider: str | None = None,
    ) -> RegisteredProvider:
        """Return the first candidate provider or raise when none is available."""
        candidates = self.candidates(
            operation,
            game_code=game_code,
            account_preference=account_preference,
            default_provider=default_provider,
        )
        if not candidates:
            suffix = f" for {game_code}" if game_code else ""
            raise NoProviderAvailableError(f"No provider registered for {operation}{suffix}")
        return candidates[0]


__all__ = ["ProviderRegistry", "RegisteredProvider"]
