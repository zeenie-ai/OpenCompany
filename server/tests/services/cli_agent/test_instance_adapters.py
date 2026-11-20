from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from services.agent_instances.admission import AdmissionGate, AdmissionHalted, get_gate, is_invalid_request
from services.cli_agent.context_bridge import AgentPoolKey, SpecializedAgentContextBridge
from services.cli_agent.providers.openai_codex import OpenAICodexProvider
from services.cli_agent.types import CodexTaskSpec
from services.plugin.tool import ToolBinding, binding_input_model


def test_gate_restores_and_deduplicates_only_classified_invalid_requests():
    gate = AdmissionGate()
    gate.record_invalid("stale_reference", "stale")
    gate.record_invalid("invalid_tool_input", "one")
    gate.record_invalid("invalid_tool_input", "one")
    gate.admit("unrelated successful tool")
    restored = AdmissionGate(gate.serialize_state())
    restored.record_invalid("invalid_transition", "two")
    assert not restored.halted
    restored.record_invalid("invalid_target", "three")
    with pytest.raises(AdmissionHalted):
        restored.admit("model")
    assert restored.invalid_count == 3
    assert is_invalid_request({"error_type": "invalid_tool_input"})


def test_schema_only_binding_preserves_validation_and_required_fields():
    binding = ToolBinding(key="remote", name="remote", description="Remote tool", schema={
        "type": "object", "properties": {"instructions": {"type": "string", "minLength": 1}},
        "required": ["instructions"], "additionalProperties": False})
    model = binding_input_model(binding)
    assert model.model_json_schema()["required"] == ["instructions"]
    assert model.model_validate({"instructions": "work"}).model_dump(exclude_unset=True) == {"instructions": "work"}
    for invalid in ({}, {"instructions": ""}, {"instructions": 4}, {"instructions": "work", "scope": "foreign"}):
        with pytest.raises(ValidationError):
            model.model_validate(invalid)


def test_typed_binding_keeps_exact_model():
    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        instructions: str
    binding = ToolBinding("spawn", "spawn_worker", "Spawn", Input.model_json_schema(),
                          args_schema=Input, execution_traits=frozenset({"starts_child"}))
    assert binding_input_model(binding) is Input


def test_runtime_capabilities_describe_adapter_limits():
    from services.agent_instances.capabilities import runtime_capabilities
    assert runtime_capabilities("codex_agent")["followup_delivery"] == "next_turn"
    assert runtime_capabilities("claude_code_agent")["continuation"] == "provider_session"
    assert runtime_capabilities("rlm_agent")["repl_state"] == "ephemeral"
    assert runtime_capabilities("rlm_agent")["stop_behavior"] == "effects_fence_and_drain"
    assert runtime_capabilities("coding_agent")["followup_delivery"] == "model_boundary"


def test_codex_explicit_resume_keeps_config_and_exact_thread(monkeypatch):
    provider = OpenAICodexProvider()
    monkeypatch.setattr(provider, "binary_path", lambda: "codex")
    argv = provider.interactive_argv(CodexTaskSpec(prompt="follow up", resume_session_id="exact-thread"), defaults={})
    assert argv[:4] == ["codex", "exec", "resume", "--json"]
    assert argv[-3:] == ["--", "exact-thread", "follow up"]
    assert 'sandbox_mode="workspace-write"' in argv
    assert "--last" not in argv and "--sandbox" not in argv


@pytest.mark.asyncio
async def test_instance_bridge_isolated_and_checkpoint_is_separate(monkeypatch):
    service = SimpleNamespace(get_instance=AsyncMock(return_value={"workflow_id": "wf", "generation": 1, "continuation": {}}),
        get_transcript=AsyncMock(return_value={"messages": [], "revision": 2, "owning_run_id": "run"}),
        checkpoint=AsyncMock(return_value={"revision": 3}))
    monkeypatch.setattr("services.agent_instances.service.AgentInstanceService", lambda db: service)
    descriptor = {"kind": "context", "agent_instance_id": "one", "agent_run_id": "run", "agent_delivery_ids": ["delivery"]}
    bridge = await SpecializedAgentContextBridge.resolve(object(), descriptor, provider="codex", agent_node_id="template")
    assert isinstance(bridge.pool_key, AgentPoolKey)
    assert bridge.pool_key.instance_id == "one"
    checkpoint = bridge.turn_checkpoint("question", "answer" * 2000)
    assert checkpoint["expected_revision"] == 2 and checkpoint["delivery_ids"] == ["delivery"]
    assert len(checkpoint["messages"][-1]["content"]) > 4000
    service.checkpoint.assert_not_awaited()
    second = await SpecializedAgentContextBridge.resolve(object(), {**descriptor, "agent_instance_id": "two"}, provider="codex", agent_node_id="template")
    assert bridge.pool_key != second.pool_key


@pytest.mark.asyncio
async def test_instance_continuation_never_silently_moves_worker(monkeypatch):
    service = SimpleNamespace(get_instance=AsyncMock(return_value={"continuation": {
        "provider": "codex", "provider_session_id": "thread", "storage_owner": "different-worker"}}))
    monkeypatch.setattr("services.agent_instances.service.AgentInstanceService", lambda db: service)
    bridge = SpecializedAgentContextBridge(object(), "wf", 1, "node", "codex", instance_id="instance", run_id="run")
    with pytest.raises(ValueError, match="continuation_unavailable"):
        await bridge.provider_session_id()


def test_failed_opaque_turn_without_response_does_not_consume_deliveries():
    bridge = SpecializedAgentContextBridge(object(), "wf", 1, "node", "codex", instance_id="instance", run_id="run",
                                           delivery_ids=("message",))
    failed = bridge.turn_checkpoint("work", "", response_recorded=False)
    assert failed["delivery_ids"] == []
    assert [message["role"] for message in failed["messages"]] == ["user"]
    succeeded = bridge.turn_checkpoint("work", "", response_recorded=True)
    assert succeeded["delivery_ids"] == ["message"]
    assert succeeded["messages"][-1]["role"] == "assistant"


@pytest.mark.asyncio
@pytest.mark.parametrize("node_type", ["claude_code_agent", "codex_agent", "rlm_agent"])
async def test_node_executor_preserves_opaque_checkpoint_and_full_result(node_type, harness, monkeypatch):
    from pathlib import Path
    from services.cli_agent.protocol import SessionResult
    import importlib

    module = importlib.import_module(f"nodes.agent.{node_type}")
    monkeypatch.setattr(module, "collect_agent_connections", AsyncMock(return_value=(None, [], [], None, None)))
    transcript = {"messages": [{"role": "assistant", "content": "x" * 5000}], "expected_revision": 2,
                  "delivery_ids": ["delivery"], "request_id": "opaque-turn"}
    admission = {"invalid_count": 1, "reason": ""}
    if node_type == "rlm_agent":
        harness.ai_service.rlm_service = SimpleNamespace(execute=AsyncMock(return_value={
            "success": True, "result": {"response": "x" * 5000},
            "agent_transcript": transcript, "agent_admission_state": admission}))
    else:
        result = SimpleNamespace(tasks=[SessionResult(task_id="task", session_id="provider", response="x" * 5000,
                success=True, cost_usd=0)], n_tasks=1, n_succeeded=1, n_failed=0, total_cost_usd=0,
                wall_clock_ms=1, provider="claude" if node_type == "claude_code_agent" else "codex", timestamp="now",
                agent_transcript=transcript, agent_admission_state=admission)
        monkeypatch.setattr("services.cli_agent.service.get_ai_cli_service", lambda: SimpleNamespace(run_batch=AsyncMock(return_value=result)))
    context = harness.build_context(workspace_dir=str(Path.cwd()), extra={"agent_instance_id": "instance", "agent_run_id": "run"})
    result = await harness.execute(node_type, {"prompt": "work"}, context=context)
    assert result["success"], result
    assert result["result"]["agent_transcript"] == transcript
    assert result["result"]["agent_admission_state"] == admission
    assert len(result["result"]["response"]) == 5000
