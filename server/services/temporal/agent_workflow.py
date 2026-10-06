"""F4.B: ``AgentWorkflow`` — Temporal child workflow for AI agent loops.

Workflow-orchestrated alternative to the in-process ``run_native_agent_loop``
in ``services/agent_runtime.py``: each LLM turn is an activity, each tool call
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
              ├─> execute_activity(agent.persist_turn)
              ├─> token check; if over threshold:
              │      execute_activity(agent.compact_context)
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
  ``run_native_agent_loop`` behaviour.

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
import json
from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy  # kept for type hints
from temporalio.workflow import ActivityCancellationType, ParentClosePolicy

from services.agent_bindings import unique_node_bindings
from services.node_registry import get_node_class
from services.tool_output import bound_tool_output, tool_output_is_capped

from ._retry_policies import (
    DEFAULT_ACTIVITY_RETRY,
    DELEGATION_CLEANUP_RETRY,
    LLM_STEP_RETRY,
    PERMIT_WAIT_RETRY,
)
from .agent_context_pressure import (
    clear_old_tool_results,
    clip_latest_turn,
    earlier_turns_over_budget,
    has_summarizable_history,
    projected_request_tokens,
    summarizer_view,
    turn_tool_chars,
)
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
LLM_STEP_HEARTBEAT_TIMEOUT = timedelta(minutes=1)


async def _execute_plugin_tool_activity(
    activity_name: str,
    activity_id: str,
    tool_payload: Dict[str, Any],
    context: Dict[str, Any],
) -> Any:
    """Preserve legacy commands; honor embedded task engines' execution policy.

    These plugins can perform irreversible device actions. Temporal's implicit
    retry defaults must not override their explicitly declared attempt limit.
    Routing comes from frozen workflow input, never live process settings.
    """
    options: Dict[str, Any] = {
        "start_to_close_timeout": TOOL_STEP_TIMEOUT,
        "heartbeat_timeout": TOOL_HEARTBEAT_TIMEOUT,
    }
    cls = get_node_class(tool_payload["node_type"])
    if getattr(cls, "workspace_task", False) and workflow.patched("workspace-task-activity-policy-v1"):
        options.update(
            start_to_close_timeout=cls.start_to_close_timeout,
            heartbeat_timeout=cls.heartbeat_timeout,
            retry_policy=cls.retry_policy.to_temporal(),
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        if context.get("temporal_worker_pool_enabled") is True:
            options["task_queue"] = cls.task_queue
        # The caller's authenticated principal is not a model argument. Keep
        # it on the leaf context so owner-only plugins cannot inherit the
        # legacy default principal accidentally.
        tool_payload = {**tool_payload, **_inherited_scope(context), "user_id": str(context.get("user_id") or "owner")}
    return await workflow.execute_activity(
        activity_name,
        args=[tool_payload],
        activity_id=activity_id,
        **options,
    )

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

# Agent/delegated children start with NO execution/run timeout, and the
# permit wait retries indefinitely. Runs may execute — or stay
# cooperatively paused — for months, and Temporal's timeout timers keep
# ticking through a pause, so any lifetime cap silently terminated
# long/paused work (skipping the compensation blocks: leaked permits,
# stuck task rows). Liveness is the activity heartbeat's job.
DUPLICATE_TOOL_NAME_ERROR_TYPE = "DuplicateToolNameError"


def _native_message(
    *,
    role: str,
    content: str,
    tool_call_id: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the canonical wire representation without SDK objects."""

    from services.llm.protocol import Message, message_to_wire

    return message_to_wire(
        Message(
            role=role,
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
    )


def _owner_message(content: str, images: Any) -> Dict[str, Any]:
    """The run's opening user message: the prompt, and the images the owner
    attached as ref-only image blocks (hydrated per provider call,
    ``services/llm/media.py``)."""

    from services.llm.media import image_blocks
    from services.llm.protocol import ContentBlock, Message, message_to_wire

    blocks = image_blocks(images if isinstance(images, list) else [])
    if not blocks:
        return _native_message(role="user", content=content)
    return message_to_wire(Message(role="user", content=content, blocks=[ContentBlock(type="text", text=content), *blocks]))


def _append_tool_result_message(
    messages: List[Dict[str, Any]],
    *,
    content: str,
    tool_call_id: str,
    name: str,
) -> None:
    """Append a tool result message in the canonical wire shape."""

    messages.append(
        _native_message(
            role="tool",
            content=content,
            tool_call_id=tool_call_id,
            name=name,
        )
    )


def _native_assistant_thinking(message: Any) -> Optional[str]:
    """Read reasoning text from a MessageWire assistant turn.

    ``agent.execute_llm_step.v1`` keeps its historical tool-call result keys,
    so native tool turns carry their reasoning only inside the canonical
    assistant message. Extracting it in workflow state preserves the public
    accumulated-thinking behavior without changing the activity envelope.
    """

    if not isinstance(message, dict):
        return None
    parts = [
        str(block.get("text") or "")
        for block in message.get("blocks") or []
        if isinstance(block, dict)
        and block.get("type") == "reasoning"
        and block.get("text")
    ]
    return "\n\n".join(parts) or None


def _tool_activity_id(tool_node_id: str, iteration: int, call_index: int) -> str:
    """Return a stable, unique id for one tool call in one agent turn."""
    return f"tool-{tool_node_id}-{iteration + 1}-{call_index + 1}"


def _delegation_child_id(
    agent_workflow_id: str,
    tool_node_id: str,
    iteration: int,
    call_index: int,
) -> str:
    """Return a stable child id for one delegation call."""
    return f"{agent_workflow_id}-delegate-{tool_node_id}-{iteration + 1}-{call_index + 1}"


def _refresh_tools_activity_id(tool_node_id: str, iteration: int, call_index: int) -> str:
    """Return a stable id for the hot-refresh owned by one tool call."""
    return f"refresh-tools-{tool_node_id}-{iteration + 1}-{call_index + 1}"


# Keys that identify the durable scope a run executes in. They are injected
# into a root node's context by MachinaWorkflow and must be inherited by
# delegated children: without them a subagent resolves no context of its own
# and its turns are silently never journalled. Forwarded verbatim — the
# framework does not interpret any of them.
_INHERITED_SCOPE_KEYS = (
    "graphVersion",
    "generation",
    "data_scope_id",
    "context_execution_id",
    "context_session_id",
    "user_id",
    "workspace_dir",
    "temporal_worker_pool_enabled",
    # The chat run the root run answers (services/chat/), so a tool call
    # or a delegated agent can attach what it produces to that answer.
    "run_scope",
    "employee_job_id",
    "parameter_snapshot",
)


# Roll the agent run over before Temporal's ~51,200-event hard terminate.
# The loop carries the transcript and a growing tool list, so a long agent
# run reaches that ceiling; the server's own suggestion is the primary
# signal and this soft cap is the deterministic backstop.
_AGENT_HISTORY_SOFT_CAP = 10_000

# State carried across a rollover: refs, counters, usage totals, and the
# live transcript itself. Carrying the transcript keeps the resumed run's
# conversation exact with no replay machinery; compaction keeps it
# token-bounded and the byte guard below keeps it under Temporal's 2 MiB
# payload error limit.
_RESUME_MARKER = "_agent_resume"

# Byte ceiling for the carried transcript (Temporal's payload error limit is
# 2 MiB for the WHOLE continue_as_new argument, which also carries the
# original context). Past this the rollover restarts from the opening
# prompt with a warning instead of failing the rollover itself.
_CAN_TRANSCRIPT_MAX_BYTES = 1_000_000


def _inherited_scope(context: Dict[str, Any]) -> Dict[str, Any]:
    """Return the scope keys a delegated child must inherit from its parent."""
    return {key: context[key] for key in _INHERITED_SCOPE_KEYS if key in context}


def _trusted_tool_scope(context: Dict[str, Any]) -> Dict[str, Any]:
    return {**_inherited_scope(context), "outputs": context.get("outputs") or context.get("inputs") or {}}


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
        "tool_call_id": str(
            call.get("id") or f"{iteration + 1}:{call_index + 1}"
        ),
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


@workflow.defn(sandboxed=False, name="DelegatedTaskWorkflow")
class DelegatedTaskWorkflow:
    """Own one queued delegation after the lead returns to its caller."""

    def __init__(self) -> None:
        self._control_paused = False

    @workflow.signal
    async def pause(self) -> None:
        self._control_paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._control_paused = False

    async def _wait_until_resumed(self) -> None:
        if self._control_paused:
            await workflow.wait_condition(lambda: not self._control_paused)

    @workflow.run
    async def run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        acquire_cancellation_options = {
            "cancellation_type": (
                ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
            )
        }
        lifecycle = request["lifecycle"]
        task_id = lifecycle["team_task_id"]
        root_id = lifecycle["root_execution_id"]
        acquired_permit_id: Optional[str] = None
        began = False
        try:
            await self._wait_until_resumed()
            info = workflow.info()
            await workflow.execute_activity(
                "agent.register_task_execution",
                args=[{**lifecycle, "runner_workflow_id": info.workflow_id,
                       "runner_run_id": info.run_id,
                       "child_workflow_id": request["child_workflow_id"]}],
                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                retry_policy=AGENT_ACTIVITY_RETRY,
                **acquire_cancellation_options,
            )
            await self._wait_until_resumed()
            acquire_payload = {
                "root_execution_id": root_id,
                "permit_id": task_id,
                "limit": request.get("limit", 3),
                "lease_version": 2,
            }
            acquire_result = await workflow.execute_activity(
                "agent.acquire_subagent_permit",
                args=[acquire_payload],
                start_to_close_timeout=timedelta(hours=1),
                heartbeat_timeout=timedelta(seconds=10),
                # Unlimited attempts — a queued delegation waits as long
                # as admission takes.
                retry_policy=PERMIT_WAIT_RETRY,
                **acquire_cancellation_options,
            )
            acquired_permit_id = str(
                (acquire_result or {}).get("lease_id")
                or task_id
            )
            await self._wait_until_resumed()
            await workflow.execute_activity(
                "agent.begin_delegation", args=[lifecycle],
                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                retry_policy=AGENT_ACTIVITY_RETRY,
                **acquire_cancellation_options,
            )
            began = True
            await self._wait_until_resumed()
            request["child_context"]["team_permit_id"] = acquired_permit_id
            child_handle = await workflow.start_child_workflow(
                "AgentWorkflow", args=[request["child_context"]],
                id=request["child_workflow_id"],
            )
            await workflow.execute_activity(
                "agent.register_task_execution",
                args=[{**lifecycle, "runner_workflow_id": info.workflow_id,
                       "runner_run_id": info.run_id,
                       "child_workflow_id": child_handle.id,
                       "child_run_id": child_handle.first_execution_run_id}],
                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                retry_policy=AGENT_ACTIVITY_RETRY,
            )
            result = await child_handle
            succeeded = bool(result.get("success", True)) if isinstance(result, dict) else True
            _response, summary = _normalise_delegated_result(result)
            return await workflow.execute_activity(
                "agent.finish_delegation",
                args=[{**lifecycle, "success": succeeded, "result": summary,
                       "error": result.get("error") if isinstance(result, dict) else None,
                       "terminal_event_id": f"{task_id}:terminal"}],
                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                retry_policy=AGENT_ACTIVITY_RETRY,
            )
        except asyncio.CancelledError:
            if began:
                try:
                    await workflow.execute_activity(
                        "agent.cancel_delegation",
                        args=[{
                            **lifecycle,
                            "reason": (
                                "Delegated task workflow cancelled"
                            ),
                            "terminal_event_id": f"{task_id}:terminal",
                        }],
                        activity_id="cancel-delegation",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=DELEGATION_CLEANUP_RETRY,
                        **acquire_cancellation_options,
                    )
                except Exception as cleanup_exc:  # noqa: BLE001
                    workflow.logger.error(
                        "DelegatedTaskWorkflow failed to persist "
                        f"cancellation for {task_id}: {cleanup_exc}"
                    )
            else:
                # Queue/acquire/begin may have completed even when their
                # result lost the cancellation race. The dedicated
                # transition is idempotent for absent/already-terminal
                # tasks and never invokes the normal failure/requeue path.
                try:
                    await workflow.execute_activity(
                        "agent.cancel_delegation",
                        args=[{
                            **lifecycle,
                            "reason": (
                                "Delegated task workflow cancelled"
                            ),
                            "terminal_event_id": f"{task_id}:terminal",
                        }],
                        activity_id="cancel-delegation-before-begin",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=DELEGATION_CLEANUP_RETRY,
                        **acquire_cancellation_options,
                    )
                except Exception as cleanup_exc:  # noqa: BLE001
                    workflow.logger.error(
                        "DelegatedTaskWorkflow failed to persist early "
                        f"cancellation for {task_id}: {cleanup_exc}"
                    )
            if acquired_permit_id:
                try:
                    await workflow.execute_activity(
                        "agent.release_subagent_permit",
                        args=[{
                            "root_execution_id": root_id,
                            "permit_id": acquired_permit_id,
                        }],
                        activity_id="release-permit-cancelled",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=DELEGATION_CLEANUP_RETRY,
                        **acquire_cancellation_options,
                    )
                    acquired_permit_id = None
                except Exception as cleanup_exc:  # noqa: BLE001
                    workflow.logger.error(
                        "DelegatedTaskWorkflow failed to release "
                        f"cancelled permit {task_id}: {cleanup_exc}"
                    )
            raise
        except Exception as exc:
            if began:
                await workflow.execute_activity(
                    "agent.finish_delegation",
                    args=[{**lifecycle, "success": False,
                           "error": f"{type(exc).__name__}: {exc}",
                           "terminal_event_id": f"{task_id}:terminal"}],
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
            raise
        finally:
            if acquired_permit_id:
                try:
                    await workflow.execute_activity(
                        "agent.release_subagent_permit",
                        args=[{
                            "root_execution_id": root_id,
                            "permit_id": acquired_permit_id,
                        }],
                        activity_id="release-permit-final",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=DELEGATION_CLEANUP_RETRY,
                        **acquire_cancellation_options,
                    )
                except Exception as release_exc:
                    workflow.logger.error(
                        "DelegatedTaskWorkflow final permit release failed "
                        f"for {task_id}: {release_exc}"
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

    def __init__(self) -> None:
        self._control_paused = False

    @workflow.signal
    async def pause(self) -> None:
        self._control_paused = True

    @workflow.signal
    async def resume(self) -> None:
        self._control_paused = False

    async def _wait_until_resumed(self) -> None:
        if self._control_paused:
            await workflow.wait_condition(lambda: not self._control_paused)

    @workflow.run
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        runtime_v2 = any((node.get("data") or {}).get("employee_recipe_version") == 2 and node.get("id") == context.get("node_id") for node in context.get("nodes", []))
        runtime_v2 = runtime_v2 and workflow.patched("employee-task-manager-runtime-v2")
        self._employee_runtime_v2 = runtime_v2
        async def fail_job(cancelled=False):
            if runtime_v2:
                await asyncio.shield(workflow.execute_activity("employee.job.failed", {**context, "cancelled": cancelled, "runtime_admission": True},
                    activity_id="employee-runtime-failed", start_to_close_timeout=PERSIST_TURN_TIMEOUT, retry_policy=AGENT_ACTIVITY_RETRY))
        try:
            result = await self._run_impl(context)
            if not result.get("success", True):
                await fail_job()
            return result
        except asyncio.CancelledError:
            await fail_job(cancelled=True)
            raise
        except Exception as exc:
            from temporalio.exceptions import is_cancelled_exception
            await fail_job(cancelled=is_cancelled_exception(exc))
            raise

    async def _run_impl(self, context: Dict[str, Any]) -> Dict[str, Any]:
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
        payload (provider, model, tools, memory, ...). Provider credentials
        are resolved inside leaf activities and never enter workflow history.
        Doing
        prep INSIDE the workflow (as an activity) keeps the orchestrator
        ignorant of agent-specific concerns and means the workflow
        owns its setup — Temporal's recommended structure.

        The resolved payload looks like::

            {
                "provider": str,
                "model": str,
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
                # Transcript-pressure controls; absent on histories recorded
                # before they existed, which then replay the original paths.
                "tool_result_max_chars": int,     # 0 keeps results whole
                "transcript_budget_bytes": int,
                "context_pressure_version": int,
            }

        Returns the final agent response, mirroring the shape
        ``services/ai.py:execute_agent`` returns today so downstream
        code (OutputPanel, edge inputs, etc.) doesn't change.
        """
        acquire_cancellation_options = {
            "cancellation_type": (
                ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
            )
        }

        # ---- Step 0: Resolve payload via the prep activity --------------
        # DB lookups + edge walking + tool schema build happen here, NOT
        # in the workflow body (workflows must be deterministic).
        payload = await workflow.execute_activity(
            "agent.prepare_payload",
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
        resume = dict(context.get(_RESUME_MARKER) or {})
        # run_id CHANGES on continue-as-new, so a resumed run would mint a
        # different execution id and break browser-session reuse, permit
        # scoping and root_execution_id fallback. Carry it explicitly.
        execution_id = (
            str(resume.get("execution_id") or "")
            or str(context.get("execution_id") or "")
            or workflow.info().run_id[:8]
        )
        task_scope_execution_id = str(
            payload.get("team_execution_id") or execution_id
        )
        # Needed by every terminal result, including agents that never make a
        # tool call (for example downstream taskTrigger automation).  It was
        # previously initialized only inside the tool-call branch.
        root_execution_id = str(
            payload.get("root_execution_id")
            or context.get("root_execution_id")
            or execution_id
        )
        max_iterations = int(payload.get("max_iterations") or _default_max_iterations())
        agent_node_id = payload["node_id"]
        agent_workflow_id = payload.get("workflow_id")
        self._parent_node_id: Optional[str] = context.get("parent_node_id")
        self._execution_id = task_scope_execution_id
        self._root_execution_id = root_execution_id
        self._provider = str(payload.get("provider") or "")

        # The provider-visible name is the dispatch key. Duplicate names fail
        # before the first billed LLM call and identify every conflicting
        # canvas node, instead of silently selecting the last connected one.
        if payload.get("parameter_snapshot"):
            context["parameter_snapshot"] = payload["parameter_snapshot"]
        if payload.get("employee_job_id"):
            context["employee_job_id"] = payload["employee_job_id"]
        binding_refresh_v2 = workflow.patched("agent-node-binding-refresh-v2")
        tools = payload.get("tools") or []
        if binding_refresh_v2:
            tools = unique_node_bindings(tools, id_key="tool_node_id")
        duplicate_tool_error = _duplicate_visible_tool_name_error(tools)
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

        # ---- Conversation persistence -----------------------------------
        # A connected Context node opts the agent into the plain conversation
        # store: ``prepare_payload`` loaded the stored transcript (and the
        # key), and the LLM-step activity saves each turn. Persistence never
        # steers the request — the activity always builds from ``messages``;
        # the key only says where to save.
        conversation_key = payload.get("conversation_key")

        # ---- Build the message list -------------------------------------
        # Precedence: carried rollover transcript (a resumed run continues
        # mid-flight) > stored conversation (a fresh firing continues the
        # conversation, with this firing's prompt appended) > bare opening.
        carried_transcript = list(resume.get("transcript") or [])
        stored_conversation = list(payload.get("conversation") or [])
        messages: List[Dict[str, Any]] = []
        user_prompt = payload.get("user_prompt") or ""
        memory_markdown = payload.get("memory_content") or ""

        system = payload.get("system_message") or ""
        if carried_transcript:
            messages = [dict(message) for message in carried_transcript]
        elif stored_conversation:
            # This firing's system message stays authoritative; stored
            # system messages are dropped so policy/skill changes take
            # effect. The new prompt arrives as the latest user message.
            if system:
                messages.append(_native_message(role="system", content=system))
            messages.extend(
                dict(wire)
                for wire in stored_conversation
                if isinstance(wire, dict) and wire.get("role") != "system"
            )
            if user_prompt:
                messages.append(_owner_message(user_prompt, payload.get("user_images")))
        else:
            if system:
                messages.append(_native_message(role="system", content=system))

            # Pre-loaded memory becomes an additional system note. The actual
            # parse / append happens in the persist_turn activity, but the
            # current markdown content seeds the conversation here.
            if memory_markdown:
                memory_content = f"## Prior conversation:\n{memory_markdown}"
                messages.append(
                    _native_message(role="system", content=memory_content)
                )

            if user_prompt:
                messages.append(_owner_message(user_prompt, payload.get("user_images")))

        # Map LLM tool name -> {node_type, version, task_queue, node_id,
        # parameters} so the workflow can schedule the right activity
        # when the LLM emits a tool_call.
        tool_index: Dict[str, Dict[str, Any]] = {t["name"]: t for t in tools}

        compaction_threshold = payload.get("compaction_threshold")

        # Transcript-pressure controls recorded by prepare-payload. A history
        # recorded before these keys existed reads 0 for each, so it keeps
        # whole tool results and replays the original compaction gate below.
        tool_output_limit = int(payload.get("tool_result_max_chars") or 0)
        pressure_version = int(payload.get("context_pressure_version") or 0)
        transcript_budget = int(payload.get("transcript_budget_bytes") or 0)

        def _result_is_capped(tool_name: Optional[str]) -> bool:
            """Whether the tool message named ``tool_name`` holds external output."""
            name = str(tool_name or "")
            if name.startswith("delegate_to_"):
                return False
            info = tool_index.get(name)
            return tool_output_is_capped(info.get("node_type") if info else None)

        thinking_accumulated = ""
        final_content: Optional[str] = None
        # Billing/observability is cumulative for the entire execution and
        # survives continue_as_new via the resume marker — the final result
        # must report every generation's tokens, not just the last one.
        # Context usage is reset only after a summary replaces the active
        # message history and exists solely to decide when to compact again.
        usage_total: Dict[str, int] = {
            k: int(v)
            for k, v in dict(resume.get("usage") or {}).items()
            if isinstance(v, int)
        }
        context_usage_total: Dict[str, int] = {
            k: int(v)
            for k, v in dict(resume.get("context_usage") or {}).items()
            if isinstance(v, int)
        }

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
        iteration_offset = int(resume.get("iteration") or 0)
        for iteration in range(iteration_offset, max_iterations):
            await self._wait_until_resumed()
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
            await self._wait_until_resumed()

            # Strip per-turn fields the activity doesn't need. Turns receive
            # only provider-neutral JSON tool definitions.
            visible_tools = [t for t in tools if not t.get("llm_hidden")]
            llm_payload = {
                "node_id": payload["node_id"],
                "workflow_id": agent_workflow_id,
                "provider": payload["provider"],
                "iteration": iteration,
                "max_iterations": max_iterations,
                "model": payload["model"],
                "messages": messages,
                "tools": [
                    t["definition"]
                    for t in visible_tools
                    if t.get("definition")
                ],
                "system_message": system,
                "temperature": payload.get("temperature", 0.7),
                "max_tokens": payload.get("max_tokens", 4096),
                "thinking_config": payload.get("thinking_config"),
                # Persistence only. The activity always builds its request
                # from ``messages`` above; the key just tells it where to
                # save the turn it actually sent. A Context node observes
                # the agent, it never steers it.
                **(
                    {"conversation_key": conversation_key}
                    if conversation_key
                    else {}
                ),
                # A provider may stop a turn to compact rather than to
                # answer. Without the finish reason that response is
                # indistinguishable from a normal completion and gets
                # returned to the user as a truncated final answer.
                "include_finish_reason": True,
                # The chat run this agent works for: the step stops when the
                # owner presses Stop, and the agent that answers the run
                # streams its text into the chat (services/chat/stream.py).
                # Activity input only, so recorded histories replay as before.
                **(
                    {"chat_run_id": payload["chat_run_id"]}
                    if payload.get("chat_run_id")
                    else {}
                ),
                **(
                    {"chat_stream": payload["chat_stream"]}
                    if payload.get("chat_stream")
                    else {}
                ),
            }

            # Transient provider failures (429 rate limit, 5xx, network)
            # retry inside the activity under LLM_STEP_RETRY (unlimited,
            # exponential backoff, provider retry_after honored via
            # next_retry_delay). Only non-retryable classifications
            # (invalid_request, authentication, ...) reach this except
            # block, and those are genuinely terminal for the run.
            try:
                step_result = await workflow.execute_activity(
                    "agent.execute_llm_step",
                    args=[llm_payload],
                    activity_id=f"llm-step-{iteration + 1}",
                    start_to_close_timeout=LLM_STEP_TIMEOUT,
                    heartbeat_timeout=LLM_STEP_HEARTBEAT_TIMEOUT,
                    retry_policy=LLM_STEP_RETRY,
                )
            except Exception as e:
                from temporalio.exceptions import is_cancelled_exception
                if getattr(self, "_employee_runtime_v2", False) and is_cancelled_exception(e):
                    raise asyncio.CancelledError() from e
                cause = getattr(e, "cause", None)
                raw_detail = str(cause) if cause is not None else str(e)
                cause_type = str(getattr(cause, "type", "") or "")
                cause_message = str(
                    getattr(cause, "message", "") or ""
                ).strip()
                recovery = {}
                if cause_type.startswith("LLMError.") or cause_type == "MissingAgentProviderCredential":
                    diagnostics = getattr(cause, "details", ())
                    if diagnostics and isinstance(diagnostics[0], dict):
                        recovery = {
                            key: diagnostics[0][key]
                            for key in ("hint", "requires_user_action", "retryable")
                            if diagnostics[0].get(key) is not None
                        }
                        if recovery:
                            recovery["error_type"] = "NodeUserError"
                safe_activity_types = {
                    "MissingAgentProviderCredential",
                    "EmptyAgentPrompt",
                }
                if cause_type.startswith("LLMError."):
                    detail = (
                        cause_message
                        or "The language model request failed."
                    )
                elif cause_type in safe_activity_types:
                    detail = (
                        cause_message
                        or "The language model request failed."
                    )
                else:
                    detail = (
                        "The language model step failed unexpectedly. "
                        "Retry the run or check server logs."
                    )
                workflow.logger.error(
                    f"AgentWorkflow LLM step failed terminally "
                    f"(iteration {iteration + 1}): {raw_detail}"
                )
                # A failed model step is terminal for this run.  Clear any
                # turn-scoped skill badges and publish an error status before
                # returning; otherwise the normal agent can remain visually
                # stuck on its last capability while the workflow has already
                # ended.  Keep the public event free of provider error text.
                await workflow.execute_activity(
                    "agent.skill.clear",
                    args=[{
                        "workflow_id": payload.get("workflow_id"),
                        "execution_id": task_scope_execution_id,
                        "agent_node_id": agent_node_id,
                    }],
                    activity_id="clear-active-skills-failed",
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                await self._emit_phase(
                    agent_node_id,
                    agent_workflow_id,
                    iteration,
                    max_iterations,
                    phase="failed",
                    status="error",
                    extra={"error": detail, "error_type": "NodeUserError", **recovery}
                    if recovery else None,
                )
                return {
                    "success": False,
                    "error": f"LLM step failed: {detail}",
                    "error_type": "LLMStepError",
                    **recovery,
                    "result": {
                        "iterations": iteration + 1,
                        "usage": usage_total,
                    },
                }

            # Accumulate usage + thinking for the eventual return value.
            for k, v in (step_result.get("usage") or {}).items():
                if isinstance(v, int):
                    usage_total[k] = usage_total.get(k, 0) + v
                    context_usage_total[k] = (
                        context_usage_total.get(k, 0) + v
                    )
            step_thinking = step_result.get("thinking")
            if not step_thinking:
                step_thinking = _native_assistant_thinking(
                    step_result.get("assistant_message")
                )
            if step_thinking:
                if thinking_accumulated:
                    thinking_accumulated += (
                        f"\n\n--- Iteration {iteration + 1} ---\n"
                        + step_thinking
                    )
                else:
                    thinking_accumulated = step_thinking

            kind = step_result.get("kind")

            # The activity returns the FULL serialized assistant message
            # (the legacy canonical {type, data} shape).
            # Appending verbatim preserves Gemini thought_signature, Anthropic
            # cache markers, OpenAI reasoning content — everything the next
            # turn's request needs.
            assistant_message = step_result.get("assistant_message")
            # Where this turn starts. Transcript-pressure relief never clears
            # or summarizes the turn whose tool results the model has not
            # read yet.
            turn_start = len(messages)
            if assistant_message:
                messages.append(assistant_message)

            # A provider that stops to compact has not answered. It carries
            # no tool calls, so it would otherwise be classified "final" and
            # its truncated content returned to the user as the response.
            # Treat it as a no-op turn and let the loop request again.
            finish_reason = str(step_result.get("finish_reason") or "").strip().lower()
            if kind != "tool_calls" and finish_reason == "compaction":
                workflow.logger.info(
                    f"Provider paused to compact at iteration {iteration + 1}; "
                    "continuing without treating the stop as a final answer"
                )
                continue

            if kind == "final":
                final_content = step_result.get("content", "")
                team_id = str(payload.get("team_id") or "")
                if team_id and payload.get("owns_execution_team"):
                    # Finalization is opportunistic. Queued/running work keeps
                    # the durable team active, but must not force this lead
                    # invocation to wait. Completion will emit taskTrigger and
                    # start the separately scoped review invocation.
                    await workflow.execute_activity(
                        "agent.finalize_team",
                        args=[{"team_id": team_id}],
                        activity_id="finalize-agent-team",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                await self._persist_turn(payload, human_text=user_prompt, assistant_text=final_content)
                break

            if kind != "tool_calls":
                workflow.logger.error(f"AgentWorkflow: unexpected LLM step kind={kind!r}")
                break

            # ---- Schedule tool activities -------------------------------
            # The LLM activity and phase broadcasts yield long enough for
            # a pause signal to arrive after the iteration-level check.
            # Admit the resulting tool/delegation command batch afresh.
            await self._wait_until_resumed()
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
            delegation_lifecycles: Dict[int, Dict[str, Any]] = {}
            delegation_release_attempts: Dict[int, int] = {}
            yielded_own_permit = False

            async def _release_delegation_permit(call_index: int) -> None:
                """Idempotently release one confirmed permit exactly once locally."""
                permit_id = delegation_permits.get(call_index)
                if not permit_id:
                    return
                release_attempt = delegation_release_attempts.get(
                    call_index,
                    0,
                )
                base_activity_id = (
                    f"release-permit-{iteration + 1}-{call_index + 1}"
                )
                release_activity_id = (
                    base_activity_id
                    if release_attempt == 0
                    else f"{base_activity_id}-recovery-{release_attempt}"
                )
                delegation_release_attempts[call_index] = (
                    release_attempt + 1
                )
                await workflow.execute_activity(
                    "agent.release_subagent_permit",
                    args=[{
                        "root_execution_id": root_execution_id,
                        "permit_id": permit_id,
                    }],
                    activity_id=release_activity_id,
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=DELEGATION_CLEANUP_RETRY,
                )
                delegation_permits.pop(call_index, None)

            async def _release_delegation_permit_for_cleanup(
                call_index: int,
            ) -> None:
                """Best-effort release that cannot replace an active failure."""
                permit_id = delegation_permits.get(call_index)
                try:
                    await _release_delegation_permit(call_index)
                except Exception as release_exc:  # noqa: BLE001
                    workflow.logger.error(
                        "AgentWorkflow failed to release delegation permit "
                        f"{permit_id or call_index}: {release_exc}"
                    )

            async def _release_remaining_delegation_permits() -> None:
                """Best-effort drain all confirmed permits in deterministic order."""
                for pending_index in sorted(list(delegation_permits)):
                    await _release_delegation_permit_for_cleanup(
                        pending_index
                    )

            async def _cancel_remaining_delegations() -> None:
                """Terminalize every task whose runner this workflow owns."""
                for pending_index in sorted(
                    list(delegation_lifecycles)
                ):
                    lifecycle = delegation_lifecycles[pending_index]
                    try:
                        await workflow.execute_activity(
                            "agent.cancel_delegation",
                            args=[{
                                **lifecycle,
                                "reason": (
                                    "Parent agent workflow cancelled"
                                ),
                                "terminal_event_id": (
                                    f"{lifecycle['team_task_id']}:terminal"
                                ),
                            }],
                            activity_id=(
                                "cancel-delegation-"
                                f"{iteration + 1}-{pending_index + 1}"
                            ),
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=DELEGATION_CLEANUP_RETRY,
                        )
                        delegation_lifecycles.pop(
                            pending_index,
                            None,
                        )
                    except Exception as cancellation_exc:  # noqa: BLE001
                        workflow.logger.error(
                            "AgentWorkflow failed to persist delegation "
                            f"cancellation for call {pending_index}: "
                            f"{cancellation_exc}"
                        )

            async def _cleanup_cancelled_delegations() -> None:
                # Terminal state and capacity are independent cleanup
                # obligations. Always attempt both and preserve the original
                # cancellation/error at the caller.
                await _cancel_remaining_delegations()
                await _release_remaining_delegation_permits()

            # A child that waits for grandchildren must not retain a slot,
            # otherwise N admitted children can all block forever trying to
            # acquire the N+1th permit. Yield while orchestrating descendants
            # and reacquire before resuming this agent's next LLM turn.
            own_logical_permit_id = str(
                context.get("team_task_id") or ""
            )
            own_permit_id = str(
                context.get("team_permit_id")
                or own_logical_permit_id
            )
            if delegation_call_indices and own_permit_id:
                await workflow.execute_activity(
                    "agent.release_subagent_permit",
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
                call_metadata = _tool_call_metadata(
                    agent_node_id=agent_node_id,
                    iteration=iteration,
                    call_index=call_index,
                    call=candidate,
                )
                child_context = {
                    # Inherited scope first: explicit keys below win.
                    **_inherited_scope(context),
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
                    "execution_id": task_scope_execution_id,
                    "root_execution_id": root_execution_id,
                    "provider": payload.get("provider"),
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
                        "parent_workflow_id": workflow.info().workflow_id,
                        "parent_run_id": workflow.info().run_id,
                        "task": task,
                        "root_execution_id": root_execution_id,
                        "delegation_depth": delegation_depth + 1,
                        "trace_id": child_context["trace_id"],
                        "assignment_event_id": (
                            f"{child_context['team_task_id']}:assigned"
                        ),
                    }
                    delegation_lifecycles[call_index] = (
                        lifecycle_payload
                    )
                    await self._wait_until_resumed()
                    await workflow.execute_activity(
                        "agent.queue_delegation",
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
                        **acquire_cancellation_options,
                    )
                    await self._wait_until_resumed()
                    acquire_payload = {
                        "root_execution_id": root_execution_id,
                        "permit_id": permit_id,
                        "limit": max_concurrent_subagents,
                        "lease_version": 2,
                    }
                    acquire_result = await workflow.execute_activity(
                        "agent.acquire_subagent_permit",
                        args=[acquire_payload],
                        activity_id=(
                            f"acquire-permit-{iteration + 1}-{call_index + 1}"
                        ),
                        start_to_close_timeout=timedelta(hours=1),
                        heartbeat_timeout=timedelta(seconds=10),
                        # Unlimited attempts — a queued delegation waits as
                        # long as admission takes.
                        retry_policy=PERMIT_WAIT_RETRY,
                        **acquire_cancellation_options,
                    )
                    delegation_permits[call_index] = str(
                        (acquire_result or {}).get("lease_id")
                        or permit_id
                    )
                    child_context["team_permit_id"] = (
                        delegation_permits[call_index]
                    )
                    # Claim only after admission. Persistence failure still
                    # prevents child startup and the task remains pending
                    # while the coordinator queues it.
                    try:
                        await self._wait_until_resumed()
                        await workflow.execute_activity(
                            "agent.begin_delegation",
                            args=[lifecycle_payload],
                            activity_id=(
                                f"begin-delegation-{iteration + 1}-{call_index + 1}"
                            ),
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                            **acquire_cancellation_options,
                        )
                    except asyncio.CancelledError:
                        # Cancellation cleanup is owned by the caller's
                        # _cleanup_cancelled_delegations() sweep; releasing
                        # here as well would double-release the permit.
                        raise
                    except BaseException:
                        await _release_delegation_permit(call_index)
                        raise
                child_id = _delegation_child_id(
                    workflow.info().workflow_id,
                    candidate_tool["tool_node_id"],
                    iteration,
                    call_index,
                )
                try:
                    await self._wait_until_resumed()
                    delegation_handles[call_index] = await workflow.start_child_workflow(
                        "AgentWorkflow",
                        args=[child_context],
                        id=child_id,
                    )
                    if team_id:
                        child_handle = delegation_handles[call_index]
                        await workflow.execute_activity(
                            "agent.register_task_execution",
                            args=[{**lifecycle_payload,
                                   "child_workflow_id": child_handle.id,
                                   "child_run_id": child_handle.first_execution_run_id}],
                            activity_id=f"register-delegation-{iteration + 1}-{call_index + 1}",
                            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                            retry_policy=AGENT_ACTIVITY_RETRY,
                            **acquire_cancellation_options,
                        )
                except asyncio.CancelledError:
                    # Cancellation cleanup is owned by the caller's
                    # _cleanup_cancelled_delegations() sweep.
                    raise
                except BaseException:
                    if team_id and call_index in delegation_permits:
                        await _release_delegation_permit(call_index)
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
                        "agent.release_subagent_permit",
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
                    "parent_workflow_id": workflow.info().workflow_id,
                    "parent_run_id": workflow.info().run_id,
                    "task": mission,
                    "employee_job_id": context.get("employee_job_id"),
                    "context": request.get("context"),
                    "acceptance_criteria": request.get("acceptance_criteria"),
                    "depends_on": request.get("depends_on") or [],
                    "root_execution_id": root_execution_id,
                    "delegation_depth": delegation_depth + 1,
                    "trace_id": trace_id,
                    "assignment_event_id": f"{task_id}:assigned",
                }
                # queue_delegation is intentionally retained: it is
                # idempotent for the pre-created task and records the same
                # lifecycle event as direct delegation without duplicating it.
                await self._wait_until_resumed()
                await workflow.execute_activity(
                    "agent.queue_delegation",
                    args=[{**lifecycle, "queued_event_id": f"{task_id}:queued"}],
                    activity_id=f"queue-task-manager-{iteration + 1}-{call_index + 1}",
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                if context.get("employee_job_id"):
                    request_context = {**(request_context or {}), "acceptance_criteria": request.get("acceptance_criteria"), "depends_on": request.get("depends_on") or []}
                context_text = request_context if isinstance(request_context, str) else _serialise_tool_result(request_context)
                child_context = {
                    # Inherited scope first: explicit keys below win.
                    **_inherited_scope(context),
                    "node_id": assignee_id, "node_type": delegate["node_type"],
                    "node_data": {**(delegate.get("parameters") or {}),
                                  "system_message": mission, "prompt": context_text or mission},
                    "inputs": {}, "workflow_id": payload.get("workflow_id"),
                    "session_id": payload.get("session_id", "default"),
                    "execution_id": execution_id, "root_execution_id": root_execution_id,
                    "parent_node_id": agent_node_id, "delegation_depth": delegation_depth + 1,
                    "team_id": team_id, "team_task_id": task_id, "trace_id": trace_id,
                    "nodes": context.get("nodes") or [], "edges": context.get("edges") or [],
                    "invocation": {"task": mission, "context": context_text},
                }
                child_id = _delegation_child_id(
                    workflow.info().workflow_id, assignee_id, iteration, call_index
                ) + f"-{task_id}"
                runner_id = f"{child_id}-runner"
                await self._wait_until_resumed()
                delegated_task_options: Dict[str, Any] = {
                    "id": runner_id,
                    "parent_close_policy": ParentClosePolicy.ABANDON,
                }
                from services.temporal.trigger_listener_workflow import (
                    event_workflow_search_attributes,
                )

                search_attributes = event_workflow_search_attributes(
                    payload.get("workflow_id")
                )
                if search_attributes is not None:
                    delegated_task_options["search_attributes"] = (
                        search_attributes
                    )
                await workflow.start_child_workflow(
                    "DelegatedTaskWorkflow",
                    args=[{"lifecycle": lifecycle, "child_context": child_context,
                           "child_workflow_id": child_id, "limit": max_concurrent_subagents}],
                    **delegated_task_options,
                )
                return {"status": "queued", "result": None, "runner_workflow_id": runner_id}

            # Preflight every Task Manager assignment activity in this LLM
            # turn concurrently. Each activity performs authorization and
            # creates its durable queue row; only trusted scheduling envelopes
            # are allowed to reach the child-workflow bridge below.
            task_manager_preflight_indices: List[int] = []
            task_manager_preflight_handles: List[Any] = []
            for preflight_index, preflight_call in enumerate(calls):
                preflight_tool = tool_index.get(preflight_call.get("name", ""))
                preflight_args = preflight_call.get("args") or {}
                if (
                    preflight_tool
                    and preflight_tool.get("node_type") == "taskManager"
                    and preflight_args.get("operation") == "assign_task"
                ):
                    preflight_metadata = _tool_call_metadata(
                        agent_node_id=agent_node_id,
                        iteration=iteration,
                        call_index=preflight_index,
                        call=preflight_call,
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
                        "execution_id": task_scope_execution_id,
                        "root_execution_id": root_execution_id,
                        "parent_node_id": agent_node_id,
                        "team_lead_node_id": agent_node_id,
                        "nodes": context.get("nodes") or [],
                        "edges": context.get("edges") or [],
                        **preflight_metadata,
                    }
                    if workflow.patched("employee-task-manager-tool-scope-v2"):
                        preflight_payload = {**_trusted_tool_scope(context), **preflight_payload}
                    task_manager_preflight_indices.append(preflight_index)
                    if not task_manager_preflight_handles:
                        # ``start_activity`` does not yield; one admission
                        # check protects this whole concurrent batch.
                        await self._wait_until_resumed()
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
                        "agent.release_subagent_permit",
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
            try:
                for delegation_index in delegation_call_indices[
                    :max_concurrent_subagents
                ]:
                    await _start_delegation(delegation_index)
                    next_delegation_to_start += 1
            except BaseException:
                await _cleanup_cancelled_delegations()
                raise

            blocked_error = None
            for call_index, call in enumerate(calls):
                if call.get("parse_error"):
                    # Native adapters retain malformed arguments rather than
                    # throwing during normalization. Return a deterministic
                    # tool result and let the model repair its invocation on
                    # the next turn; never execute partially parsed args.
                    _append_tool_result_message(
                        messages,
                        content=_serialise_tool_result(
                            {
                                "error": "Tool arguments were invalid JSON",
                                "detail": str(call.get("parse_error")),
                                "raw_arguments": str(
                                    call.get("raw_arguments") or ""
                                ),
                            }
                        ),
                        tool_call_id=call.get("id", ""),
                        name=call.get("name", ""),
                    )
                    continue
                tool_info = tool_index.get(call.get("name", ""))
                if tool_info is None:
                    workflow.logger.warning(f"AgentWorkflow: LLM called unknown tool {call.get('name')!r}; " "returning error to model")
                    _append_tool_result_message(
                        messages,
                        content=(
                            f"Error: tool {call.get('name')!r} is not "
                            "connected to this agent."
                        ),
                        tool_call_id=call.get("id", ""),
                        name=call.get("name", ""),
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
                        _append_tool_result_message(
                            messages,
                            content=(
                                '{"error": "delegate_to_* requires a '
                                "non-empty 'task' argument describing "
                                'what the agent should do."}'
                            ),
                            tool_call_id=call.get("id", ""),
                            name=tool_name,
                        )
                        continue
                    if delegation_depth >= max_delegation_depth:
                        _append_tool_result_message(
                            messages,
                            content=(
                                '{"error": "Maximum delegation depth '
                                f'{max_delegation_depth} exceeded."}}'
                            ),
                            tool_call_id=call.get("id", ""),
                            name=tool_name,
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
                    # Canvas-aware tools (agentBuilder/taskManager for caller
                    # resolution and Android for its connected model) opt in
                    # via the BaseNode.needs_canvas
                    # ClassVar. Default tools execute against their own
                    # params alone and don't see the parent canvas.
                    plugin_cls = get_node_class(tool_info["node_type"])
                    if plugin_cls is not None and plugin_cls.needs_canvas:
                        child_nodes = context.get("nodes") or []
                        child_edges = context.get("edges") or []
                    else:
                        child_nodes = []
                        child_edges = []

                call_metadata = _tool_call_metadata(
                    agent_node_id=agent_node_id,
                    iteration=iteration,
                    call_index=call_index,
                    call=call,
                )
                tool_payload = {
                    "node_id": tool_info["tool_node_id"],
                    "node_type": tool_info["node_type"],
                    "node_data": tool_node_data,
                    # The model's arguments, unmerged. ToolNodes with a split
                    # ToolInput schema must validate these on their own — the
                    # merged node_data above validates against Params
                    # (extra="ignore"), which silently DROPS model arguments
                    # like Simple Memory's operation/content and degrades the
                    # call to a no-op.
                    "tool_args": call_args,
                    **({"parameter_snapshot": context["parameter_snapshot"]} if context.get("parameter_snapshot") else {}),
                    "inputs": {},
                    "workflow_id": payload.get("workflow_id"),
                    "session_id": payload.get("session_id", "default"),
                    "execution_id": task_scope_execution_id,
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
                    # Provider call ids are normally present. The stable
                    # workflow position keeps CloudEvents occurrence IDs
                    # unique when an adapter omits one.
                    "tool_call_id": str(
                        call.get("id") or f"{iteration + 1}:{call_index + 1}"
                    ),
                    **call_metadata,
                    # Chat (services/chat/steps.py): a call of a stopped run
                    # is not run, and a call of the agent answering the run
                    # shows as a working step. Activity input only.
                    **(
                        {"chat_run_id": payload["chat_run_id"]}
                        if payload.get("chat_run_id")
                        else {}
                    ),
                    **(
                        {"chat_stream": payload["chat_stream"]}
                        if payload.get("chat_stream")
                        else {}
                    ),
                }
                if workflow.patched("employee-task-manager-tool-scope-v2"):
                    tool_payload = {**_trusted_tool_scope(context), **tool_payload}

                tool_activity_name = (
                    "agent.skill.invoke"
                    if tool_info["node_type"] == "_builtin_skill"
                    else f"node.{tool_info['node_type']}.v{tool_info['version']}"
                )

                try:
                    await self._emit_phase(
                        agent_node_id,
                        agent_workflow_id,
                        iteration,
                        max_iterations,
                        phase="executing_tool",
                        extra={
                            "tool_name": call.get("name", ""),
                            "tool_node_id": tool_info["tool_node_id"],
                            "tool_call_id": str(
                                call.get("id")
                                or f"{iteration + 1}:{call_index + 1}"
                            ),
                        },
                    )
                except asyncio.CancelledError:
                    await _cleanup_cancelled_delegations()
                    raise

                try:
                    if is_delegation and tool_info["node_type"] in AGENT_WORKFLOW_TYPES:
                        handle = delegation_handles[call_index]
                        try:
                            tool_result = await handle
                        except asyncio.CancelledError:
                            # Cancellation cleanup is owned by the outer
                            # _cleanup_cancelled_delegations() sweep.
                            raise
                        except BaseException:
                            await _release_delegation_permit(call_index)
                            raise
                        else:
                            await _release_delegation_permit(call_index)
                        # The child is done — drop its handle so a completed
                        # delegation no longer blocks the rollover guard.
                        # (An agent that delegates every turn previously
                        # could never continue_as_new and grew until
                        # Temporal's hard history terminate.)
                        delegation_handles.pop(call_index, None)
                        child_succeeded = (
                            bool(tool_result.get("success", True))
                            if isinstance(tool_result, dict) else True
                        )
                        child_error = (
                            tool_result.get("error")
                            if isinstance(tool_result, dict) else None
                        )
                        child_response, child_summary = _normalise_delegated_result(
                            tool_result
                        )
                        team_id = str(payload.get("team_id") or context.get("team_id") or "")
                        if team_id:
                            task_id = (
                                f"task-{root_execution_id}-{agent_node_id}-"
                                f"{iteration + 1}-{call_index + 1}"
                            )
                            await workflow.execute_activity(
                                "agent.finish_delegation",
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
                                    "success": child_succeeded,
                                    "result": child_summary,
                                    "error": child_error,
                                    "terminal_event_id": f"{task_id}:terminal",
                                }],
                                activity_id=(
                                    f"finish-delegation-{iteration + 1}-{call_index + 1}"
                                ),
                                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                                retry_policy=AGENT_ACTIVITY_RETRY,
                            )
                            delegation_lifecycles.pop(
                                call_index,
                                None,
                            )
                        child_recovery = {
                            key: tool_result[key]
                            for key in ("hint", "requires_user_action", "retryable")
                            if isinstance(tool_result, dict) and key in tool_result
                        }
                        tool_result = {
                            "success": child_succeeded,
                            "status": "submitted" if child_succeeded else "failed",
                            "result": child_response,
                            "usage": child_summary.get("usage"),
                            **({"error": child_error} if child_error else {}),
                            **child_recovery,
                        }
                        if next_delegation_to_start < len(delegation_call_indices):
                            await _start_delegation(
                                delegation_call_indices[next_delegation_to_start]
                            )
                            next_delegation_to_start += 1
                    else:
                        tool_activity_id = _tool_activity_id(
                            tool_info["tool_node_id"],
                            iteration,
                            call_index,
                        )
                        if call_index in task_manager_preflight_results:
                            tool_result = task_manager_preflight_results[call_index]
                            if isinstance(tool_result, BaseException):
                                raise tool_result
                        else:
                            await self._wait_until_resumed()
                            tool_result = await _execute_plugin_tool_activity(
                                tool_activity_name,
                                tool_activity_id,
                                tool_payload,
                                context,
                            )
                        if (
                            tool_info["node_type"] == "taskManager"
                            and isinstance(tool_result, dict)
                            and isinstance(tool_result.get("delegation_request"), dict)
                        ):
                            if call_index in task_manager_delegation_tasks:
                                # Await in original tool-call order; every
                                # child was already started above, so slow
                                # earlier siblings do not prevent later work.
                                # pop: a finished task must not block the
                                # rollover guard for the rest of the turn.
                                delegated = await task_manager_delegation_tasks.pop(call_index)
                            else:
                                delegated = await _run_task_manager_delegation(
                                    tool_result["delegation_request"], call_index, call
                                )
                            tool_result = {
                                **tool_result,
                                "delegation_status": delegated["status"],
                                "delegation_result": delegated["result"],
                                "delegation_usage": delegated.get("usage"),
                            }
                    if (
                        isinstance(tool_result, dict)
                        and tool_result.get("error")
                        and tool_result.get("requires_user_action") is True
                        and workflow.patched("agent-user-action-error-v1")
                    ):
                        blocked_error = blocked_error or {
                            "error": tool_result["error"],
                            "error_type": "NodeUserError",
                            "hint": tool_result.get("hint"),
                            "requires_user_action": True,
                            "retryable": False,
                        }
                    tool_content = _serialise_tool_result(tool_result)
                    if (
                        tool_output_limit
                        and not is_delegation
                        and tool_output_is_capped(tool_info["node_type"])
                    ):
                        # Only the model's copy is cut; the tool node keeps
                        # its whole result.
                        tool_content = bound_tool_output(
                            tool_content, tool_output_limit
                        )
                    await self._emit_phase(
                        agent_node_id,
                        agent_workflow_id,
                        iteration,
                        max_iterations,
                        phase="tool_completed",
                        extra={
                            "tool_name": call.get("name", ""),
                            "tool_node_id": tool_info["tool_node_id"],
                            "tool_call_id": str(
                                call.get("id") or f"{iteration + 1}:{call_index + 1}"
                            ),
                        },
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
                            refresh_activity_id = _refresh_tools_activity_id(
                                tool_info["tool_node_id"],
                                iteration,
                                call_index,
                            )
                            refresh_payload = {
                                "operations": ops_from_tool,
                                "agent_node_type": payload.get("node_type") or context.get("node_type"),
                                **call_metadata,
                            }
                            if binding_refresh_v2:
                                refresh_payload["bound_node_ids"] = [tool.get("tool_node_id") for tool in tools if tool.get("tool_node_id")]
                                refresh_payload["graph_snapshot"] = {"nodes": context.get("nodes") or [], "edges": context.get("edges") or []}
                                refresh_payload["parameter_snapshot"] = context.get("parameter_snapshot") or {}
                            await self._wait_until_resumed()
                            refresh_result = await workflow.execute_activity(
                                "agent.refresh_tools",
                                args=[refresh_payload],
                                activity_id=refresh_activity_id,
                                start_to_close_timeout=timedelta(seconds=30),
                                retry_policy=AGENT_ACTIVITY_RETRY,
                            )
                            if binding_refresh_v2 and refresh_result.get("graph_snapshot"):
                                refreshed_graph = refresh_result["graph_snapshot"]
                                context["nodes"] = refreshed_graph.get("nodes") or context.get("nodes") or []
                                context["edges"] = refreshed_graph.get("edges") or context.get("edges") or []
                                context["parameter_snapshot"] = {**(context.get("parameter_snapshot") or {}), **(refresh_result.get("parameter_updates") or {})}
                            added_tools = refresh_result.get("tools") or []
                            if binding_refresh_v2:
                                added_tools = unique_node_bindings(
                                    added_tools, id_key="tool_node_id",
                                    bound=[tool.get("tool_node_id") for tool in tools if tool.get("tool_node_id")],
                                )
                            refresh_duplicate_error = _duplicate_visible_tool_name_error(
                                [*tools, *added_tools]
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
                                delegates = [tool for tool in tools if tool.get("name", "").startswith("delegate_to_")]
                                if delegates and binding_refresh_v2:
                                    roster = "\n".join(
                                        f"{tool.get('tool_node_id')}: {(tool.get('tool_info') or {}).get('label') or tool.get('node_type')}"
                                        for tool in delegates
                                    )
                                    messages.append(_native_message(
                                        role="system",
                                        content="Updated connected teammates (assignee_node_id: label/type):\n" + roster,
                                    ))
                                workflow.logger.info(
                                    "AgentWorkflow rebound %d tool(s) after canvas mutation (total bound=%d)",
                                    len(added_tools),
                                    len(tools),
                                )
                except asyncio.CancelledError:
                    await _cleanup_cancelled_delegations()
                    raise
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
                        try:
                            await workflow.execute_activity(
                                "agent.finish_delegation",
                                args=[{
                                    "team_id": team_id,
                                    "team_task_id": task_id,
                                    "parent_agent_node_id": agent_node_id,
                                    "child_agent_node_id": tool_info["tool_node_id"],
                                    "child_agent_name": str(
                                        (tool_info.get("tool_info") or {}).get(
                                            "label"
                                        )
                                        or tool_info.get("node_type")
                                        or "agent"
                                    ),
                                    "workflow_id": payload.get("workflow_id"),
                                    "parent_agent_workflow_id": (
                                        workflow.info().workflow_id
                                    ),
                                    "root_execution_id": root_execution_id,
                                    "trace_id": str(call.get("id", "") or ""),
                                    "success": False,
                                    "error": f"{type(e).__name__}: {e}",
                                    "terminal_event_id": f"{task_id}:terminal",
                                }],
                                activity_id=(
                                    "finish-delegation-"
                                    f"{iteration + 1}-{call_index + 1}"
                                ),
                                start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                                retry_policy=AGENT_ACTIVITY_RETRY,
                            )
                            delegation_lifecycles.pop(
                                call_index,
                                None,
                            )
                        except asyncio.CancelledError:
                            await _cleanup_cancelled_delegations()
                            raise
                    tool_content = f'{{"error": "{type(e).__name__}: {e}"}}'
                    try:
                        await self._emit_phase(
                            agent_node_id,
                            agent_workflow_id,
                            iteration,
                            max_iterations,
                            phase="tool_completed",
                            extra={
                                "tool_name": call.get("name", ""),
                                "tool_node_id": tool_info["tool_node_id"],
                                "tool_call_id": str(
                                    call.get("id")
                                    or f"{iteration + 1}:{call_index + 1}"
                                ),
                                # Do not put raw failures into public status
                                # events.  This safe flag is enough for the
                                # broadcaster to retain ``tool <name>`` with a
                                # failed capability state.
                                "tool_failed": True,
                            },
                        )
                    except asyncio.CancelledError:
                        await _cleanup_cancelled_delegations()
                        raise

                _append_tool_result_message(
                    messages,
                    content=tool_content,
                    tool_call_id=call.get("id", ""),
                    name=call.get("name", ""),
                )

            if yielded_own_permit:
                await self._wait_until_resumed()
                await workflow.execute_activity(
                    "agent.acquire_subagent_permit",
                    args=[{
                        "root_execution_id": root_execution_id,
                        "permit_id": own_permit_id,
                        "limit": max_concurrent_subagents,
                    }],
                    activity_id=f"reacquire-own-permit-{iteration + 1}",
                    start_to_close_timeout=timedelta(hours=1),
                    heartbeat_timeout=timedelta(seconds=10),
                    retry_policy=AGENT_ACTIVITY_RETRY,
                    **acquire_cancellation_options,
                )

            # ---- Persist this turn (append-per-turn) -------------------
            # Snapshot the most recent user/assistant pair into memory.
            await self._persist_turn(
                payload,
                human_text=user_prompt,
                assistant_text="",  # interim turn — body lives in tool_results
                interim=True,
            )

            # ---- Transcript pressure and compaction ----------------------
            if blocked_error:
                # Complete/persist the current tool turn, then stop before
                # another model request or paid compaction can be scheduled.
                if conversation_key and workflow.patched("agent-blocked-tool-turn-save-v1"):
                    await workflow.execute_activity(
                        "agent.persist_turn",
                        args=[{"conversation_key": conversation_key,
                               "tool_results": [m for m in messages[turn_start:] if m.get("role") == "tool"]}],
                        activity_id="persist-blocked-tool-turn",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                await workflow.execute_activity(
                    "agent.skill.clear",
                    args=[{"workflow_id": payload.get("workflow_id"),
                           "execution_id": task_scope_execution_id,
                           "agent_node_id": agent_node_id}],
                    activity_id="clear-active-skills-blocked",
                    start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                    retry_policy=AGENT_ACTIVITY_RETRY,
                )
                await self._emit_phase(
                    agent_node_id, agent_workflow_id, iteration, max_iterations,
                    phase="failed", status="error", extra=blocked_error,
                )
                return {"success": False, **blocked_error,
                        "result": {"iterations": iteration + 1, "usage": usage_total}}

            # Summarize, then swap messages: no memory-node gate, no
            # checkpoint machinery. A later rollover resumes from the
            # compacted state for free, because the next LLM turn sends and
            # saves the compacted messages. The recorded
            # ``context_pressure_version`` picks the rules:
            #   - version 1 (agent_context_pressure): clear old tool results
            #     past the byte budget, summarize when the NEXT request
            #     reaches the threshold or the earlier turns alone keep the
            #     transcript over budget, and never summarize this turn,
            #     whose tool results the model has not read yet;
            #   - no version (a history recorded before it existed): the
            #     original rule, cumulative tokens -> summarize everything,
            #     so a replay schedules exactly the commands it recorded.
            compact_source: Optional[List[Dict[str, Any]]] = None
            kept_turn: List[Dict[str, Any]] = []
            if pressure_version >= 1:
                clearing = clear_old_tool_results(
                    messages,
                    turn_start=turn_start,
                    budget_bytes=transcript_budget,
                    is_capped=_result_is_capped,
                )
                if clearing.changed:
                    workflow.logger.info(
                        f"AgentWorkflow cleared {clearing.changed} earlier "
                        f"tool result(s) at iteration {iteration + 1}: "
                        f"{clearing.bytes_before} -> {clearing.bytes_after} "
                        f"bytes (budget {transcript_budget})"
                    )
                token_total = projected_request_tokens(
                    step_result.get("usage"),
                    turn_tool_chars(messages, turn_start)
                    - clearing.chars_removed,
                )
                history_over_budget = earlier_turns_over_budget(
                    messages,
                    turn_start=turn_start,
                    budget_bytes=transcript_budget,
                    size=clearing.bytes_after,
                )
                if compaction_threshold and (
                    token_total >= compaction_threshold or history_over_budget
                ):
                    if has_summarizable_history(messages, turn_start):
                        workflow.logger.info(
                            f"AgentWorkflow compaction triggered at iteration "
                            f"{iteration + 1}: next request about "
                            f"{token_total} tokens (threshold "
                            f"{compaction_threshold}), transcript "
                            f"{clearing.bytes_after} bytes (budget "
                            f"{transcript_budget}); summarizing {turn_start} "
                            f"earlier message(s), keeping this turn's "
                            f"{len(messages) - turn_start}"
                        )
                        compact_source = summarizer_view(messages[:turn_start])
                        kept_turn = messages[turn_start:]
                    else:
                        workflow.logger.info(
                            f"AgentWorkflow compaction skipped at iteration "
                            f"{iteration + 1}: nothing older than this turn "
                            "to summarize"
                        )
            else:
                token_total = int(
                    context_usage_total.get("total_tokens") or 0
                )
                if not token_total:
                    token_total = sum(
                        int(context_usage_total.get(key) or 0)
                        for key in (
                            "input_tokens",
                            "cache_creation_tokens",
                            "cache_read_tokens",
                            "output_tokens",
                        )
                    )
                if compaction_threshold and token_total >= compaction_threshold:
                    workflow.logger.info(
                        f"AgentWorkflow compaction triggered at iteration "
                        f"{iteration + 1}: {token_total} tokens >= threshold "
                        f"{compaction_threshold} ({len(messages)} live messages)"
                    )
                    compact_source = messages
            if compact_source is not None:
                compact_payload = {
                    "session_id": payload.get("session_id", "default"),
                    "node_id": payload["node_id"],
                    "messages": compact_source,
                    "provider": payload["provider"],
                    "model": payload["model"],
                }
                await self._wait_until_resumed()
                try:
                    compact_result = await workflow.execute_activity(
                        "agent.compact_context",
                        args=[compact_payload],
                        activity_id=f"compact-context-{iteration + 1}",
                        start_to_close_timeout=COMPACT_MEMORY_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                except Exception as compact_error:
                    # Compaction is the run's pressure-relief valve. If it
                    # fails even after the activity policy's retries, the
                    # transcript can only grow until the provider rejects
                    # it — fail the run loudly NOW, at the moment the
                    # cause is clear, instead of later with a confusing
                    # context-overflow error.
                    cause = getattr(compact_error, "cause", None)
                    compact_detail = str(
                        getattr(cause, "message", "") or cause or compact_error
                    )
                    workflow.logger.error(
                        f"AgentWorkflow compaction failed terminally "
                        f"(iteration {iteration + 1}): {compact_detail}"
                    )
                    # Same terminal-cleanup contract as the LLM-failure
                    # path: clear turn-scoped skill badges before the
                    # error status, or the canvas stays stuck on the last
                    # capability after the workflow has ended.
                    await workflow.execute_activity(
                        "agent.skill.clear",
                        args=[{
                            "workflow_id": payload.get("workflow_id"),
                            "execution_id": task_scope_execution_id,
                            "agent_node_id": agent_node_id,
                        }],
                        activity_id="clear-active-skills-compaction-failed",
                        start_to_close_timeout=PERSIST_TURN_TIMEOUT,
                        retry_policy=AGENT_ACTIVITY_RETRY,
                    )
                    await self._emit_phase(
                        agent_node_id,
                        agent_workflow_id,
                        iteration,
                        max_iterations,
                        phase="failed",
                        status="error",
                    )
                    return {
                        "success": False,
                        "error": f"Compaction failed: {compact_detail}",
                        "error_type": "CompactionError",
                        "result": {
                            "iterations": iteration + 1,
                            "usage": usage_total,
                        },
                    }
                # The summarizer is another billed model call. Include it in
                # execution-wide usage, but never in the active-context
                # counter.
                for key, value in (
                    compact_result.get("usage") or {}
                ).items():
                    if isinstance(value, int):
                        usage_total[key] = (
                            usage_total.get(key, 0) + value
                        )
                # The activity raises on any failure, so a result here
                # always carries a non-empty summary.
                summary = compact_result.get("summary", "")
                dropped_count = len(messages) - len(kept_turn)
                # Rebuild as: the ORIGINAL system prompt, verbatim, plus ONE
                # user message carrying the summary and the live request.
                #
                # The system prompt must never be modified or duplicated by
                # compaction: (1) it is the agent's contract (personality +
                # tool/delegation guidance) and must stay byte-stable for
                # provider prompt caching and behavioral consistency; (2) the
                # next firing's seeding drops stored system messages so
                # policy changes take effect — anything compaction stores
                # under the system role silently vanishes on the next
                # firing, which is how a summary once survived only until
                # the next chat message while the noisy tool tail outlived
                # it.
                #
                # The summary rides a USER message for the same reason: user
                # wires persist through seeding, so the compacted knowledge
                # crosses firings with the conversation it summarizes.
                compacted_content = (
                    "## Compacted conversation summary\n"
                    "The conversation so far was compacted to stay within "
                    "the model's context window. Treat this summary as the "
                    "authoritative record of prior work; do not repeat "
                    "completed steps.\n\n"
                    f"{summary}"
                )
                if user_prompt:
                    compacted_content += (
                        f"\n\n## Current request\n{user_prompt}"
                    )
                messages = [
                    _native_message(
                        role="system",
                        content=system,
                    ),
                    _native_message(
                        role="user",
                        content=compacted_content,
                    ),
                    # Version 1 keeps this turn verbatim, because its tool
                    # results are still unread. Empty on the original path.
                    *kept_turn,
                ]
                context_usage_total = {}
                turn_start = len(messages) - len(kept_turn)
                workflow.logger.info(
                    f"AgentWorkflow compaction applied at iteration "
                    f"{iteration + 1}: {dropped_count} messages -> "
                    f"{len(messages)} (summary {len(summary)} chars; "
                    f"{len(kept_turn)} message(s) of this turn kept verbatim; "
                    "earlier tool calls/results now live only inside the "
                    "summary; system prompt preserved verbatim)"
                )

            if pressure_version >= 1:
                # Last resort, against whatever room the earlier turns now
                # leave: cut this turn's external results to one length that
                # fits. Clipping only after summarizing keeps as much of the
                # unread results as possible.
                clipping = clip_latest_turn(
                    messages,
                    turn_start=turn_start,
                    budget_bytes=transcript_budget,
                    is_capped=_result_is_capped,
                )
                if clipping.changed:
                    workflow.logger.info(
                        f"AgentWorkflow cut {clipping.changed} tool result(s) "
                        f"of this turn at iteration {iteration + 1}: "
                        f"{clipping.bytes_before} -> {clipping.bytes_after} "
                        f"bytes (budget {transcript_budget})"
                    )
                if transcript_budget and clipping.bytes_after > transcript_budget:
                    workflow.logger.warning(
                        f"AgentWorkflow transcript is still "
                        f"{clipping.bytes_after} bytes after relief at "
                        f"iteration {iteration + 1} (budget "
                        f"{transcript_budget}"
                        + (
                            ""
                            if compaction_threshold
                            else "; compaction is off for this run"
                        )
                        + ")"
                    )

            # ---- Continue-as-new -------------------------------------
            # Only at a clean turn boundary, and never while a delegation
            # is in flight: delegation_handles and the Task-Manager task
            # map hold live ChildWorkflowHandle / asyncio.Task objects
            # that cannot cross the boundary. The own-permit reacquire
            # above is the permit-safe point.
            from services.temporal.trigger_listener_workflow import (
                _history_pressure,
            )

            if _history_pressure(_AGENT_HISTORY_SOFT_CAP):
                delegations_live = bool(
                    delegation_handles or task_manager_delegation_tasks
                )
                if not delegations_live:
                    # The live transcript crosses the boundary directly.
                    # Compaction keeps it token-bounded; the byte guard
                    # below keeps a pathological transcript away from
                    # Temporal's 2 MiB payload error (which would fail the
                    # rollover itself). Oversized and uncompactable means
                    # restarting from the opening prompt with a warning —
                    # visible, and strictly better than a dead run.
                    carried = messages
                    transcript_bytes = len(
                        json.dumps(carried, default=str).encode("utf-8")
                    )
                    if transcript_bytes > _CAN_TRANSCRIPT_MAX_BYTES:
                        workflow.logger.warning(
                            f"AgentWorkflow rollover transcript is "
                            f"{transcript_bytes} bytes (> "
                            f"{_CAN_TRANSCRIPT_MAX_BYTES}); dropping to the "
                            "opening prompt"
                        )
                        carried = []
                    workflow.logger.info(
                        f"AgentWorkflow continue_as_new at iteration "
                        f"{iteration + 1} (history pressure)"
                    )
                    workflow.continue_as_new(
                        args=[
                            {
                                **context,
                                _RESUME_MARKER: {
                                    "iteration": iteration + 1,
                                    "execution_id": execution_id,
                                    "transcript": carried,
                                    "usage": usage_total,
                                    "context_usage": context_usage_total,
                                },
                            }
                        ]
                    )

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
            "team_id": payload.get("team_id") or context.get("team_id"),
            "execution_id": context.get("execution_id"),
            "root_execution_id": root_execution_id,
        }

        # Persist the result to the OutputStore via the workflow_service
        # so ParameterResolver can resolve {{aiAgent.response}} in
        # downstream nodes. F4.A's activity wrapper does this via
        # NodeExecutor; F4.B needs an explicit activity because we
        # bypass NodeExecutor entirely.
        await workflow.execute_activity(
            "agent.store_output",
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

        await workflow.execute_activity(
            "agent.skill.clear",
            args=[{"workflow_id": payload.get("workflow_id"), "execution_id": task_scope_execution_id,
                   "agent_node_id": agent_node_id}],
            activity_id="clear-active-skills",
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )
        if payload.get("employee_runtime_delivery") and workflow.patched("employee-task-manager-runtime-v2"):
            await workflow.execute_activity("employee.job.deliver", {**context, "team_id": result_payload.get("team_id"),
                "outputs": {**(context.get("outputs") or {}), agent_node_id: result_payload}},
                activity_id="employee-reviewed-delivery", start_to_close_timeout=timedelta(days=31),
                heartbeat_timeout=timedelta(minutes=2), retry_policy=AGENT_ACTIVITY_RETRY)

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
            "agent.broadcast_progress",
            args=[
                {
                    "node_id": node_id,
                    "workflow_id": workflow_id,
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                    "phase": phase,
                    "execution_id": getattr(self, "_execution_id", None),
                    "root_execution_id": getattr(self, "_root_execution_id", None),
                    "provider": getattr(self, "_provider", None),
                    **({"status": status} if status else {}),
                    **(extra or {}),
                }
            ],
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )

        if self._parent_node_id:
            # Parent progress is a relationship signal, not capability
            # ownership.  Never mirror the child's ``tool_name`` /
            # ``tool_node_id`` extras onto the parent: the status activity
            # turns those fields into durable ``last_capability`` metadata,
            # which made a child's tool usage appear on other agent cards.
            # The parent keeps its own delegate/task-manager tool status while
            # this phase-only event merely indicates that descendant work is
            # advancing.
            await workflow.execute_activity(
                "agent.broadcast_progress",
                args=[
                    {
                        "node_id": self._parent_node_id,
                        "workflow_id": workflow_id,
                        "iteration": iteration,
                        "max_iterations": max_iterations,
                        "phase": "delegating",
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
            "agent.persist_turn",
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


def _normalise_delegated_result(result: Any) -> tuple[str, Dict[str, Any]]:
    """Expose the child's answer, not its internal AgentWorkflow envelope."""
    if isinstance(result, dict):
        payload = result.get("result") if isinstance(result.get("result"), dict) else result
        response = result.get("response")
        if response is None:
            response = payload.get("response")
        if response is None:
            response = result.get("result", result.get("error", ""))
        text = response if isinstance(response, str) else _serialise_tool_result(response)
        summary = {
            "response": text,
            "usage": payload.get("usage") or result.get("usage") or {},
            "model": payload.get("model") or result.get("model"),
            "provider": payload.get("provider") or result.get("provider"),
        }
        return text, {key: value for key, value in summary.items() if value not in (None, {})}
    text = str(result) if result is not None else ""
    return text, {"response": text}


def _serialise_tool_result(result: Any) -> str:
    """Return a string body for a ``ToolMessage``.

    Mirrors the in-process tool-call serialisation in
    ``services/agent_runtime.py:run_native_agent_loop``: feed the LLM the handler's raw
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
