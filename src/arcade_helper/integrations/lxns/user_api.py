"""LXNS user-scoped APIs backed by OAuth or personal user tokens."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote, urljoin

from arcade_helper.core.context import CatalogContext
from arcade_helper.providers import ProviderApiResponse


class LxnsUserApi:
    """Low-level LXNS /api/v0/user endpoint caller."""

    def __init__(self, context: CatalogContext):
        self.context = context

    async def oauth_call(
        self,
        access_token: str,
        method: Literal["GET", "POST", "PUT", "DELETE"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        """Call a user-scoped LXNS endpoint with an OAuth access token."""
        return await self._call(
            method,
            endpoint,
            auth_headers={"Authorization": f"Bearer {access_token}"},
            **kwargs,
        )

    async def personal_call(
        self,
        user_token: str,
        method: Literal["GET", "POST", "PUT", "DELETE"],
        endpoint: str,
        /,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        """Call a user-scoped LXNS endpoint with a personal user token."""
        return await self._call(
            method,
            endpoint,
            auth_headers={"X-User-Token": user_token},
            **kwargs,
        )

    async def _call(
        self,
        method: str,
        endpoint: str,
        /,
        *,
        auth_headers: dict[str, str],
        path_params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_data: Any = None,
        **kwargs: Any,
    ) -> ProviderApiResponse:
        endpoint = _format_endpoint(endpoint, path_params=path_params)
        url = _build_user_url(self.context.base_url, endpoint)
        request_headers = {**self.context.headers, **(headers or {}), **auth_headers}

        method = method.upper()
        if method == "GET":
            data = await self.context.get_json(url, headers=request_headers, **kwargs)
        elif method in {"POST", "PUT", "DELETE"}:
            data = await self.context.post_json(url, headers=request_headers, json_data=json_data, **kwargs)
        else:
            raise ValueError(f"Unsupported LXNS user API method: {method}")
        return ProviderApiResponse(provider="lxns", method=method, url=url, data=data)


def _build_user_url(base_url: str, endpoint: str) -> str:
    if "://" in endpoint or endpoint.startswith("//"):
        raise ValueError("LXNS user API endpoint must be a relative path")

    relative = endpoint.lstrip("/")
    if not relative.startswith("api/v0/user/"):
        raise ValueError("LXNS user API endpoint must start with /api/v0/user/")

    base = (base_url or "https://maimai.lxns.net").rstrip("/") + "/"
    return urljoin(base, relative)


def _format_endpoint(endpoint: str, *, path_params: dict[str, Any] | None) -> str:
    if not path_params:
        return endpoint
    quoted = {key: quote(str(value), safe="") for key, value in path_params.items()}
    return endpoint.format_map(quoted)


__all__ = ["LxnsUserApi"]
