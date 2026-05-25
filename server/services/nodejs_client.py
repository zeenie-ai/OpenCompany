"""HTTP client for Node.js code execution server."""

from typing import Any, Dict, List, Optional
import aiohttp
from core.logging import get_logger

logger = get_logger(__name__)


class NodeJSClient:
    """Async HTTP client for Node.js executor service."""

    def __init__(self, base_url: str, timeout: int = 30):
        """Initialize client with base URL and timeout.

        Args:
            base_url: Base URL of Node.js server (e.g., http://127.0.0.1:3020)
            timeout: Default request timeout in seconds
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def health_check(self) -> Dict[str, Any]:
        """Check if Node.js server is healthy.

        Returns:
            Health status dict with status, service, node_version
        """
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(f"{self._base_url}/health") as response:
                return await response.json()

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

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(f"{self._base_url}/execute", json=payload) as response:
                return await response.json()

    async def install_packages(self, packages: List[str]) -> Dict[str, Any]:
        """Install npm packages.

        Args:
            packages: List of package names (e.g., ['lodash', 'axios@1.0.0'])

        Returns:
            Result with success and message
        """
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(f"{self._base_url}/packages/install", json={"packages": packages}) as response:
                return await response.json()

    async def list_packages(self) -> Dict[str, Any]:
        """List installed npm packages.

        Returns:
            Dict with success and packages dict
        """
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.get(f"{self._base_url}/packages") as response:
                return await response.json()
