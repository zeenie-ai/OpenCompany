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
    # CloudEvents v1.0 envelope (RFC §6.4) — type is
    # ``com.machinaos.node.parameters.updated``; ``source_hint="agent"``
    # distinguishes this autonomous write from a user-edited save.
    broadcaster = container.status_broadcaster()
    await broadcaster.broadcast_node_parameters_updated(
        memory_node_id,
        parameters=params,
        source_hint="agent",
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


@activity.defn(name="agent.broadcast_progress.v1")
async def broadcast_agent_progress(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Emit a CloudEvents-shaped ``agent_progress`` broadcast per turn.

    Wraps :meth:`services.status_broadcaster.StatusBroadcaster.broadcast_agent_progress`
    so ``AgentWorkflow`` can schedule it deterministically (workflows
    cannot call broadcaster methods directly — they have to go through
    an activity).

    Wire-format key ``agent_progress`` is the same channel the legacy
    LangGraph path uses (services/ai.py:execute_agent), and the inner
    payload is a CloudEvents v1.0 ``WorkflowEvent`` with
    ``type="com.machinaos.agent.progress"``. The FE routes
    ``data.iteration`` / ``data.max_iterations`` into
    ``nodeStatusStore`` so the "N / max" badge on the canvas updates
    in real time.

    ``payload`` shape::

        {
            "node_id": str,
            "workflow_id": Optional[str],
            "iteration": int,
            "max_iterations": int,
            "phase": Optional[str],  # e.g. "llm_step", "tool_dispatch"
        }

    Returns ``{"emitted": True}`` for completeness — callers don't
    typically inspect the result.
    """
    from core.container import container

    broadcaster = container.status_broadcaster()
    await broadcaster.broadcast_agent_progress(
        payload["node_id"],
        workflow_id=payload.get("workflow_id"),
        iteration=int(payload.get("iteration", 0)),
        max_iterations=int(payload.get("max_iterations", 0)),
        phase=payload.get("phase"),
    )
    return {"emitted": True}


@activity.defn(name="agent.prepare_payload.v1")
async def prepare_agent_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve everything ``AgentWorkflow`` needs from the canvas + DB.

    The workflow itself cannot do DB lookups or LangChain tool builds
    (deterministic-replay constraint). This activity runs *before* the
    workflow is scheduled and returns the fully-resolved payload.

    Mirrors the prep half of ``services.ai.AIService.execute_agent``,
    minus the LangGraph loop (which now lives in ``AgentWorkflow.run``):

    1. Read node parameters from DB via ``database.get_node_parameters``.
    2. Walk edges via ``services.plugin.edge_walker.collect_agent_connections``
       for ``memory_data``, ``skill_data``, ``tool_data``, ``input_data``.
    3. Resolve api_key / proxy from ``auth_service``.
    4. Resolve max_tokens / temperature / thinking config via the
       model-registry helpers.
    5. Build tool schemas — call ``AIService._build_tool_from_node`` per
       tool entry and serialise each ``StructuredTool.args_schema``
       Pydantic class to a JSON-schema dict (so the workflow can pass
       it back into the LLM activity for ``bind_tools``).
    6. Compose ``system_message`` (skill prompt injection + delegation
       contract).

    ``context`` shape (same as the legacy execute_node_activity context)::

        {
            "node_id": str,
            "node_type": str,
            "node_data": dict,    # node parameters from canvas
            "workflow_id": str,
            "session_id": str,
            "nodes": list,        # full canvas (for edge walking)
            "edges": list,
            "inputs": dict,       # upstream node outputs
        }

    Returns the dict that ``AgentWorkflow.run`` expects (see its
    docstring for the canonical shape).

    Falls back gracefully when fields are missing — agents with no
    connected tools / skills / memory still produce a valid payload
    that AgentWorkflow can run.
    """
    activity.logger.info(
        f"Preparing AgentWorkflow payload for {context.get('node_type')!r} "
        f"node_id={context.get('node_id')!r}"
    )

    # Lazy imports — keep agent_activities.py top-level light so the
    # worker can register this activity without dragging the whole
    # AI service in for every plugin.
    from core.container import container
    from services.ai import AIService, _resolve_max_tokens, _resolve_temperature
    from services.ai import ThinkingConfig, get_default_model_async, is_model_valid_for_provider
    from services.plugin.edge_walker import collect_agent_connections

    node_id = context["node_id"]
    node_type = context["node_type"]
    workflow_id = context.get("workflow_id")
    session_id = context.get("session_id", "default")

    database = container.database()
    auth = container.auth_service()
    ai_service = AIService()

    # ---- Node parameters ------------------------------------------------
    # The orchestrator passes context["node_data"] but DB has the
    # authoritative version (UI saves edit -> DB; node_data is a
    # snapshot at scheduling time). Prefer DB for liveness.
    db_params = await database.get_node_parameters(node_id) or {}
    parameters = {**(context.get("node_data") or {}), **db_params}

    options = parameters.get("options") or {}
    flattened = {**parameters, **options}

    prompt = parameters.get("prompt", "")
    system_message = parameters.get("system_message") or "You are a helpful assistant"

    api_key = flattened.get("api_key")
    provider = parameters.get("provider", "openai")
    model = parameters.get("model", "")
    if isinstance(model, str) and model.startswith("[FREE] "):
        model = model[7:]

    if not model or not is_model_valid_for_provider(model, provider):
        model = await get_default_model_async(provider, database)

    if not api_key:
        # Try auth_service one more time (covers chatAgent flow where
        # node params don't carry the api_key directly).
        api_key = await auth.get_api_key(provider) or await auth.get_api_key(f"{provider}_api_key")
    if not api_key:
        raise RuntimeError(
            f"API key for provider {provider!r} required for AgentWorkflow "
            f"node {node_id!r}; configure it in the Credentials Modal."
        )

    max_tokens = _resolve_max_tokens(flattened, model, provider)

    thinking_config_obj: Any = None
    thinking_config_dict: Any = None
    if flattened.get("thinking_enabled"):
        thinking_config_obj = ThinkingConfig(
            enabled=True,
            budget=int(flattened.get("thinking_budget", 2048)),
            effort=flattened.get("reasoning_effort", "medium"),
            level=flattened.get("thinking_level", "medium"),
            format=flattened.get("reasoning_format", "parsed"),
        )
        thinking_config_dict = {
            "enabled": True,
            "budget": thinking_config_obj.budget,
            "effort": thinking_config_obj.effort,
            "level": thinking_config_obj.level,
            "format": thinking_config_obj.format,
        }

    temperature = _resolve_temperature(
        flattened, model, provider,
        bool(thinking_config_obj and thinking_config_obj.enabled),
    )

    # ---- Edge walking ---------------------------------------------------
    walk_context = {
        "nodes": context.get("nodes") or [],
        "edges": context.get("edges") or [],
        "workflow_id": workflow_id,
    }
    memory_data, skill_data, tool_data, input_data, _task_data = (
        await collect_agent_connections(
            node_id, walk_context, database, log_prefix=f"[AgentWorkflow:{node_type}]",
        )
    )

    # ---- Skill prompt injection ----------------------------------------
    from services.ai import _build_skill_system_prompt
    skill_prompt, has_personality = _build_skill_system_prompt(
        skill_data, log_prefix=f"[AgentWorkflow:{node_type}]",
    )
    if skill_prompt:
        system_message = skill_prompt if has_personality else f"{system_message}\n\n{skill_prompt}"

    # ---- Memory ---------------------------------------------------------
    memory_node_id = ""
    memory_content = ""
    memory_window_size = 10
    if memory_data:
        memory_node_id = memory_data.get("node_id") or ""
        memory_content = memory_data.get("memory_content") or ""
        memory_window_size = int(memory_data.get("window_size") or 10)

    # ---- Tools ----------------------------------------------------------
    # Build LangChain StructuredTool per tool entry, then serialise its
    # args_schema to JSON Schema so the AgentWorkflow's LLM step can
    # rebuild a placeholder StructuredTool from it.
    tools_payload: List[Dict[str, Any]] = []
    for tool_info in tool_data or []:
        try:
            tool, config = await ai_service._build_tool_from_node(tool_info)
        except Exception as e:  # noqa: BLE001 — defensive: skip a broken tool
            activity.logger.warning(
                f"prepare_payload: failed to build tool {tool_info.get('node_type')!r}: {e}"
            )
            continue
        if tool is None:
            continue
        # Look up plugin class for activity-dispatch metadata.
        from services.node_registry import get_node_class
        cls = get_node_class(tool_info.get("node_type", ""))
        version = getattr(cls, "version", 1) if cls else 1
        task_queue = getattr(cls, "task_queue", "machina-default") if cls else "machina-default"

        # Serialise args_schema to JSON Schema. StructuredTool.args_schema
        # is a Pydantic model; .model_json_schema() returns a dict the
        # LLM activity can use to bind a placeholder tool.
        args_schema_dict: Dict[str, Any] = {}
        try:
            if tool.args_schema is not None:
                args_schema_dict = tool.args_schema.model_json_schema()
        except Exception as e:  # noqa: BLE001 — defensive
            activity.logger.debug(
                f"prepare_payload: failed to serialise args_schema for "
                f"{tool_info.get('node_type')!r}: {e}"
            )

        tools_payload.append({
            "name": tool.name,
            "description": tool.description or "",
            "args_schema": args_schema_dict,
            "node_type": tool_info.get("node_type", ""),
            "version": version,
            "task_queue": task_queue,
            "tool_node_id": tool_info.get("node_id", ""),
            "parameters": tool_info.get("parameters") or {},
        })

    # ---- Compaction threshold ------------------------------------------
    # Model-aware threshold (50% of context window per agent.compaction.ratio
    # in llm_defaults.json). Reuse the existing CompactionService helper.
    compaction_threshold: int | None = None
    try:
        from services.compaction import get_compaction_service
        svc = get_compaction_service()
        cfg = svc.anthropic_config(model=model, provider=provider)
        compaction_threshold = int(cfg.get("context_token_threshold") or 0) or None
    except Exception:  # noqa: BLE001 — defensive, optional feature
        compaction_threshold = None

    # ---- Optional auto-prompt fallback from input_data -----------------
    # When `prompt` is empty AND a chatTrigger / whatsappReceive / etc.
    # is connected via input-main, use the upstream output's `message`
    # / `text` / `content` as the user prompt. Same fallback the legacy
    # agent path uses.
    if not prompt and input_data:
        out = input_data.get("result") or input_data
        for field in ("message", "text", "content"):
            if field in out and out[field]:
                prompt = str(out[field])
                break

    return {
        "node_id": node_id,
        "node_type": node_type,
        "workflow_id": workflow_id,
        "session_id": session_id,
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system_message": system_message,
        "user_prompt": prompt,
        "tools": tools_payload,
        "memory_node_id": memory_node_id,
        "memory_content": memory_content,
        "memory_window_size": memory_window_size,
        "max_iterations": int(parameters.get("max_iterations") or 50),
        "thinking_config": thinking_config_dict,
        "compaction_threshold": compaction_threshold,
    }


def collect_agent_activities() -> List[Any]:
    """Return the five F4.B agent activities for worker registration.

    Mirrors :func:`services.temporal.plugin_activities.collect_plugin_activities`
    for the workflow-orchestrated agent loop. Workers register these so
    ``AgentWorkflow`` can schedule them; ``MachinaWorkflow`` doesn't
    invoke any of them directly — AgentWorkflow.run() owns the entire
    setup + execution + observation pipeline.
    """
    return [
        execute_llm_step,
        persist_agent_turn,
        compact_agent_memory,
        prepare_agent_payload,
        broadcast_agent_progress,
    ]
