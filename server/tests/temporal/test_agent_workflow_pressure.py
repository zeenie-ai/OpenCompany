"""``AgentWorkflow`` under transcript pressure (``context_pressure_version`` 1).

Drives ``AgentWorkflow.run`` in-process with ``workflow.*`` patched, the style
of ``test_agent_llm_contract.py``. Every test records the messages each LLM
step was sent: that is what the model saw, and what the conversation store
saved for the next firing.

The incident these lock (docs-internal/errors.md, entry 28): uncapped tool
results grew the transcript past Temporal's payload warning, the token gate
never fired because it summed every request, and the saved conversation
outgrew the 1 MB seed cap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from services.llm.protocol import Message, ToolCall, message_to_wire
from services.temporal.agent_context_pressure import (
    CLEARED_TOOL_RESULT,
    CONTEXT_PRESSURE_VERSION,
    transcript_bytes,
)

SCRAPE_ACTIVITY = "node.tikhubAction.v1"
TRUNCATED = "[Tool result truncated"


def _tool(name: str, node_type: str, node_id: str) -> dict:
    return {
        "name": name,
        "definition": {
            "name": name,
            "description": f"Run {name}",
            "parameters": {"type": "object", "properties": {}},
        },
        "node_type": node_type,
        "version": 1,
        "task_queue": "default",
        "tool_node_id": node_id,
        "parameters": {},
        "tool_info": {"node_id": node_id, "node_type": node_type},
    }


def _payload(**overrides) -> dict:
    payload = {
        "node_id": "agent-1",
        "node_type": "aiAgent",
        "workflow_id": "graph-1",
        "session_id": "session-1",
        "provider": "openai",
        "model": "test-model",
        "max_tokens": 100,
        "temperature": 0,
        "system_message": "Be useful",
        "user_prompt": "research the account",
        "tools": [
            _tool("scrape", "tikhubAction", "scraper-1"),
            _tool("Skill", "_builtin_skill", "agent-1_skill_runtime"),
        ],
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": 10,
        "thinking_config": None,
        "compaction_threshold": None,
        "tool_result_max_chars": 100_000,
        "transcript_budget_bytes": 393_216,
        "context_pressure_version": CONTEXT_PRESSURE_VERSION,
    }
    payload.update(overrides)
    return payload


def _legacy(payload: dict) -> dict:
    """The same payload as recorded before the pressure keys existed."""
    for key in (
        "tool_result_max_chars",
        "transcript_budget_bytes",
        "context_pressure_version",
    ):
        payload.pop(key)
    return payload


def _usage(total: int = 110) -> dict:
    return {"input_tokens": total - 10, "output_tokens": 10, "total_tokens": total}


def _tool_turn(*calls, total: int = 110) -> dict:
    """An LLM step that calls each ``(call_id, tool_name)`` pair."""
    tool_calls = [ToolCall(id=call_id, name=name, args={}) for call_id, name in calls]
    return {
        "kind": "tool_calls",
        "assistant_message": message_to_wire(
            Message(role="assistant", tool_calls=tool_calls)
        ),
        "calls": [{"id": call_id, "name": name, "args": {}} for call_id, name in calls],
        "usage": _usage(total),
    }


def _final() -> dict:
    return {
        "kind": "final",
        "content": "done",
        "thinking": None,
        "assistant_message": message_to_wire(
            Message(role="assistant", content="done")
        ),
        "usage": _usage(),
    }


def _scraped(chars: int) -> dict:
    """A tool activity's envelope around a result of about ``chars``."""
    return {"success": True, "result": {"data": "x" * chars}}


class _Run:
    """Scripted activities for one ``AgentWorkflow.run``."""

    def __init__(self, payload: dict, turns: List[dict], tools: Dict[str, Any]):
        self.payload = payload
        self.turns = list(turns)
        self.tools = tools
        self.sent: List[List[dict]] = []
        self.compactions: List[dict] = []

    async def execute_activity(self, name, *, args, **_kwargs):
        if name == "agent.prepare_payload":
            return self.payload
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            self.sent.append([dict(message) for message in args[0]["messages"]])
            return self.turns.pop(0)
        if name == "agent.compact_context":
            self.compactions.append(args[0])
            return {
                "success": True,
                "summary": "The account was researched across several calls.",
                "usage": _usage(40),
            }
        if name in self.tools:
            return self.tools[name]
        if name in ("agent.store_output", "agent.skill.clear"):
            return {"ok": True}
        raise AssertionError(f"Unexpected activity {name}")


@pytest.fixture
def patched_workflow(monkeypatch):
    import services.temporal.agent_workflow as workflow_module

    temporal_workflow = workflow_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="agent-run-1", run_id="run-id-12345678"),
    )
    monkeypatch.setattr(
        workflow_module,
        "get_node_class",
        lambda _node_type: None,
    )
    return temporal_workflow


async def _run(monkeypatch, patched_workflow, run: _Run) -> dict:
    from services.temporal.agent_workflow import AgentWorkflow

    monkeypatch.setattr(patched_workflow, "execute_activity", run.execute_activity)
    result = await AgentWorkflow().run({"node_id": "agent-1", "execution_id": "root-1"})
    assert result["success"] is True, result
    return result


def _tool_messages(messages: List[dict]) -> List[dict]:
    return [message for message in messages if message.get("role") == "tool"]


class TestToolResultCap:
    @pytest.mark.asyncio
    async def test_an_external_result_is_cut_before_the_model_reads_it(
        self, monkeypatch, patched_workflow
    ):
        run = _Run(
            _payload(tool_result_max_chars=1_000),
            [_tool_turn(("c1", "scrape")), _final()],
            {SCRAPE_ACTIVITY: _scraped(5_000)},
        )

        await _run(monkeypatch, patched_workflow, run)

        (result,) = _tool_messages(run.sent[1])
        assert result["content"].startswith('{"data": "xxx')
        assert "showing the first 1,000 of 5,012 characters" in result["content"]
        assert len(result["content"]) < 1_200
        # The repeated tool_result block carries the cut text, not the whole.
        assert result["blocks"][0]["text"] == result["content"]

    @pytest.mark.asyncio
    async def test_skill_loads_are_never_cut(self, monkeypatch, patched_workflow):
        instructions = {"status": "loaded", "instructions": "i" * 5_000}
        run = _Run(
            _payload(tool_result_max_chars=1_000),
            [_tool_turn(("c1", "Skill")), _final()],
            {"agent.skill.invoke": instructions},
        )

        await _run(monkeypatch, patched_workflow, run)

        (result,) = _tool_messages(run.sent[1])
        assert TRUNCATED not in result["content"]
        assert "i" * 5_000 in result["content"]

    @pytest.mark.asyncio
    async def test_a_history_recorded_before_the_cap_keeps_whole_results(
        self, monkeypatch, patched_workflow
    ):
        run = _Run(
            _legacy(_payload()),
            [_tool_turn(("c1", "scrape")), _final()],
            {SCRAPE_ACTIVITY: _scraped(150_000)},
        )

        await _run(monkeypatch, patched_workflow, run)

        (result,) = _tool_messages(run.sent[1])
        assert TRUNCATED not in result["content"]
        assert len(result["content"]) > 150_000


class TestClearing:
    @pytest.mark.asyncio
    async def test_old_results_are_cleared_and_the_latest_turn_is_kept(
        self, monkeypatch, patched_workflow
    ):
        run = _Run(
            _payload(transcript_budget_bytes=60_000),
            [
                _tool_turn(("c1", "scrape")),
                _tool_turn(("c2", "scrape")),
                _tool_turn(("c3", "scrape")),
                _final(),
            ],
            {SCRAPE_ACTIVITY: _scraped(20_000)},
        )

        await _run(monkeypatch, patched_workflow, run)

        third, fourth = run.sent[2], run.sent[3]
        assert [m["content"] == CLEARED_TOOL_RESULT for m in _tool_messages(third)] == [
            True,
            False,
        ]
        assert [m["content"] == CLEARED_TOOL_RESULT for m in _tool_messages(fourth)] == [
            True,
            True,
            False,
        ]
        # Every call still has its answer, in order.
        assert [m["tool_call_id"] for m in _tool_messages(fourth)] == ["c1", "c2", "c3"]
        assert all(transcript_bytes(sent) <= 60_000 for sent in run.sent)
        # Clearing is not compaction: no summarizer call was needed.
        assert run.compactions == []

    @pytest.mark.asyncio
    async def test_one_turn_over_the_budget_is_cut_to_fit(
        self, monkeypatch, patched_workflow
    ):
        run = _Run(
            _payload(transcript_budget_bytes=100_000),
            [
                _tool_turn(("c1", "scrape"), ("c2", "scrape"), ("c3", "scrape")),
                _final(),
            ],
            {SCRAPE_ACTIVITY: _scraped(40_000)},
        )

        await _run(monkeypatch, patched_workflow, run)

        results = _tool_messages(run.sent[1])
        assert len(results) == 3
        assert all(TRUNCATED in result["content"] for result in results)
        assert transcript_bytes(run.sent[1]) <= 100_000


class TestTokenGate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("versioned", "expected_compactions"),
        [(True, 0), (False, 1)],
        ids=["next-request", "legacy-running-sum"],
    )
    async def test_the_gate_measures_the_next_request_not_a_running_sum(
        self, monkeypatch, patched_workflow, versioned, expected_compactions
    ):
        # Every request is 400 tokens, well under the 1,000 threshold. Only
        # a running sum of them (the original gate) crosses it, on turn 3.
        payload = _payload(compaction_threshold=1_000)
        if not versioned:
            payload = _legacy(payload)
        run = _Run(
            payload,
            [
                _tool_turn(("c1", "scrape"), total=400),
                _tool_turn(("c2", "scrape"), total=400),
                _tool_turn(("c3", "scrape"), total=400),
                _final(),
            ],
            {SCRAPE_ACTIVITY: _scraped(10)},
        )

        await _run(monkeypatch, patched_workflow, run)

        assert len(run.compactions) == expected_compactions


class TestSummarization:
    @pytest.mark.asyncio
    async def test_a_summary_keeps_the_turn_the_model_has_not_read(
        self, monkeypatch, patched_workflow
    ):
        run = _Run(
            _payload(compaction_threshold=500),
            [
                # Over the threshold already, but there is nothing older
                # than this first turn to summarize: skipped, not failed.
                _tool_turn(("c1", "scrape"), total=600),
                _tool_turn(("c2", "scrape"), total=600),
                _final(),
            ],
            {SCRAPE_ACTIVITY: _scraped(10)},
        )

        await _run(monkeypatch, patched_workflow, run)

        assert len(run.compactions) == 1
        summarized = run.compactions[0]["messages"]
        assert [m["role"] for m in summarized] == ["system", "user", "assistant", "tool"]
        assert all("blocks" not in m and "provider_state" not in m for m in summarized)

        after = run.sent[2]
        assert [m["role"] for m in after] == ["system", "user", "assistant", "tool"]
        assert after[0]["content"] == "Be useful"
        assert after[1]["content"].startswith("## Compacted conversation summary")
        assert after[1]["content"].endswith("## Current request\nresearch the account")
        # The latest turn survives verbatim: its call and its unread result.
        assert after[2]["tool_calls"][0]["id"] == "c2"
        assert after[3]["tool_call_id"] == "c2"
        assert "x" * 10 in after[3]["content"]

    @pytest.mark.asyncio
    async def test_a_text_heavy_history_is_summarized_when_it_overflows(
        self, monkeypatch, patched_workflow
    ):
        # A stored conversation of long messages: no tool results to clear,
        # so only a summary brings the transcript back under budget.
        stored = [
            message_to_wire(Message(role="user", content="q" * 30_000)),
            message_to_wire(Message(role="assistant", content="a" * 30_000)),
        ]
        run = _Run(
            _payload(
                compaction_threshold=10**9,
                transcript_budget_bytes=100_000,
                conversation=stored,
            ),
            [_tool_turn(("c1", "scrape")), _final()],
            {SCRAPE_ACTIVITY: _scraped(100)},
        )

        await _run(monkeypatch, patched_workflow, run)

        assert len(run.compactions) == 1
        assert [m["role"] for m in run.sent[1]] == ["system", "user", "assistant", "tool"]
        assert transcript_bytes(run.sent[1]) <= 100_000

    @pytest.mark.asyncio
    async def test_compaction_off_never_summarizes(self, monkeypatch, patched_workflow):
        stored = [
            message_to_wire(Message(role="user", content="q" * 30_000)),
            message_to_wire(Message(role="assistant", content="a" * 30_000)),
        ]
        run = _Run(
            _payload(
                compaction_threshold=None,
                transcript_budget_bytes=100_000,
                conversation=stored,
            ),
            [_tool_turn(("c1", "scrape")), _final()],
            {SCRAPE_ACTIVITY: _scraped(100)},
        )

        await _run(monkeypatch, patched_workflow, run)

        assert run.compactions == []
