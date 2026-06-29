"""High-level LXNS provider facade."""

from __future__ import annotations

from arcade_helper.core.context import CatalogContext, HttpPostJson
from arcade_helper.games.chunithm.client import ChunithmClient
from arcade_helper.games.maimai.client import MaimaiClient

from .constants import site_config_url
from .oauth_client import OauthClient
from .user_api import LxnsUserApi


class LxnsClient:
    """Facade for LXNS-backed maimai and CHUNITHM data.

    The shape mirrors provider-style clients: callers talk to the provider once,
    then access game-specific APIs through `maimai` and `chunithm`.
    """

    def __init__(
        self,
        context: CatalogContext,
        *,
        http_post_json: HttpPostJson | None = None,
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        scope: str = "",
        state_ttl_seconds: int = 600,
        relay_url: str = "",
        oauth_client: OauthClient | None = None,
    ):
        self.context = context
        self.user = LxnsUserApi(context)
        self.maimai = MaimaiClient(context)
        self.chunithm = ChunithmClient(context)
        post_json = http_post_json
        if post_json is None and context.http_post_json is not None:
            post_json = context.post_json
        self.oauth = oauth_client
        if self.oauth is None and post_json is not None:
            self.oauth = OauthClient(
                http_post_json=post_json,
                client_id=client_id,
                client_secret=client_secret,
                base_url=context.base_url or "https://maimai.lxns.net",
                redirect_uri=redirect_uri,
                scope=scope,
                state_ttl_seconds=state_ttl_seconds,
                relay_url=relay_url,
            )

    async def site_config(self) -> dict:
        """Fetch LXNS site configuration."""
        return await self.context.get_json(
            site_config_url(base_url=self.context.base_url),
            headers=dict(self.context.headers),
        )


__all__ = ["LxnsClient"]

