"""HTTP client for the JS code execution sidecar (runs on bun)."""

from typing import Any, Dict, List, Optional
import aiohttp
from core.logging import get_logger

logger = get_logger(__name__)

#: The header carrying the sidecar's token (``server/nodejs/src/index.ts``).
TOKEN_HEADER = "X-OpenCompany-Executor-Token"


class NodeJSClient:
    """Async HTTP client for the JS executor service."""

    def __init__(self, base_url: str, timeout: int = 30, *, token: str):
        """Initialize client with base URL and timeout.

        Args:
            base_url: Base URL of the sidecar (composed from NODEJS_EXECUTOR_PORT; defaults live in .env.template)
            timeout: Default request timeout in seconds
            token: The token the sidecar was started with, sent on every request
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._headers = {TOKEN_HEADER: token}

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)

    @staticmethod
    async def _read(response: aiohttp.ClientResponse) -> Dict[str, Any]:
        if response.status == 401:
            # An external executor (NODEJS_EXECUTOR_URL), or a shared one on the
            # port: the runtime sends a token it made only to its own sidecar.
            raise RuntimeError(
                "the executor refused this backend's token (HTTP 401): it was started "
                "with a different NODEJS_EXECUTOR_TOKEN. Give it and this backend the "
                "same one."
            )
        return await response.json()

    async def health_check(self) -> Dict[str, Any]:
        """Check if the sidecar is healthy.

        Returns:
            Health status dict with status, service, node_version
        """
        async with self._session() as session:
            async with session.get(f"{self._base_url}/health") as response:
                return await self._read(response)

    async def execute(
        self, code: str, input_data: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None, language: str = "javascript"
    ) -> Dict[str, Any]:
        """Execute JavaScript/TypeScript code.

        Args:
            code: Code to execute
            input_data: Data available as input_data in code
            timeout: Execution timeout in milliseconds
            language: 'javascript' or 'typescript'

        Returns:
            Execution result with success, output, console_output, execution_time_ms
        """
        payload = {
            "code": code,
            "input_data": input_data or {},
            "language": language,
        }
        if timeout is not None:
            payload["timeout"] = timeout

        async with self._session() as session:
            async with session.post(f"{self._base_url}/execute", json=payload) as response:
                return await self._read(response)

    async def install_packages(self, packages: List[str]) -> Dict[str, Any]:
        """Install npm packages.

        Args:
            packages: List of package names (e.g., ['lodash', 'axios@1.0.0'])

        Returns:
            Result with success and message
        """
        async with self._session() as session:
            async with session.post(f"{self._base_url}/packages/install", json={"packages": packages}) as response:
                return await self._read(response)

    async def list_packages(self) -> Dict[str, Any]:
        """List installed npm packages.

        Returns:
            Dict with success and packages dict
        """
        async with self._session() as session:
            async with session.get(f"{self._base_url}/packages") as response:
                return await self._read(response)
