"""RLM's plain-conversation Context boundary."""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from services.rlm.adapters import ToolBridgeAdapter
from services.rlm.service import RLMService


@pytest.mark.asyncio
async def test_rlm_loads_context_into_prompt_and_records_the_turn():
    bridge = MagicMock()
    bridge.record_turn = AsyncMock()
    bridge.augment_prompt = MagicMock(
        side_effect=lambda prompt: f"portable-history\n{prompt}"
    )
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value="secret")
    prompts = []

    fake_rlm = ModuleType("rlm")

    class RLM:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def completion(self, prompt):
            prompts.append(prompt)
            return SimpleNamespace(
                response=f"answer:{prompt}",
                metadata={"iterations": [{"index": 1}]},
                usage_summary=SimpleNamespace(
                    total_cost=0.01,
                    total_input_tokens=12,
                    total_output_tokens=3,
                ),
            )

    fake_rlm.RLM = RLM
    fake_logger = ModuleType("rlm.logger")
    fake_logger.RLMLogger = lambda: object()

    with (
        patch.dict(
            sys.modules,
            {"rlm": fake_rlm, "rlm.logger": fake_logger},
        ),
        patch(
            "services.cli_agent.context_bridge."
            "SpecializedAgentContextBridge.resolve",
            AsyncMock(return_value=bridge),
        ) as resolve,
        patch(
            "services.llm.config.is_model_valid_for_provider",
            return_value=True,
        ),
        patch(
            "services.rlm.service.BackendAdapter.adapt",
            return_value=("backend", {}),
        ),
        patch(
            "services.rlm.service.ChatModelExtractor.extract",
            AsyncMock(return_value=([], [])),
        ),
        patch(
            "services.rlm.service.ToolBridgeAdapter.bridge",
            return_value=[],
        ) as tool_bridge,
        patch(
            "services.skill_runtime.skill_tool_info",
            return_value=None,
        ),
    ):
        result = await RLMService(auth=auth).execute(
            "rlm-1",
            {
                "prompt": "solve",
                "provider": "openai",
                "model": "gpt-4o",
            },
            context_data={
                "kind": "context",
                "workflow_id": "wf",
                "context_node_id": "ctx",
                "generation": 2,
                "execution_id": "run-1",
            },
            workflow_id="wf",
            context={"execution_id": "run-1"},
            database=MagicMock(),
        )

    assert result["success"] is True
    resolve_kwargs = resolve.await_args.kwargs
    assert resolve_kwargs["provider"] == "rlm"
    assert resolve_kwargs["agent_node_id"] == "rlm-1"
    # The stored conversation was rendered into the prompt sent to RLM.
    assert prompts == ["portable-history\nsolve"]
    # The exchange was recorded with the ORIGINAL prompt, never the
    # augmented one — otherwise every save would nest the rendered
    # transcript inside itself.
    bridge.record_turn.assert_awaited_once_with(
        "solve", "answer:portable-history\nsolve"
    )
    assert tool_bridge.call_args.kwargs["ambiguous_outcome_sink"] is None
    assert "session_id" not in result["result"]
    assert "resume" not in result["result"]


class _RememberInput(BaseModel):
    content: str


@pytest.mark.asyncio
async def test_rlm_tool_timeout_cancels_and_records_exact_ambiguous_event(
    monkeypatch,
):
    cancelled = asyncio.Event()

    async def execute_tool(name, arguments, config):
        assert name == "memory"
        assert config["node_id"] == "memory-1"
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(
        "services.handlers.tools.execute_tool",
        execute_tool,
    )
    monkeypatch.setattr(
        ToolBridgeAdapter,
        "TOOL_TIMEOUT_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        ToolBridgeAdapter,
        "CANCELLATION_GRACE_SECONDS",
        1.0,
    )
    events = []

    async def record_ambiguous(payload):
        events.append(payload)

    tools = ToolBridgeAdapter.bridge(
        [
            {
                "node_type": "simpleMemory",
                "node_id": "memory-1",
                "label": "Memory",
                "parameters": {"reset_policy": "preserve"},
                "_agent_tool_name": "memory",
                "_agent_tool_input_model": _RememberInput,
                "_agent_tool_execution": {
                    "namespace": "owner:wf:memory-1"
                },
            }
        ],
        context={"execution_id": "run-1"},
        loop=asyncio.get_running_loop(),
        ambiguous_outcome_sink=record_ambiguous,
    )

    with pytest.raises(TimeoutError, match="outcome is ambiguous"):
        await asyncio.to_thread(
            tools["memory"]["tool"],
            content="exact durable value",
        )

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert events == [
        {
            "outcome": "ambiguous",
            "reason": "tool_timeout",
            "tool_name": "memory",
            "tool_node_id": "memory-1",
            "tool_node_type": "simpleMemory",
            "arguments": {"content": "exact durable value"},
            "timeout_seconds": 0.02,
            "cancel_requested": True,
            "cancellation_observed": True,
        }
    ]
