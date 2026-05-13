"""F4.B: per-turn agent activities for ``AgentWorkflow``.

The legacy ``execute_agent`` / ``execute_chat_agent`` in ``services/ai.py``
run an entire LangGraph loop inside a single Temporal activity. F4.B
splits that loop across Temporal workflow boundaries so tool calls can
land as separate activities on per-type worker pools (per RFC §6.3
plus the F4 deferred follow-up). Activities defined here:

- :func:`execute_llm_step` — one ``chat_model.ainvoke()`` turn. Returns
  either ``{"kind": "final", ...}`` (no tool calls) or
  ``{"kind": "tool_calls", "calls": [...], ...}`` for the workflow to
  schedule. **Lives on TaskQueue.AI_HEAVY** when the per-queue pool is
  wired (until then, default queue is fine).
- :func:`persist_agent_turn` — append one human/AI message pair to the
  connected ``simpleMemory`` markdown via the same helpers
  ``services.ai`` uses today. Per user decision (plan §15) memory
  appends per turn, not on completion, so workflow failures don't lose
  progress.
- :func:`compact_agent_memory` — invoke ``CompactionService.compact_context``
  when token thresholds trip. Returns the compacted summary message
  list. Token accounting stays in the workflow state.

Determinism: every activity is a leaf computation (LLM ainvoke, DB
write, summarisation). The workflow that calls them is sandboxed=False
and may read frozen registry dicts deterministically.

References:
- Temporal AI Cookbook -- https://docs.temporal.io/ai-cookbook
- ``temporalio.contrib.openai_agents.activity_as_tool`` mirrors this
  pattern; we re-implement manually because MachinaOs uses LangChain
  (``chat_model.bind_tools`` for schema generation) rather than the
  OpenAI Agents SDK.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from temporalio import activity

logger = logging.getLogger(__name__)


# Activity result shapes — keep these in sync with AgentWorkflow's
# expectations. Pydantic was considered but plain dicts keep the
# payload-serialisation cost flat (Temporal serialises via JSON anyway)
# and avoid pulling Pydantic into the workflow sandbox boundary.

# {"kind": "final", "content": str, "thinking": Optional[str], "usage": dict}
# {"kind": "tool_calls", "calls": [{"id": str, "name": str, "args": dict}], "usage": dict}


@activity.defn(name="agent.execute_llm_step.v1")
async def execute_llm_step(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one LLM turn with bound tools and return the structured response.

    ``payload`` shape::

        {
            "provider": "anthropic" | "openai" | ...,
            "model": "claude-sonnet-4-6",
            "api_key": "...",                  # already resolved
            "messages": [{"role": "user", "content": "..."}, ...],
            "tools": [                          # tool schemas (already built)
                {"name": "calculator", "description": "...", "args_schema": {...}}
            ],
            "system_message": Optional[str],
            "temperature": float,
            "max_tokens": int,
            "thinking_config": Optional[dict],
        }

    Returns one of:
    - ``{"kind": "final", "content": str, "thinking": Optional[str], "usage": dict}``
    - ``{"kind": "tool_calls", "calls": [{"id", "name", "args"}], "usage": dict}``

    The activity intentionally keeps no state across turns; the workflow
    owns the messages list. This makes replay safe per Temporal's
    determinism contract — each activity is a pure transformation.

    Heartbeats every 30 s so long LLM streams don't trip
    ``heartbeat_timeout``.
    """
    activity.logger.info(
        f"Agent LLM step: provider={payload.get('provider')} "
        f"model={payload.get('model')} messages={len(payload.get('messages', []))}"
    )
    activity.heartbeat(f"LLM step starting: {payload.get('model')}")

    # Lazy import — keep top-level light so the worker can register this
    # activity without pulling LangChain in for every plugin.
    from services.ai import AIService, extract_thinking_from_response

    provider = payload["provider"]
    model = payload["model"]
    api_key = payload["api_key"]
    messages = payload.get("messages", [])
    tools_schemas = payload.get("tools", [])
    temperature = payload.get("temperature", 0.7)
    max_tokens = payload.get("max_tokens", 4096)
    thinking_config = payload.get("thinking_config")

    ai_service = AIService()

    chat_model = ai_service.create_model(
        provider=provider,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking_config,
    )

    # Bind tools so the LLM sees their schemas. Tool schemas in the
    # payload are already serialised StructuredTool definitions — we
    # rebuild LangChain StructuredTool wrappers from them. The tools
    # themselves are NOT executed here; the workflow schedules them as
    # separate activities once the LLM emits tool_calls.
    if tools_schemas:
        from langchain_core.tools import StructuredTool
        from pydantic import create_model

        bound_tools: List[Any] = []
        for ts in tools_schemas:
            # Materialise a no-op StructuredTool — execution is deferred
            # to the workflow's tool activity dispatch.
            def _placeholder(**kwargs):  # noqa: ARG001
                raise NotImplementedError("Tool is dispatched via Temporal activity")

            # Recreate a minimal Pydantic args schema. The real type
            # safety lives in the plugin's Params model; here we just
            # need names + descriptions for the LLM.
            args_schema = ts.get("args_schema") or {}
            fields = {
                k: (Any, v.get("description", "") if isinstance(v, dict) else "")
                for k, v in args_schema.get("properties", {}).items()
            }
            Args = create_model(f"{ts['name']}Args", **fields) if fields else None

            bound_tools.append(
                StructuredTool.from_function(
                    func=_placeholder,
                    name=ts["name"],
                    description=ts.get("description", ""),
                    args_schema=Args,
                )
            )
        chat_model = chat_model.bind_tools(bound_tools)

    # Reconstruct LangChain message objects from the workflow's dict
    # representation. Workflow state is serialisable JSON dicts; the
    # activity rehydrates them.
    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    rehydrated = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            rehydrated.append(SystemMessage(content=content))
        elif role == "user":
            rehydrated.append(HumanMessage(content=content))
        elif role == "assistant":
            ai = AIMessage(content=content)
            if m.get("tool_calls"):
                ai.tool_calls = m["tool_calls"]
            rehydrated.append(ai)
        elif role == "tool":
            rehydrated.append(
                ToolMessage(
                    content=content,
                    tool_call_id=m.get("tool_call_id", ""),
                    name=m.get("name", ""),
                )
            )

    activity.heartbeat("LLM step: invoking model")
    response = await chat_model.ainvoke(rehydrated)
    activity.heartbeat("LLM step: model returned")

    # Extract usage if present (anthropic-style token counts).
    usage: Dict[str, Any] = {}
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = dict(response.usage_metadata)
    elif hasattr(response, "response_metadata"):
        meta_usage = response.response_metadata.get("usage", {})
        if meta_usage:
            usage = dict(meta_usage)

    # Tool calls → return them for the workflow to schedule.
    if hasattr(response, "tool_calls") and response.tool_calls:
        return {
            "kind": "tool_calls",
            "calls": [
                {
                    "id": tc.get("id", ""),
                    "name": tc.get("name", ""),
                    "args": tc.get("args", {}),
                }
                for tc in response.tool_calls
            ],
            "usage": usage,
        }

    # No tool calls → final response.
    text, thinking = extract_thinking_from_response(response)
    return {
        "kind": "final",
        "content": text or "",
        "thinking": thinking,
        "usage": usage,
    }


@activity.defn(name="agent.persist_turn.v1")
async def persist_agent_turn(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Append one ``(human, assistant)`` pair to the connected memory.

    Per user decision in plan §15 (F4.B), memory appends per turn so
    failure mid-loop doesn't lose progress. Uses the same markdown
    helpers ``services/ai.py`` calls today via
    ``services.memory.markdown.append_to_memory_markdown``.

    ``payload``::

        {
            "memory_node_id": str,            # the simpleMemory node
            "human_text": str,
            "assistant_text": str,
            "window_size": int,
        }

    Returns ``{"appended": bool, "trimmed_count": int}``.

    No-op when ``memory_node_id`` is empty (the agent has no memory
    connected).
    """
    memory_node_id = payload.get("memory_node_id") or ""
    if not memory_node_id:
        return {"appended": False, "trimmed_count": 0}

    from core.container import container
    from services.memory.markdown import (
        append_to_memory_markdown,
        trim_markdown_window,
    )

    database = container.database()
    params = await database.get_node_parameters(memory_node_id) or {}
    current = params.get("memory_content", "")

    updated = append_to_memory_markdown(
        current,
        human_text=payload.get("human_text", ""),
        assistant_text=payload.get("assistant_text", ""),
    )

    window_size = int(payload.get("window_size", 10))
    trimmed_content, trimmed_pairs = trim_markdown_window(updated, window_size)

    params["memory_content"] = trimmed_content
    await database.save_node_parameters(memory_node_id, params)

    # Broadcast so the parameter panel auto-refetches mid-run.
    broadcaster = container.status_broadcaster()
    await broadcaster.broadcast(
        {
            "type": "node_parameters_updated",
            "node_id": memory_node_id,
            "parameters": params,
        }
    )

    return {"appended": True, "trimmed_count": len(trimmed_pairs)}


@activity.defn(name="agent.compact_memory.v1")
async def compact_agent_memory(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Compact the running conversation when token budget exceeded.

    Wraps ``CompactionService.compact_context`` (existing, in
    ``services/compaction.py``) so the workflow can replace its
    ``messages`` list with the summary message when needed.

    ``payload``::

        {
            "session_id": str,
            "node_id": str,
            "memory_content": str,
            "provider": str,
            "api_key": str,
            "model": str,
        }

    Returns ``{"success": bool, "summary": str, "tokens_before": int, "tokens_after": int}``.
    Caller (workflow) replaces its ``messages`` with a single system
    message wrapping the summary, resets token counters, and continues.
    """
    from services.compaction import get_compaction_service

    activity.heartbeat("Compacting agent memory")
    svc = get_compaction_service()
    result = await svc.compact_context(
        session_id=payload["session_id"],
        node_id=payload["node_id"],
        memory_content=payload.get("memory_content", ""),
        provider=payload["provider"],
        api_key=payload["api_key"],
        model=payload["model"],
    )
    return result


def collect_agent_activities() -> List[Any]:
    """Return the three F4.B agent activities for worker registration.

    Mirrors :func:`services.temporal.plugin_activities.collect_plugin_activities`
    for the workflow-orchestrated agent loop. Workers register these so
    ``AgentWorkflow`` can schedule them; ``MachinaWorkflow`` doesn't
    invoke them directly.
    """
    return [execute_llm_step, persist_agent_turn, compact_agent_memory]
