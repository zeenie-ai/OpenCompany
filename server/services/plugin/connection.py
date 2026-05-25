"""Authed HTTP facade (Nango pattern).

Handlers never see tokens. They call ``await ctx.connection(id).get(url)``
and the Connection resolves credentials, injects auth into the request,
handles retry on auth failure, and tracks usage for cost attribution.

Only HTTP-bearing credentials use this — an LLM API key inside a
native SDK call still goes through ``cred_class.resolve()`` directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from core.logging import get_logger
from services.plugin.credential import Credential

logger = get_logger(__name__)


class Connection:
    """Lazy, per-call authed httpx wrapper bound to a credential class.

    One instance per (credential_id, user_id, session_id) per node call.
    Created by :meth:`NodeContext.connection`. Not thread-safe — each
    concurrent node call should get its own.
    """

    def __init__(
        self,
        credential_cls: type[Credential],
        *,
        user_id: str = "owner",
        timeout: float = 30.0,
        session_id: str = "default",
        node_id: Optional[str] = None,
    ):
        self._cred_cls = credential_cls
        self._user_id = user_id
        self._timeout = timeout
        self._session_id = session_id
        self._node_id = node_id
        self._secrets: Optional[Dict[str, Any]] = None
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def credential_id(self) -> str:
        return self._cred_cls.id

    async def credentials(self) -> Dict[str, Any]:
        """Resolved secret dict (cached). Raises ``PermissionError`` if
        the user hasn't connected this provider."""
        if self._secrets is None:
            self._secrets = await self._cred_cls.resolve(user_id=self._user_id)
        return self._secrets

    async def refresh(self) -> Dict[str, Any]:
        """Force a re-resolve (after a 401/403). The auth service is
        responsible for actually rotating the token."""
        self._secrets = None
        return await self.credentials()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "Connection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        data: Optional[Any] = None,
        _retry_on_auth: bool = True,
    ) -> httpx.Response:
        secrets = await self.credentials()
        req: Dict[str, Any] = {
            "headers": dict(headers or {}),
            "params": dict(params or {}),
        }
        if json is not None:
            req["json"] = json
        if data is not None:
            req["data"] = data
        req = self._cred_cls.inject(secrets, req)

        client = self._get_client()
        response = await client.request(method, url, **req)

        if _retry_on_auth and response.status_code in (401, 403):
            logger.debug(
                "[Connection] auth retry for %s (%s) status=%s",
                self.credential_id,
                method,
                response.status_code,
            )
            await self.refresh()
            secrets = await self.credentials()
            req_retry = self._cred_cls.inject(
                secrets,
                {
                    "headers": dict(headers or {}),
                    "params": dict(params or {}),
                    **({"json": json} if json is not None else {}),
                    **({"data": data} if data is not None else {}),
                },
            )
            response = await client.request(method, url, **req_retry)

        return response

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)
