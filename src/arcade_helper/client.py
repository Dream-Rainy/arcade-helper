"""Public facade for external Chiffon game data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .auth import AuthService
from .catalog import SongCatalogService
from .aliases import AliasService
from .core.context import CatalogContext, HttpGetJson, HttpPostJson
from .core.errors import (
    ClientClosedError,
    ClientNotStartedError,
    ClientStartingError,
    StorageNotConfiguredError,
)
from .games.arcade_songs import ArcadeSongsProvider
from .integrations.lxns.accounts import AccountStore
from .integrations.lxns.client import LxnsClient
from .integrations.lxns.oauth_client import OauthClient
from .integrations.lxns.provider import LxnsProvider
from .players import PlayerService
from .providers.registry import ProviderRegistry
from .storage.local import JsonAccountStore
from .users import UserService


class ArcadeHelperClient:
    """Top-level arcade data client.

    ``maimai`` and ``chunithm`` expose game-specific LXNS/API facades.
    ``catalog`` is the preferred entrypoint for song lookup, sync, and generic
    arcade-songs catalogs.

    Example:
        client = ArcadeHelperClient(http_get_json=..., local_data_dir="./data")
        await client.lifecycle.start()
        songs = await client.catalog.arcade_songs.search("sdvx", "FLOWER")
    """

    def __init__(
        self,
        *,
        http_get_json: HttpGetJson,
        http_post_json: HttpPostJson | None = None,
        headers: dict[str, str] | None = None,
        ingame_data_base_dir: str = "",
        lxns_base_url: str = "https://maimai.lxns.net",
        logger: Any = None,
        lxns_client_id: str = "",
        lxns_client_secret: str = "",
        lxns_oauth_redirect_uri: str = "",
        lxns_oauth_scope: str = "",
        lxns_oauth_state_ttl_seconds: int = 600,
        lxns_oauth_relay_url: str = "",
        lxns_oauth_client: OauthClient | None = None,
        local_data_dir: str | Path | None = None,
        providers: ProviderRegistry | None = None,
        account_store: AccountStore | None = None,
        song_store: Any | None = None,
        catalog_store: Any | None = None,
        alias_policy: Any | None = None,
        alias_daily_limit: int = 20,
        alias_pending_limit: int = 5,
    ):
        self._state = "created"
        self.lifecycle = ArcadeHelperLifecycle(self)
        self.context = CatalogContext(
            http_get_json=http_get_json,
            http_post_json=http_post_json,
            ensure_started=self._ensure_started,
            ingame_data_base_dir=ingame_data_base_dir,
            headers=dict(headers or {}),
            base_url=lxns_base_url,
            logger=logger or logging.getLogger(__name__),
        )
        self.lxns = LxnsClient(
            self.context,
            client_id=lxns_client_id,
            client_secret=lxns_client_secret,
            redirect_uri=lxns_oauth_redirect_uri,
            scope=lxns_oauth_scope,
            state_ttl_seconds=lxns_oauth_state_ttl_seconds,
            relay_url=lxns_oauth_relay_url,
            oauth_client=lxns_oauth_client,
        )
        self.maimai = self.lxns.maimai
        self.chunithm = self.lxns.chunithm
        self.accounts = account_store or _default_account_store(local_data_dir)
        self.providers = providers or ProviderRegistry()
        self.lxns_provider = LxnsProvider(self.lxns)
        self.providers.register(self.lxns_provider, self.lxns_provider.operations)
        arcade_songs_cache_dir = Path(local_data_dir) / "arcade-songs" if local_data_dir is not None else None
        self.arcade_songs_provider = ArcadeSongsProvider(self.context, cache_dir=arcade_songs_cache_dir)
        self.providers.register(self.arcade_songs_provider, self.arcade_songs_provider.operations)
        self.users = UserService(
            store=self.accounts,
            lxns_client=self.lxns,
            logger=self.context.logger,
            ensure_started=self._ensure_started,
        )
        self.auth = AuthService(
            store=self.accounts,
            users=self.users,
            lxns_client=self.lxns,
            oauth=self.lxns.oauth,
            logger=self.context.logger,
            ensure_started=self._ensure_started,
        )
        self.players = PlayerService(
            providers=self.providers,
            users=self.users,
            logger=self.context.logger,
            ensure_started=self._ensure_started,
            ensure_account_token=lambda account_key: self.auth.ensure_lxns_token_valid(account_key=account_key),
        )
        self.catalog = SongCatalogService(
            providers=self.providers,
            song_store=song_store,
            catalog_store=catalog_store,
            logger=self.context.logger,
        )
        self.aliases = AliasService(catalog_store, policy=alias_policy, daily_limit=alias_daily_limit, pending_limit=alias_pending_limit)
        self.catalog.aliases = self.aliases

    @classmethod
    def from_context(
        cls,
        context: CatalogContext,
        *,
        lxns_client_id: str = "",
        lxns_client_secret: str = "",
        lxns_oauth_redirect_uri: str = "",
        lxns_oauth_scope: str = "",
        lxns_oauth_state_ttl_seconds: int = 600,
        lxns_oauth_relay_url: str = "",
        lxns_oauth_client: OauthClient | None = None,
        providers: ProviderRegistry | None = None,
        account_store: AccountStore | None = None,
        local_data_dir: str | Path | None = None,
        song_store: Any | None = None,
        catalog_store: Any | None = None,
        alias_policy: Any | None = None,
        alias_daily_limit: int = 20,
        alias_pending_limit: int = 5,
    ) -> "ArcadeHelperClient":
        """Build a client from an existing context and optional stores/providers."""
        client = cls.__new__(cls)
        client._state = "created"
        client.lifecycle = ArcadeHelperLifecycle(client)
        client.context = context
        client.context.ensure_started = client._ensure_started
        client.lxns = LxnsClient(
            context,
            client_id=lxns_client_id,
            client_secret=lxns_client_secret,
            redirect_uri=lxns_oauth_redirect_uri,
            scope=lxns_oauth_scope,
            state_ttl_seconds=lxns_oauth_state_ttl_seconds,
            relay_url=lxns_oauth_relay_url,
            oauth_client=lxns_oauth_client,
        )
        client.maimai = client.lxns.maimai
        client.chunithm = client.lxns.chunithm
        client.accounts = account_store or _default_account_store(local_data_dir)
        client.providers = providers or ProviderRegistry()
        client.lxns_provider = LxnsProvider(client.lxns)
        client.providers.register(client.lxns_provider, client.lxns_provider.operations)
        arcade_songs_cache_dir = Path(local_data_dir) / "arcade-songs" if local_data_dir is not None else None
        client.arcade_songs_provider = ArcadeSongsProvider(client.context, cache_dir=arcade_songs_cache_dir)
        client.providers.register(client.arcade_songs_provider, client.arcade_songs_provider.operations)
        client.users = UserService(
            store=client.accounts,
            lxns_client=client.lxns,
            logger=client.context.logger,
            ensure_started=client._ensure_started,
        )
        client.auth = AuthService(
            store=client.accounts,
            users=client.users,
            lxns_client=client.lxns,
            oauth=client.lxns.oauth,
            logger=client.context.logger,
            ensure_started=client._ensure_started,
        )
        client.players = PlayerService(
            providers=client.providers,
            users=client.users,
            logger=client.context.logger,
            ensure_started=client._ensure_started,
            ensure_account_token=lambda account_key: client.auth.ensure_lxns_token_valid(account_key=account_key),
        )
        client.catalog = SongCatalogService(
            providers=client.providers,
            song_store=song_store,
            catalog_store=catalog_store,
            logger=client.context.logger,
        )
        client.aliases = AliasService(catalog_store, policy=alias_policy, daily_limit=alias_daily_limit, pending_limit=alias_pending_limit)
        client.catalog.aliases = client.aliases
        return client

    @property
    def state(self) -> str:
        """Current lifecycle state: created, starting, started, stopping, or stopped."""
        return self._state

    def _ensure_started(self) -> None:
        if self._state == "started":
            return
        if self._state == "starting":
            raise ClientStartingError("ArcadeHelperClient is still starting")
        if self._state in {"stopping", "stopped", "closed"}:
            raise ClientClosedError("ArcadeHelperClient has been stopped")
        raise ClientNotStartedError("ArcadeHelperClient.lifecycle.start() must be awaited before use")


class ArcadeHelperLifecycle:
    """Async lifecycle controller for ``ArcadeHelperClient``."""

    def __init__(self, client: ArcadeHelperClient) -> None:
        self._client = client

    async def start(self) -> None:
        """Mark the client ready for service calls after validating providers."""
        if self._client._state == "started":
            return
        if self._client._state == "starting":
            raise ClientStartingError("ArcadeHelperClient is already starting")
        if self._client._state in {"stopping", "stopped", "closed"}:
            raise ClientClosedError("ArcadeHelperClient cannot be restarted after stop")

        self._client._state = "starting"
        try:
            if not self._client.providers.operations():
                raise ClientNotStartedError("ArcadeHelperClient has no registered providers")
            self._client._state = "started"
        except Exception:
            self._client._state = "created"
            raise

    async def stop(self) -> None:
        """Stop the client lifecycle; stopped clients cannot be restarted."""
        if self._client._state in {"stopped", "closed"}:
            return
        if self._client._state == "created":
            self._client._state = "stopped"
            return

        self._client._state = "stopping"
        self._client._state = "stopped"


def _default_account_store(local_data_dir: str | Path | None) -> AccountStore:
    if local_data_dir is not None:
        return JsonAccountStore(Path(local_data_dir) / "accounts.json")
    try:
        from .storage.tortoise import TortoiseAccountStore
    except ImportError as exc:
        raise StorageNotConfiguredError(
            "Account storage requires an injected account_store, local_data_dir, "
            "or the storage-tortoise extra."
        ) from exc
    return TortoiseAccountStore()


__all__ = ["ArcadeHelperClient", "ArcadeHelperLifecycle"]
