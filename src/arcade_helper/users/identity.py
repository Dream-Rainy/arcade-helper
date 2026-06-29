"""Platform and provider identity value objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformIdentity:
    """Identity supplied by the caller's platform, such as QQ."""

    platform: str
    account_key: str
    account_name: str | None = None

    def __post_init__(self) -> None:
        platform = self.platform.strip().lower()
        account_key = self.account_key.strip()
        if not platform:
            raise ValueError("platform must not be empty")
        if not account_key:
            raise ValueError("account_key must not be empty")
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "account_key", account_key)

    @classmethod
    def qq(cls, qq: str) -> "PlatformIdentity":
        """Create a QQ platform identity."""
        return cls(platform="qq", account_key=qq, account_name=f"QQ_{qq}")

    @property
    def cache_key(self) -> str:
        """Stable key suitable for caches and OAuth state binding."""
        return f"{self.platform}:{self.account_key}"


@dataclass(frozen=True)
class ProviderAccountIdentity:
    """A linked account owned by an external provider."""

    provider: str
    account_key: str

    def __post_init__(self) -> None:
        provider = self.provider.strip().lower()
        account_key = self.account_key.strip()
        if not provider:
            raise ValueError("provider must not be empty")
        if not account_key:
            raise ValueError("account_key must not be empty")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "account_key", account_key)


__all__ = ["PlatformIdentity", "ProviderAccountIdentity"]
