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
- :func:`compact_context` — summarize the live conversation via
  ``CompactionService`` when token thresholds trip. The workflow swaps
  its ``messages`` for the summary; token accounting stays in the
  workflow state.

Determinism: every activity is a leaf computation (LLM ainvoke, DB
write, summarisation). The workflow that calls them is sandboxed=False
and may read frozen registry dicts deterministically.

References:
- Temporal AI Cookbook -- https://docs.temporal.io/ai-cookbook
- ``temporalio.contrib.openai_agents.activity_as_tool`` mirrors this
  pattern; we re-implement manually because OpenCompany uses native SDK
  (``chat_model.bind_tools`` for schema generation) rather than the
  OpenAI Agents SDK.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Mapping, Optional

from temporalio import activity
from temporalio.exceptions import ApplicationError


logger = logging.getLogger(__name__)

# Byte ceiling for the stored conversation returned by ``prepare_agent_payload``
# as a fresh-run seed; the whole activity result must stay under Temporal's
# 2 MiB payload error limit. Over the cap the load raises the non-retryable
# ``ConversationTooLarge`` so the user clears the conversation from the
# Context panel, rather than the agent silently running without its memory.
# New transcripts stay well below it: each external tool result is capped and
# ``AgentWorkflow`` clears old results past ``transcript_budget_bytes`` (see
# ``agent_context_pressure``). Same value as ``_CAN_TRANSCRIPT_MAX_BYTES`` on
# the rollover path.
_SEED_TRANSCRIPT_MAX_BYTES = 1_000_000

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
    if any(
        (getattr(m, "role", None) or getattr(m, "type", ""))
        in ("user", "assistant", "human", "ai", "tool")
        for m in messages
    ):
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


def _unsaved_endpoint_error(provider: str) -> Optional[ApplicationError]:
    """The failure for a named endpoint with no key row, else ``None``.

    Saving an endpoint always stores a key (the user's or the declared
    placeholder), so a missing one means the endpoint was removed or never
    chosen. Retrying cannot fix that, and ``MissingAgentProviderCredential``
    is a type the AgentWorkflow shows to the user as is.
    """
    from services.llm.endpoints import unconfigured_endpoint_message

    message = unconfigured_endpoint_message(provider)
    if not message:
        return None
    return ApplicationError(message, type="MissingAgentProviderCredential", non_retryable=True)


async def _resolve_activity_api_key(payload: Dict[str, Any]) -> str:
    """Resolve a provider credential inside an activity, never workflow state.

    Pre-cutover histories already contain ``api_key`` and continue to use it.
    New histories carry only provider/node identifiers so credentials are not
    written into Temporal history. The database fallback preserves nodes that
    keep a key in their own configuration instead of the credential service.
    """

    recorded = payload.get("api_key")
    if isinstance(recorded, str) and recorded:
        return recorded

    from core.container import container

    provider = str(payload.get("provider") or "")
    auth = container.auth_service()
    api_key = await auth.get_api_key(provider)
    if not api_key:
        api_key = await auth.get_api_key(f"{provider}_api_key")

    if not api_key and payload.get("node_id"):
        database = container.database()
        parameters = (
            await database.get_node_parameters(str(payload["node_id"])) or {}
        )
        options = parameters.get("options") or {}
        candidate = parameters.get("api_key") or options.get("api_key")
        if isinstance(candidate, str) and candidate:
            api_key = candidate

    if not api_key:
        raise _unsaved_endpoint_error(provider) or ApplicationError(
            f"API key for provider {provider!r} is not configured",
            type="MissingAgentProviderCredential",
            non_retryable=True,
        )
    return str(api_key)


def _as_temporal_llm_error(error: Any):
    """Translate a structured SDK failure at the Temporal boundary.

    Raw provider messages can contain payload fragments or internal endpoint
    details. The user-facing message is category-based, while the complete
    structured diagnostics remain JSON-safe in ``ApplicationError.details``
    and therefore in Temporal history for operators.
    """

    from temporalio.exceptions import ApplicationError

    category_value = getattr(getattr(error, "category", None), "value", None)
    category = str(category_value or "unknown")
    provider = str(getattr(error, "provider", None) or "LLM provider")
    safe_message = str(
        getattr(error, "user_message", None)
        or "The language model provider request failed."
    )
    details = {
        "provider": provider,
        "category": category,
        "retryable": bool(getattr(error, "retryable", False)),
        "status_code": getattr(error, "status_code", None),
        "provider_code": getattr(error, "provider_code", None),
        "request_id": getattr(error, "request_id", None),
        "retry_after": getattr(error, "retry_after", None),
        "retry_after_raw": getattr(error, "retry_after_raw", None),
    }
    # Honor the provider's own pacing: a 429 with Retry-After should wait
    # exactly that long before the next attempt instead of the policy's
    # generic backoff.
    retry_after = details["retry_after"]
    next_retry_delay = (
        timedelta(seconds=float(retry_after))
        if details["retryable"]
        and isinstance(retry_after, (int, float))
        and retry_after > 0
        else None
    )
    return ApplicationError(
        safe_message,
        details,
        type=f"LLMError.{category}",
        non_retryable=not details["retryable"],
        next_retry_delay=next_retry_delay,
    )


def _native_tool_definition(tool: Any) -> Dict[str, Any]:
    """Serialize an AgentToolSpec (or legacy StructuredTool) for history."""

    definition = getattr(tool, "definition", None)
    if definition is not None:
        return {
            "name": str(definition.name),
            "description": str(definition.description or ""),
            "parameters": dict(definition.parameters or {}),
        }

    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        parameters = dict(schema)
    elif schema is not None and hasattr(schema, "model_json_schema"):
        parameters = schema.model_json_schema()
    elif schema is not None and hasattr(schema, "schema"):
        parameters = schema.schema()
    else:
        parameters = {"type": "object", "properties": {}}

    from services.plugin.tool import inline_schema_refs

    return {
        "name": str(getattr(tool, "name", "")),
        "description": str(getattr(tool, "description", "") or ""),
        "parameters": inline_schema_refs(parameters),
    }


async def _await_with_llm_heartbeats(
    awaitable: Any,
    *,
    detail: str,
    interval_seconds: float = 20.0,
) -> Any:
    """Await one buffered SDK request while heartbeating the activity."""

    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=interval_seconds,
                )
            except TimeoutError:
                activity.heartbeat(detail)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _execute_native_llm_step(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one native-SDK model turn and retain the v1 activity envelope."""

    from core.container import container
    from services.agent_runtime import run_native_llm_step
    from services.llm.messages import filter_empty_messages
    from services.llm.protocol import (
        LLMError,
        Message,
        ThinkingConfig,
        ToolDef,
        message_to_wire,
        messages_from_wire,
    )

    messages = filter_empty_messages(
        messages_from_wire(payload.get("messages") or [])
    )
    _ensure_llm_contents(messages)

    tool_defs: List[ToolDef] = []
    for value in payload.get("tools") or []:
        definition = value.get("definition", value)
        tool_defs.append(
            ToolDef(
                name=str(definition.get("name") or ""),
                description=str(definition.get("description") or ""),
                parameters=dict(
                    definition.get("parameters")
                    or {"type": "object", "properties": {}}
                ),
            )
        )

    thinking_data = payload.get("thinking_config")
    thinking = (
        ThinkingConfig(**thinking_data)
        if isinstance(thinking_data, dict)
        else None
    )
    api_key = await _resolve_activity_api_key(payload)
    try:
        response = await _await_with_llm_heartbeats(
            run_native_llm_step(
                container.chat_unifier(),
                provider=payload["provider"],
                api_key=api_key,
                messages=messages,
                model=payload["model"],
                temperature=payload.get("temperature", 0.7),
                max_tokens=payload.get("max_tokens", 4096),
                thinking=thinking,
                tools=tool_defs,
                context_management=payload.get("context_management"),
                # Temporal owns the one-shot retry contract. SDK retries could
                # otherwise re-bill a request after an ambiguous transport loss.
                sdk_max_retries=0,
                explicit_max_retries=0,
                # Keep structured metadata until this activity boundary,
                # where it is converted to a safe Temporal failure.
                translate_errors=False,
            ),
            detail=f"LLM step waiting: {payload.get('model')}",
        )
    except LLMError as error:
        raise _as_temporal_llm_error(error) from error

    assistant = response.assistant_message or Message(
        role="assistant",
        content=response.content or "",
        tool_calls=list(response.tool_calls or []),
    )
    assistant_wire = message_to_wire(assistant)
    usage = asdict(response.usage)

    # Persist the conversation exactly as it happened: the message list this
    # activity just sent to the provider plus the reply it got back.
    # ``messages`` above IS the argument handed to ``ChatUnifier.chat`` —
    # nothing is rebuilt, inferred, or re-typed, so the stored conversation
    # cannot drift from the request.
    await _save_conversation(
        payload,
        sent=[dict(message_to_wire(message)) for message in messages],
        assistant_wire=dict(assistant_wire),
    )

    if response.tool_calls:
        calls = []
        for tool_call in response.tool_calls:
            call = {
                "id": tool_call.id,
                "name": tool_call.name,
                "args": tool_call.args,
            }
            raw_arguments = getattr(tool_call, "raw_arguments", None)
            parse_error = getattr(tool_call, "parse_error", None)
            if raw_arguments is not None:
                call["raw_arguments"] = raw_arguments
            if parse_error:
                call["parse_error"] = parse_error
            calls.append(call)
        result = {
            "kind": "tool_calls",
            "assistant_message": assistant_wire,
            "calls": calls,
            "usage": usage,
        }
        if payload.get("include_finish_reason"):
            result["finish_reason"] = response.finish_reason
        return result

    result = {
        "kind": "final",
        "assistant_message": assistant_wire,
        "content": response.content or "",
        "thinking": response.thinking,
        "usage": usage,
    }
    if payload.get("include_finish_reason"):
        result["finish_reason"] = response.finish_reason
    return result


async def _save_conversation(
    payload: Dict[str, Any],
    *,
    sent: List[Dict[str, Any]],
    assistant_wire: Dict[str, Any],
) -> None:
    """Persist the full transcript for this turn, when a key is attached.

    Persistence only: it never influences the request. ``sent`` is the
    exact list handed to the provider; ``sent + [assistant]`` IS the
    conversation after this turn, saved whole (last-write-wins upsert per
    key). Living inside the LLM activity means per-turn durability with
    zero extra activities and zero Temporal-history payload.
    """
    key = payload.get("conversation_key")
    if not isinstance(key, dict):
        return
    try:
        from core.container import container
        from services.agent_context import save_conversation

        messages = [*sent, assistant_wire]
        saved_bytes = len(json.dumps(messages, default=str).encode("utf-8"))
        if saved_bytes > _SEED_TRANSCRIPT_MAX_BYTES // 2:
            # The next firing refuses to load a row over the cap
            # (ConversationTooLarge), so say so while there is still room.
            activity.logger.warning(
                f"Saved conversation for agent {key.get('agent_node_id')!r} "
                f"(workflow {key.get('workflow_id')!r}, generation "
                f"{key.get('generation')}) is {saved_bytes} bytes, over half "
                f"the {_SEED_TRANSCRIPT_MAX_BYTES}-byte limit a later run can "
                "load"
            )
        await save_conversation(
            container.database(),
            workflow_id=str(key.get("workflow_id") or ""),
            generation=int(key.get("generation") or 0),
            agent_node_id=str(key.get("agent_node_id") or ""),
            messages=messages,
        )
    except Exception:
        # Persistence must never fail the run. The provider has already
        # been called and billed by this point, so raising here would fail
        # the turn — and, for a team lead, stall its next delegation —
        # over a bookkeeping write.
        activity.logger.warning(
            "Conversation save failed; execution continues",
            exc_info=True,
        )


@activity.defn(name="agent.execute_llm_step")
async def execute_llm_step(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run one LLM turn with bound tools and return the structured response.

    The single LLM-step activity. There is exactly one implementation: the
    request is always built from ``payload["messages"]``. A ``context_ref``
    only asks for the turn to be journalled afterwards — attaching a Context
    node observes the agent, it never changes what the agent sends.

    ``payload`` shape::

        {
            "provider": "anthropic" | "openai" | ...,
            "model": "claude-sonnet-4-6",
            "node_id": "...",                  # credential lookup only
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

    result = await _execute_native_llm_step(payload)
    activity.heartbeat("LLM step: model returned")
    return result


@activity.defn(name="agent.persist_turn")
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


@activity.defn(name="agent.broadcast_progress")
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
    # Tool phases normally keep the AgentWorkflow itself in its existing
    # ``executing`` state, so their callers do not need to repeat a status on
    # every progress event.  They *do* carry the LLM-visible tool name though.
    # Previously this activity only copied payload details into ``node_status``
    # when ``status`` was explicitly present.  The parallel ``agent_progress``
    # CloudEvent contains phase/counter fields only, which meant normal
    # Temporal agents rendered a generic "Using Tool" phase and immediately
    # lost the actual capability name.  Team leads appeared healthier because
    # their Task Manager/skill paths also emitted dedicated status events.
    capability_data = {
        key: payload[key]
        for key in ("tool_name", "tool_node_id", "tool_failed")
        if payload.get(key) is not None
    }
    status_for_node = status or ("executing" if capability_data else None)
    if status_for_node:
        lifecycle_data = {
            "agent_type": "temporal",
            **({"phase": phase} if phase else {}),
            **capability_data,
        }
        if phase == "starting":
            lifecycle_data.update({"active_skills": [], "last_skills": [], "last_tool_name": None, "last_capability": None})
        await broadcaster.update_node_status(
            node_id,
            status_for_node,
            lifecycle_data,
            workflow_id=workflow_id,
        )

    # Tool use is a first-class CloudEvents occurrence.  Keep it separate
    # from the raw node-status projection: the latter is a reconnect snapshot,
    # while this envelope supplies durable identity, ownership and retry
    # deduplication semantics to live consumers.
    tool_name = str(payload.get("tool_name") or "")
    if tool_name and tool_name != "Skill" and phase in {"executing_tool", "tool_completed"}:
        state = (
            "started"
            if phase == "executing_tool"
            else ("failed" if payload.get("tool_failed") else "completed")
        )
        identity = "|".join(
            (
                str(workflow_id or ""),
                str(payload.get("execution_id") or ""),
                node_id,
                str(payload.get("tool_call_id") or payload.get("iteration") or ""),
                tool_name,
                state,
            )
        )
        await broadcaster.broadcast_agent_capability(
            node_id,
            capability_kind="tool",
            capability_name=tool_name,
            state=state,
            workflow_id=workflow_id,
            execution_id=str(payload.get("execution_id") or "") or None,
            root_execution_id=str(payload.get("root_execution_id") or "") or None,
            target_node_id=str(payload.get("tool_node_id") or "") or None,
            provider=str(payload.get("provider") or "") or None,
            invocation_source="temporal",
            tool_call_id=str(payload.get("tool_call_id") or "") or None,
            error_code=("TOOL_EXECUTION_FAILED" if state == "failed" else None),
            event_id=f"agent-capability-{hashlib.sha256(identity.encode()).hexdigest()}",
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


@activity.defn(name="agent.store_output")
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


@activity.defn(name="agent.prepare_payload")
async def prepare_agent_payload(context: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve everything ``AgentWorkflow`` needs from the canvas + DB.

    The workflow itself cannot do DB lookups or tool builds
    (deterministic-replay constraint). This activity runs *before* the
    workflow is scheduled and returns the fully-resolved payload.

    Mirrors the prep half of ``services.ai.AIService.execute_agent``,
    minus the agent loop (which lives in ``AgentWorkflow.run`` for the
    F4.B Temporal path and ``services.agent_runtime.run_native_agent_loop``
    in-process):

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
    from services.llm.config import (
        get_default_model_async,
        is_model_valid_for_provider,
        resolve_max_tokens,
        resolve_temperature,
    )
    from services.llm.protocol import ThinkingConfig
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
        # ``inputs`` holds this run's direct upstream outputs (the firing
        # trigger, for a deployed agent). Resolving from it keeps two
        # concurrent firings from reading each other's message out of the
        # shared session store.
        parameters = await workflow_service._param_resolver.resolve(
            parameters,
            node_id,
            nodes,
            edges,
            session_id,
            run_outputs=context.get("inputs") or None,
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
        endpoint_error = _unsaved_endpoint_error(provider)
        if endpoint_error:
            raise endpoint_error
        raise RuntimeError(
            f"API key for provider {provider!r} required for AgentWorkflow " f"node {node_id!r}; configure it in the Credentials Modal."
        )

    max_tokens = resolve_max_tokens(flattened, model, provider)

    thinking_config_obj: Any = None
    thinking_config_dict: Any = None
    if flattened.get("thinking_enabled"):
        thinking_config_obj = ThinkingConfig(
            enabled=True,
            budget=int(flattened.get("thinking_budget", 2048)),
            effort=flattened.get("reasoning_effort", "medium"),
            # Do not invent a level for Gemini 2.5/Vertex; those models reject
            # receiving thinking_level alongside a token budget.
            level=flattened.get("thinking_level"),
            format=flattened.get("reasoning_format", "parsed"),
        )
        thinking_config_dict = {
            "enabled": True,
            "budget": thinking_config_obj.budget,
            "effort": thinking_config_obj.effort,
            "level": thinking_config_obj.level,
            "format": thinking_config_obj.format,
        }

    temperature = resolve_temperature(
        flattened,
        model,
        provider,
        bool(thinking_config_obj and thinking_config_obj.enabled),
    )

    # ---- Edge walking ---------------------------------------------------
    # Carry the execution context through rather than rebuilding it from a
    # fixed key list. Connected nodes decide what they need from it: the
    # Context descriptor requires ``generation`` and refuses to build without
    # one, then reads ``session_id`` / ``explicit_session_id`` /
    # ``delegated_task_id`` to pick its thread. A four-key rebuild dropped all
    # of them, so every Temporal run walked edges as if it had no admitted
    # generation and the agent journalled nothing — while the in-process path
    # (nodes/agent/_inline.py) passed the context whole and worked.
    walk_context = {
        **context,
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
    # The descriptor is forwarded verbatim as ``context_descriptor``. Reading
    # it only through the legacy markdown keys below silently yields an empty
    # transcript for any descriptor that does not carry them, which is how a
    # journal-backed agent ends up starting every run with no history. The
    # keys are read with ``.get`` and never interpreted here — whichever
    # plugin produced the descriptor owns its meaning.
    memory_node_id = ""
    memory_content = ""
    memory_window_size = 10
    context_descriptor: Dict[str, Any] = dict(memory_data or {})
    if memory_data:
        memory_node_id = memory_data.get("node_id") or ""
        memory_content = memory_data.get("memory_content") or ""
        memory_window_size = int(memory_data.get("window_size") or 10)

    # ---- Conversation (plain JSON transcript) ---------------------------
    # A connected Context node opts the agent into durable conversation
    # persistence: key (workflow_id, generation, agent_node_id), loaded here
    # as the fresh-run seed and saved per turn inside the LLM step. Every
    # firing — chat messages AND task-completion reviews — continues the
    # same conversation; Reset admits a new generation, which is a new key.
    # Load failures are LOUD: running the agent with its memory silently
    # missing wastes billed tokens on a run that has forgotten everything.
    # A transient DB error gets the activity retry budget; an over-sized
    # transcript is user-actionable (clear or compact) and fails fast.
    # See docs-internal/agent_context_flow.md.
    conversation: List[Dict[str, Any]] = []
    conversation_key: Optional[Dict[str, Any]] = None
    generation = int(context.get("generation") or 0)
    if (
        context_descriptor.get("kind") == "context"
        and workflow_id
        and generation > 0
    ):
        conversation_key = {
            "workflow_id": str(workflow_id),
            "generation": generation,
            "agent_node_id": node_id,
        }
        try:
            from services.agent_context import load_conversation

            conversation = await load_conversation(
                database,
                **conversation_key,
            )
        except Exception as exc:
            raise ApplicationError(
                f"Conversation load failed for agent {node_id!r} "
                f"(generation {generation}): {type(exc).__name__}",
                type="ConversationLoadFailed",
                non_retryable=False,
            ) from exc
        if conversation:
            seed_bytes = len(
                json.dumps(conversation, default=str).encode("utf-8")
            )
            if seed_bytes > _SEED_TRANSCRIPT_MAX_BYTES:
                raise ApplicationError(
                    f"Stored conversation for agent {node_id!r} is "
                    f"{seed_bytes} bytes (limit "
                    f"{_SEED_TRANSCRIPT_MAX_BYTES}). Clear the conversation "
                    "from the Context panel, then run again. Settings > "
                    "Tool Result Limit caps how much one tool call can add "
                    "to it.",
                    type="ConversationTooLarge",
                    non_retryable=True,
                )

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
    from services.skill_runtime import skill_tool_info

    effective_tool_data = list(tool_data or [])
    progressive_skill_tool = skill_tool_info(skill_data or [], node_id)
    if progressive_skill_tool:
        effective_tool_data.append(progressive_skill_tool)
    for tool_info in effective_tool_data:
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
                # Provider-neutral function declaration for the native
                # activity branch.  It is JSON-only so it can live safely in
                # Temporal history and never requires rebuilding a
                # StructuredTool inside the native LLM activity.
                "definition": _native_tool_definition(tool),
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
        from services.plugin.edge_walker import format_teammate_roster_line

        delegates = "\n".join(
            format_teammate_roster_line({**(tool.get("tool_info") or {}), "node_type": tool["node_type"]})
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
            # A disabled service must actually disable compaction — the
            # workflow treats a falsy threshold as "never compact".
            if cfg.get("enabled"):
                compaction_threshold = int(cfg.get("context_token_threshold") or 0) or None
    except Exception:  # noqa: BLE001 — defensive, optional feature
        compaction_threshold = None
        activity.logger.warning(
            f"Compaction threshold unavailable for agent {node_id!r}; this "
            "run will not summarize its conversation",
            exc_info=True,
        )

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
            prompt = json.dumps(out, ensure_ascii=False, default=str)

    # Read user-overridable globals once at prep time.
    #   - auto_rebind_tools: forwarded into every per-tool activity so
    #     agentBuilder's summary + the workflow rebind branch read the
    #     same value.
    #   - agent_recursion_limit: applied to the agent loop's hard step
    #     cap. Per-user override beats env Settings.
    #   - tool_result_max_chars: the most one external tool result may add
    #     to the transcript. Per-user override beats env Settings.
    auto_rebind_tools = True
    settings_recursion_limit: Optional[int] = None
    from core.config import Settings as _DelegationSettings
    from services.tool_output import resolve_tool_output_limit

    delegation_settings = _DelegationSettings()
    max_concurrent_subagents = int(delegation_settings.max_concurrent_subagents)
    max_delegation_depth = int(delegation_settings.max_delegation_depth)
    tool_result_max_chars = resolve_tool_output_limit(None, delegation_settings)
    try:
        user_settings = await database.get_user_settings()
        if user_settings is not None:
            auto_rebind_tools = bool(
                user_settings.get("auto_rebind_tools_after_canvas_change", True)
            )
            tool_result_max_chars = resolve_tool_output_limit(
                user_settings, delegation_settings
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

    from services.temporal.agent_context_pressure import (
        CONTEXT_PRESSURE_VERSION,
        transcript_budget_bytes,
    )

    return {
        "node_id": node_id,
        "node_type": node_type,
        "workflow_id": workflow_id,
        "session_id": session_id,
        "provider": provider,
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system_message": system_message,
        "user_prompt": prompt,
        "tools": tools_payload,
        "memory_node_id": memory_node_id,
        "memory_content": memory_content,
        "memory_window_size": memory_window_size,
        "context_descriptor": context_descriptor,
        "conversation": conversation,
        "conversation_key": conversation_key,
        "max_iterations": effective_recursion_limit,
        "thinking_config": thinking_config_dict,
        "compaction_threshold": compaction_threshold,
        # Transcript-pressure controls. Recording them in this result pins
        # the run's behavior on replay: a history recorded before these keys
        # existed replays through AgentWorkflow's original paths.
        "tool_result_max_chars": tool_result_max_chars,
        "transcript_budget_bytes": transcript_budget_bytes(),
        "context_pressure_version": CONTEXT_PRESSURE_VERSION,
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


@activity.defn(name="agent.refresh_tools")
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
                "definition": _native_tool_definition(tool),
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


@activity.defn(name="agent.skill.invoke")
async def invoke_agent_skill(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Retry-safe, history-recorded progressive skill invocation."""
    from services.skill_runtime import execute_skill_tool

    node_data = dict(payload.get("node_data") or {})
    descriptors = node_data.pop("skill_descriptors", [])
    action_args = {
        key: node_data.get(key)
        for key in ("action", "skill_name", "path", "query", "cursor", "limit")
        if key in node_data
    }
    return await execute_skill_tool(
        action_args,
        {
            "parameters": {"skill_descriptors": descriptors, "agent_node_id": payload.get("parent_node_id")},
            "workflow_id": payload.get("workflow_id"),
            "execution_id": payload.get("execution_id"),
            "root_execution_id": payload.get("root_execution_id"),
            "parent_node_id": payload.get("parent_node_id"),
            # New histories carry the provider call id. The Temporal activity
            # id is a stable fallback for pre-marker histories and remains
            # identical across activity retries.
            "tool_call_id": payload.get("tool_call_id") or activity.info().activity_id,
            "provider": payload.get("provider"),
            "skill_invocation_source": "temporal",
        },
    )


@activity.defn(name="agent.skill.clear")
async def clear_agent_skills(payload: Dict[str, Any]) -> Dict[str, Any]:
    from services.skill_runtime import clear_skill_turn

    await clear_skill_turn(
        str(payload.get("workflow_id") or ""),
        str(payload.get("execution_id") or ""),
        str(payload.get("agent_node_id") or ""),
    )
    return {"cleared": True}


@activity.defn(name="agent.begin_delegation")
async def begin_agent_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotently persist and claim a delegation before child startup."""
    from services.agent_team import get_agent_team_service
    from temporalio.exceptions import ApplicationError

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

    terminal_statuses = {
        "submitted",
        "accepted",
        "failed",
        "cancelled",
        "skipped",
        "completed",
    }

    def raise_terminal(status: str) -> None:
        raise ApplicationError(
            f"Delegated team task {task_id} is already terminal ({status})",
            type="DelegationTaskTerminal",
            non_retryable=True,
        )

    status = str(task.get("status") or "")
    if status in {"pending", "queued"}:
        claimed = await service.claim_task(team_id, task_id, child_id)
        tasks = await service.database.get_team_tasks(team_id)
        task = next((item for item in tasks if item.get("id") == task_id), None)
        status = str((task or {}).get("status") or "")
        if status in terminal_statuses:
            raise_terminal(status)
        if (
            not claimed
            or status != "running"
            or task.get("assigned_to") != child_id
        ):
            raise RuntimeError("Failed to claim delegated team task")
    elif status in terminal_statuses:
        raise_terminal(status)
    elif status != "running" or task.get("assigned_to") != child_id:
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

    # Cancellation may commit while the idempotent assignment message is
    # being persisted. Re-read immediately before returning permission to
    # start the child so a terminal task is never reported as claimed.
    tasks = await service.database.get_team_tasks(team_id)
    task = next((item for item in tasks if item.get("id") == task_id), None)
    status = str((task or {}).get("status") or "")
    if status in terminal_statuses:
        raise_terminal(status)
    if (
        not task
        or status != "running"
        or task.get("assigned_to") != child_id
    ):
        raise RuntimeError("Delegated team task lost its claim before child startup")
    return {"team_id": team_id, "team_task_id": task_id, "claimed": True}


@activity.defn(name="agent.queue_delegation")
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


@activity.defn(name="agent.cancel_delegation")
async def cancel_agent_delegation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist cancellation as a terminal state without failure requeueing."""
    from services.agent_team import get_agent_team_service

    team_id = str(payload.get("team_id") or "")
    task_id = str(payload.get("team_task_id") or "")
    child_id = str(payload.get("child_agent_node_id") or "")
    parent_id = str(payload.get("parent_agent_node_id") or "")
    reason = str(
        payload.get("reason")
        or payload.get("error")
        or "Delegated agent cancelled"
    )
    if not all((team_id, task_id, child_id, parent_id)):
        raise ValueError("Incomplete delegation cancellation payload")

    service = get_agent_team_service()
    task = await service.cancel_delegation(team_id, task_id, reason)
    if task is None:
        raise RuntimeError("Failed to cancel delegated team task")

    status = str(task.get("status") or "")
    cancellation_applied = bool(task.get("cancellation_applied"))
    if status == "cancelled":
        persisted_reason = str(task.get("cancellation_reason") or reason)
        message = await service.send_message(
            team_id,
            child_id,
            f"Task {task_id} cancelled: {persisted_reason}",
            to_agent=parent_id,
            message_type="error",
            event_id=str(
                payload.get("terminal_event_id") or f"{task_id}:cancelled"
            ),
            extra_data={
                "status": "cancelled",
                "task_id": task_id,
                "reason": persisted_reason,
                "root_execution_id": payload.get("root_execution_id"),
                "trace_id": payload.get("trace_id"),
            },
        )
        if not message:
            raise RuntimeError("Failed to persist delegation cancellation event")
    else:
        persisted_reason = None

    return {
        "team_id": team_id,
        "team_task_id": task_id,
        "status": status,
        "cancelled": status == "cancelled",
        "cancellation_applied": cancellation_applied,
        "reason": persisted_reason,
    }


def _subagent_lease_id(
    permit_id: str,
    temporal_activity_id: str,
    attempt: int,
) -> str:
    """Return the bounded physical lease identity for one activity attempt."""
    identity = f"{permit_id}\0{temporal_activity_id}\0{attempt}".encode()
    return f"subagent-lease-v2-{hashlib.sha256(identity).hexdigest()}"


@activity.defn(name="agent.acquire_subagent_permit")
async def acquire_subagent_permit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Poll the durable root coordinator until this delegation is admitted."""
    from services.agent_team import get_agent_team_service

    root_id = str(payload.get("root_execution_id") or "")
    permit_id = str(payload.get("permit_id") or "")
    limit = max(1, int(payload.get("limit") or 3))
    lease_version = int(payload.get("lease_version") or 1)
    if not root_id or not permit_id:
        raise ValueError("root_execution_id and permit_id are required")
    if lease_version not in {1, 2}:
        raise ValueError("lease_version must be 1 or 2")

    service = get_agent_team_service()
    lease_id = permit_id
    attempt = 1
    if lease_version == 2:
        info = activity.info()
        temporal_activity_id = str(info.activity_id)
        attempt = max(1, int(info.attempt))
        lease_id = _subagent_lease_id(
            permit_id,
            temporal_activity_id,
            attempt,
        )
        for prior_attempt in range(1, attempt):
            prior_lease_id = _subagent_lease_id(
                permit_id,
                temporal_activity_id,
                prior_attempt,
            )
            if not await service.release_subagent_permit(
                root_id,
                prior_lease_id,
            ):
                raise RuntimeError(
                    "Failed to reconcile prior subagent permit lease "
                    f"{prior_lease_id}"
                )

    try:
        while True:
            if await service.acquire_subagent_permit(root_id, lease_id, limit):
                result = {
                    "root_execution_id": root_id,
                    "permit_id": permit_id,
                    "acquired": True,
                }
                if lease_version == 2:
                    result.update({
                        "lease_version": 2,
                        "lease_id": lease_id,
                        "attempt": attempt,
                    })
                return result

            heartbeat = {
                "root_execution_id": root_id,
                "permit_id": permit_id,
                "status": "queued",
            }
            if lease_version == 2:
                heartbeat.update({
                    "lease_version": 2,
                    "lease_id": lease_id,
                    "attempt": attempt,
                })
            activity.heartbeat(heartbeat)
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        # The database commit may have completed even when cancellation wins
        # the await race and the activity result never reaches workflow
        # history. Always compensate the attempted permit locally. Releasing
        # a permit that was never acquired is an expected no-op here.
        try:
            compensated = await service.release_subagent_permit(
                root_id,
                lease_id,
            )
            if not compensated:
                raise RuntimeError("Permit compensation was rejected")
        except Exception as release_exc:  # noqa: BLE001
            logger.error(
                "Failed to compensate cancelled subagent permit acquisition",
                extra={
                    "root_execution_id": root_id,
                    "permit_id": permit_id,
                    "lease_id": lease_id,
                    "error": str(release_exc),
                },
            )
        raise


@activity.defn(name="agent.release_subagent_permit")
async def release_subagent_permit(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Idempotently release a durable root-wide concurrency permit."""
    from services.agent_team import get_agent_team_service

    root_id = str(payload.get("root_execution_id") or "")
    permit_id = str(payload.get("permit_id") or "")
    lease_id = str(payload.get("lease_id") or permit_id)
    lease_version = int(payload.get("lease_version") or (2 if payload.get("lease_id") else 1))
    if not root_id or not permit_id or not lease_id:
        raise ValueError("root_execution_id and permit_id are required")
    released = await get_agent_team_service().release_subagent_permit(
        root_id,
        lease_id,
    )
    if not released:
        raise RuntimeError("Failed to release subagent permit")
    result = {
        "root_execution_id": root_id,
        "permit_id": permit_id,
        "released": True,
    }
    if lease_version == 2:
        result.update({"lease_version": 2, "lease_id": lease_id})
    return result


@activity.defn(name="agent.finish_delegation")
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


@activity.defn(name="agent.register_task_execution")
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


@activity.defn(name="agent.finalize_team")
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
        prepare_agent_payload,
        broadcast_agent_progress,
        store_agent_output,
        refresh_agent_tools,
        invoke_agent_skill,
        clear_agent_skills,
        begin_agent_delegation,
        queue_agent_delegation,
        # Scheduled unconditionally by the cancellation-unwind paths in
        # AgentWorkflow/DelegatedTaskWorkflow. Its absence here left
        # agent.cancel_delegation.v1 unregistered, so every cancelled
        # delegation burned DELEGATION_CLEANUP_RETRY (10 attempts) against
        # an unknown activity type and the failure was swallowed, leaving
        # team task rows non-terminal.
        cancel_agent_delegation,
        acquire_subagent_permit,
        release_subagent_permit,
        register_task_execution,
        finish_agent_delegation,
        finalize_agent_team,
        # Conversation persistence is inside execute_llm_step (save) and
        # prepare_agent_payload (load); the summarizer is the one extra
        # activity this surface needs.
        compact_context,
    ]


# ---------------------------------------------------------------------------
# Conversation persistence surface.
#
# Persistence observes the agent; it never feeds the request directly.
# ``prepare_agent_payload`` loads the stored conversation as a fresh-run
# seed, ``_save_conversation`` (inside the LLM step) persists each turn,
# and ``compact_context`` summarizes the live conversation.
# ---------------------------------------------------------------------------

_SENSITIVE_GRAPH_KEYS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "password",
        "secret",
        "clientsecret",
        "authorization",
    }
)
def _normalise_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())
def _strip_credentials(value: Any) -> Any:
    """Copy graph/runtime metadata without credential-shaped fields."""

    if isinstance(value, Mapping):
        return {
            str(key): _strip_credentials(item)
            for key, item in value.items()
            if _normalise_key(key) not in _SENSITIVE_GRAPH_KEYS
        }
    if isinstance(value, list):
        return [_strip_credentials(item) for item in value]
    if isinstance(value, tuple):
        return [_strip_credentials(item) for item in value]
    return value
def _non_retryable(message: str, error_type: str) -> ApplicationError:
    return ApplicationError(
        message,
        type=error_type,
        non_retryable=True,
    )
def _tool_identity(tool: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "name": str(tool.get("name") or ""),
        "node_id": str(tool.get("tool_node_id") or ""),
        "node_type": str(tool.get("node_type") or ""),
        "version": int(tool.get("version") or 1),
        "task_queue": str(tool.get("dispatch_task_queue") or ""),
    }
@activity.defn(name="agent.compact_context")
async def compact_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize the live conversation into a compact replacement.

    Simple by design: render the wire messages to text, ask
    ``CompactionService`` for its five-section summary, return it. The
    workflow swaps its ``messages`` for the summary. No journal side
    effects and no checkpoints — the next LLM turn journals a fresh
    ``request.snapshot`` containing the compacted messages, so the
    Context panel and any later rollover see the compacted state
    naturally.

    ``payload``::

        {
            "session_id": str,
            "node_id": str,
            "messages": [<message wire>],   # the live transcript
            "provider": str,
            "model": str,
        }

    Returns ``{"success": bool, "summary": str, "usage": dict}``.
    """

    from services.compaction import get_compaction_service
    from services.llm.protocol import message_from_wire

    activity.heartbeat("Compacting agent context")
    svc = get_compaction_service()
    if svc is None:
        # Singleton wired by the FastAPI lifespan (main.py). Retryable:
        # a worker bootstrap race resolves itself within the retry
        # policy's backoff window.
        raise ApplicationError(
            "CompactionService not initialized (worker bootstrap race)",
            type="CompactionFailed",
            non_retryable=False,
        )

    lines: List[str] = []
    for wire in payload.get("messages") or []:
        if not isinstance(wire, dict):
            continue
        try:
            message = message_from_wire(wire)
        except Exception:
            continue
        line = f"{message.role.upper()}: {message.content}"
        if message.tool_calls:
            line += "\nTOOL_CALLS: " + json.dumps(
                [
                    {"name": call.name, "args": call.args}
                    for call in message.tool_calls
                ],
                ensure_ascii=False,
                default=str,
            )
        if message.tool_call_id:
            line += (
                f"\nTOOL_RESULT_FOR: {message.name or message.tool_call_id}"
            )
        lines.append(line)

    rendered = "\n\n".join(lines)
    activity.logger.info(
        f"Compacting agent context: {len(lines)} messages "
        f"({len(rendered)} rendered chars) via "
        f"{payload['provider']}/{payload['model']}"
    )
    result = await svc.compact_context(
        session_id=str(payload.get("session_id") or "default"),
        node_id=payload["node_id"],
        memory_content=rendered,
        provider=payload["provider"],
        api_key=await _resolve_activity_api_key(payload),
        model=payload["model"],
        # Temporal owns activity retries; never repeat an ambiguous provider
        # request inside the activity or SDK.
        explicit_max_retries=0,
    )
    if result.get("success") and result.get("summary"):
        usage = result.get("usage") or {}
        activity.logger.info(
            f"Agent context compacted: summary {len(result['summary'])} "
            f"chars (summarizer usage: "
            f"in={usage.get('input_tokens', 0)} "
            f"out={usage.get('output_tokens', 0)})"
        )
    if not result.get("success") or not result.get("summary"):
        # Compaction is the run's pressure-relief valve. Raising (retryable)
        # gives transient summarizer failures the activity policy's retry
        # budget; if it still cannot compact, the workflow fails the run
        # loudly rather than letting the transcript grow unbounded.
        raise ApplicationError(
            f"Compaction failed: {result.get('error') or 'empty summary'}",
            type="CompactionFailed",
            non_retryable=False,
        )
    return result


