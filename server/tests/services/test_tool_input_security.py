"""ToolInput and locked-schema security contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict

from nodes.tool.current_time_tool import CurrentTimeToolNode
from nodes.tool.simple_memory import (
    SimpleMemoryNode,
    SimpleMemoryParams,
    SimpleMemoryToolInput,
)
from services.agent_runtime import AgentToolSpec, _validated_tool_args
from services.ai import AIService
from services.llm.protocol import ToolCall, ToolDef
from services.plugin import ActionNode, NodeContext, Operation, ToolNode
from services.ws_handler_registry import get_ws_handlers


class _ConfiguredToolParams(BaseModel):
    endpoint: str
    model_config = ConfigDict(extra="forbid")


class _ConfiguredToolOutput(BaseModel):
    endpoint: str
    query: str


class _DefaultedToolInput(BaseModel):
    query: str
    configured_limit: int = 25


class _ValidationItem(BaseModel):
    count: int


class _ValidationParams(BaseModel):
    items: list[_ValidationItem]


class _ValidationToolNode(ToolNode, abstract=True):
    type = "_testValidationFeedback"
    Params = _ValidationParams

    @Operation("run")
    async def run(self, ctx: NodeContext, params: _ValidationParams):
        raise AssertionError("Invalid input must never execute")


class _ConfiguredToolNode(ToolNode, abstract=True):
    type = "_testConfiguredToolInput"
    display_name = "Configured ToolInput Test"
    group = ("tool",)
    description = "Test-only ToolInput boundary"
    handles = (
        {
            "name": "output-tool",
            "kind": "output",
            "position": "top",
            "label": "Tool",
            "role": "tools",
        },
    )
    ui_hints = {"hideRunButton": True}
    Params = _ConfiguredToolParams
    ToolInput = _ConfiguredToolParams
    Output = _ConfiguredToolOutput
    server_controlled_fields = frozenset({"endpoint"})

    @Operation("run")
    async def run(
        self, ctx: NodeContext, params: _ConfiguredToolParams
    ) -> _ConfiguredToolOutput:
        return _ConfiguredToolOutput(
            endpoint=params.endpoint,
            query=str(ctx.raw.get("query") or ""),
        )


class _DualPurposeActionNode(ActionNode, abstract=True):
    type = "_testDualPurposeAction"
    usable_as_tool = True
    Params = _ConfiguredToolParams
    Output = _ConfiguredToolOutput

    @Operation("run")
    async def run(
        self, ctx: NodeContext, params: _ConfiguredToolParams
    ) -> _ConfiguredToolOutput:
        return _ConfiguredToolOutput(
            endpoint=params.endpoint,
            query=str(ctx.raw.get("query") or ""),
        )


def test_tool_input_defaults_to_params():
    assert CurrentTimeToolNode.ToolInput is CurrentTimeToolNode.Params
    assert CurrentTimeToolNode.tool_input_model() is CurrentTimeToolNode.Params


@pytest.mark.asyncio
@pytest.mark.parametrize("node_cls", [_ConfiguredToolNode, _DualPurposeActionNode])
async def test_node_error_recovery_metadata_reaches_agent_tools(node_cls):
    from services.plugin import NodeUserError

    class BlockedTool(node_cls, abstract=True):
        @Operation("run")
        async def run(self, ctx, params):
            raise NodeUserError("Spending cap reached", hint="Review billing", requires_user_action=True)

    result = await BlockedTool().execute_as_tool(
        {"endpoint": "test"}, {"endpoint": "test"},
        NodeContext(node_id="tool-1", node_type=BlockedTool.type, raw={}),
    )
    assert result == {
        "success": False, "error_type": "NodeUserError", "error": "Spending cap reached",
        "hint": "Review billing", "requires_user_action": True, "retryable": False,
    }


def test_native_validation_does_not_materialize_omitted_defaults():
    spec = AgentToolSpec(
        definition=ToolDef(
            name="defaulted",
            description="test",
            parameters=_DefaultedToolInput.model_json_schema(),
        ),
        args_schema=_DefaultedToolInput,
    )
    args, error = _validated_tool_args(
        ToolCall(id="call-1", name="defaulted", args={"query": "hello"}),
        {"defaulted": spec},
    )
    assert error is None
    assert args == {"query": "hello"}


@pytest.mark.parametrize("item", [{"count": "private-value"}, {"credential": "private-value"}])
@pytest.mark.parametrize("boundary", ["native", "tool", "node"])
async def test_validation_feedback_identifies_nested_field_without_input_values(item, boundary):
    arguments = {"items": [item]}
    if boundary == "native":
        spec = AgentToolSpec(
            definition=ToolDef(name="validate", description="test", parameters=_ValidationParams.model_json_schema()),
            args_schema=_ValidationParams,
        )
        value, error = _validated_tool_args(
            ToolCall(id="invalid", name="validate", args=arguments), {"validate": spec},
        )
        assert value is None
    else:
        node = _ValidationToolNode()
        context = NodeContext(node_id="validate", node_type=node.type)
        if boundary == "tool":
            result = await node.execute_as_tool(arguments, {}, context)
        else:
            result = await node.execute(context.node_id, arguments, context)
        assert result["success"] is False
        assert result["error_type"] == "ValidationError"
        error = result["error"]
    assert "items.0.count:" in error
    assert "private-value" not in error
    assert "credential" not in error
    assert "input_value" not in error
    assert "errors.pydantic.dev" not in error


def test_task_manager_stored_schema_cannot_replace_locked_input():
    from nodes.tool.task_manager import TaskManagerNode, TaskManagerToolInput

    malicious = {"db_schema_config": {"fields": {"workflow_id": {"type": "string", "required": True}}}}
    assert AIService._get_tool_schema(object(), "taskManager", malicious) is TaskManagerToolInput
    assert TaskManagerNode.as_tool_schema()["parameters"]["additionalProperties"] is False


def test_simple_memory_has_split_locked_schema_and_canonical_name():
    assert SimpleMemoryNode.Params is SimpleMemoryParams
    assert SimpleMemoryNode.ToolInput is SimpleMemoryToolInput
    assert SimpleMemoryNode.tool_schema_locked is True
    assert SimpleMemoryNode.tool_name == "memory"
    schema = SimpleMemoryNode.as_tool_schema()["parameters"]
    assert "operation" in schema["properties"]
    assert "reset_policy" not in schema["properties"]


def test_stored_custom_schema_cannot_replace_memory_tool_input():
    malicious = {
        "db_schema_config": {
            "fields": {
                "workflow_id": {
                    "type": "string",
                    "required": True,
                }
            }
        }
    }
    resolved = AIService._get_tool_schema(
        object(), "simpleMemory", malicious
    )
    assert resolved is SimpleMemoryToolInput
    assert "workflow_id" not in resolved.model_fields


def test_memory_panel_handlers_are_plugin_registered():
    handlers = get_ws_handlers()
    assert {
        "list_memory_items",
        "get_memory_item",
        "remember_memory",
        "update_memory_item",
        "forget_memory_item",
        "clear_memory_items",
    }.issubset(handlers)


async def test_memory_builder_ignores_stored_name_and_schema():
    class _Database:
        async def get_tool_schema(self, _node_id):
            return {
                "tool_name": "steal_scope",
                "tool_description": "untrusted",
                "schema_config": {
                    "fields": {
                        "workflow_id": {
                            "type": "string",
                            "required": True,
                        }
                    }
                },
            }

    service = AIService.__new__(AIService)
    service.database = _Database()
    tool, _config = await service._build_tool_from_node(
        {
            "node_id": "memory-node",
            "node_type": "simpleMemory",
            "parameters": {"reset_policy": "preserve"},
            "label": "Memory",
        }
    )
    assert tool.definition.name == "memory"
    assert "operation" in tool.definition.parameters["properties"]
    assert "workflow_id" not in tool.definition.parameters["properties"]


async def test_model_arguments_cannot_override_server_controlled_config():
    node = _ConfiguredToolNode()
    context = NodeContext(
        node_id="configured-tool",
        node_type=_ConfiguredToolNode.type,
        raw={"query": "hello"},
    )
    result = await node.execute_as_tool(
        {"endpoint": "https://attacker.invalid"},
        {"endpoint": "https://trusted.internal"},
        context,
    )
    assert result["endpoint"] == "https://trusted.internal"


async def test_action_node_tools_keep_their_existing_merge_contract():
    node = _DualPurposeActionNode()
    result = await node.execute_as_tool(
        {"endpoint": "https://model.example"},
        {"endpoint": "https://configured.example"},
        NodeContext(
            node_id="dual-purpose",
            node_type=_DualPurposeActionNode.type,
            raw={"query": "hello"},
        ),
    )
    assert result == {
        "endpoint": "https://model.example",
        "query": "hello",
    }


@pytest.mark.parametrize("node_cls", [_ConfiguredToolNode, _DualPurposeActionNode])
async def test_tool_errors_preserve_failure_and_classification(node_cls, monkeypatch):
    node = node_cls()
    envelope = {"success": False, "error": "Wait before continuing.", "error_type": "rate_limited", "retry_after": 3}
    monkeypatch.setattr(node, "execute", AsyncMock(return_value=envelope))
    result = await node.execute_as_tool({"endpoint": "https://configured.example"}, {"endpoint": "https://configured.example"}, NodeContext(node_id="test", node_type=node_cls.type))
    assert result == {"success": False, "error": "Wait before continuing.", "error_type": "rate_limited"}
    assert envelope["success"] is False and envelope["retry_after"] == 3


@pytest.mark.parametrize("node_cls", [_ConfiguredToolNode, _DualPurposeActionNode])
@pytest.mark.parametrize("failure", [RuntimeError("Unexpected operation failure"), ValueError("Invalid state")])
async def test_unexpected_tool_failures_retain_their_type(node_cls, failure):
    class FailingTool(node_cls, abstract=True):
        @Operation("run")
        async def run(self, ctx, params):
            raise failure

    result = await FailingTool().execute_as_tool(
        {"endpoint": "test"}, {"endpoint": "test"}, NodeContext(node_id="test", node_type=node_cls.type),
    )

    assert result == {"success": False, "error": str(failure), "error_type": type(failure).__name__}


@pytest.mark.parametrize("node_cls", [_ConfiguredToolNode, _DualPurposeActionNode])
async def test_tool_errors_copy_only_declared_non_null_fields(node_cls, monkeypatch):
    monkeypatch.setattr(node_cls, "tool_error_fields", frozenset({"success", "error_type", "retry_after", "next_action", "url", "absent"}))
    node = node_cls()
    envelope = {
        "success": False, "error": "Inspect the page.", "error_type": "outcome_unknown",
        "retry_after": 0, "next_action": "snapshot", "url": None,
        "internal_token": "do-not-expose", "result": {"private": "payload"},
    }
    monkeypatch.setattr(node, "execute", AsyncMock(return_value=envelope))
    result = await node.execute_as_tool({"endpoint": "https://configured.example"}, {"endpoint": "https://configured.example"}, NodeContext(node_id="test", node_type=node_cls.type))
    assert result == {
        "success": False, "error": "Inspect the page.", "error_type": "outcome_unknown",
        "retry_after": 0, "next_action": "snapshot",
    }
    assert "internal_token" in envelope and "url" in envelope


@pytest.mark.parametrize("node_cls", [_ConfiguredToolNode, _DualPurposeActionNode])
async def test_opted_in_error_metadata_does_not_change_success_results(node_cls, monkeypatch):
    monkeypatch.setattr(node_cls, "tool_error_fields", frozenset({"success", "error_type"}))
    node = node_cls()
    payload = {"endpoint": "https://configured.example", "query": "hello", "error": "a field in the successful result"}
    envelope = payload if issubclass(node_cls, ToolNode) else {"success": True, "result": payload}
    monkeypatch.setattr(node, "execute", AsyncMock(return_value=envelope))
    assert await node.execute_as_tool({"endpoint": "https://configured.example"}, {"endpoint": "https://configured.example"}, NodeContext(node_id="test", node_type=node_cls.type)) == payload


async def test_memory_panel_scope_is_resolved_from_workflow_and_auth(
    monkeypatch,
):
    from nodes.tool.simple_memory import _handlers

    class _Database:
        async def get_workflow(self, workflow_id):
            assert workflow_id == "workflow-1"
            return SimpleNamespace(
                data={
                    "owner_id": "authenticated-user",
                    "nodes": [
                        {"id": "memory-1", "type": "simpleMemory"}
                    ]
                }
            )

    monkeypatch.setattr(_handlers, "get_database", lambda: _Database())
    websocket = SimpleNamespace(
        state=SimpleNamespace(user_id="authenticated-user"),
        scope={},
    )
    _store, scope = await _handlers._resolve_store_and_scope(
        {
            "workflow_id": "workflow-1",
            "memory_node_id": "memory-1",
            "namespace_id": "client-chosen-namespace",
            "user_id": "client-chosen-user",
        },
        websocket,
    )
    assert scope.workflow_id == "workflow-1"
    assert scope.memory_node_id == "memory-1"
    assert scope.owner_id == "authenticated-user"
    assert scope.namespace_id != "client-chosen-namespace"


class _LockedModeParams(BaseModel):
    query: str = ""
    mode: str = "safe"


class _LockedModeOutput(BaseModel):
    query: str
    mode: str


class _LockedLegacyToolNode(ToolNode, abstract=True):
    type = "_testLockedLegacyTool"
    display_name = "Locked Legacy Tool Test"
    group = ("tool",)
    description = "Test-only locked field on a ToolInput-is-Params tool"
    handles = _ConfiguredToolNode.handles
    ui_hints = {"hideRunButton": True}
    Params = _LockedModeParams
    ToolInput = _LockedModeParams
    Output = _LockedModeOutput
    server_controlled_fields = frozenset({"mode"})

    @Operation("run")
    async def run(self, ctx: NodeContext, params: _LockedModeParams) -> _LockedModeOutput:
        return _LockedModeOutput(query=params.query, mode=params.mode)


class _LockedDualPurposeActionNode(ActionNode, abstract=True):
    type = "_testLockedDualPurposeAction"
    usable_as_tool = True
    Params = _LockedModeParams
    Output = _LockedModeOutput
    server_controlled_fields = frozenset({"mode"})

    @Operation("run")
    async def run(self, ctx: NodeContext, params: _LockedModeParams) -> _LockedModeOutput:
        return _LockedModeOutput(query=params.query, mode=params.mode)


async def test_locked_field_falls_back_to_its_default_not_the_model():
    result = await _LockedLegacyToolNode().execute_as_tool(
        {"query": "hello", "mode": "unsafe"},
        {},
        NodeContext(node_id="locked-tool", node_type=_LockedLegacyToolNode.type, raw={}),
    )
    assert result == {"query": "hello", "mode": "safe"}


@pytest.mark.parametrize("saved", [{"mode": "saved"}, {}])
async def test_action_node_tools_cannot_set_locked_fields(saved):
    result = await _LockedDualPurposeActionNode().execute_as_tool(
        {"query": "hello", "mode": "unsafe"},
        saved,
        NodeContext(node_id="locked-action", node_type=_LockedDualPurposeActionNode.type, raw={}),
    )
    assert result == {"query": "hello", "mode": saved.get("mode", "safe")}


def _capture_dispatch(harness, monkeypatch):
    captured: dict = {}

    async def fake_dispatch(node_id, node_type, params, ctx):
        captured["params"] = params
        captured["ctx"] = ctx
        return {"success": True, "result": {}}

    monkeypatch.setattr(harness.executor, "_dispatch", fake_dispatch)
    return captured


@pytest.mark.parametrize(
    ("node_type", "field", "saved", "model"),
    [
        ("discordSend", "account_id", "default", "attacker-bot"),
        ("browser", "interaction", "read_only", "full"),
        ("browser", "profile_id", "owner-profile", "someone-elses-profile"),
    ],
)
async def test_temporal_tool_calls_cannot_override_locked_settings(harness, monkeypatch, node_type, field, saved, model):
    # The agent workflow hands the per-type activity ``{**tool params, **call
    # args}`` plus the raw call args as ``tool_args``; the saved setting must win.
    harness.database.get_node_parameters = AsyncMock(return_value={field: saved})
    captured = _capture_dispatch(harness, monkeypatch)
    model_args = {field: model, "note": "from the model"}

    await harness.executor.execute(
        node_id="tool-1",
        node_type=node_type,
        parameters=dict(model_args),
        context=harness.build_context(extra={"tool_args": dict(model_args)}),
    )

    assert captured["params"][field] == saved
    assert captured["params"]["note"] == "from the model"
    assert field not in captured["ctx"]["tool_args"]
    assert captured["ctx"]["tool_args"]["note"] == "from the model"


async def test_temporal_tool_call_drops_a_locked_field_that_was_never_saved(harness, monkeypatch):
    harness.database.get_node_parameters = AsyncMock(return_value={})
    captured = _capture_dispatch(harness, monkeypatch)

    await harness.executor.execute(
        node_id="discord-1",
        node_type="discordSend",
        parameters={"account_id": "attacker-bot"},
        context=harness.build_context(extra={"tool_args": {"account_id": "attacker-bot"}}),
    )

    assert "account_id" not in captured["params"]


async def test_locked_fields_bind_only_tool_calls(harness, monkeypatch):
    # A canvas run or a workflow edge is the operator's own configuration.
    harness.database.get_node_parameters = AsyncMock(return_value={"account_id": "default"})
    captured = _capture_dispatch(harness, monkeypatch)

    await harness.executor.execute(
        node_id="discord-1",
        node_type="discordSend",
        parameters={"account_id": "ops-bot"},
        context=harness.build_context(),
    )

    assert captured["params"]["account_id"] == "ops-bot"
