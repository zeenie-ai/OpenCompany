"""F4.B: ``AgentWorkflow`` — Temporal child workflow for AI agent loops.

Workflow-orchestrated alternative to the in-process ``_run_agent_loop``
inside ``services/ai.py``: each LLM turn is an activity, each tool call
is a per-type activity (registered via ``BaseNode.as_activity()``,
F4.A), and memory persistence happens per turn so a workflow failure
mid-loop doesn't lose progress.

Architecture (matches Temporal's AI Cookbook canonical pattern):

    MachinaWorkflow.run()
       └─> execute_child_workflow(AgentWorkflow, payload)
              loop:
              ├─> execute_activity(agent.execute_llm_step.v1)
              │      → returns "final" OR "tool_calls"
              ├─> if tool_calls:
              │      execute_activity(node.{tool_type}.v1) for each
              ├─> execute_activity(agent.persist_turn.v1)
              ├─> token check; if over threshold:
              │      execute_activity(agent.compact_memory.v1)
              └─> repeat until "final" or max_iterations

User decisions baked in (plan §15):
- **Path 1** (agent-as-child-workflow), confirmed.
- ``rlm_agent``, ``claude_code_agent`` are NOT migrated here. Their
  loops are externalised (RLM REPL / Claude CLI ``--resume`` session)
  and live in single Temporal activities via the F4.A per-type
  dispatch path. They never enter ``AgentWorkflow``.
- Memory appends per turn (not on completion).
- Tool activity failure (after retries) returns an error to the LLM as
  a ``ToolMessage`` and the agent continues — matches the in-process
  ``_run_agent_loop`` behaviour.

Determinism:
- ``sandboxed=False`` so we can import frozen registry dicts
  (``services.node_registry``) for tool name resolution.
- All non-deterministic operations (LLM calls, DB writes, broadcasts)
  go through activities. The workflow itself only mutates its own
  ``messages`` / ``iteration`` / ``token_total`` state.

References:
- https://docs.temporal.io/ai-cookbook
- https://github.com/temporal-community/temporal-ai-agent
- ``temporalio/sdk-python contrib/openai_agents/``
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy  # kept for type hints

from services.node_registry import get_node_class

from ._retry_policies import DEFAULT_ACTIVITY_RETRY, LLM_STEP_RETRY
from .workflow import AGENT_WORKFLOW_TYPES


# Activity timeouts. LLM step can stream for several minutes on
# reasoning models; tool calls vary widely (python: seconds, browser:
# minutes). Tool activities use the plugin-declared
# ``start_to_close_timeout`` automatically — we only set defaults here
# for the agent-specific activities.
LLM_STEP_TIMEOUT = timedelta(minutes=10)
PERSIST_TURN_TIMEOUT = timedelta(seconds=30)
COMPACT_MEMORY_TIMEOUT = timedelta(minutes=5)

# Tool activity defaults — plugin classes can override via
# ``cls.start_to_close_timeout`` (F4.A); these are floor values.
TOOL_STEP_TIMEOUT = timedelta(minutes=10)
TOOL_HEARTBEAT_TIMEOUT = timedelta(minutes=2)

# Bounded loop count to defend against a runaway LLM. Plugin classes
# override via ``payload["max_iterations"]`` (set by
# ``prepare_agent_payload`` from Settings.agent_recursion_limit). This
# module-level fallback fires only if the payload omits the key, which
# should never happen — kept as a defensive backstop.
def _default_max_iterations() -> int:
    """Read the env-backed default once, fall back to JSON via
    ``model_registry.get_agent_defaults`` so this still works in
    one-off CLI scripts that bypass Settings."""
    try:
        from core.config import Settings

        return int(Settings().agent_recursion_limit)
    except Exception:  # noqa: BLE001
        try:
            from services.model_registry import get_model_registry

            return int(get_model_registry().get_agent_defaults().get("recursion_limit") or 200)
        except Exception:  # noqa: BLE001
            return 200

# Retry policy for the agent's own activities (LLM step, persist,
# compact). Tool activities use their plugin's policy. Wave 12 D1:
# delegates to the shared constant so the policy's
# non_retryable_error_types include ``NodeUserError`` — user-correctable
# failures inside the LLM step fail fast instead of burning 3 retries.
AGENT_ACTIVITY_RETRY: RetryPolicy = DEFAULT_ACTIVITY_RETRY

# Temporal patch marker for per-call command identity and duplicate-name
# validation. Existing histories must retain their recorded activity/child
# ids and last-wins tool index; new histories take the isolated path.
TOOL_CALL_IDENTITY_V2_PATCH = "agent-tool-call-identity-v2"
TASK_MANAGER_DELEGATION_PATCH = "agent-task-manager-delegation-v1"
DUPLICATE_TOOL_NAME_ERROR_TYPE = "DuplicateToolNameError"


def _tool_activity_id_v2(tool_node_id: str, iteration: int, call_index: int) -> str:
    """Return a stable, unique id for one tool call in one agent turn."""
    return f"tool-{tool_node_id}-{iteration + 1}-{call_index + 1}"


def _delegation_child_id_v2(
    agent_workflow_id: str,
    tool_node_id: str,
    iteration: int,
    call_index: int,
) -> str:
    """Return a stable child id for one delegation call."""
    return f"{agent_workflow_id}-delegate-{tool_node_id}-{iteration + 1}-{call_index + 1}"


def _refresh_tools_activity_id_v2(tool_node_id: str, iteration: int, call_index: int) -> str:
    """Return a stable id for the hot-refresh owned by one tool call."""
    return f"refresh-tools-{tool_node_id}-{iteration + 1}-{call_index + 1}"


def _tool_call_metadata(
    *,
    agent_node_id: str,
    iteration: int,
    call_index: int,
    call: Dict[str, Any],
) -> Dict[str, Any]:
    """Serializable correlation fields shared by one call's commands."""
    return {
        "invoking_agent_node_id": agent_node_id,
        "agent_iteration": iteration + 1,
        "tool_call_index": call_index + 1,
        "tool_call_id": str(call.get("id", "") or ""),
    }


def _duplicate_visible_tool_name_conflicts(
    tools: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, str]]]:
    """Return deterministic provider-name conflicts for one agent."""
    grouped: Dict[str, List[Dict[str, str]]] = {}
    for tool in tools:
        name = str(tool.get("name", "") or "")
        tool_info = tool.get("tool_info") or {}
        grouped.setdefault(name, []).append(
            {
                "node_id": str(tool.get("tool_node_id") or "<missing-node-id>"),
                "label": str(tool_info.get("label") or tool.get("node_type") or "tool"),
            }
        )
    return {
        name: sorted(entries, key=lambda entry: (entry["node_id"], entry["label"]))
        for name, entries in sorted(grouped.items())
        if len(entries) > 1
    }


def _duplicate_visible_tool_name_error(tools: List[Dict[str, Any]]) -> Optional[str]:
    """Describe duplicate provider-visible names, or return ``None``.

    Tool dispatch is name based and providers also require a unique function
    surface. Reporting every conflicting node is safer than silently selecting
    the last entry or inventing aliases that change the LLM contract.
    """
    conflicts = _duplicate_visible_tool_name_conflicts(tools)
    if not conflicts:
        return None

    details: List[str] = []
    for name in sorted(conflicts):
        identities: List[str] = []
        for identity in conflicts[name]:
            identities.append(
                f"{identity['label']} ({identity['node_id']})"
            )
        details.append(f"{name!r}: {', '.join(identities)}")

    return (
        "Duplicate LLM-visible tool names are not allowed: "
        + "; ".join(details)
        + ". Assign a unique Tool Name to each connected tool."
    )


@workflow.defn(sandboxed=False, name="AgentWorkflow")
class AgentWorkflow:
    """Run an AI agent as a Temporal child workflow.

    Scheduled by ``MachinaWorkflow.run()`` when:
      - ``settings.temporal_agent_workflow_enabled`` is True, AND
      - the node type is in the migrating set (``aiAgent``,
        ``chatAgent``, 12 specialized agents, 2 team leads).

    ``rlm_agent`` / ``claude_code_agent`` skip this workflow and stay
    as F4.A per-type activities.
    """

    @workflow.run
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run the agent loop.

        ``context`` shape (same as the legacy ``execute_node_activity``
        context — the orchestrator passes the same dict it would have
        passed to an activity)::

            {
                "node_id": str,
                "node_type": str,
                "node_data": dict,    # node parameters from canvas
                "workflow_id": Optional[str],
                "session_id": str,
                "nodes": list,        # full canvas (for edge walking)
                "edges": list,
                "inputs": dict,       # upstream node outputs
                # Present only when spawned as a delegation child by a
                # parent AgentWorkflow's delegate_to_* tool call. This is
                # the per-invocation input contract (Temporal's
                # input-vs-config separation): it always wins over stored
                # node configuration in prepare_agent_payload.
                "invocation": {"task": str, "context": str},  # optional
                "parent_node_id": str,                        # optional
            }

        The workflow's FIRST step is to schedule
        ``agent.prepare_payload.v1`` which returns the fully-resolved
        payload (provider, model, api_key, tools, memory, ...). Doing
        prep INSIDE the workflow (as an activity) keeps the orchestrator
        ignorant of agent-specific concerns and means the workflow
        owns its setup — Temporal's recommended structure.

        The resolved payload looks like::

            {
                "provider": str,
                "model": str,
                "api_key": str,
                "max_tokens": int,
                "temperature": float,
                "system_message": str,
                "user_prompt": str,
                "tools": [
                    {
                        "name": str,        # LLM-facing name
                        "node_type": str,   # plugin type for activity dispatch
                        "version": int,     # plugin class version
                        "task_queue": str,  # plugin task_queue (queue routing future)
                        "tool_node_id": str,
                        "parameters": dict, # plugin params from DB
                        "tool_info": dict,  # raw collect_agent_connections entry — passed to execute_llm_step which rebuilds the StructuredTool via ai_service._build_tool_from_node
                    },
                ],
                "memory_node_id": Optional[str],
                "memory_content": str,          # pre-loaded markdown
                "memory_window_size": int,
                "max_iterations": int,
                "thinking_config": Optional[dict],
                "compaction_threshold": Optional[int],
            }

        Returns the final agent response, mirroring the shape
        ``services/ai.py:execute_agent`` returns today so downstream
        code (OutputPanel, edge inputs, etc.) doesn't change.
        """
        # Activity inputs and command ids are recorded in Temporal Event
        # History. Old histories take the exact legacy branches below; new
        # runs record this marker and receive per-call command identity plus
        # deterministic duplicate-name validation.
        use_tool_call_identity_v2 = workflow.patched(TOOL_CALL_IDENTITY_V2_PATCH)
        use_task_manager_delegation = workflow.patched(TASK_MANAGER_DELEGATION_PATCH)

        # ---- Step 0: Resolve payload via the prep activity --------------
        # DB lookups + edge walking + tool schema build happen here, NOT
        # in the workflow body (workflows must be deterministic).
        payload = await workflow.execute_activity(
            "agent.prepare_payload.v1",
            args=[context],
            activity_id="prepare-payload",
            start_to_close_timeout=PERSIST_TURN_TIMEOUT * 2,  # 60s default
            retry_policy=AGENT_ACTIVITY_RETRY,
        )
        # Stable per-run execution id, forwarded into every tool-call
        # activity so session-keyed nodes (browser) reuse one instance
        # across iterations instead of minting a fresh uuid per call
        # (node_executor.py fallback). Delegation children inherit it via
        # the ``child_context`` spread below. ``workflow.info().run_id``
        # is deterministic — safe inside workflow code.
        execution_id = str(context.get("execution_id") or "") or workflow.info().run_id[:8]
        max_iterations = int(payload.get("max_iterations") or _default_max_iterations())
        agent_node_id = payload["node_id"]
        agent_workflow_id = payload.get("workflow_id")
        self._parent_node_id: Optional[str] = context.get("parent_node_id")

        # The provider-visible name is the dispatch key. The legacy dict
        # comprehension silently selected the last connected node when two
        # tools shared a name. New histories fail before the first billed LLM
        # call and identify every conflicting canvas node.
        tools = payload.get("tools") or []
        duplicate_tool_error = (
            _duplicate_visible_tool_name_error(tools)
            if use_tool_call_identity_v2
            else None
        )
        duplicate_tool_conflicts = (
            _duplicate_visible_tool_name_conflicts(tools)
            if duplicate_tool_error
            else {}
        )
        if duplicate_tool_error:
            await self._emit_phase(
                agent_node_id,
                agent_workflow_id,
                0,
                max_iterations,
                phase="failed",
                status="error",
                extra={
                    "error_type": DUPLICATE_TOOL_NAME_ERROR_TYPE,
                    "error": duplicate_tool_error,
                    "conflicts": duplicate_tool_conflicts,
                },
            )
            return {
                "success": False,
                "error_type": DUPLICATE_TOOL_NAME_ERROR_TYPE,
                "error": duplicate_tool_error,
                "conflicts": duplicate_tool_conflicts,
                "node_id": agent_node_id,
                "node_type": payload.get("node_type"),
                "execution_id": execution_id,
                "result": {"iterations": 0, "usage": {}},
            }

        # ---- Build initial message list ---------------------------------
        # Workflow state is JSON dicts in LangChain's canonical shape
        # ({"type": "<role>", "data": {"content": "...", ...}}) so the
        # activity's ``messages_from_dict`` round-trips preserve every
        # provider-specific field (Gemini ``thought_signature``,
        # Anthropic cache markers, OpenAI reasoning content).
        messages: List[Dict[str, Any]] = []

        system = payload.get("system_message") or ""
        if system:
            messages.append({"type": "system", "data": {"content": system}})

        # Pre-loaded memory becomes an additional system note. The actual
        # parse / append happens in the persist_turn activity, but the
        # current markdown content seeds the conversation here.
        memory_markdown = payload.get("memory_content") or ""
        if memory_markdown:
            messages.append(
                {
                    "type": "system",
                    "data": {"content": f"## Prior conversation:\n{memory_markdown}"},
                }
            )

        user_prompt = payload.get("user_prompt") or ""
        if user_prompt:
            messages.append({"type": "human", "data": {"content": user_prompt}})

        # Map LLM tool name -> {node_type, version, task_queue, node_id,
        # parameters} so the workflow can schedule the right activity
        # when the LLM emits a tool_call.
        tool_index: Dict[str, Dict[str, Any]] = {t["name"]: t for t in tools}

        token_total = 0
        compaction_threshold = payload.get("compaction_threshold")
        thinking_accumulated = ""
        final_content: Optional[str] = None
        usage_total: Dict[str, int] = {}

        # Emit "executing" + phase="starting" via the existing
        # broadcast_agent_progress activity (CloudEvents
        # com.opencompany.agent.progress + raw-dict node_status for
        # canvas glow). Mirrors what F4.A's _node_activity wrapper
        # does for non-agent plugins.
        await self._emit_phase(
            agent_node_id,
            agent_workflow_id,
            0,
            max_iterations,
            phase="starting",
            status="executing",
        )

        # ---- Main loop --------------------------------------------------
        for iteration in range(max_iterations):
            workflow.logger.info(f"AgentWorkflow iteration {iteration} " f"(messages={len(messages)} tools={len(tools)})")

            # CloudEvents-shaped agent_progress per LLM turn. Mirrors the
            # the in-process agent loop's per-turn broadcast (RFC §6.4).
            # FE consumes the typed envelope and updates the canvas
            # node's "N / max" iteration badge live.
            await self._emit_phase(
                agent_node_id,
                agent_workflow_id,
                iteration,
                max_iterations,
                phase="llm_step",
            )

            # Strip per-turn fields the activity doesn't need.
            # Pass raw ``tool_info`` dicts through; the activity rebuilds
            # the real StructuredTool via ``ai_service._build_tool_from_node``
            # — the same helper the legacy agent path uses. We never
            # serialise/deserialise the args_schema (that round-trip was
            # the source of the Gemini ``properties.<field> Input should be
            # a valid dictionary`` validation crash).
            llm_payload = {
                "provider": payload["provider"],
                "model": payload["model"],
                "api_key": payload["api_key"],
                "messages": messages,
                "tool_data": [t["tool_info"] for t in tools if not t.get("llm_hidden")],
                "system_message": system,
                "temperature": payload.get("temperature", 0.7),
                "max_tokens": payload.get("max_tokens", 4096),
                "thinking_config": payload.get("thinking_config"),
            }

            # Wave 17.2: one-shot retry. The LLM call is not idempotent —
            # a worker crash mid-call must not silently re-bill the full
            # prompt (3x under the shared policy). The workflow owns the
            # failure instead: message history is intact here, so a
            # future enhancement can re-ask with context; today we
            # surface the error to the canvas and stop the loop.
            try:
                step_result = await workflow.execute_activity(
                    "agent.execute_llm_step.v1",
                    args=[llm_payload],
                    activity_id=f"llm-step-{iteration + 1}",
                    start_to_close_timeout=LLM_STEP_TIMEOUT,
                    retry_policy=LLM_STEP_RETRY,
                )
            except Exception as e:
                cause = getattr(e, "cause", None)
                detail = str(cause) if cause is not None else str(e)
                workflow.logger.error(f"AgentWorkflow LLM step failed (iteration {iteration + 1}, " f"no auto-retry): {detail}")
                return {
                    "success": False,
                    "error": f"LLM step failed: {detail}",
                    "error_type": "LLMStepError",
                    "result": {
                        "iterations": iteration + 1,
                        "usage": usage_total,
                    },
                }

            # Accumulate usage + thinking for the eventual return value.
            for k, v in (step_result.get("usage") or {}).items():
                if isinstance(v, int):
                    usage_total[k] = usage_total.get(k, 0) + v
            if step_result.get("thinking"):
                if thinking_accumulated:
                    thinking_accumulated += f"\n\n--- Iteration {iteration + 1} ---\n" + step_result["thinking"]
                else:
                    thinking_accumulated = step_result["thinking"]

            kind = step_result.get("kind")

            # The activity returns the FULL serialized assistant message
            # (LangChain's canonical {type, data} shape from messages_to_dict).
            # Appending verbatim preserves Gemini thought_signature, Anthropic
            # cache markers, OpenAI reasoning content — everything the next
            # turn's request needs.
            assistant_message = step_result.get("assistant_message")
            if assistant_message:
                messages.append(assistant_message)

            if kind == "final":
                final_content = step_result.get("content", "")
                team_id = str(payload.get("team_id") or "")
                if team_id and payload.get("owns_execution_team"):
                    finalization = await workflow.execute_activity(
                        "agent.finalize_team.v1",
                        args=[{"team_id": team_id}],
                        activity_id="finalize-agent-team",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    if finalization.get("status") == "active":
                        messages.append({
                            "type": "human",
                            "data": {"content": (
                                "Team finalization is blocked: delegated work is still queued, running, "
                                "failed, or awaiting review. Use Task Manager to inspect every task and "
                                "accept, retry, reassign, or cancel it before producing the final report."
                            )},
                        })
                        continue
                await self._persist_turn(payload, human_text=user_prompt, assistant_text=final_content)
                break

            if kind != "tool_calls":
                workflow.logger.error(f"AgentWorkflow: unexpected LLM step kind={kind!r}")
                break

            # ---- Schedule tool activities -------------------------------
            calls = step_result.get("calls") or []
            workflow.logger.info(f"AgentWorkflow scheduling {len(calls)} tool call(s)")

            # Start delegation children before awaiting their results.  The
            # previous per-call execute_child_workflow loop serialized an
            # entire team.  Keep a bounded sliding window so at most three
            # descendants from this turn are active, while tool messages are
            # still appended in the model's original call order.
            delegation_depth = int(context.get("delegation_depth") or 0)
            max_delegation_depth = min(
                2, int(payload.get("max_delegation_depth") or 2)
            )
            max_concurrent_subagents = max(
                1, int(payload.get("max_concurrent_subagents") or 3)
            )
            root_execution_id = str(
                payload.get("root_execution_id")
                or context.get("root_execution_id")
                or execution_id
            )
            delegation_call_indices = [
                index
                for index, candidate in enumerate(calls)
                if str(candidate.get("name", "")).startswith("delegate_to_")
                and (tool_index.get(candidate.get("name", "")) or {}).get("node_type")
                in AGENT_WORKFLOW_TYPES
                and (
                    str((candidate.get("args") or {}).get("task", "") or "")
                    or str((candidate.get("args") or {}).get("context", "") or "")
                )
                and delegation_depth < max_delegation_depth
            ]
            delegation_handles: Dict[int, Any] = {}
            delegation_permits: Dict[int, str] = {}
            yielded_own_permit = False

            # A child that waits for grandchildren must not retain a slot,
            # otherwise N admitted children can all block forever trying to
            # acquire the N+1th permit. Yield while orchestrating descendants
            # and reacquire before resuming this agent's next LLM turn.
            own_permit_id = str(context.get("team_task_id") or "")
            if delegation_call_indices and own_permit_id:
                await workflow.execute_activity(
                    "agent.release_subagent_permit.v1",
                    args=[{
                        "root_execution_id": root_execution_id,
                        "permit_id": own_permit_id,
                    }],
                    activity_id=f"yield-own-permit-{iteration + 1}",
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                yielded_own_permit = True

            async def _start_delegation(call_index: int) -> None:
                candidate = calls[call_index]
                candidate_tool = tool_index[candidate.get("name", "")]
                candidate_args = candidate.get("args") or {}
                task = str(candidate_args.get("task", "") or "")
                task_context = str(candidate_args.get("context", "") or "")
                call_metadata = (
                    _tool_call_metadata(
                        agent_node_id=agent_node_id,
                        iteration=iteration,
                        call_index=call_index,
                        call=candidate,
                    )
                    if use_tool_call_identity_v2
                    else {}
                )
                child_context = {
                    "node_id": candidate_tool["tool_node_id"],
                    "node_type": candidate_tool["node_type"],
                    "node_data": {
                        **(candidate_tool.get("parameters") or {}),
                        "system_message": task,
                        "prompt": task_context or task,
                    },
                    "inputs": {},
                    "workflow_id": payload.get("workflow_id"),
                    "session_id": payload.get("session_id", "default"),
                    "execution_id": execution_id,
                    "root_execution_id": root_execution_id,
                    "parent_node_id": agent_node_id,
                    "delegation_depth": delegation_depth + 1,
                    "team_id": payload.get("team_id") or context.get("team_id"),
                    "team_task_id": (
                        f"task-{root_execution_id}-{agent_node_id}-"
                        f"{iteration + 1}-{call_index + 1}"
                    ),
                    "trace_id": str(candidate.get("id", "") or ""),
                    "nodes": context.get("nodes") or [],
                    "edges": context.get("edges") or [],
                    "invocation": {"task": task, "context": task_context},
                    **call_metadata,
                }
                team_id = str(child_context.get("team_id") or "")
                if team_id:
                    permit_id = child_context["team_task_id"]
                    lifecycle_payload = {
                        "team_id": team_id,
                        "team_task_id": child_context["team_task_id"],
                        "parent_agent_node_id": agent_node_id,
                        "child_agent_node_id": candidate_tool["tool_node_id"],
                        "child_agent_name": str(
                            (candidate_tool.get("tool_info") or {}).get("label")
                            or candidate_tool.get("node_type")
                            or "agent"
                        ),
                        "workflow_id": payload.get("workflow_id"),
                        "parent_agent_workflow_id": workflow.info().workflow_id,
                        "task": task,
                        "root_execution_id": root_execution_id,
                        "delegation_depth": delegation_depth + 1,
                        "trace_id": child_context["trace_id"],
                        "assignment_event_id": (
                            f"{child_context['team_task_id']}:assigned"
                        ),
                    }
                    await workflow.execute_activity(
                        "agent.queue_delegation.v1",
                        args=[{
                            **lifecycle_payload,
                            "queued_event_id": (
                                f"{child_context['team_task_id']}:queued"
                            ),
                        }],
                        activity_id=(
                            f"queue-delegation-{iteration + 1}-{call_index + 1}"
                        ),
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    await workflow.execute_activity(
                        "agent.acquire_subagent_permit.v1",
                        args=[{
                            "root_execution_id": root_execution_id,
                            "permit_id": permit_id,
                            "limit": max_concurrent_subagents,
                        }],
                        activity_id=(
                            f"acquire-permit-{iteration + 1}-{call_index + 1}"
                        ),
                        start_to_close_timeout=timedelta(hours=1),
                        heartbeat_timeout=timedelta(seconds=10),
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    delegation_permits[call_index] = permit_id
                    # Claim only after admission. Persistence failure still
                    # prevents child startup and the task remains pending
                    # while the coordinator queues it.
                    try:
                        await workflow.execute_activity(
                            "agent.begin_delegation.v1",
                            args=[lifecycle_payload],
                            activity_id=(
                                f"begin-delegation-{iteration + 1}-{call_index + 1}"
                            ),
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                        )
                    except BaseException:
                        await workflow.execute_activity(
                            "agent.release_subagent_permit.v1",
                            args=[{
                                "root_execution_id": root_execution_id,
                                "permit_id": permit_id,
                            }],
                            activity_id=(
                                f"release-permit-{iteration + 1}-{call_index + 1}"
                            ),
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                        )
                        raise
                child_id = (
                    _delegation_child_id_v2(
                        workflow.info().workflow_id,
                        candidate_tool["tool_node_id"],
                        iteration,
                        call_index,
                    )
                    if use_tool_call_identity_v2
                    else f"{workflow.info().workflow_id}-delegate-{candidate_tool['tool_node_id']}-{iteration}"
                )
                try:
                    delegation_handles[call_index] = await workflow.start_child_workflow(
                        "AgentWorkflow",
                        args=[child_context],
                        id=child_id,
                        execution_timeout=timedelta(hours=1),
                        run_timeout=timedelta(hours=1),
                    )
                except BaseException:
                    if team_id and call_index in delegation_permits:
                        await workflow.execute_activity(
                            "agent.release_subagent_permit.v1",
                            args=[{
                                "root_execution_id": root_execution_id,
                                "permit_id": delegation_permits[call_index],
                            }],
                            activity_id=(
                                f"release-permit-{iteration + 1}-{call_index + 1}"
                            ),
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                        )
                    raise

            async def _run_task_manager_delegation(
                request: Dict[str, Any], call_index: int, call: Dict[str, Any]
            ) -> Any:
                """Execute a trusted Task Manager scheduling envelope.

                ``assign_task`` has already authorized the teammate and
                persisted the TeamTask. This bridge resolves that teammate
                against the workflow's bound delegate surface and reuses the
                exact durable permit/claim/child/finalize lifecycle used by a
                direct ``delegate_to_*`` call.
                """
                nonlocal yielded_own_permit

                delegate_name = str(request.get("delegate_name") or "")
                assignee_id = str(request.get("assignee_node_id") or "")
                task_id = str(request.get("team_task_id") or "")
                mission = str(request.get("task") or "")
                request_context = request.get("context") or ""
                if not all((delegate_name, assignee_id, task_id, mission)):
                    raise ValueError("Task Manager returned an incomplete delegation_request")

                delegate = tool_index.get(delegate_name)
                if (
                    delegate is None
                    or str(delegate.get("tool_node_id") or "") != assignee_id
                    or delegate.get("node_type") not in AGENT_WORKFLOW_TYPES
                ):
                    raise ValueError(
                        "Task Manager assignee is not a connected Temporal delegate"
                    )
                if delegation_depth >= max_delegation_depth:
                    raise ValueError(
                        f"Maximum delegation depth {max_delegation_depth} exceeded"
                    )

                # A lead child cannot retain its own descendant permit while
                # waiting for another one; doing so can deadlock when every
                # root-wide slot is occupied by leads assigning descendants.
                if own_permit_id and not yielded_own_permit:
                    await workflow.execute_activity(
                        "agent.release_subagent_permit.v1",
                        args=[{"root_execution_id": root_execution_id, "permit_id": own_permit_id}],
                        activity_id=f"yield-own-permit-task-manager-{iteration + 1}",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    yielded_own_permit = True

                team_id = str(payload.get("team_id") or context.get("team_id") or "")
                if not team_id:
                    raise ValueError("Task Manager delegation requires an execution team")
                trace_id = str(call.get("id", "") or "")
                lifecycle = {
                    "team_id": team_id,
                    "team_task_id": task_id,
                    "parent_agent_node_id": agent_node_id,
                    "child_agent_node_id": assignee_id,
                    "child_agent_name": str(
                        (delegate.get("tool_info") or {}).get("label")
                        or delegate.get("node_type")
                        or "agent"
                    ),
                    "workflow_id": payload.get("workflow_id"),
                    "parent_agent_workflow_id": workflow.info().workflow_id,
                    "task": mission,
                    "root_execution_id": root_execution_id,
                    "delegation_depth": delegation_depth + 1,
                    "trace_id": trace_id,
                    "assignment_event_id": f"{task_id}:assigned",
                }
                # queue_delegation is intentionally retained: it is
                # idempotent for the pre-created task and records the same
                # lifecycle event as direct delegation without duplicating it.
                await workflow.execute_activity(
                    "agent.queue_delegation.v1",
                    args=[{**lifecycle, "queued_event_id": f"{task_id}:queued"}],
                    activity_id=f"queue-task-manager-{iteration + 1}-{call_index + 1}",
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                await workflow.execute_activity(
                    "agent.acquire_subagent_permit.v1",
                    args=[{
                        "root_execution_id": root_execution_id,
                        "permit_id": task_id,
                        "limit": max_concurrent_subagents,
                    }],
                    activity_id=f"acquire-task-manager-{iteration + 1}-{call_index + 1}",
                    start_to_close_timeout=timedelta(hours=1),
                    heartbeat_timeout=timedelta(seconds=10),
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                claimed = False
                finalization_started = False
                try:
                    await workflow.execute_activity(
                        "agent.begin_delegation.v1",
                        args=[lifecycle],
                        activity_id=f"begin-task-manager-{iteration + 1}-{call_index + 1}",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    claimed = True
                    context_text = (
                        request_context
                        if isinstance(request_context, str)
                        else _serialise_tool_result(request_context)
                    )
                    child_context = {
                        "node_id": assignee_id,
                        "node_type": delegate["node_type"],
                        "node_data": {
                            **(delegate.get("parameters") or {}),
                            "system_message": mission,
                            "prompt": context_text or mission,
                        },
                        "inputs": {},
                        "workflow_id": payload.get("workflow_id"),
                        "session_id": payload.get("session_id", "default"),
                        "execution_id": execution_id,
                        "root_execution_id": root_execution_id,
                        "parent_node_id": agent_node_id,
                        "delegation_depth": delegation_depth + 1,
                        "team_id": team_id,
                        "team_task_id": task_id,
                        "trace_id": trace_id,
                        "nodes": context.get("nodes") or [],
                        "edges": context.get("edges") or [],
                        "invocation": {"task": mission, "context": context_text},
                    }
                    child_id = _delegation_child_id_v2(
                        workflow.info().workflow_id, assignee_id, iteration, call_index
                    ) + f"-{task_id}"
                    result = await workflow.execute_child_workflow(
                        "AgentWorkflow",
                        args=[child_context],
                        id=child_id,
                        execution_timeout=timedelta(hours=1),
                        run_timeout=timedelta(hours=1),
                    )
                    succeeded = bool(result.get("success", True)) if isinstance(result, dict) else True
                    finalization_started = True
                    finalization = await workflow.execute_activity(
                        "agent.finish_delegation.v1",
                        args=[{
                            **lifecycle,
                            "success": succeeded,
                            "result": result if isinstance(result, dict) else {"result": result},
                            "error": result.get("error") if isinstance(result, dict) else None,
                            "terminal_event_id": f"{task_id}:terminal",
                        }],
                        activity_id=f"finish-task-manager-{iteration + 1}-{call_index + 1}",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    return {
                        "result": result,
                        "status": finalization.get("status", "submitted"),
                    }
                except Exception as exc:
                    if claimed and not finalization_started:
                        await workflow.execute_activity(
                            "agent.finish_delegation.v1",
                            args=[{
                                **lifecycle,
                                "success": False,
                                "error": f"{type(exc).__name__}: {exc}",
                                "terminal_event_id": f"{task_id}:terminal",
                            }],
                            activity_id=f"finish-task-manager-{iteration + 1}-{call_index + 1}",
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                        )
                    raise
                finally:
                    await workflow.execute_activity(
                        "agent.release_subagent_permit.v1",
                        args=[{"root_execution_id": root_execution_id, "permit_id": task_id}],
                        activity_id=f"release-task-manager-{iteration + 1}-{call_index + 1}",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )

            # Preflight every Task Manager assignment activity in this LLM
            # turn concurrently. Each activity performs authorization and
            # creates its durable queue row; only trusted scheduling envelopes
            # are allowed to reach the child-workflow bridge below.
            task_manager_preflight_indices: List[int] = []
            task_manager_preflight_handles: List[Any] = []
            if use_task_manager_delegation:
                for preflight_index, preflight_call in enumerate(calls):
                    preflight_tool = tool_index.get(preflight_call.get("name", ""))
                    preflight_args = preflight_call.get("args") or {}
                    if (
                        preflight_tool
                        and preflight_tool.get("node_type") == "taskManager"
                        and preflight_args.get("operation") == "assign_task"
                    ):
                        preflight_metadata = (
                            _tool_call_metadata(
                                agent_node_id=agent_node_id,
                                iteration=iteration,
                                call_index=preflight_index,
                                call=preflight_call,
                            )
                            if use_tool_call_identity_v2
                            else {}
                        )
                        preflight_payload = {
                            "node_id": preflight_tool["tool_node_id"],
                            "node_type": "taskManager",
                            "node_data": {
                                **(preflight_tool.get("parameters") or {}),
                                **preflight_args,
                            },
                            "inputs": {},
                            "workflow_id": payload.get("workflow_id"),
                            "session_id": payload.get("session_id", "default"),
                            "execution_id": execution_id,
                            "root_execution_id": root_execution_id,
                            "parent_node_id": agent_node_id,
                            "team_lead_node_id": agent_node_id,
                            "nodes": context.get("nodes") or [],
                            "edges": context.get("edges") or [],
                            **preflight_metadata,
                        }
                        task_manager_preflight_indices.append(preflight_index)
                        task_manager_preflight_handles.append(
                            workflow.start_activity(
                                f"node.taskManager.v{preflight_tool['version']}",
                                args=[preflight_payload],
                                activity_id=(
                                    f"task-manager-preflight-{iteration + 1}-"
                                    f"{preflight_index + 1}"
                                ),
                                start_to_close_timeout=TOOL_STEP_TIMEOUT,
                                heartbeat_timeout=TOOL_HEARTBEAT_TIMEOUT,
                            )
                        )

            task_manager_preflight_results: Dict[int, Any] = {}
            task_manager_delegation_tasks: Dict[int, asyncio.Task[Any]] = {}
            if task_manager_preflight_handles:
                preflight_results = await asyncio.gather(
                    *task_manager_preflight_handles, return_exceptions=True
                )
                for preflight_index, preflight_result in zip(
                    task_manager_preflight_indices, preflight_results
                ):
                    task_manager_preflight_results[preflight_index] = preflight_result

                # Yield a child lead's slot exactly once before descendant
                # assignment coroutines contend for root-wide permits.
                if own_permit_id and not yielded_own_permit:
                    await workflow.execute_activity(
                        "agent.release_subagent_permit.v1",
                        args=[{
                            "root_execution_id": root_execution_id,
                            "permit_id": own_permit_id,
                        }],
                        activity_id=f"yield-own-permit-task-manager-{iteration + 1}",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    yielded_own_permit = True

                for preflight_index in task_manager_preflight_indices:
                    preflight_result = task_manager_preflight_results[preflight_index]
                    if isinstance(preflight_result, BaseException):
                        continue
                    delegation_request = preflight_result.get("delegation_request")
                    if isinstance(delegation_request, dict):
                        task_manager_delegation_tasks[preflight_index] = asyncio.create_task(
                            _run_task_manager_delegation(
                                delegation_request,
                                preflight_index,
                                calls[preflight_index],
                            )
                        )

            next_delegation_to_start = 0
            for delegation_index in delegation_call_indices[:max_concurrent_subagents]:
                await _start_delegation(delegation_index)
                next_delegation_to_start += 1

            for call_index, call in enumerate(calls):
                tool_info = tool_index.get(call.get("name", ""))
                if tool_info is None:
                    workflow.logger.warning(f"AgentWorkflow: LLM called unknown tool {call.get('name')!r}; " "returning error to model")
                    messages.append(
                        {
                            "type": "tool",
                            "data": {
                                "content": (f"Error: tool {call.get('name')!r} is not " "connected to this agent."),
                                "tool_call_id": call.get("id", ""),
                                "name": call.get("name", ""),
                            },
                        }
                    )
                    continue

                # Delegation tools (``delegate_to_<child>``) need different
                # arg handling than regular tools:
                #
                #   1. LLM passes ``{"task": "...", "context": "..."}`` —
                #      this is per-invocation INPUT, not node configuration.
                #      For child AgentWorkflows it travels as the workflow
                #      input's ``invocation`` field (Temporal input-contract
                #      pattern); the prep activity applies it AFTER config
                #      resolution so stored params (e.g. the node's empty
                #      default ``prompt``) can never clobber the delegated
                #      task — without that guarantee Gemini fails with
                #      ``contents are required``. For bypass agents
                #      dispatched as plain activities (rlm_agent /
                #      claude_code_agent) the task is mapped into
                #      ``node_data`` (``task → system_message``,
                #      ``context-or-task → prompt``) because their activity
                #      consumes ``node_data`` verbatim with no DB re-merge.
                #   2. The child agent needs the full canvas (``nodes`` +
                #      ``edges``) so its ``collect_agent_connections`` edge
                #      walk can find its own skills / memory / tools.
                #      Regular tools don't need this — they execute against
                #      their own params alone.
                #
                # Same task/context semantics as the legacy fire-and-forget
                # ``handlers.tools._execute_delegated_agent``.
                call_args = call.get("args") or {}
                tool_name = call.get("name", "")
                is_delegation = tool_name.startswith("delegate_to_")

                if is_delegation:
                    task_description = str(call_args.get("task", "") or "")
                    task_context = str(call_args.get("context", "") or "")
                    if not task_description and not task_context:
                        # Invalid invocation — reject at the call boundary
                        # instead of spawning a child that cannot run.
                        messages.append(
                            {
                                "type": "tool",
                                "data": {
                                    "content": (
                                        '{"error": "delegate_to_* requires a '
                                        "non-empty 'task' argument describing "
                                        'what the agent should do."}'
                                    ),
                                    "tool_call_id": call.get("id", ""),
                                    "name": tool_name,
                                },
                            }
                        )
                        continue
                    if delegation_depth >= max_delegation_depth:
                        messages.append(
                            {
                                "type": "tool",
                                "data": {
                                    "content": (
                                        '{"error": "Maximum delegation depth '
                                        f'{max_delegation_depth} exceeded."}}'
                                    ),
                                    "tool_call_id": call.get("id", ""),
                                    "name": tool_name,
                                },
                            }
                        )
                        continue
                    tool_node_data = {
                        **(tool_info.get("parameters") or {}),
                        # Consumed only by the activity-dispatch fallback
                        # below (bypass agents); the child-AgentWorkflow
                        # path reads the ``invocation`` field instead.
                        "system_message": task_description,
                        "prompt": task_context or task_description,
                    }
                    child_nodes = context.get("nodes") or []
                    child_edges = context.get("edges") or []
                else:
                    tool_node_data = {
                        **(tool_info.get("parameters") or {}),
                        **call_args,
                    }
                    # Canvas-aware tools (currently only agentBuilder, which
                    # walks edges to resolve its calling agent + mutates
                    # the canvas) opt in via the BaseNode.needs_canvas
                    # ClassVar. Default tools execute against their own
                    # params alone and don't see the parent canvas.
                    plugin_cls = get_node_class(tool_info["node_type"])
                    if plugin_cls is not None and plugin_cls.needs_canvas:
                        child_nodes = context.get("nodes") or []
                        child_edges = context.get("edges") or []
                    else:
                        child_nodes = []
                        child_edges = []

                call_metadata = (
                    _tool_call_metadata(
                        agent_node_id=agent_node_id,
                        iteration=iteration,
                        call_index=call_index,
                        call=call,
                    )
                    if use_tool_call_identity_v2
                    else {}
                )
                tool_payload = {
                    "node_id": tool_info["tool_node_id"],
                    "node_type": tool_info["node_type"],
                    "node_data": tool_node_data,
                    "inputs": {},
                    "workflow_id": payload.get("workflow_id"),
                    "session_id": payload.get("session_id", "default"),
                    "execution_id": execution_id,
                    "parent_node_id": agent_node_id,
                    "team_lead_node_id": agent_node_id,
                    "team_id": payload.get("team_id") or context.get("team_id"),
                    "root_execution_id": root_execution_id,
                    "delegation_depth": delegation_depth,
                    "nodes": child_nodes,
                    "edges": child_edges,
                    # Surface the auto-rebind toggle into the tool's ctx
                    # so canvas-mutating tools (agentBuilder) render their
                    # summary text to match the user's current preference.
                    "auto_rebind_tools": bool(payload.get("auto_rebind_tools", True)),
                    **call_metadata,
                }

                tool_activity_name = f"node.{tool_info['node_type']}.v{tool_info['version']}"

                await self._emit_phase(
                    agent_node_id,
                    agent_workflow_id,
                    iteration,
                    max_iterations,
                    phase="executing_tool",
                    extra={"tool_name": call.get("name", ""), "tool_node_id": tool_info["tool_node_id"]},
                )

                try:
                    if is_delegation and tool_info["node_type"] in AGENT_WORKFLOW_TYPES:
                        handle = delegation_handles[call_index]
                        try:
                            tool_result = await handle
                        finally:
                            permit_id = delegation_permits.get(call_index)
                            if permit_id:
                                await workflow.execute_activity(
                                    "agent.release_subagent_permit.v1",
                                    args=[{
                                        "root_execution_id": root_execution_id,
                                        "permit_id": permit_id,
                                    }],
                                    activity_id=(
                                        f"release-permit-{iteration + 1}-{call_index + 1}"
                                    ),
                                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                                    retry_policy=AGENT_ACTIVITY_RETRY,
                                )
                        team_id = str(payload.get("team_id") or context.get("team_id") or "")
                        if team_id:
                            task_id = (
                                f"task-{root_execution_id}-{agent_node_id}-"
                                f"{iteration + 1}-{call_index + 1}"
                            )
                            await workflow.execute_activity(
                                "agent.finish_delegation.v1",
                                args=[{
                                    "team_id": team_id,
                                    "team_task_id": task_id,
                                    "parent_agent_node_id": agent_node_id,
                                    "child_agent_node_id": tool_info["tool_node_id"],
                                    "child_agent_name": str(
                                        (tool_info.get("tool_info") or {}).get("label")
                                        or tool_info.get("node_type")
                                        or "agent"
                                    ),
                                    "workflow_id": payload.get("workflow_id"),
                                    "parent_agent_workflow_id": workflow.info().workflow_id,
                                    "root_execution_id": root_execution_id,
                                    "trace_id": str(call.get("id", "") or ""),
                                    "success": bool(tool_result.get("success", True))
                                    if isinstance(tool_result, dict) else True,
                                    "result": tool_result if isinstance(tool_result, dict) else {"result": tool_result},
                                    "error": tool_result.get("error")
                                    if isinstance(tool_result, dict) else None,
                                    "terminal_event_id": f"{task_id}:terminal",
                                }],
                                activity_id=(
                                    f"finish-delegation-{iteration + 1}-{call_index + 1}"
                                ),
                                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                                retry_policy=AGENT_ACTIVITY_RETRY,
                            )
                        if next_delegation_to_start < len(delegation_call_indices):
                            await _start_delegation(
                                delegation_call_indices[next_delegation_to_start]
                            )
                            next_delegation_to_start += 1
                    else:
                        tool_activity_id = (
                            _tool_activity_id_v2(
                                tool_info["tool_node_id"],
                                iteration,
                                call_index,
                            )
                            if use_tool_call_identity_v2
                            else f"tool-{tool_info['tool_node_id']}-{iteration + 1}"
                        )
                        if call_index in task_manager_preflight_results:
                            tool_result = task_manager_preflight_results[call_index]
                            if isinstance(tool_result, BaseException):
                                raise tool_result
                        else:
                            tool_result = await workflow.execute_activity(
                                tool_activity_name,
                                args=[tool_payload],
                                activity_id=tool_activity_id,
                                start_to_close_timeout=TOOL_STEP_TIMEOUT,
                                heartbeat_timeout=TOOL_HEARTBEAT_TIMEOUT,
                            )
                        if (
                            use_task_manager_delegation
                            and tool_info["node_type"] == "taskManager"
                            and isinstance(tool_result, dict)
                            and isinstance(tool_result.get("delegation_request"), dict)
                        ):
                            if call_index in task_manager_delegation_tasks:
                                # Await in original tool-call order; every
                                # child was already started above, so slow
                                # earlier siblings do not prevent later work.
                                delegated = await task_manager_delegation_tasks[call_index]
                            else:
                                delegated = await _run_task_manager_delegation(
                                    tool_result["delegation_request"], call_index, call
                                )
                            tool_result = {
                                **tool_result,
                                "delegation_status": delegated["status"],
                                "delegation_result": delegated["result"],
                            }
                    tool_content = _serialise_tool_result(tool_result)
                    await self._emit_phase(
                        agent_node_id,
                        agent_workflow_id,
                        iteration,
                        max_iterations,
                        phase="tool_completed",
                        extra={"tool_name": call.get("name", "")},
                    )

                    # Hot-rebind: if the tool returned ``operations`` (canvas
                    # mutation), schedule ``agent.refresh_tools.v1`` to build
                    # new tool_payload entries from the ops and splice them
                    # into the workflow's live ``tools`` / ``tool_index``.
                    # The next ``execute_llm_step`` invocation rebuilds the
                    # bound LLM surface from this updated list, so the new
                    # tool is callable in the very next iteration without a
                    # Run-stop-Run cycle.
                    auto_rebind_enabled = bool(payload.get("auto_rebind_tools", True))
                    if auto_rebind_enabled and isinstance(tool_result, dict):
                        ops_from_tool = tool_result.get("operations") or []
                        if ops_from_tool:
                            # Deliberately multi-attempt (unlike the LLM
                            # step's one-shot LLM_STEP_RETRY): rebuilding
                            # the tool surface from canvas state is fully
                            # idempotent, so retries are free.
                            refresh_activity_id = (
                                _refresh_tools_activity_id_v2(
                                    tool_info["tool_node_id"],
                                    iteration,
                                    call_index,
                                )
                                if use_tool_call_identity_v2
                                else f"refresh-tools-{tool_info['tool_node_id']}-{iteration + 1}"
                            )
                            refresh_payload = {
                                "operations": ops_from_tool,
                                "agent_node_type": payload.get("node_type") or context.get("node_type"),
                                **call_metadata,
                            }
                            refresh_result = await workflow.execute_activity(
                                "agent.refresh_tools.v1",
                                args=[refresh_payload],
                                activity_id=refresh_activity_id,
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=AGENT_ACTIVITY_RETRY,
                            )
                            added_tools = refresh_result.get("tools") or []
                            refresh_duplicate_error = (
                                _duplicate_visible_tool_name_error([*tools, *added_tools])
                                if use_tool_call_identity_v2
                                else None
                            )
                            refresh_duplicate_conflicts = (
                                _duplicate_visible_tool_name_conflicts([*tools, *added_tools])
                                if refresh_duplicate_error
                                else {}
                            )
                            if refresh_duplicate_error:
                                workflow.logger.warning(
                                    "AgentWorkflow rejected hot-rebound tools: %s",
                                    refresh_duplicate_error,
                                )
                                tool_content = _serialise_tool_result(
                                    {
                                        "error_type": DUPLICATE_TOOL_NAME_ERROR_TYPE,
                                        "error": refresh_duplicate_error,
                                        "conflicts": refresh_duplicate_conflicts,
                                    }
                                )
                                await self._emit_phase(
                                    agent_node_id,
                                    agent_workflow_id,
                                    iteration,
                                    max_iterations,
                                    phase="tool_error",
                                    extra={
                                        "error_type": DUPLICATE_TOOL_NAME_ERROR_TYPE,
                                        "error": refresh_duplicate_error,
                                        "conflicts": refresh_duplicate_conflicts,
                                        **call_metadata,
                                    },
                                )
                                added_tools = []
                            for new_tool in added_tools:
                                tools.append(new_tool)
                                tool_index[new_tool["name"]] = new_tool
                            if added_tools:
                                workflow.logger.info(
                                    "AgentWorkflow rebound %d tool(s) after canvas mutation (total bound=%d)",
                                    len(added_tools),
                                    len(tools),
                                )
                except Exception as e:  # noqa: BLE001 — Temporal handles retries
                    # After all retries exhausted, surface the error to
                    # the LLM (per user decision: LLM sees error and
                    # continues — matches the in-process agent loop).
                    workflow.logger.warning(f"AgentWorkflow tool {tool_info['node_type']!r} failed: {e}")
                    team_id = str(payload.get("team_id") or context.get("team_id") or "")
                    if is_delegation and team_id:
                        task_id = (
                            f"task-{root_execution_id}-{agent_node_id}-"
                            f"{iteration + 1}-{call_index + 1}"
                        )
                        await workflow.execute_activity(
                            "agent.finish_delegation.v1",
                            args=[{
                                "team_id": team_id,
                                "team_task_id": task_id,
                                "parent_agent_node_id": agent_node_id,
                                "child_agent_node_id": tool_info["tool_node_id"],
                                "child_agent_name": str(
                                    (tool_info.get("tool_info") or {}).get("label")
                                    or tool_info.get("node_type")
                                    or "agent"
                                ),
                                "workflow_id": payload.get("workflow_id"),
                                "parent_agent_workflow_id": workflow.info().workflow_id,
                                "root_execution_id": root_execution_id,
                                "trace_id": str(call.get("id", "") or ""),
                                "success": False,
                                "error": f"{type(e).__name__}: {e}",
                                "terminal_event_id": f"{task_id}:terminal",
                            }],
                            activity_id=(
                                f"finish-delegation-{iteration + 1}-{call_index + 1}"
                            ),
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                        )
                    tool_content = f'{{"error": "{type(e).__name__}: {e}"}}'

                messages.append(
                    {
                        "type": "tool",
                        "data": {
                            "content": tool_content,
                            "tool_call_id": call.get("id", ""),
                            "name": call.get("name", ""),
                        },
                    }
                )

            if yielded_own_permit:
                await workflow.execute_activity(
                    "agent.acquire_subagent_permit.v1",
                    args=[{
                        "root_execution_id": root_execution_id,
                        "permit_id": own_permit_id,
                        "limit": max_concurrent_subagents,
                    }],
                    activity_id=f"reacquire-own-permit-{iteration + 1}",
                    start_to_close_timeout=timedelta(hours=1),
                    heartbeat_timeout=timedelta(seconds=10),
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )

            # ---- Persist this turn (append-per-turn) -------------------
            # Snapshot the most recent user/assistant pair into memory.
            await self._persist_turn(
                payload,
                human_text=user_prompt,
                assistant_text="",  # interim turn — body lives in tool_results
                interim=True,
            )

            # ---- Compaction check --------------------------------------
            token_total = sum(v for k, v in usage_total.items() if k in ("input_tokens", "output_tokens"))
            if compaction_threshold and token_total >= compaction_threshold and memory_markdown:
                workflow.logger.info(f"AgentWorkflow compaction triggered: {token_total} tokens")
                compact_result = await workflow.execute_activity(
                    "agent.compact_memory.v1",
                    args=[
                        {
                            "session_id": payload.get("session_id", "default"),
                            "node_id": payload["node_id"],
                            "memory_content": memory_markdown,
                            "provider": payload["provider"],
                            "api_key": payload["api_key"],
                            "model": payload["model"],
                        }
                    ],
                    activity_id=f"compact-memory-{iteration + 1}",
                    start_to_close_timeout=COMPACT_MEMORY_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                # Compaction is best-effort. When the service errors or
                # was not initialized (worker bootstrap race), keep the
                # existing messages and let the loop continue — masking
                # the failure would surface as a confused LLM, not a
                # workflow crash.
                if not compact_result.get("success"):
                    workflow.logger.warning(
                        "AgentWorkflow compaction failed (%s); continuing " "with un-compacted history",
                        compact_result.get("error", "no error reported"),
                    )
                else:
                    summary = compact_result.get("summary", "")
                    if summary:
                        # Replace the running messages with the summary
                        # plus the last user prompt — same pattern
                        # ``CompactionService`` uses today in services/ai.py.
                        messages = [
                            {"type": "system", "data": {"content": system}},
                            {"type": "system", "data": {"content": f"## Compacted summary:\n{summary}"}},
                            {"type": "human", "data": {"content": user_prompt}},
                        ]
                        memory_markdown = summary
                        usage_total = {}

        else:
            # Loop exited without break -- hit max_iterations.
            workflow.logger.warning(f"AgentWorkflow hit max_iterations={max_iterations}; truncating")
            final_content = final_content or (
                "[AgentWorkflow truncated after max_iterations; " "the model did not produce a final response]"
            )

        result_payload = {
            "response": final_content,
            "thinking": thinking_accumulated or None,
            "model": payload.get("model"),
            "provider": payload.get("provider"),
            "usage": usage_total,
        }

        # Persist the result to the OutputStore via the workflow_service
        # so ParameterResolver can resolve {{aiAgent.response}} in
        # downstream nodes. F4.A's activity wrapper does this via
        # NodeExecutor; F4.B needs an explicit activity because we
        # bypass NodeExecutor entirely.
        await workflow.execute_activity(
            "agent.store_output.v1",
            args=[
                {
                    "node_id": agent_node_id,
                    "session_id": payload.get("session_id", "default"),
                    "result": result_payload,
                }
            ],
            activity_id="store-output",
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )

        # Final lifecycle broadcast — canvas glow goes green + FE
        # consumers of com.opencompany.agent.progress see phase="completed".
        await self._emit_phase(
            agent_node_id,
            agent_workflow_id,
            max_iterations,
            max_iterations,
            phase="completed",
            status="success",
        )

        return {"success": True, "result": result_payload}

    # ---- Private helpers ------------------------------------------------

    async def _emit_phase(
        self,
        node_id: str,
        workflow_id: Optional[str],
        iteration: int,
        max_iterations: int,
        *,
        phase: str,
        status: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Schedule the ``agent.broadcast_progress.v1`` activity with a
        single ``phase`` label. When ``status`` is set, the activity
        also broadcasts a raw-dict node_status update so the canvas
        glows accordingly (executing / success / error). When this
        workflow is a delegated child (``self._parent_node_id`` set),
        also mirrors the broadcast onto the parent's node_id so the
        parent's canvas badge advances alongside the child.
        """
        await workflow.execute_activity(
            "agent.broadcast_progress.v1",
            args=[
                {
                    "node_id": node_id,
                    "workflow_id": workflow_id,
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                    "phase": phase,
                    **({"status": status} if status else {}),
                    **(extra or {}),
                }
            ],
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )

        if self._parent_node_id:
            await workflow.execute_activity(
                "agent.broadcast_progress.v1",
                args=[
                    {
                        "node_id": self._parent_node_id,
                        "workflow_id": workflow_id,
                        "iteration": iteration,
                        "max_iterations": max_iterations,
                        "phase": "delegating",
                        **(extra or {}),
                    }
                ],
                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                retry_policy=AGENT_ACTIVITY_RETRY,
            )

    async def _persist_turn(
        self,
        payload: Dict[str, Any],
        *,
        human_text: str,
        assistant_text: str,
        interim: bool = False,
    ) -> None:
        """Schedule the ``agent.persist_turn.v1`` activity.

        Skips when no memory node is connected (the agent has no
        ``simpleMemory`` neighbour).
        """
        memory_node_id = payload.get("memory_node_id") or ""
        if not memory_node_id:
            return
        # Interim turns (tool-call mid-loops) are append-only with
        # empty assistant text; the next final turn fills it in.
        if interim and not assistant_text:
            return
        await workflow.execute_activity(
            "agent.persist_turn.v1",
            args=[
                {
                    "memory_node_id": memory_node_id,
                    "human_text": human_text,
                    "assistant_text": assistant_text,
                    "window_size": int(payload.get("memory_window_size") or 10),
                }
            ],
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )


def _serialise_tool_result(result: Any) -> str:
    """Return a string body for a ``ToolMessage``.

    Mirrors the in-process tool-call serialisation in
    ``services/ai.py:_run_agent_loop``: feed the LLM the handler's raw
    return value (``json.dumps(result, default=str)``), NOT the Temporal
    activity envelope. The F4.A per-type activity wraps the handler
    result as ``{"success": bool, "result": {...}, "node_id": ...,
    "node_type": ..., "timestamp": ...}``; we strip the envelope so the
    LLM doesn't see infrastructure metadata.
    """
    import json as _json

    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "result" in result and "success" in result:
        # F4.A activity envelope — unwrap to match legacy tool_executor.
        result = result.get("result", {})
    try:
        return _json.dumps(result, default=str)
    except Exception:  # noqa: BLE001 — defensive
        return str(result)
