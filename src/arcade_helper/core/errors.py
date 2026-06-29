"""Typed errors for the external data layer."""

from __future__ import annotations


class ArcadeHelperError(Exception):
    """Base error for ``arcade_helper``."""


class ClientLifecycleError(ArcadeHelperError):
    """Raised when a client is used in an invalid lifecycle state."""


class ClientNotStartedError(ClientLifecycleError):
    """Raised when a started client is required."""


class ClientStartingError(ClientLifecycleError):
    """Raised when an API is called while the client is starting."""


class ClientClosedError(ClientLifecycleError):
    """Raised when an API is called after the client has been closed."""


class ProviderError(ArcadeHelperError):
    """Base error for provider routing and provider calls."""


class ProviderUnavailableError(ProviderError):
    """Raised when a selected provider is unavailable."""


class ProviderResponseError(ProviderError):
    """Raised when a provider returns an invalid or failed response."""


class ProviderRateLimitedError(ProviderError):
    """Raised when a provider rate limit prevents the operation."""


class NoProviderAvailableError(ProviderError):
    """Raised when no provider can satisfy an operation."""


class AuthError(ArcadeHelperError):
    """Base error for authentication and account authorization."""


class OAuthExchangeError(AuthError):
    """Raised when OAuth code exchange fails."""


class OAuthStateMismatchError(AuthError):
    """Raised when OAuth callback state does not match a stored flow."""


class OAuthStateExpiredError(AuthError):
    """Raised when OAuth callback state is expired."""


class OAuthStateConsumedError(AuthError):
    """Raised when OAuth callback state has already been consumed."""


class TokenRefreshError(AuthError):
    """Raised when OAuth token refresh fails."""


class AccountNotBoundError(AuthError):
    """Raised when an operation requires a linked account."""


class AccountError(ArcadeHelperError):
    """Base error for external account state."""


class AccountConflictError(AccountError):
    """Raised when account state conflicts with the requested operation."""


class DefaultAccountNotFoundError(AccountError):
    """Raised when no default account can be resolved."""


class CatalogError(ArcadeHelperError):
    """Base error for song catalog operations."""


class SongNotFoundError(CatalogError):
    """Raised when a song cannot be found."""


class CatalogSyncError(CatalogError):
    """Raised when catalog synchronization fails."""


class CacheError(ArcadeHelperError):
    """Raised when cache access or invalidation fails."""


class StorageError(ArcadeHelperError):
    """Base error for storage configuration and migration."""


class StorageNotConfiguredError(StorageError):
    """Raised when an operation needs storage but no store is configured."""


class AccountStoreMigrationError(StorageError):
    """Raised when account store export or import fails."""


__all__ = [
    "AccountConflictError",
    "AccountError",
    "AccountNotBoundError",
    "CacheError",
    "CatalogError",
    "CatalogSyncError",
    "ArcadeHelperError",
    "ClientClosedError",
    "ClientLifecycleError",
    "ClientNotStartedError",
    "ClientStartingError",
    "DefaultAccountNotFoundError",
    "NoProviderAvailableError",
    "OAuthExchangeError",
    "OAuthStateConsumedError",
    "OAuthStateExpiredError",
    "OAuthStateMismatchError",
    "ProviderError",
    "ProviderRateLimitedError",
    "ProviderResponseError",
    "ProviderUnavailableError",
    "SongNotFoundError",
    "StorageError",
    "StorageNotConfiguredError",
    "AccountStoreMigrationError",
    "TokenRefreshError",
]
