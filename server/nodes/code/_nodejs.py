"""Shared JS executor sidecar access for the JS/TS plugins.

Both ``javascript_executor`` and ``typescript_executor`` dispatch
through the same persistent bun-hosted sidecar. This helper owns the
singleton HTTP client AND the on-demand spawn: :func:`acquire_client`
ensures the backend-supervised runtime (see :mod:`._runtime`) is up
before handing back the client, so JS/TS nodes work in every mode
without any CLI-side service wiring.
"""

from __future__ import annotations

import os
from typing import Optional

from ._client import NodeJSClient
from ._runtime import executor_port, get_nodejs_executor_runtime

_client: Optional[NodeJSClient] = None


def executor_base_url() -> str:
    """Configured sidecar URL — env-driven, no hardcoded port.

    ``NODEJS_EXECUTOR_URL`` wins outright (remote/external executor);
    otherwise the URL composes from the plugin-owned port.
    """
    return os.environ.get("NODEJS_EXECUTOR_URL") or (
        f"http://localhost:{executor_port()}"
    )


def get_nodejs_client(base_url: str | None = None, timeout: int = 30) -> NodeJSClient:
    global _client
    if _client is None:
        _client = NodeJSClient(
            base_url or executor_base_url(), timeout, token=get_nodejs_executor_runtime().token
        )
    return _client


async def acquire_client() -> NodeJSClient:
    """Ensure the executor sidecar is running, then return the client.

    When ``NODEJS_EXECUTOR_URL`` points at an external executor the
    spawn step is skipped entirely — that URL is authoritative.
    """
    if not os.environ.get("NODEJS_EXECUTOR_URL"):
        await get_nodejs_executor_runtime().ensure_started()
    return get_nodejs_client()
