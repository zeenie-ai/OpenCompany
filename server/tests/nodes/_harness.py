"""NodeTestHarness - drive any node handler through NodeExecutor with mocks.

Usage:
    async def test_my_node(harness):
        result = await harness.execute(
            node_type="braveSearch",
            params={"query": "hello"},
        )
        harness.assert_envelope(result, success=True)
        assert result["result"]["query"] == "hello"

The harness builds a NodeExecutor with stub services (Database, AIService,
MapsService, TextService, AndroidService, Settings) so the registry dispatch
runs end-to-end without any real I/O.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock


@dataclass
class HarnessContext:
    """Captured state from a harness run, useful for assertions."""

    output_store_calls: List[tuple] = field(default_factory=list)
    """List of (session_id, node_id, output_key, value) tuples written by the executor."""


class NodeTestHarness:
    """Drives node handlers through NodeExecutor with mocked services."""

    def __init__(
        self,
        database: Optional[MagicMock] = None,
        ai_service: Optional[MagicMock] = None,
        maps_service: Optional[MagicMock] = None,
        text_service: Optional[MagicMock] = None,
        android_service: Optional[MagicMock] = None,
        settings: Optional[MagicMock] = None,
    ):
        # Lazy import so conftest stubs land before NodeExecutor pulls in core.logging.
        from services.node_executor import NodeExecutor

        self.database = database or _build_mock_database()
        self.ai_service = ai_service or _build_mock_ai_service()
        self.maps_service = maps_service or _build_mock_maps_service()
        self.text_service = text_service or _build_mock_text_service()
        self.android_service = android_service or _build_mock_android_service()
        self.settings = settings or _build_mock_settings()
        self.captured = HarnessContext()

        self.executor = NodeExecutor(
            database=self.database,
            ai_service=self.ai_service,
            maps_service=self.maps_service,
            text_service=self.text_service,
            android_service=self.android_service,
            settings=self.settings,
            output_store=self._record_output,
        )

    async def _record_output(self, session_id: str, node_id: str, key: str, value: Any) -> None:
        """Capture writes to the output store so tests can inspect downstream state."""
        self.captured.output_store_calls.append((session_id, node_id, key, value))

    def build_context(
        self,
        *,
        session_id: str = "test_session",
        workflow_id: str = "test_workflow",
        execution_id: Optional[str] = None,
        nodes: Optional[List[Dict]] = None,
        edges: Optional[List[Dict]] = None,
        upstream_outputs: Optional[Dict[str, Any]] = None,
        workspace_dir: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Synthesize the standard handler context dict.

        upstream_outputs maps "<source_node_id>::<output_key>" -> value, so
        get_output_fn can serve any handle the handler asks for.
        """
        outputs = upstream_outputs or {}

        async def get_output_fn(sess: str, source_id: str, output_key: str) -> Any:
            # Look up by composite key, then by source_id alone, then None
            return outputs.get(f"{source_id}::{output_key}", outputs.get(source_id))

        ctx: Dict[str, Any] = {
            "session_id": session_id,
            "workflow_id": workflow_id,
            "execution_id": execution_id or str(uuid.uuid4())[:8],
            "nodes": nodes or [],
            "edges": edges or [],
            "get_output_fn": get_output_fn,
            "start_time": time.time(),
        }
        if workspace_dir is not None:
            ctx["workspace_dir"] = workspace_dir
        if extra:
            ctx.update(extra)
        return ctx

    async def execute(
        self,
        node_type: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        node_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        upstream_outputs: Optional[Dict[str, Any]] = None,
        nodes: Optional[List[Dict]] = None,
        edges: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Run a node through the full executor pipeline."""
        ctx = context or self.build_context(
            upstream_outputs=upstream_outputs,
            nodes=nodes,
            edges=edges,
        )
        return await self.executor.execute(
            node_id=node_id or f"test_{node_type}_{uuid.uuid4().hex[:6]}",
            node_type=node_type,
            parameters=params or {},
            context=ctx,
        )

    async def call_handler(
        self,
        handler: Callable[..., Awaitable[Dict[str, Any]]],
        params: Optional[Dict[str, Any]] = None,
        *,
        node_type: str = "test_node",
        node_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        **handler_kwargs: Any,
    ) -> Dict[str, Any]:
        """Invoke a handler directly, bypassing dispatch.

        Useful when a handler has injected service dependencies that the
        registry binds via partial(). Pass them as handler_kwargs.
        """
        ctx = context or self.build_context()
        return await handler(
            node_id or f"direct_{node_type}",
            node_type,
            params or {},
            ctx,
            **handler_kwargs,
        )

    # ---------------------- assertion helpers ---------------------- #

    @staticmethod
    def assert_envelope(result: Dict[str, Any], *, success: Optional[bool] = None) -> None:
        """Verify the result conforms to the standard handler envelope.

        Required fields:
          - success: bool
          - on success=True: 'result' key present
          - on success=False: 'error' key present
        """
        assert isinstance(result, dict), f"handler must return dict, got {type(result)}"
        assert "success" in result, f"envelope missing 'success': {result}"
        assert isinstance(result["success"], bool), "envelope 'success' must be bool"

        if result["success"]:
            assert "result" in result, f"successful envelope missing 'result': {result}"
        else:
            assert "error" in result, f"failed envelope missing 'error': {result}"
            assert isinstance(result["error"], str), "envelope 'error' must be str"

        if success is not None:
            assert result["success"] is success, f"expected success={success}, got {result['success']} " f"(error={result.get('error')})"

    @staticmethod
    def assert_output_shape(result: Dict[str, Any], expected_keys: List[str]) -> None:
        """Verify the result payload has all expected top-level keys."""
        assert result.get("success"), f"cannot check shape on failed envelope: {result}"
        payload = result.get("result", {})
        assert isinstance(payload, dict), f"result payload must be dict, got {type(payload)}"
        missing = [k for k in expected_keys if k not in payload]
        assert not missing, f"result payload missing keys {missing}: {payload}"

    def output_for(self, node_id: str, key: str = "output_main") -> Any:
        """Fetch the most recent value the executor wrote to the output store."""
        for sess, nid, k, v in reversed(self.captured.output_store_calls):
            if nid == node_id and k == key:
                return v
        return None


# ---------------------- mock builders ---------------------- #


def _build_mock_database() -> MagicMock:
    db = MagicMock(name="Database")
    db.get_node_parameters = AsyncMock(return_value={})
    db.save_node_parameters = AsyncMock(return_value=None)
    db.save_api_usage_metric = AsyncMock(return_value=None)
    db.add_token_usage_metric = AsyncMock(return_value=None)
    db.get_chat_messages = AsyncMock(return_value=[])
    db.add_chat_message = AsyncMock(return_value=None)
    db.add_console_log = AsyncMock(return_value=None)
    return db


def _build_mock_ai_service() -> MagicMock:
    svc = MagicMock(name="AIService")
    svc.auth = MagicMock(name="AuthService")
    svc.auth.get_api_key = AsyncMock(return_value="test-api-key")
    svc.auth.get_stored_models = AsyncMock(return_value=["test-model"])
    svc.auth.get_oauth_tokens = AsyncMock(return_value=None)
    svc.execute_chat = AsyncMock(
        return_value={
            "success": True,
            "result": {"response": "mocked response", "model": "test-model", "provider": "test"},
        }
    )
    svc.execute_agent = AsyncMock(
        return_value={
            "success": True,
            "result": {"response": "mocked agent response", "model": "test-model"},
        }
    )
    svc.execute_chat_agent = AsyncMock(
        return_value={
            "success": True,
            "result": {"response": "mocked chat agent response", "model": "test-model"},
        }
    )
    svc.fetch_models = AsyncMock(return_value=["test-model-1", "test-model-2"])
    return svc


def _build_mock_maps_service() -> MagicMock:
    svc = MagicMock(name="MapsService")
    svc.geocode = AsyncMock(return_value={"lat": 0.0, "lng": 0.0})
    svc.nearby_places = AsyncMock(return_value=[])
    return svc


def _build_mock_text_service() -> MagicMock:
    svc = MagicMock(name="TextService")
    svc.generate_text = AsyncMock(return_value="mocked text")
    # Plugin paths for text generator / file handler now call these names
    # directly on the TextService singleton.
    svc.execute_text_generator = AsyncMock(
        return_value={
            "success": True,
            "text": "mocked text",
        }
    )
    svc.execute_file_handler = AsyncMock(
        return_value={
            "success": True,
            "content": "mocked content",
        }
    )
    return svc


def _build_mock_android_service() -> MagicMock:
    svc = MagicMock(name="AndroidService")
    svc.execute_action = AsyncMock(return_value={"success": True, "data": {}})
    svc.is_connected = MagicMock(return_value=True)
    return svc


def _build_mock_settings() -> MagicMock:
    settings = MagicMock(name="Settings")
    settings.google_maps_api_key = ""
    settings.workspace_base_dir = "data/workspaces"
    settings.compaction_enabled = False
    settings.compaction_threshold = 100000
    return settings
