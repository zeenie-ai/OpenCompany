"""F4.B: per-turn agent activities for ``AgentWorkflow``.

The in-process ``execute_agent`` / ``execute_chat_agent`` in
``services/ai.py`` run the entire agent loop inside a single Temporal
activity. F4.B splits that loop across Temporal workflow boundaries so
tool calls can land as separate activities on per-type worker pools (per
RFC §6.3 plus the F4 deferred follow-up). Activities defined here:

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
  pattern; we re-implement manually because OpenCompany uses LangChain
  (``chat_model.bind_tools`` for schema generation) rather than the
  OpenAI Agents SDK.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from temporalio import activity

logger = logging.getLogger(__name__)


# Activity result shapes — keep these in sync with AgentWorkflow's
# expectations. Pydantic was considered but plain dicts keep the
# payload-serialisation cost flat (Temporal serialises via JSON anyway)
# and avoid pulling Pydantic into the workflow sandbox boundary.

# {"kind": "final", "content": str, "thinking": Optional[str], "usage": dict}
# {"kind": "tool_calls", "calls": [{"id": str, "name": str, "args": dict}], "usage": dict}


def _ensure_llm_contents(messages: List[Any]) -> None:
    """Fail fast when the filtered message list has no invokable content.

    Providers require at least one non-system message (Gemini splits
    SystemMessages into ``system_instruction`` and then rejects the empty
    ``contents`` list with an opaque retryable ``ValueError: contents are
    required``). An empty prompt is an invalid-input condition, so raise
    Temporal's ``ApplicationError`` with ``non_retryable=True`` — the
    documented mechanism for business-rule failures (see
    docs.temporal.io/encyclopedia/retry-policies) — instead of burning
    the retry budget on a deterministic failure.
    """
    if any(getattr(m, "type", "") in ("human", "ai", "tool") for m in messages):
        return
    from temporalio.exceptions import ApplicationError

    raise ApplicationError(
        "Agent has no invokable content: message list contains no user, "
        "assistant, or tool messages. Provide a non-empty Task Manager mission, "
        "set the agent's 'prompt' parameter, or "
        "connect an input trigger.",
        type="EmptyAgentPrompt",
        non_retryable=True,
    )


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
        f"Agent LLM step: provider={payload.get('provider')} " f"model={payload.get('model')} messages={len(payload.get('messages', []))}"
    )
    activity.heartbeat(f"LLM step starting: {payload.get('model')}")

    # Lazy import — keep top-level light so the worker can register this
    # activity without pulling LangChain in for every plugin.
    from core.container import container
    from services.ai import extract_thinking_from_response

    provider = payload["provider"]
    model = payload["model"]
    api_key = payload["api_key"]
    messages = payload.get("messages", [])
    tool_data = payload.get("tool_data", [])
    temperature = payload.get("temperature", 0.7)
    max_tokens = payload.get("max_tokens", 4096)
    thinking_config = payload.get("thinking_config")

    ai_service = container.ai_service()

    chat_model = ai_service.create_model(
        provider=provider,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking_config,
    )

    # Reuse the same tool-binding path execute_agent uses. The returned
    # StructuredTool has a proper Pydantic args_schema every provider's
    # bind_tools knows how to convert. The tool's callback is never
    # invoked here — the workflow schedules per-type activities for each
    # tool_call the model emits.
    bound_tools: List[Any] = []
    for tool_info in tool_data:
        tool, _config = await ai_service._build_tool_from_node(tool_info)
        if tool is not None:
            bound_tools.append(tool)
    if bound_tools:
        chat_model = chat_model.bind_tools(bound_tools)

    # Workflow state is serialisable JSON dicts. Use LangChain's own
    # ``messages_from_dict`` / ``messages_to_dict`` helpers so
    # provider-specific metadata (Gemini ``thought_signature``,
    # Anthropic cache fields, OpenAI ``reasoning_content``) survives
    # the workflow ↔ activity round-trip. Manually constructing
    # AIMessage(content=...) from a stripped dict loses these and
    # blows up Gemini's ``Function call is missing a thought_signature``
    # on the next turn.
    from langchain_core.messages import messages_from_dict
    from services.ai import filter_empty_messages

    rehydrated = messages_from_dict(messages)
    # Same filter ``_run_agent_loop`` runs in services/ai.py — empty-content
    # messages trigger 400s on Gemini / Anthropic.
    rehydrated = filter_empty_messages(rehydrated)
    _ensure_llm_contents(rehydrated)

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

    # Serialise the full assistant message so the workflow can append
    # it verbatim to its messages list (preserves thought_signature,
    # cache metadata, etc.). The workflow extracts ``tool_calls`` from
    # ``response.tool_calls`` separately for scheduling.
    from langchain_core.messages import messages_to_dict

    assistant_dict = messages_to_dict([response])[0]

    if hasattr(response, "tool_calls") and response.tool_calls:
        return {
            "kind": "tool_calls",
            "assistant_message": assistant_dict,
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
        "assistant_message": assistant_dict,
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
    from services.memory.runtime import append_memory_turns_atomic

    database = container.database()
    mutation_id = payload.get("mutation_id")
    if not mutation_id:
        # Temporal keeps activity_id stable across automatic retries, making
        # it a durable idempotency key without changing workflow payloads.
        try:
            info = activity.info()
            mutation_id = (
                f"temporal-memory:{info.workflow_id}:{info.activity_id}:"
                f"{memory_node_id}"
            )
        except Exception:  # pragma: no cover - direct unit invocation
            mutation_id = None

    params, trimmed_pairs, applied = await append_memory_turns_atomic(
        database,
        memory_node_id,
        [
            ("human", payload.get("human_text", "")),
            ("ai", payload.get("assistant_text", "")),
        ],
        window_size=int(payload.get("window_size", 10)),
        mutation_id=mutation_id,
    )

    # Broadcast so the parameter panel auto-refetches mid-run.
    # CloudEvents v1.0 envelope (RFC §6.4) — type is
    # ``com.opencompany.node.parameters.updated``; ``source_hint="agent"``
    # distinguishes this autonomous write from a user-edited save.
    # StatusBroadcaster is a module-level singleton (not on the DI
    # container) — same pattern handlers/tools.py / handlers/triggers.py
    # use. ``container.status_broadcaster()`` does NOT exist.
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    if applied:
        await broadcaster.broadcast_node_parameters_updated(
            memory_node_id,
            parameters=params,
            source_hint="agent",
        )

    return {
        "appended": applied,
        "applied": applied,
        "trimmed_count": len(trimmed_pairs),
    }


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
    if svc is None:
        # CompactionService is a Singleton wired by the FastAPI lifespan
        # (main.py). If the worker activity runs before lifespan init,
        # the singleton is None and compaction must no-op so the agent
        # loop keeps running — the workflow checks ``success`` and
        # keeps the existing messages list on False.
        return {
            "success": False,
            "error": "CompactionService not initialized (worker bootstrap race)",
            "summary": "",
            "tokens_before": 0,
            "tokens_after": 0,
        }
    return await svc.compact_context(
        session_id=payload["session_id"],
        node_id=payload["node_id"],
        memory_content=payload.get("memory_content", ""),
        provider=payload["provider"],
        api_key=payload["api_key"],
        model=payload["model"],
    )


@activity.defn(name="agent.broadcast_progress.v1")
async def broadcast_agent_progress(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Emit a CloudEvents-shaped ``agent_progress`` broadcast per turn.

    Wraps :meth:`services.status_broadcaster.StatusBroadcaster.broadcast_agent_progress`
    so ``AgentWorkflow`` can schedule it deterministically (workflows
    cannot call broadcaster methods directly — they have to go through
    an activity).

    Wire-format key ``agent_progress`` is the same channel the in-process
    agent-loop path uses (services/ai.py:execute_agent), and the inner
    payload is a CloudEvents v1.0 ``WorkflowEvent`` with
    ``type="com.opencompany.agent.progress"``. The FE routes
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
    # StatusBroadcaster is a module-level singleton (not on the DI
    # container) — same pattern handlers/tools.py / handlers/triggers.py
    # use. ``container.status_broadcaster()`` does NOT exist.
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    node_id = payload["node_id"]
    workflow_id = payload.get("workflow_id")
    phase = payload.get("phase")

    # Optional canvas-glow status update (raw-dict, same idiom F4.A's
    # _node_activity wrapper uses). Lets the FE swap node colors on
    # executing/success/error without a separate CloudEvents handler.
    status = payload.get("status")
    if status:
        await broadcaster.update_node_status(
            node_id,
            status,
            {"agent_type": "temporal", **({"phase": phase} if phase else {})},
            workflow_id=workflow_id,
        )

    # CloudEvents v1.0 envelope (com.opencompany.agent.progress). Drives
    # the iteration badge + phase indicator on the canvas.
    await broadcaster.broadcast_agent_progress(
        node_id,
        workflow_id=workflow_id,
        iteration=int(payload.get("iteration", 0)),
        max_iterations=int(payload.get("max_iterations", 0)),
        phase=phase,
    )
    return {"emitted": True}


@activity.defn(name="agent.store_output.v1")
async def store_agent_output(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist the agent's final ``result`` dict via the existing
    ``WorkflowService.store_node_output`` so ``ParameterResolver`` can
    resolve ``{{aiAgent.response}}`` template references in downstream
    nodes. The F4.A activity path stores via ``NodeExecutor``; F4.B
    needs this dedicated activity because ``AgentWorkflow`` doesn't go
    through ``WorkflowService.execute_node``.

    ``payload`` shape::

        {
            "node_id": str,
            "session_id": str,
            "result": dict,  # AgentWorkflow.run() return.result
        }

    Mirrors what ``NodeExecutor.execute`` writes for every output handle
    (``output_main`` / ``output_top`` / ``output_0``).
    """
    from core.container import container

    workflow_service = container.workflow_service()
    node_id = payload["node_id"]
    session_id = payload.get("session_id", "default")
    data = payload.get("result") or {}
    for output_name in ("output_main", "output_top", "output_0"):
        await workflow_service.store_node_output(session_id, node_id, output_name, data)
    return {"stored": True}


@activity.defn(name="agent.prepare_payload.v1")
async def prepare_agent_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve everything ``AgentWorkflow`` needs from the canvas + DB.

    The workflow itself cannot do DB lookups or LangChain tool builds
    (deterministic-replay constraint). This activity runs *before* the
    workflow is scheduled and returns the fully-resolved payload.

    Mirrors the prep half of ``services.ai.AIService.execute_agent``,
    minus the agent loop (which lives in ``AgentWorkflow.run`` for the
    F4.B Temporal path and ``services.ai._run_agent_loop`` in-process):

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
            # Optional — present only for delegation children spawned by
            # a parent AgentWorkflow's delegate_to_* call. Per-invocation
            # input: applied AFTER config resolution, never overridden by
            # stored node parameters (the node's persisted ``prompt`` is
            # typically the empty Pydantic default).
            "invocation": {"task": str, "context": str},
        }

    Returns the dict that ``AgentWorkflow.run`` expects (see its
    docstring for the canonical shape).

    Falls back gracefully when fields are missing — agents with no
    connected tools / skills / memory still produce a valid payload
    that AgentWorkflow can run.
    """
    activity.logger.info(f"Preparing AgentWorkflow payload for {context.get('node_type')!r} " f"node_id={context.get('node_id')!r}")

    # Lazy imports — keep agent_activities.py top-level light so the
    # worker can register this activity without dragging the whole
    # AI service in for every plugin.
    from core.container import container
    from services.ai import _resolve_max_tokens, _resolve_temperature
    from services.ai import ThinkingConfig, get_default_model_async, is_model_valid_for_provider
    from services.plugin.edge_walker import (
        collect_agent_connections,
        collect_teammate_connections,
        extract_task_event_payload,
        format_task_context,
    )

    node_id = context["node_id"]
    node_type = context["node_type"]
    workflow_id = context.get("workflow_id")
    session_id = context.get("session_id", "default")

    database = container.database()
    auth = container.auth_service()
    # AIService is a DI singleton — pull it from the container so its
    # constructor dependencies are wired. Direct ``AIService()`` raises
    # ``TypeError: missing 4 required positional arguments``.
    ai_service = container.ai_service()

    # ---- Node parameters ------------------------------------------------
    # The orchestrator passes context["node_data"] but DB has the
    # authoritative version (UI saves edit -> DB; node_data is a
    # snapshot at scheduling time). Prefer DB for liveness.
    db_params = await database.get_node_parameters(node_id) or {}
    parameters = {**(context.get("node_data") or {}), **db_params}

    # Resolve {{node.field}} template variables — same step NodeExecutor
    # runs before dispatching to handlers in the legacy path. Without
    # this the agent receives literal "{{chatTrigger.message}}" strings.
    workflow_service = container.workflow_service()
    nodes = context.get("nodes") or []
    edges = context.get("edges") or []
    if nodes and edges:
        parameters = await workflow_service._param_resolver.resolve(
            parameters,
            node_id,
            nodes,
            edges,
            session_id,
        )

    options = parameters.get("options") or {}
    flattened = {**parameters, **options}

    prompt = parameters.get("prompt", "")
    system_message = parameters.get("system_message") or "You are a helpful assistant"

    # Per-invocation input (delegation contract) beats stored configuration.
    # The parent's delegate_to_* call passes {"task", "context"} as the
    # child workflow's ``invocation`` input field. Faithful mirror of the
    # legacy working path (``handlers.tools._execute_delegated_agent``,
    # which applies the remap AFTER loading DB params so it always wins):
    # ``task`` is the mission directive → system_message; ``context`` is
    # the input data → prompt, falling back to task (DelegateToAgentSchema
    # in services/ai.py declares task required, context optional). Applied
    # after the config merge so the node's persisted empty default
    # ``prompt`` can never clobber the delegated task.
    invocation = context.get("invocation") or {}
    if invocation.get("task") or invocation.get("context"):
        system_message = invocation.get("task") or "You are a helpful assistant"
        prompt = invocation.get("context") or invocation.get("task") or ""

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
            f"API key for provider {provider!r} required for AgentWorkflow " f"node {node_id!r}; configure it in the Credentials Modal."
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
        flattened,
        model,
        provider,
        bool(thinking_config_obj and thinking_config_obj.enabled),
    )

    # ---- Edge walking ---------------------------------------------------
    walk_context = {
        "nodes": context.get("nodes") or [],
        "edges": context.get("edges") or [],
        "workflow_id": workflow_id,
        # MachinaWorkflow passes upstream results as ``inputs``. Edge walking
        # uses the legacy node-id keyed ``outputs`` name, so bridge the two
        # shapes for chat triggers and taskTrigger alike.
        "outputs": context.get("outputs") or context.get("inputs") or {},
    }
    memory_data, skill_data, tool_data, input_data, task_data = await collect_agent_connections(
        node_id,
        walk_context,
        database,
        log_prefix=f"[AgentWorkflow:{node_type}]",
    )

    # taskTrigger may be wired to input-task (task_data) or input-main
    # (input_data). In both cases preserve the CloudEvent payload as invokable
    # content. This is an external automation run, separate from the owning
    # AgentWorkflow which already receives the child result for durable review.
    trigger_task_data = task_data
    if not trigger_task_data and isinstance(input_data, dict):
        trigger_task_data = extract_task_event_payload(input_data)
    if trigger_task_data:
        task_prompt = format_task_context(trigger_task_data)
        prompt = f"{task_prompt}\n\n{prompt}" if prompt else task_prompt

    # Team-handle edges are configuration edges and are intentionally not
    # returned by collect_agent_connections.  Expand them here before tools
    # are built, mirroring the legacy inline agent path.  Without this step
    # Temporal removes teammates from graph scheduling but never exposes a
    # delegate_to_* function to the lead LLM.
    execution_team_id: Optional[str] = context.get("team_id")
    team_execution_id: Optional[str] = None
    if trigger_task_data:
        # Completion automation runs in a new Temporal execution but reviews
        # the durable task in the execution that originally assigned it.
        execution_team_id = str(trigger_task_data.get("team_id") or "") or None
        team_execution_id = (
            str(trigger_task_data.get("execution_id") or "") or None
        )
    owns_execution_team = False
    if node_type in {"orchestrator_agent", "ai_employee"}:
        teammates = await collect_teammate_connections(
            node_id, walk_context, database
        )
        all_nodes = walk_context["nodes"]
        all_edges = walk_context["edges"]
        for teammate in teammates:
            teammate_id = teammate["node_id"]
            child_tools: List[Dict[str, Any]] = []
            for edge in all_edges:
                if (
                    edge.get("target") != teammate_id
                    or edge.get("targetHandle") != "input-tools"
                ):
                    continue
                child_id = edge.get("source")
                child = next(
                    (candidate for candidate in all_nodes if candidate.get("id") == child_id),
                    None,
                )
                if child:
                    child_tools.append(
                        {
                            "node_id": child_id,
                            "node_type": child.get("type", ""),
                            "label": child.get("data", {}).get("label")
                            or child.get("type", ""),
                        }
                    )
            entry = {
                **teammate,
                "child_tools": child_tools,
            }
            tool_data = [*(tool_data or []), entry]
        # A taskTrigger completion is a separate downstream automation run.
        # It must not mint a second empty "active" team for the same lead;
        # doing so makes the owning execution's submitted/accepted tasks seem
        # to disappear from Task Manager and Team Monitor.
        if teammates and workflow_id and not trigger_task_data:
            from services.agent_team import get_agent_team_service

            execution_id = str(context.get("execution_id") or "")
            root_execution_id = str(
                context.get("root_execution_id") or execution_id
            )
            if execution_id:
                team = await get_agent_team_service().get_or_create_execution_team(
                    team_lead_node_id=node_id,
                    teammates=teammates,
                    workflow_id=workflow_id,
                    execution_id=execution_id,
                    root_execution_id=root_execution_id,
                    team_lead_type=node_type,
                    team_lead_label=parameters.get("label") or node_type,
                    config={"mode": "parallel"},
                )
                if not team:
                    raise RuntimeError("Failed to persist agent execution team")
                execution_team_id = team.get("team_id") or team.get("id")
                owns_execution_team = True

    # ---- Skill prompt injection ----------------------------------------
    from services.ai import _build_skill_system_prompt

    skill_prompt, has_personality = _build_skill_system_prompt(
        skill_data,
        log_prefix=f"[AgentWorkflow:{node_type}]",
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
    # We call ``ai_service._build_tool_from_node`` once here ONLY to
    # extract the LLM-visible tool name (the workflow needs it to map
    # ``tool_call.name`` back to a node_id when scheduling the per-type
    # activity). The actual StructuredTool — with its proper Pydantic
    # ``args_schema`` — gets rebuilt inside ``execute_llm_step`` against
    # the same ``tool_info`` dict via the same helper. We never serialise
    # the schema to JSON-Schema-and-back: that round-trip strips type
    # info and the reconstructed ``(Any, default_string)`` placeholder
    # blew up Gemini's ``convert_to_genai_function_declarations``
    # (``properties.<field> Input should be a valid dictionary or object``).
    tools_payload: List[Dict[str, Any]] = []
    for tool_info in tool_data or []:
        try:
            tool, _config = await ai_service._build_tool_from_node(tool_info)
        except Exception as e:  # noqa: BLE001 — defensive: skip a broken tool
            activity.logger.warning(f"prepare_payload: failed to build tool {tool_info.get('node_type')!r}: {e}")
            continue
        if tool is None:
            continue
        # Look up plugin class for activity-dispatch metadata.
        from services.node_registry import get_node_class

        cls = get_node_class(tool_info.get("node_type", ""))
        version = getattr(cls, "version", 1) if cls else 1
        task_queue = getattr(cls, "task_queue", "machina-default") if cls else "machina-default"

        tools_payload.append(
            {
                "name": tool.name,
                "node_type": tool_info.get("node_type", ""),
                "version": version,
                "task_queue": task_queue,
                "tool_node_id": tool_info.get("node_id", ""),
                "parameters": tool_info.get("parameters") or {},
                # Raw tool_info — what ``collect_agent_connections`` returned
                # and what ``_build_tool_from_node`` accepts as input. Passed
                # through the workflow verbatim so ``execute_llm_step`` can
                # rebuild the real StructuredTool inside the activity.
                "tool_info": tool_info,
                # Team leads create and dispatch durable work through Task
                # Manager. Delegate descriptors stay in workflow state for
                # trusted assignee resolution, but are not callable directly
                # by the model.
                "llm_hidden": (
                    node_type in {"orchestrator_agent", "ai_employee"}
                    and tool.name.startswith("delegate_to_")
                ),
            }
        )

    if any(tool["name"].startswith("delegate_to_") for tool in tools_payload):
        delegates = "\n".join(
            f"- {(tool.get('tool_info') or {}).get('node_id')}: "
            f"{(tool.get('tool_info') or {}).get('label', tool['node_type'])} "
            f"({tool['node_type']})"
            for tool in tools_payload
            if tool["name"].startswith("delegate_to_")
        )
        system_message = (
            f"{system_message}\n\n"
            "You lead a team of independent agents. All delegation MUST use the "
            "task_manager tool with operation='assign_task'. Provide title, a bounded "
            "mission, relevant context, acceptance criteria, and exactly one connected "
            "assignee_node_id. Never call delegate_to_* directly. You may issue multiple "
            "assign_task tool calls in one response; they are durably queued and run in "
            "parallel subject to the team limit. Review submitted tasks with list_tasks "
            "and get_task, then accept, retry, modify, reassign, or cancel before finishing.\n"
            "When assign_task returns status='queued', do not poll or wait in this run. "
            "Tell the user the task was delegated and return immediately. Completion starts "
            "a separate taskTrigger review with the owning task context.\n"
            "For mutations, copy task.id into task_id and task.revision into expected_revision. "
            "After a single child submits work, accept_task may omit them only when that submitted "
            "task is unambiguous.\n"
            f"Connected teammates (assignee_node_id: label/type):\n{delegates}"
        )

    # ---- Compaction threshold ------------------------------------------
    # Model-aware threshold (50% of context window per agent.compaction.ratio
    # in llm_defaults.json). Reuse the existing CompactionService helper.
    # ``anthropic_config`` is async (awaits _get_compaction_ratio) — must
    # be awaited. ``get_compaction_service`` returns Optional[...] so the
    # service may not be initialized yet (e.g. worker bootstrap order).
    compaction_threshold: int | None = None
    try:
        from services.compaction import get_compaction_service

        svc = get_compaction_service()
        if svc is not None:
            cfg = await svc.anthropic_config(model=model, provider=provider)
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
        if not prompt and isinstance(out, dict) and out:
            import json

            prompt = json.dumps(out, ensure_ascii=False, default=str)

    # Read user-overridable globals once at prep time.
    #   - auto_rebind_tools: forwarded into every per-tool activity so
    #     agentBuilder's summary + the workflow rebind branch read the
    #     same value.
    #   - agent_recursion_limit: applied to the agent loop's hard step
    #     cap. Per-user override beats env Settings.
    auto_rebind_tools = True
    settings_recursion_limit: Optional[int] = None
    from core.config import Settings as _DelegationSettings

    delegation_settings = _DelegationSettings()
    max_concurrent_subagents = int(delegation_settings.max_concurrent_subagents)
    max_delegation_depth = int(delegation_settings.max_delegation_depth)
    try:
        user_settings = await database.get_user_settings()
        if user_settings is not None:
            auto_rebind_tools = bool(
                user_settings.get("auto_rebind_tools_after_canvas_change", True)
            )
            raw_limit = user_settings.get("agent_recursion_limit")
            if isinstance(raw_limit, int) and raw_limit > 0:
                settings_recursion_limit = raw_limit
            raw_concurrency = user_settings.get("max_concurrent_subagents")
            if isinstance(raw_concurrency, int) and raw_concurrency > 0:
                max_concurrent_subagents = raw_concurrency
            raw_depth = user_settings.get("max_delegation_depth")
            if isinstance(raw_depth, int) and raw_depth > 0:
                max_delegation_depth = min(2, raw_depth)
    except Exception as exc:  # noqa: BLE001 — defensive read
        activity.logger.debug(f"user_settings read failed: {exc}")

    # Precedence: per-node parameter > per-user UserSettings > env Settings.
    node_param_limit = parameters.get("max_iterations")
    if isinstance(node_param_limit, int) and node_param_limit > 0:
        effective_recursion_limit = node_param_limit
    elif settings_recursion_limit is not None:
        effective_recursion_limit = settings_recursion_limit
    else:
        from core.config import Settings as _Settings

        try:
            effective_recursion_limit = int(_Settings().agent_recursion_limit)
        except Exception:  # noqa: BLE001 — last-resort fallback
            effective_recursion_limit = 200

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
        "max_iterations": effective_recursion_limit,
        "thinking_config": thinking_config_dict,
        "compaction_threshold": compaction_threshold,
        "auto_rebind_tools": auto_rebind_tools,
        "max_concurrent_subagents": max_concurrent_subagents,
        "max_delegation_depth": max_delegation_depth,
        "team_id": execution_team_id,
        "team_execution_id": team_execution_id,
        "owns_execution_team": owns_execution_team,
        "root_execution_id": str(
            context.get("root_execution_id")
            or context.get("execution_id")
            or ""
        ),
    }


@activity.defn(name="agent.refresh_tools.v1")
async def refresh_agent_tools(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build fresh ``tool_payload`` entries from a workflow_ops batch.

    Called by ``AgentWorkflow.run`` after a tool returns
    ``operations`` (canvas mutation, today only from ``agentBuilder``).
    The activity translates each ``add_node`` op with
    ``component_kind="tool"`` into the same ``tool_payload`` shape
    :func:`prepare_agent_payload` emits — so the workflow body can
    splice the new entries into its existing ``tools`` / ``tool_index``
    structures with zero schema drift.

    Reuses (no duplication):

      * :meth:`AIService._build_tool_from_node` — the canonical
        StructuredTool builder.
      * :func:`services.node_registry.get_node_class` — for
        ``component_kind`` + ``version`` + ``task_queue`` metadata.
      * The exact loop body from :func:`prepare_agent_payload`
        lines 584-614.

    Payload shape::

        {"operations": [<workflow_op>, ...]}

    Returns::

        {"tools": [<tool_payload entry>, ...]}
    """
    from core.container import container
    from services.node_registry import get_node_class

    ai_service = container.ai_service()
    operations: List[Dict[str, Any]] = payload.get("operations") or []
    team_lead_refresh = payload.get("agent_node_type") in {"orchestrator_agent", "ai_employee"}
    new_tools_payload: List[Dict[str, Any]] = []

    for op in operations:
        if op.get("type") != "add_node":
            continue
        node_type = op.get("node_type") or ""
        if not node_type:
            continue
        cls = get_node_class(node_type)
        if cls is None:
            continue
        kind = getattr(cls, "component_kind", "")
        # Match the catalogue filter: pure ToolNode (kind=='tool') OR
        # dual-purpose ActionNode with usable_as_tool=True (the bulk of
        # spawnable plugins — twitterSearch / googleGmail / pythonExecutor
        # / fileRead / etc.). Exclude chat-model plugins even when
        # usable_as_tool=True.
        is_agent_delegate = kind == "agent"
        is_tool = kind == "tool"
        is_dual_purpose = bool(getattr(cls, "usable_as_tool", False)) and kind != "model"
        if not (is_tool or is_dual_purpose or is_agent_delegate):
            continue
        tool_info: Dict[str, Any] = {
            "node_id": op.get("minted_id") or op.get("client_ref") or f"new_{node_type}",
            "node_type": node_type,
            "parameters": op.get("parameters") or {},
            "label": op.get("label") or node_type,
        }
        try:
            tool, _config = await ai_service._build_tool_from_node(tool_info)
        except Exception as e:  # noqa: BLE001 — skip one, keep building the batch
            activity.logger.warning(
                f"refresh_tools: failed to build tool {node_type!r}: {e}"
            )
            continue
        if tool is None:
            continue
        version = getattr(cls, "version", 1)
        task_queue = getattr(cls, "task_queue", "machina-default")
        new_tools_payload.append(
            {
                "name": tool.name,
                "node_type": node_type,
                "version": version,
                "task_queue": task_queue,
                "tool_node_id": tool_info["node_id"],
                "parameters": tool_info["parameters"],
                "tool_info": tool_info,
                "llm_hidden": bool(team_lead_refresh and is_agent_delegate),
            }
        )

    activity.logger.info(
        "refresh_tools: built %d tool(s) from %d operation(s)",
        len(new_tools_payload),
        len(operations),
    )
    return {"tools": new_tools_payload}


@activity.defn(name="agent.begin_delegation.v1")
async def begin_agent_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotently persist and claim a delegation before child startup."""
    from services.agent_team import get_agent_team_service

    team_id = str(payload.get("team_id") or "")
    task_id = str(payload.get("team_task_id") or "")
    child_id = str(payload.get("child_agent_node_id") or "")
    parent_id = str(payload.get("parent_agent_node_id") or "")
    mission = str(payload.get("task") or "")
    if not all((team_id, task_id, child_id, parent_id, mission)):
        raise ValueError("Incomplete durable delegation payload")

    service = get_agent_team_service()
    tasks = await service.database.get_team_tasks(team_id)
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        task = await service.add_task(
            team_id, title=mission[:200], description=mission,
            created_by=parent_id, task_id=task_id,
        )
        if not task:
            raise RuntimeError("Failed to persist delegated team task")

    if str(task.get("status") or "") in {"pending", "queued", ""}:
        claimed = await service.claim_task(team_id, task_id, child_id)
        if not claimed:
            tasks = await service.database.get_team_tasks(team_id)
            task = next((item for item in tasks if item.get("id") == task_id), None)
            if not task or task.get("assigned_to") != child_id:
                raise RuntimeError("Failed to claim delegated team task")
    elif task.get("assigned_to") and task.get("assigned_to") != child_id:
        raise RuntimeError("Delegated team task is claimed by another agent")

    message = await service.send_message(
        team_id, parent_id, f"Assigned task to {child_id}: {mission}",
        to_agent=child_id, message_type="assignment",
        event_id=str(payload.get("assignment_event_id") or f"{task_id}:assigned"),
        extra_data={
            "status": "started", "task_id": task_id,
            "root_execution_id": payload.get("root_execution_id"),
            "delegation_depth": payload.get("delegation_depth"),
            "trace_id": payload.get("trace_id"),
        },
    )
    if not message:
        raise RuntimeError("Failed to persist delegation assignment event")
    return {"team_id": team_id, "team_task_id": task_id, "claimed": True}


@activity.defn(name="agent.queue_delegation.v1")
async def queue_agent_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a pending task before it waits for a root-wide permit."""
    from services.agent_team import get_agent_team_service

    team_id = str(payload.get("team_id") or "")
    task_id = str(payload.get("team_task_id") or "")
    parent_id = str(payload.get("parent_agent_node_id") or "")
    child_id = str(payload.get("child_agent_node_id") or "")
    mission = str(payload.get("task") or "")
    if not all((team_id, task_id, parent_id, child_id, mission)):
        raise ValueError("Incomplete queued delegation payload")
    service = get_agent_team_service()
    tasks = await service.database.get_team_tasks(team_id)
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        task = await service.add_task(
            team_id, title=mission[:200], description=mission,
            created_by=parent_id, task_id=task_id,
        )
        if not task:
            raise RuntimeError("Failed to persist queued delegation")
    message = await service.send_message(
        team_id, parent_id, f"Queued task for {child_id}: {mission}",
        to_agent=child_id, message_type="assignment",
        event_id=str(payload.get("queued_event_id") or f"{task_id}:queued"),
        extra_data={"status": "queued", "task_id": task_id},
    )
    if not message:
        raise RuntimeError("Failed to persist delegation queue event")
    return {"team_id": team_id, "team_task_id": task_id, "status": "queued"}


@activity.defn(name="agent.acquire_subagent_permit.v1")
async def acquire_subagent_permit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Poll the durable root coordinator until this delegation is admitted."""
    from services.agent_team import get_agent_team_service

    root_id = str(payload.get("root_execution_id") or "")
    permit_id = str(payload.get("permit_id") or "")
    limit = max(1, int(payload.get("limit") or 3))
    if not root_id or not permit_id:
        raise ValueError("root_execution_id and permit_id are required")
    service = get_agent_team_service()
    while True:
        if await service.acquire_subagent_permit(root_id, permit_id, limit):
            return {"root_execution_id": root_id, "permit_id": permit_id, "acquired": True}
        activity.heartbeat({"root_execution_id": root_id, "permit_id": permit_id, "status": "queued"})
        await asyncio.sleep(1)


@activity.defn(name="agent.release_subagent_permit.v1")
async def release_subagent_permit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotently release a durable root-wide concurrency permit."""
    from services.agent_team import get_agent_team_service

    root_id = str(payload.get("root_execution_id") or "")
    permit_id = str(payload.get("permit_id") or "")
    if not root_id or not permit_id:
        raise ValueError("root_execution_id and permit_id are required")
    released = await get_agent_team_service().release_subagent_permit(root_id, permit_id)
    if not released:
        raise RuntimeError("Failed to release subagent permit")
    return {"root_execution_id": root_id, "permit_id": permit_id, "released": True}


@activity.defn(name="agent.finish_delegation.v1")
async def finish_agent_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotently persist a delegated child's terminal result."""
    from services.agent_team import get_agent_team_service

    team_id = str(payload.get("team_id") or "")
    task_id = str(payload.get("team_task_id") or "")
    child_id = str(payload.get("child_agent_node_id") or "")
    parent_id = str(payload.get("parent_agent_node_id") or "")
    if not all((team_id, task_id, child_id, parent_id)):
        raise ValueError("Incomplete delegation completion payload")
    service = get_agent_team_service()
    tasks = await service.database.get_team_tasks(team_id)
    task = next((item for item in tasks if item.get("id") == task_id), None)
    if task is None:
        raise RuntimeError("Delegated team task does not exist")

    succeeded = bool(payload.get("success"))
    target_status = "submitted" if succeeded else "failed"
    if task.get("status") not in {"submitted", "accepted", "failed", "cancelled", "skipped"}:
        if succeeded:
            ok = await service.complete_task(team_id, task_id, payload.get("result") or {})
        else:
            ok = await service.fail_task(
                team_id, task_id, str(payload.get("error") or "Delegated agent failed")
            )
        if not ok:
            raise RuntimeError(f"Failed to mark delegated task {target_status}")

    # Re-read the authoritative state. ``fail_task`` may queue another
    # attempt, so the requested failure is not necessarily terminal.
    tasks = await service.database.get_team_tasks(team_id)
    task = next((item for item in tasks if item.get("id") == task_id), task)
    persisted_status = str(task.get("status") or target_status)
    is_requeued = (not succeeded) and persisted_status in {"pending", "queued", "blocked"}
    target_status = "requeued" if is_requeued else persisted_status

    error = str(payload.get("error") or "Delegated agent failed")
    content = f"Task {task_id} completed" if succeeded else f"Task {task_id} failed: {error}"
    message = await service.send_message(
        team_id, child_id, content, to_agent=parent_id,
        message_type="result" if succeeded else "error",
        event_id=str(payload.get("terminal_event_id") or f"{task_id}:{target_status}"),
        extra_data={
            "status": target_status, "task_id": task_id,
            "root_execution_id": payload.get("root_execution_id"),
            "trace_id": payload.get("trace_id"),
        },
    )
    if not message:
        raise RuntimeError("Failed to persist delegation terminal event")

    # taskTrigger is an external automation consumer. Publish only after the
    # durable transition/message succeed, and never publish a terminal failure
    # for an attempt that the database actually requeued.
    if succeeded or not is_requeued:
        from nodes.agent._events import (
            broadcast_agent_task_completed,
            broadcast_agent_task_failed,
        )

        lifecycle_data = {
            "team_id": team_id,
            "execution_id": task.get("execution_id"),
            "root_execution_id": payload.get("root_execution_id"),
            "trace_id": payload.get("trace_id"),
            "parent_agent_workflow_id": payload.get("parent_agent_workflow_id"),
        }
        event_id = str(payload.get("terminal_event_id") or f"{task_id}:{target_status}")
        if succeeded:
            result = payload.get("result") or {}
            if isinstance(result, dict):
                result_value = result.get("response", result.get("result", result))
            else:
                result_value = result
            result_text = str(result_value)
            await broadcast_agent_task_completed(
                task_id=task_id,
                agent_name=str(payload.get("child_agent_name") or child_id),
                agent_node_id=child_id,
                parent_node_id=parent_id,
                workflow_id=payload.get("workflow_id"),
                result=result_text,
                event_id=event_id,
                lifecycle_data=lifecycle_data,
            )
        else:
            await broadcast_agent_task_failed(
                task_id=task_id,
                agent_name=str(payload.get("child_agent_name") or child_id),
                agent_node_id=child_id,
                parent_node_id=parent_id,
                workflow_id=payload.get("workflow_id"),
                error=error,
                event_id=event_id,
                lifecycle_data=lifecycle_data,
            )
    return {"team_id": team_id, "team_task_id": task_id, "status": target_status}


@activity.defn(name="agent.register_task_execution.v1")
async def register_task_execution(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist actual runner/child Temporal identities for trace inspection."""
    from services.agent_team import get_agent_team_service

    team_id = str(payload.get("team_id") or "")
    task_id = str(payload.get("team_task_id") or "")
    if not team_id or not task_id:
        raise ValueError("team_id and team_task_id are required")
    task = await get_agent_team_service().database.get_durable_team_task(team_id, task_id)
    if not task:
        raise ValueError("Delegated team task does not exist")
    registered = await get_agent_team_service().database.register_team_task_execution(
        team_id=team_id, task_id=task_id,
        attempt_number=int(payload.get("attempt_number", task.get("current_attempt", 0))),
        runner_workflow_id=payload.get("runner_workflow_id"),
        runner_run_id=payload.get("runner_run_id"),
        child_workflow_id=payload.get("child_workflow_id"),
        child_run_id=payload.get("child_run_id"),
        parent_workflow_id=payload.get("parent_workflow_id"),
        parent_run_id=payload.get("parent_run_id"),
    )
    if not registered:
        raise RuntimeError("Failed to register delegated Temporal execution")
    return {"team_id": team_id, "team_task_id": task_id, "registered": True}


@activity.defn(name="agent.finalize_team.v1")
async def finalize_agent_team(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Finalize a lead's team after all delegated tasks become terminal."""
    from services.agent_team import get_agent_team_service

    team_id = str(payload.get("team_id") or "")
    if not team_id:
        raise ValueError("team_id is required")
    service = get_agent_team_service()
    tasks = await service.database.get_team_tasks(team_id)
    # Child completion is only a submission.  The lead (or a human operator)
    # must explicitly accept every required task before the execution team can
    # be finalized.
    if any(task.get("status") not in {"accepted", "failed", "cancelled", "skipped"} for task in tasks):
        return {"team_id": team_id, "status": "active"}
    status = "failed" if any(task.get("status") == "failed" for task in tasks) else "completed"
    if not await service.database.update_team_status(team_id, status):
        raise RuntimeError(f"Failed to finalize team as {status}")
    if service.broadcaster:
        await service.broadcaster.broadcast_team_event(
            team_id, f"team_{status}", {"team_id": team_id, "status": status}
        )
    return {"team_id": team_id, "status": status}


def collect_agent_activities() -> List[Any]:
    """Return the F4.B agent activities for worker registration.

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
        store_agent_output,
        refresh_agent_tools,
        begin_agent_delegation,
        queue_agent_delegation,
        acquire_subagent_permit,
        release_subagent_permit,
        register_task_execution,
        finish_agent_delegation,
        finalize_agent_team,
    ]
