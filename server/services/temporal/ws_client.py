"""WebSocket client for Temporal activities.

Uses aiohttp ClientSession for proper connection pooling and concurrent
request handling. Each activity creates its own WebSocket connection
to avoid race conditions, but the session manages connection reuse.

References:
- https://docs.aiohttp.org/en/stable/client_quickstart.html
- https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html
"""

import asyncio
import json
import uuid
from typing import Any, Dict, Optional
from contextlib import asynccontextmanager

import aiohttp

from core.logging import get_logger
from core.config import Settings

logger = get_logger(__name__)


def _default_ws_url() -> str:
    """Resolve the activity-side WS URL from current ``Settings``.

    Deferred to first call so module import doesn't require the full env
    surface — same rationale as ``activities._resolve_urls``.
    """
    settings = Settings()
    return f"ws://{settings.host}:{settings.port}/ws/internal"


class WSConnectionPool:
    """WebSocket connection pool using aiohttp ClientSession.

    aiohttp ClientSession provides built-in connection pooling with:
    - Connection reuse and keep-alive
    - Configurable connection limits
    - Proper concurrent request handling

    Each execute_node call gets its own WebSocket connection from the pool,
    avoiding the ConcurrencyError that occurs when sharing a single connection.
    """

    def __init__(self, url: Optional[str] = None, pool_size: int = 100):
        self.url = url if url is not None else _default_ws_url()
        self.pool_size = pool_size
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session with connection pooling."""
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    connector = aiohttp.TCPConnector(
                        limit=self.pool_size,
                        limit_per_host=self.pool_size,
                        enable_cleanup_closed=True,
                    )
                    timeout = aiohttp.ClientTimeout(
                        total=300,  # 5 min total timeout
                        connect=10,  # 10 sec connect timeout
                    )
                    self._session = aiohttp.ClientSession(
                        connector=connector,
                        timeout=timeout,
                    )
                    logger.info(
                        "Created shared session",
                        pool_size=self.pool_size,
                    )
        return self._session

    @asynccontextmanager
    async def connection(self):
        """Get a WebSocket connection from the pool.

        Usage:
            async with pool.connection() as ws:
                await ws.send_json(request)
                response = await ws.receive_json()
        """
        session = await self._get_session()
        async with session.ws_connect(
            self.url,
            heartbeat=20,
            receive_timeout=120,
        ) as ws:
            yield ws

    async def execute_node(
        self,
        node_id: str,
        node_type: str,
        data: Dict[str, Any],
        context: Dict[str, Any],
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """Execute a node via WebSocket.

        Each call gets its own connection from the pool, allowing
        concurrent execution without race conditions.
        """
        request_id = str(uuid.uuid4())

        message = {
            "type": "execute_node",
            "request_id": request_id,
            "node_id": node_id,
            "node_type": node_type,
            "parameters": data,
            "nodes": context.get("nodes", []),
            "edges": context.get("edges", []),
            "session_id": context.get("session_id", "default"),
            "workflow_id": context.get("workflow_id"),
            "execution_id": context.get("execution_id"),
        }
        for key in (
            "auto_rebind_tools",
            "invoking_agent_node_id",
            "agent_iteration",
            "tool_call_index",
            "tool_call_id",
        ):
            if key in context:
                message[key] = context[key]

        try:
            async with self.connection() as ws:
                await ws.send_json(message)
                logger.debug("Sent execute_node", node_id=node_id)

                # Wait for response with matching request_id
                async with asyncio.timeout(timeout):
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            response = json.loads(msg.data)
                            if response.get("request_id") == request_id:
                                logger.debug(
                                    "Received response",
                                    node_id=node_id,
                                    success=response.get("success"),
                                )
                                return response
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            raise Exception(f"WebSocket error: {ws.exception()}")
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            raise Exception("WebSocket closed unexpectedly")

                raise Exception(f"No response received for request {request_id}")

        except asyncio.TimeoutError:
            raise Exception(f"WebSocket request timeout ({timeout}s) for node {node_id}")
        except aiohttp.ClientError as e:
            raise Exception(f"WebSocket connection error: {e}")

    async def close(self):
        """Close the connection pool."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("Session closed")


# Global connection pool instance
_pool: Optional[WSConnectionPool] = None
_pool_lock = asyncio.Lock()


async def get_ws_pool() -> WSConnectionPool:
    """Get or create the global WebSocket connection pool."""
    global _pool

    async with _pool_lock:
        if _pool is None:
            _pool = WSConnectionPool()
        return _pool


async def execute_node_ws(
    node_id: str,
    node_type: str,
    data: Dict[str, Any],
    context: Dict[str, Any],
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Execute a node via WebSocket using the connection pool.

    This is the recommended way to execute nodes from activities.
    Each call gets its own connection from the pool.
    """
    pool = await get_ws_pool()
    return await pool.execute_node(
        node_id=node_id,
        node_type=node_type,
        data=data,
        context=context,
        timeout=timeout,
    )


async def close_ws_pool() -> None:
    """Close the global WebSocket connection pool."""
    global _pool

    async with _pool_lock:
        if _pool:
            await _pool.close()
            _pool = None


# Backwards compatibility aliases
async def get_ws_client() -> WSConnectionPool:
    """Alias for get_ws_pool() for backwards compatibility."""
    return await get_ws_pool()


async def close_ws_client() -> None:
    """Alias for close_ws_pool() for backwards compatibility."""
    await close_ws_pool()


# For backwards compatibility with old code that expects TemporalWSClient
class TemporalWSClient:
    """Backwards compatibility wrapper around WSConnectionPool."""

    def __init__(self, url: Optional[str] = None):
        self._pool = WSConnectionPool(url=url)

    @property
    def connected(self) -> bool:
        return True  # Pool manages connections

    async def connect(self) -> None:
        pass  # Pool connects on demand

    async def disconnect(self) -> None:
        await self._pool.close()

    async def execute_node(
        self,
        node_id: str,
        node_type: str,
        data: Dict[str, Any],
        context: Dict[str, Any],
        timeout: float = 120.0,
    ) -> Dict[str, Any]:
        return await self._pool.execute_node(
            node_id=node_id,
            node_type=node_type,
            data=data,
            context=context,
            timeout=timeout,
        )
