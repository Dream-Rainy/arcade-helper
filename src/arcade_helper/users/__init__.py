"""User and external account domain services."""

from .identity import PlatformIdentity, ProviderAccountIdentity
from .service import ExternalUser, LinkedAccountSummary, UserBindResult, UserService, UserUnbindResult

__all__ = [
    "ExternalUser",
    "LinkedAccountSummary",
    "PlatformIdentity",
    "ProviderAccountIdentity",
    "UserBindResult",
    "UserService",
    "UserUnbindResult",
]
