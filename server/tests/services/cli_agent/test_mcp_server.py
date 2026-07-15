"""MCP server integration tests.

Covers:
  - Bearer-token registry: register/lookup/unregister
  - 401 on missing / malformed / wrong / expired tokens
  - Per-batch scoping: tools see the right `BatchContext`
  - Lockfile format (VSCode-shape) + stale-PID sweep
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from services.cli_agent.lockfile import (
    list_active_lockfiles,
    lockfile_path,
    remove_ide_lockfile,
    sweep_stale_lockfiles,
    write_ide_lockfile,
)
from services.cli_agent.mcp_server import (
    BatchContext,
    _reset_for_tests,
    active_batch_count,
    get_mcp_app,
    issue_token,
    lookup_batch,
    rebind_batch,
    register_batch,
    unregister_batch,
)
from services.tool_identity import DuplicateToolNameError


# ---------------------------------------------------------------------------
# Token registry
# ---------------------------------------------------------------------------


class TestTokenRegistry:
    def setup_method(self):
        _reset_for_tests()

    def test_issue_token_is_random_64_hex(self):
        a = issue_token()
        b = issue_token()
        assert a != b
        assert len(a) == 64
        int(a, 16)  # parses as hex

    def test_register_lookup_unregister(self):
        token = issue_token()
        ctx = BatchContext(
            workflow_id="wf",
            node_id="n",
            workspace_dir=Path("."),
        )
        assert lookup_batch(token) is None
        register_batch(token, ctx)
        assert lookup_batch(token) is ctx
        assert active_batch_count() == 1
        unregister_batch(token)
        assert lookup_batch(token) is None
        assert active_batch_count() == 0

    def test_unregister_idempotent(self):
        unregister_batch("nonexistent")  # should not raise

    def test_token_collision_with_different_ctx_raises(self):
        token = issue_token()
        ctx1 = BatchContext(workflow_id="a", node_id="n", workspace_dir=Path("."))
        ctx2 = BatchContext(workflow_id="b", node_id="n", workspace_dir=Path("."))
        register_batch(token, ctx1)
        with pytest.raises(ValueError, match="collision"):
            register_batch(token, ctx2)

    def test_duplicate_effective_names_fail_before_registration(self):
        token = issue_token()
        ctx = BatchContext(
            workflow_id="wf",
            node_id="agent-a",
            workspace_dir=Path("."),
            connected_tools=[
                {"node_id": "tool-a", "node_type": "httpRequest", "label": "HTTP A"},
                {"node_id": "tool-b", "node_type": "httpRequest", "label": "HTTP B"},
            ],
        )

        with pytest.raises(DuplicateToolNameError) as raised:
            register_batch(token, ctx)

        assert lookup_batch(token) is None
        assert active_batch_count() == 0
        assert [item["node_id"] for item in raised.value.conflicts["httpRequest"]] == [
            "tool-a",
            "tool-b",
        ]

    def test_duplicate_rebind_leaves_existing_context_unchanged(self):
        token = issue_token()
        ctx = BatchContext(
            workflow_id="wf-old",
            node_id="agent-old",
            execution_id="run-old",
            workspace_dir=Path("."),
            connected_tools=[{"node_id": "tool-a", "node_type": "httpRequest"}],
        )
        register_batch(token, ctx)

        with pytest.raises(DuplicateToolNameError):
            rebind_batch(
                token,
                workflow_id="wf-new",
                node_id="agent-new",
                execution_id="run-new",
                connected_tools=[
                    {"node_id": "tool-b", "node_type": "calculatorTool"},
                    {"node_id": "tool-c", "node_type": "calculatorTool"},
                ],
            )

        assert ctx.workflow_id == "wf-old"
        assert ctx.node_id == "agent-old"
        assert ctx.execution_id == "run-old"
        assert ctx.connected_tools == [{"node_id": "tool-a", "node_type": "httpRequest"}]

    def test_same_name_is_valid_on_different_agents(self):
        token_a = issue_token()
        token_b = issue_token()
        register_batch(
            token_a,
            BatchContext(
                workflow_id="wf",
                node_id="agent-a",
                workspace_dir=Path("."),
                connected_tools=[{"node_id": "tool-a", "node_type": "httpRequest"}],
            ),
        )
        register_batch(
            token_b,
            BatchContext(
                workflow_id="wf",
                node_id="agent-b",
                workspace_dir=Path("."),
                connected_tools=[{"node_id": "tool-b", "node_type": "httpRequest"}],
            ),
        )

        assert active_batch_count() == 2


# ---------------------------------------------------------------------------
# ASGI auth middleware
# ---------------------------------------------------------------------------


class TestAuthMiddleware:
    def setup_method(self):
        _reset_for_tests()

    @pytest.mark.asyncio
    async def test_no_authorization_header_returns_401(self):
        app = get_mcp_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_malformed_authorization_returns_401(self):
        app = get_mcp_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": "Token abc"},
            )
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_token_returns_401(self):
        app = get_mcp_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": "Bearer unknown_token"},
            )
            assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_token_returns_401(self):
        """Token registered then unregistered behaves as unknown."""
        token = issue_token()
        register_batch(
            token,
            BatchContext(
                workflow_id="wf",
                node_id="n",
                workspace_dir=Path("."),
            ),
        )
        unregister_batch(token)
        app = get_mcp_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 401


# ---------------------------------------------------------------------------
# Lockfile format
# ---------------------------------------------------------------------------


class TestLockfile:
    def test_claude_lockfile_path_pid_lock(self, tmp_path):
        path = lockfile_path(
            ide_lockfile_dir=tmp_path,
            pid=12345,
            port=3010,
            ide_name="claude",
        )
        assert path.name == "12345.lock"

    def test_gemini_lockfile_path_includes_port(self, tmp_path):
        path = lockfile_path(
            ide_lockfile_dir=tmp_path,
            pid=12345,
            port=3010,
            ide_name="gemini",
        )
        assert "12345" in path.name
        assert "3010" in path.name
        assert path.name.endswith(".json")

    def test_lockfile_payload_matches_vscode_shape(self, tmp_path):
        path = write_ide_lockfile(
            ide_lockfile_dir=tmp_path,
            pid=99999,
            port=3010,
            token="abc123",
            workspace_dir=tmp_path / "ws",
            ide_name="claude",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        # VSCode-style fields
        assert payload["port"] == 3010
        assert payload["authToken"] == "abc123"
        assert payload["ideName"] == "claude"
        assert "url" in payload
        assert "workspaceFolders" in payload
        assert payload["transport"] == "http"
        # Our extra: pid for the stale-sweep
        assert payload["pid"] == 99999

    def test_default_url_constructed(self, tmp_path):
        path = write_ide_lockfile(
            ide_lockfile_dir=tmp_path,
            pid=1,
            port=3010,
            token="t",
            workspace_dir=tmp_path,
            ide_name="claude",
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        # FastMCP serves at `/mcp` of the sub-app, mounted at `/mcp/ide`.
        # The lockfile must advertise the absolute JSON-RPC endpoint.
        assert payload["url"] == "http://127.0.0.1:3010/mcp/ide/mcp"

    def test_remove_lockfile_safe_when_missing(self, tmp_path):
        # Should never raise
        remove_ide_lockfile(None)
        remove_ide_lockfile(tmp_path / "does-not-exist.lock")

    def test_sweep_removes_dead_pid_lockfile(self, tmp_path):
        # Find a guaranteed-dead PID by walking up high. PID 0 is the
        # system idle process on Windows and the swapper on POSIX, so
        # `psutil.pid_exists(0)` returns True everywhere — DON'T use 0.
        import psutil

        dead_pid = 99_999_999
        while psutil.pid_exists(dead_pid):
            dead_pid += 1
            if dead_pid > 999_999_999:  # paranoid bound
                pytest.skip("could not find a dead PID")

        write_ide_lockfile(
            ide_lockfile_dir=tmp_path,
            pid=dead_pid,
            port=3010,
            token="t",
            workspace_dir=tmp_path,
            ide_name="claude",
        )
        # Write one with the live PID
        live_path = write_ide_lockfile(
            ide_lockfile_dir=tmp_path,
            pid=os.getpid(),
            port=3010,
            token="t",
            workspace_dir=tmp_path,
            ide_name="claude",
        )

        before = list_active_lockfiles(tmp_path)
        assert len(before) >= 2

        n = sweep_stale_lockfiles(tmp_path)
        # Should have removed at least the dead-PID one
        assert n >= 1

        after = list_active_lockfiles(tmp_path)
        # Live one survives
        assert live_path in after

    def test_sweep_safe_on_nonexistent_dir(self, tmp_path):
        assert sweep_stale_lockfiles(tmp_path / "no") == 0
        assert sweep_stale_lockfiles(None) == 0


# ---------------------------------------------------------------------------
# OpenCompany workflow-tool bridge — per-batch dynamic FastMCP exposure.
# Each wired node lands as `mcp__opencompany__<node_type>`; FastMCP infers
# the schema from the typed `params` annotation (no custom translation).
#
# These tests drive FastMCP's own ``list_tools`` / ``call_tool`` API so
# we exercise the same code path the spawned ``claude`` CLI hits over
# JSON-RPC.
# ---------------------------------------------------------------------------


class TestOpenCompanyToolBridge:
    def setup_method(self):
        _reset_for_tests()
        import nodes  # noqa: F401 — populate plugin registry

    @staticmethod
    def _ctx(node_type: str, label: str) -> BatchContext:
        return BatchContext(
            workflow_id="wf_test",
            node_id="claude_code_agent_1",
            workspace_dir=Path("."),
            connected_tools=[
                {
                    "node_id": f"{node_type}_1",
                    "node_type": node_type,
                    "label": label,
                    "parameters": {},
                }
            ],
        )

    @pytest.mark.asyncio
    async def test_register_batch_exposes_tool_on_fastmcp(self):
        """The connected workflow node must surface in ``list_tools`` so
        claude sees ``mcp__opencompany__<node_type>`` on first
        ``tools/list``."""
        from services.cli_agent.mcp_server import get_mcp_app

        get_mcp_app()  # ensure FastMCP singleton built
        from services.cli_agent.mcp_server import _mcp_singleton as mcp

        assert mcp is not None

        ctx = self._ctx("calculatorTool", "Calculator")
        token = issue_token()
        register_batch(token, ctx)
        try:
            tools = await mcp.list_tools()
            names = {t.name for t in tools}
            assert "calculatorTool" in names
            calc = next(t for t in tools if t.name == "calculatorTool")
            # FastMCP infers the inputSchema from the typed `params` arg
            # (Pydantic-v2 → JSON Schema 2020-12 wire format).
            assert {"operation", "a", "b"}.issubset(calc.inputSchema["properties"].keys())
        finally:
            unregister_batch(token)

        # After unregister the tool refcount hits zero and FastMCP drops it.
        tools_after = await mcp.list_tools()
        assert "calculatorTool" not in {t.name for t in tools_after}

    @pytest.mark.asyncio
    async def test_call_tool_dispatches_via_execute_tool(self):
        """``mcp.call_tool`` for a wired node routes through
        ``services.handlers.tools.execute_tool`` and returns the
        plugin's result envelope."""
        from services.cli_agent.mcp_server import (
            _current_batch,
            get_mcp_app,
        )

        get_mcp_app()
        from services.cli_agent.mcp_server import _mcp_singleton as mcp

        ctx = self._ctx("calculatorTool", "Calculator")
        token = issue_token()
        register_batch(token, ctx)
        reset = _current_batch.set(ctx)
        try:
            out = await mcp.call_tool(
                "calculatorTool",
                {"operation": "multiply", "a": 2, "b": 21},
            )
        finally:
            _current_batch.reset(reset)
            unregister_batch(token)

        # FastMCP's `call_tool` returns either a content sequence or a
        # dict, depending on `structured_output`. Either way the
        # calculator's `result` (42) must be in there.
        text = repr(out)
        assert "42" in text, text

    @pytest.mark.asyncio
    async def test_unconnected_tool_call_is_blocked_by_batch_scope(self):
        """If a tool is registered globally (because some other batch
        wired it) but the calling batch didn't, the per-handler
        ``_require_batch`` check returns 403."""
        from services.cli_agent.mcp_server import _current_batch, get_mcp_app

        get_mcp_app()
        from services.cli_agent.mcp_server import _mcp_singleton as mcp

        # Batch A wires both tools — registers them globally.
        token_a = issue_token()
        register_batch(
            token_a,
            BatchContext(
                workflow_id="wf_a",
                node_id="cc_a",
                workspace_dir=Path("."),
                connected_tools=[
                    {"node_id": "c1", "node_type": "calculatorTool", "label": "C", "parameters": {}},
                    {"node_id": "h1", "node_type": "httpRequest", "label": "H", "parameters": {}},
                ],
            ),
        )
        # Batch B wires only one. Even though `httpRequest` is exposed
        # globally (refcount=1), batch B's ctx forbids it.
        ctx_b = self._ctx("calculatorTool", "C")
        token_b = issue_token()
        register_batch(token_b, ctx_b)
        reset = _current_batch.set(ctx_b)
        try:
            out = await mcp.call_tool(
                "httpRequest",
                {"method": "GET", "url": "x"},
            )
        finally:
            _current_batch.reset(reset)
            unregister_batch(token_b)
            unregister_batch(token_a)

        text = repr(out)
        assert "403" in text or "not connected" in text, text

    # -- live, real-tool runs (no mocks) ----------------------------------

    @pytest.mark.asyncio
    async def test_currentTimeTool_real(self):
        from services.cli_agent.mcp_server import _current_batch, get_mcp_app

        get_mcp_app()
        from services.cli_agent.mcp_server import _mcp_singleton as mcp

        ctx = self._ctx("currentTimeTool", "Time")
        token = issue_token()
        register_batch(token, ctx)
        reset = _current_batch.set(ctx)
        try:
            tools = {t.name for t in await mcp.list_tools()}
            assert "currentTimeTool" in tools
            out = await mcp.call_tool("currentTimeTool", {"timezone": "UTC"})
        finally:
            _current_batch.reset(reset)
            unregister_batch(token)
        text = repr(out)
        assert "UTC" in text, text

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_duckduckgoSearch_real(self):
        from services.cli_agent.mcp_server import _current_batch, get_mcp_app

        get_mcp_app()
        from services.cli_agent.mcp_server import _mcp_singleton as mcp

        ctx = self._ctx("duckduckgoSearch", "DDG")
        token = issue_token()
        register_batch(token, ctx)
        reset = _current_batch.set(ctx)
        try:
            try:
                out = await mcp.call_tool(
                    "duckduckgoSearch",
                    {"query": "OpenCompany Anthropic Claude", "max_results": 3},
                )
            except OSError as exc:
                pytest.skip(f"network unavailable: {exc}")
        finally:
            _current_batch.reset(reset)
            unregister_batch(token)
        text = repr(out)
        if "error" in text and "duckduckgo" in text.lower():
            pytest.skip(f"ddgs unavailable: {text[:200]}")
        assert "duckduckgo" in text.lower(), text[:200]

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_httpRequest_real(self):
        from services.cli_agent.mcp_server import _current_batch, get_mcp_app

        get_mcp_app()
        from services.cli_agent.mcp_server import _mcp_singleton as mcp

        ctx = self._ctx("httpRequest", "HTTP")
        token = issue_token()
        register_batch(token, ctx)
        reset = _current_batch.set(ctx)
        try:
            try:
                out = await mcp.call_tool(
                    "httpRequest",
                    {"method": "GET", "url": "https://httpbin.org/get?bridge=ok"},
                )
            except OSError as exc:
                pytest.skip(f"network unavailable: {exc}")
        finally:
            _current_batch.reset(reset)
            unregister_batch(token)
        text = repr(out)
        if '"status": 200' not in text and "'status': 200" not in text:
            pytest.skip(f"httpbin unavailable: {text[:200]}")
        assert "bridge" in text and "ok" in text, text[:300]
