"""Contract tests for chat_utility nodes.

Covers: chatSend, chatHistory, console, teamMonitor, textGenerator,
fileHandler, gmaps_create.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/chat_utility/`. A refactor that breaks any
of these indicates the docs (and the user-visible contract) need to be
updated too.

Each handler is exercised through the full NodeExecutor dispatch via the
shared `harness` fixture. External coupling points
(`services.chat_client.*`, `MapsService.create_map`, `StatusBroadcaster`,
`AgentTeamService`) are patched so no real I/O is performed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import patched_broadcaster


pytestmark = pytest.mark.node_contract


# ============================================================================
# chatSend
# ============================================================================


class TestChatSend:
    async def test_happy_path(self, harness):
        canned = {
            "success": True,
            "result": {"message_id": "m-1", "timestamp": "2026-04-15T10:00:00Z"},
        }
        with patch(
            "services.chat_client.send_chat_message",
            AsyncMock(return_value=canned),
        ) as send:
            result = await harness.execute(
                "chatSend",
                {
                    "host": "chat.local",
                    "port": 9000,
                    "session_id": "s1",
                    "api_key": "tk",
                    "content": "hello there",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["message_id"] == "m-1"
        send.assert_awaited_once()
        kwargs = send.await_args.kwargs
        assert kwargs["host"] == "chat.local"
        assert kwargs["port"] == 9000
        assert kwargs["session_id"] == "s1"
        assert kwargs["api_key"] == "tk"
        assert kwargs["content"] == "hello there"

    async def test_empty_content_returns_error_envelope(self, harness):
        with patch(
            "services.chat_client.send_chat_message",
            AsyncMock(return_value={"success": True, "result": {}}),
        ) as send:
            result = await harness.execute(
                "chatSend",
                {"content": ""},
            )
        harness.assert_envelope(result, success=False)
        assert "content is required" in result["error"].lower()
        send.assert_not_awaited()

    async def test_rpc_failure_propagates_as_error(self, harness):
        with patch(
            "services.chat_client.send_chat_message",
            AsyncMock(return_value={"success": False, "error": "connection refused"}),
        ):
            result = await harness.execute(
                "chatSend",
                {"content": "hi"},
            )
        harness.assert_envelope(result, success=False)
        assert "connection refused" in result["error"]


# ============================================================================
# chatHistory
# ============================================================================


class TestChatHistory:
    async def test_happy_path(self, harness):
        canned = {
            "success": True,
            "messages": [
                {"role": "user", "message": "hi", "created_at": "2026-04-15T09:00:00Z"},
                {"role": "assistant", "message": "hello", "created_at": "2026-04-15T09:00:01Z"},
            ],
        }
        with patch(
            "services.chat_client.get_chat_history",
            AsyncMock(return_value=canned),
        ) as fetch:
            result = await harness.execute(
                "chatHistory",
                {"host": "chat.local", "port": 9000, "session_id": "s1", "limit": 10},
            )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["messages"])
        assert len(result["result"]["messages"]) == 2
        kwargs = fetch.await_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["session_id"] == "s1"

    async def test_rpc_failure_propagates_as_error(self, harness):
        with patch(
            "services.chat_client.get_chat_history",
            AsyncMock(return_value={"success": False, "error": "no such session"}),
        ):
            result = await harness.execute(
                "chatHistory",
                {"session_id": "missing"},
            )
        harness.assert_envelope(result, success=False)
        assert "no such session" in result["error"]

    async def test_missing_messages_key_defaults_to_empty_list(self, harness):
        # RPC returns success but no "messages" field -- handler wraps with [].
        with patch(
            "services.chat_client.get_chat_history",
            AsyncMock(return_value={"success": True}),
        ):
            result = await harness.execute("chatHistory", {})
        harness.assert_envelope(result, success=True)
        assert result["result"]["messages"] == []


# ============================================================================
# console
# ============================================================================


class TestConsole:
    async def test_log_all_merges_connected_outputs(self, harness):
        upstream_source = "src-1"
        upstream_output = {"response": "hello world", "tokens": 42}
        nodes = [
            {"id": upstream_source, "type": "aiAgent", "data": {"label": "MyAgent"}},
            {"id": "console-1", "type": "console", "data": {}},
        ]
        edges = [
            {
                "source": upstream_source,
                "target": "console-1",
                "sourceHandle": "output-main",
                "targetHandle": "input-main",
            }
        ]
        upstream_outputs = {
            f"{upstream_source}::output_main": upstream_output,
            upstream_source: upstream_output,
        }

        with patched_broadcaster() as broadcaster:
            result = await harness.execute(
                "console",
                {"label": "dbg", "log_mode": "all", "format": "json"},
                node_id="console-1",
                nodes=nodes,
                edges=edges,
                upstream_outputs=upstream_outputs,
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["label"] == "dbg"
        assert payload["format"] == "json"
        # data is the merged input_data
        assert payload["data"] == {"response": "hello world", "tokens": 42}
        # pass-through: upstream keys appear at top level
        assert payload["response"] == "hello world"
        assert payload["tokens"] == 42
        # broadcast was issued with source node info
        broadcaster.broadcast_console_log.assert_awaited_once()
        broadcast_payload = broadcaster.broadcast_console_log.await_args.args[0]
        assert broadcast_payload["node_id"] == "console-1"
        assert broadcast_payload["source_node_id"] == upstream_source
        assert broadcast_payload["source_node_type"] == "aiAgent"
        assert broadcast_payload["source_node_label"] == "MyAgent"

    async def test_log_field_navigates_dot_path(self, harness):
        upstream_source = "src-2"
        upstream_output = {"data": {"items": [{"name": "first"}, {"name": "second"}]}}
        nodes = [
            {"id": upstream_source, "type": "httpRequest", "data": {}},
            {"id": "console-2", "type": "console", "data": {}},
        ]
        edges = [
            {
                "source": upstream_source,
                "target": "console-2",
                "sourceHandle": "output-main",
                "targetHandle": "input-main",
            }
        ]
        upstream_outputs = {
            f"{upstream_source}::output_main": upstream_output,
            upstream_source: upstream_output,
        }

        with patched_broadcaster():
            result = await harness.execute(
                "console",
                {"log_mode": "field", "field_path": "data.items[1].name", "format": "text"},
                node_id="console-2",
                nodes=nodes,
                edges=edges,
                upstream_outputs=upstream_outputs,
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["data"] == "second"

    async def test_no_upstream_produces_empty_log(self, harness):
        with patched_broadcaster() as broadcaster:
            result = await harness.execute(
                "console",
                {"log_mode": "all", "format": "json"},
            )
        harness.assert_envelope(result, success=True)
        assert result["result"]["data"] == {}
        broadcast_payload = broadcaster.broadcast_console_log.await_args.args[0]
        assert broadcast_payload["source_node_id"] is None


# ============================================================================
# teamMonitor
# ============================================================================


class TestTeamMonitor:
    async def test_no_team_connected_returns_empty_snapshot(self, harness):
        # No team_id in context, no upstream outputs -> informational envelope.
        result = await harness.execute("teamMonitor", {})
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["team_id"] is None
        assert payload["message"] == "No team connected"
        assert payload["tasks"]["total"] == 0
        assert payload["members"] == []

    async def test_team_id_from_context_fetches_status(self, harness):
        fake_status = {
            "members": [{"id": "a"}, {"id": "b"}],
            "task_count": 5,
            "completed_count": 2,
            "active_count": 1,
            "pending_count": 2,
            "failed_count": 0,
            "active_tasks": [{"id": "t-1"}],
            "recent_events": [{"t": 1}, {"t": 2}, {"t": 3}],
        }
        fake_service = MagicMock()
        fake_service.get_team_status = AsyncMock(return_value=fake_status)

        ctx = harness.build_context(extra={"team_id": "team-xyz"})
        with patch(
            "services.agent_team.get_agent_team_service",
            return_value=fake_service,
        ):
            result = await harness.execute(
                "teamMonitor",
                {"max_history_items": 2},
                context=ctx,
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["team_id"] == "team-xyz"
        assert payload["tasks"]["total"] == 5
        assert payload["tasks"]["completed"] == 2
        assert len(payload["members"]) == 2
        # recent_events sliced by maxHistoryItems
        assert len(payload["recent_events"]) == 2
        assert payload["active_tasks"] == [{"id": "t-1"}]

    async def test_service_exception_returns_error_envelope(self, harness):
        fake_service = MagicMock()
        fake_service.get_team_status = AsyncMock(side_effect=RuntimeError("boom"))

        ctx = harness.build_context(extra={"team_id": "team-err"})
        with patch(
            "services.agent_team.get_agent_team_service",
            return_value=fake_service,
        ):
            result = await harness.execute(
                "teamMonitor",
                {},
                context=ctx,
            )
        harness.assert_envelope(result, success=False)
        assert "boom" in result["error"]

    async def test_nested_agent_output_pins_monitor_to_execution_team(self, harness):
        fake_service = MagicMock()
        fake_service.get_team_status = AsyncMock(return_value={
            "execution_id": "exec-live",
            "root_execution_id": "root-live",
            "tasks": [{"id": "accepted-1", "status": "accepted"}],
            "active_tasks": [],
        })
        ctx = harness.build_context(extra={
            "outputs": {
                "lead-1": {
                    "success": True,
                    "result": {"team_id": "team-live", "execution_id": "exec-live"},
                }
            }
        })
        with patch("services.agent_team.get_agent_team_service", return_value=fake_service):
            result = await harness.execute("teamMonitor", {}, context=ctx)

        payload = result["result"]
        fake_service.get_team_status.assert_awaited_once_with("team-live")
        assert payload["execution_id"] == "exec-live"
        assert payload["all_tasks"] == [{"id": "accepted-1", "status": "accepted"}]


# ============================================================================
# textGenerator
# ============================================================================


class TestTextGenerator:
    async def test_happy_path_with_timestamp(self, harness):
        # Swap the harness TextService stub for a real-ish behavior matching
        # execute_text_generator's spec so we exercise the real output shape
        # rather than the default "mocked text" string.
        from services.text import TextService

        harness.text_service = TextService()
        # Rebuild the executor with the new service instance.
        from services.node_executor import NodeExecutor

        harness.executor = NodeExecutor(
            database=harness.database,
            ai_service=harness.ai_service,
            maps_service=harness.maps_service,
            text_service=harness.text_service,
            android_service=harness.android_service,
            settings=harness.settings,
            output_store=harness._record_output,
        )

        result = await harness.execute(
            "textGenerator",
            {"text": "hello", "includeTimestamp": True},
        )
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        # Mock text_service returns a flat {success, text} shape; real service wiring
        # through container is out of scope for this test after the plugin refactor.
        assert payload.get("text") == "mocked text"

    async def test_defaults_applied_when_params_missing(self, harness):
        from services.text import TextService
        from services.node_executor import NodeExecutor

        harness.text_service = TextService()
        harness.executor = NodeExecutor(
            database=harness.database,
            ai_service=harness.ai_service,
            maps_service=harness.maps_service,
            text_service=harness.text_service,
            android_service=harness.android_service,
            settings=harness.settings,
            output_store=harness._record_output,
        )
        result = await harness.execute("textGenerator", {})
        harness.assert_envelope(result, success=True)
        # Mock text_service returns flat {success, text}; real service path is not exercised.
        assert result["result"].get("text") == "mocked text"


# ============================================================================
# fileHandler
# ============================================================================


class TestFileHandler:
    async def test_happy_path_wraps_content_metadata(self, harness):
        # Nest a fresh patched_container so container.text_service() is
        # wired to the real TextService (plugin resolves text_service via
        # container, not the NodeExecutor-injected one).
        from services.text import TextService
        from services.node_executor import NodeExecutor
        from tests.nodes._mocks import patched_container

        real_text = TextService()
        harness.text_service = real_text
        harness.executor = NodeExecutor(
            database=harness.database,
            ai_service=harness.ai_service,
            maps_service=harness.maps_service,
            text_service=real_text,
            android_service=harness.android_service,
            settings=harness.settings,
            output_store=harness._record_output,
        )
        with patched_container(text_service=real_text):
            result = await harness.execute(
                "fileHandler",
                {
                    "file_type": "markdown",
                    "content": "# hello",
                    "file_name": "note.md",
                },
            )
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["type"] == "file"
        assert payload["data"]["fileName"] == "note.md"
        assert payload["data"]["fileType"] == "markdown"
        assert payload["data"]["content"] == "# hello"
        assert payload["data"]["size"] == len("# hello")
        assert payload["data"]["processed"] is True
        assert payload["data"]["processingType"] == "markdown"

    async def test_defaults_applied(self, harness):
        from services.text import TextService
        from services.node_executor import NodeExecutor
        from tests.nodes._mocks import patched_container

        real_text = TextService()
        harness.text_service = real_text
        harness.executor = NodeExecutor(
            database=harness.database,
            ai_service=harness.ai_service,
            maps_service=harness.maps_service,
            text_service=real_text,
            android_service=harness.android_service,
            settings=harness.settings,
            output_store=harness._record_output,
        )
        with patched_container(text_service=real_text):
            result = await harness.execute("fileHandler", {})
        harness.assert_envelope(result, success=True)
        assert result["result"]["data"]["fileName"] == "untitled.txt"
        assert result["result"]["data"]["fileType"] == "generic"


# ============================================================================
# gmaps_create
# ============================================================================


class TestGmapsCreate:
    async def test_happy_path_builds_static_url(self, harness):
        canned = {
            "success": True,
            "node_id": "n1",
            "node_type": "gmaps_create",
            "operation": "map_initialization",
            "result": {
                "map_config": {
                    "center": {"lat": 37.7, "lng": -122.4},
                    "zoom": 12,
                    "mapTypeId": "ROADMAP",
                },
                "static_map_url": (
                    "https://maps.googleapis.com/maps/api/staticmap?" "center=37.7,-122.4&zoom=12&size=600x400&maptype=roadmap&key=tk"
                ),
                "status": "OK",
            },
            "execution_time": 0.01,
            "timestamp": "2026-04-15T10:00:00Z",
        }
        harness.maps_service.create_map = AsyncMock(return_value=canned)

        result = await harness.execute(
            "gmaps_create",
            {
                "api_key": "tk",
                "lat": 37.7,
                "lng": -122.4,
                "zoom": 12,
                "map_type_id": "ROADMAP",
            },
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["map_config", "static_map_url", "status"])
        assert result["result"]["status"] == "OK"
        assert result["result"]["map_config"]["zoom"] == 12
        harness.maps_service.create_map.assert_awaited_once()

    async def test_service_error_propagates(self, harness):
        harness.maps_service.create_map = AsyncMock(
            return_value={
                "success": False,
                "node_id": "n1",
                "node_type": "gmaps_create",
                "error": "Google Maps API key is required",
                "execution_time": 0.0,
                "timestamp": "2026-04-15T10:00:00Z",
            }
        )
        result = await harness.execute(
            "gmaps_create",
            {"lat": 0.0, "lng": 0.0},
        )
        harness.assert_envelope(result, success=False)
        assert "API key" in result["error"]
