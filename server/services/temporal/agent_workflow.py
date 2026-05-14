"""F4.B: ``AgentWorkflow`` — Temporal child workflow for AI agent loops.

Replaces the inline LangGraph loop inside ``services/ai.py:execute_agent``
with a workflow-orchestrated loop: each LLM turn is an activity, each
tool call is a per-type activity (registered via
``BaseNode.as_activity()``, F4.A), and memory persistence happens per
turn so a workflow failure mid-loop doesn't lose progress.

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
- ``deep_agent``, ``rlm_agent``, ``claude_code_agent`` are NOT migrated
  here. Their loops are externalised (deepagents / RLM REPL / Claude
  CLI ``--resume`` session) and live in single Temporal activities via
  the F4.A per-type dispatch path. They never enter ``AgentWorkflow``.
- Memory appends per turn (not on completion).
- Tool activity failure (after retries) returns an error to the LLM as
  a ``ToolMessage`` and the agent continues — matches today's LangGraph
  behaviour.

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

from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy  # kept for type hints

from ._retry_policies import DEFAULT_ACTIVITY_RETRY


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
# can override via ``payload["max_iterations"]``; this is the hard cap.
DEFAULT_MAX_ITERATIONS = 50

# Retry policy for the agent's own activities (LLM step, persist,
# compact). Tool activities use their plugin's policy. Wave 12 D1:
# delegates to the shared constant so the policy's
# non_retryable_error_types include ``NodeUserError`` — user-correctable
# failures inside the LLM step fail fast instead of burning 3 retries.
AGENT_ACTIVITY_RETRY: RetryPolicy = DEFAULT_ACTIVITY_RETRY


@workflow.defn(sandboxed=False, name="AgentWorkflow")
class AgentWorkflow:
    """Run an AI agent as a Temporal child workflow.

    Scheduled by ``MachinaWorkflow.run()`` when:
      - ``settings.temporal_agent_workflow_enabled`` is True, AND
      - the node type is in the migrating set (``aiAgent``,
        ``chatAgent``, 12 specialized agents, 2 team leads).

    ``deep_agent`` / ``rlm_agent`` / ``claude_code_agent`` skip this
    workflow and stay as F4.A per-type activities.
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
        # ---- Step 0: Resolve payload via the prep activity --------------
        # DB lookups + edge walking + tool schema build happen here, NOT
        # in the workflow body (workflows must be deterministic).
        payload = await workflow.execute_activity(
            "agent.prepare_payload.v1",
            args=[context],
            start_to_close_timeout=PERSIST_TURN_TIMEOUT * 2,  # 60s default
            retry_policy=AGENT_ACTIVITY_RETRY,
        )
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
            messages.append({
                "type": "system",
                "data": {"content": f"## Prior conversation:\n{memory_markdown}"},
            })

        user_prompt = payload.get("user_prompt") or ""
        if user_prompt:
            messages.append({"type": "human", "data": {"content": user_prompt}})

        # Map LLM tool name -> {node_type, version, task_queue, node_id,
        # parameters} so the workflow can schedule the right activity
        # when the LLM emits a tool_call.
        tools = payload.get("tools") or []
        tool_index: Dict[str, Dict[str, Any]] = {
            t["name"]: t for t in tools
        }

        max_iterations = int(payload.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
        token_total = 0
        compaction_threshold = payload.get("compaction_threshold")
        thinking_accumulated = ""
        final_content: Optional[str] = None
        usage_total: Dict[str, int] = {}

        agent_node_id = payload["node_id"]
        agent_workflow_id = payload.get("workflow_id")

        # Emit "executing" + phase="starting" via the existing
        # broadcast_agent_progress activity (CloudEvents
        # com.machinaos.agent.progress + raw-dict node_status for
        # canvas glow). Mirrors what F4.A's _node_activity wrapper
        # does for non-agent plugins.
        await self._emit_phase(
            agent_node_id, agent_workflow_id, 0, max_iterations,
            phase="starting", status="executing",
        )

        # ---- Main loop --------------------------------------------------
        for iteration in range(max_iterations):
            workflow.logger.info(
                f"AgentWorkflow iteration {iteration} "
                f"(messages={len(messages)} tools={len(tools)})"
            )

            # CloudEvents-shaped agent_progress per LLM turn. Mirrors the
            # legacy LangGraph path's super-step broadcast (RFC §6.4).
            # FE consumes the typed envelope and updates the canvas
            # node's "N / max" iteration badge live.
            await self._emit_phase(
                agent_node_id, agent_workflow_id, iteration, max_iterations,
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
                "tool_data": [t["tool_info"] for t in tools],
                "system_message": system,
                "temperature": payload.get("temperature", 0.7),
                "max_tokens": payload.get("max_tokens", 4096),
                "thinking_config": payload.get("thinking_config"),
            }

            step_result = await workflow.execute_activity(
                "agent.execute_llm_step.v1",
                args=[llm_payload],
                start_to_close_timeout=LLM_STEP_TIMEOUT,
                retry_policy=AGENT_ACTIVITY_RETRY,
            )

            # Accumulate usage + thinking for the eventual return value.
            for k, v in (step_result.get("usage") or {}).items():
                if isinstance(v, int):
                    usage_total[k] = usage_total.get(k, 0) + v
            if step_result.get("thinking"):
                if thinking_accumulated:
                    thinking_accumulated += (
                        f"\n\n--- Iteration {iteration + 1} ---\n"
                        + step_result["thinking"]
                    )
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
                await self._persist_turn(
                    payload, human_text=user_prompt, assistant_text=final_content
                )
                break

            if kind != "tool_calls":
                workflow.logger.error(
                    f"AgentWorkflow: unexpected LLM step kind={kind!r}"
                )
                break

            # ---- Schedule tool activities -------------------------------
            calls = step_result.get("calls") or []
            workflow.logger.info(f"AgentWorkflow scheduling {len(calls)} tool call(s)")

            for call in calls:
                tool_info = tool_index.get(call.get("name", ""))
                if tool_info is None:
                    workflow.logger.warning(
                        f"AgentWorkflow: LLM called unknown tool {call.get('name')!r}; "
                        "returning error to model"
                    )
                    messages.append({
                        "type": "tool",
                        "data": {
                            "content": (
                                f"Error: tool {call.get('name')!r} is not "
                                "connected to this agent."
                            ),
                            "tool_call_id": call.get("id", ""),
                            "name": call.get("name", ""),
                        },
                    })
                    continue

                tool_payload = {
                    "node_id": tool_info["tool_node_id"],
                    "node_type": tool_info["node_type"],
                    "node_data": {**(tool_info.get("parameters") or {}), **(call.get("args") or {})},
                    "inputs": {},
                    "workflow_id": payload.get("workflow_id"),
                    "session_id": payload.get("session_id", "default"),
                    "nodes": [],   # tool activity doesn't need full canvas
                    "edges": [],
                }

                tool_activity_name = (
                    f"node.{tool_info['node_type']}.v{tool_info['version']}"
                )

                await self._emit_phase(
                    agent_node_id, agent_workflow_id, iteration, max_iterations,
                    phase="executing_tool",
                    extra={"tool_name": call.get("name", ""), "tool_node_id": tool_info["tool_node_id"]},
                )

                try:
                    tool_result = await workflow.execute_activity(
                        tool_activity_name,
                        args=[tool_payload],
                        start_to_close_timeout=TOOL_STEP_TIMEOUT,
                        heartbeat_timeout=TOOL_HEARTBEAT_TIMEOUT,
                    )
                    tool_content = _serialise_tool_result(tool_result)
                    await self._emit_phase(
                        agent_node_id, agent_workflow_id, iteration, max_iterations,
                        phase="tool_completed",
                        extra={"tool_name": call.get("name", "")},
                    )
                except Exception as e:  # noqa: BLE001 — Temporal handles retries
                    # After all retries exhausted, surface the error to
                    # the LLM (per user decision: LLM sees error and
                    # continues — matches LangGraph today).
                    workflow.logger.warning(
                        f"AgentWorkflow tool {tool_info['node_type']!r} failed: {e}"
                    )
                    tool_content = (
                        f'{{"error": "{type(e).__name__}: {e}"}}'
                    )

                messages.append({
                    "type": "tool",
                    "data": {
                        "content": tool_content,
                        "tool_call_id": call.get("id", ""),
                        "name": call.get("name", ""),
                    },
                })

            # ---- Persist this turn (append-per-turn) -------------------
            # Snapshot the most recent user/assistant pair into memory.
            await self._persist_turn(
                payload,
                human_text=user_prompt,
                assistant_text="",  # interim turn — body lives in tool_results
                interim=True,
            )

            # ---- Compaction check --------------------------------------
            token_total = sum(
                v for k, v in usage_total.items()
                if k in ("input_tokens", "output_tokens")
            )
            if (
                compaction_threshold
                and token_total >= compaction_threshold
                and memory_markdown
            ):
                workflow.logger.info(
                    f"AgentWorkflow compaction triggered: {token_total} tokens"
                )
                compact_result = await workflow.execute_activity(
                    "agent.compact_memory.v1",
                    args=[{
                        "session_id": payload.get("session_id", "default"),
                        "node_id": payload["node_id"],
                        "memory_content": memory_markdown,
                        "provider": payload["provider"],
                        "api_key": payload["api_key"],
                        "model": payload["model"],
                    }],
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
                        "AgentWorkflow compaction failed (%s); continuing "
                        "with un-compacted history",
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
            workflow.logger.warning(
                f"AgentWorkflow hit max_iterations={max_iterations}; truncating"
            )
            final_content = final_content or (
                "[AgentWorkflow truncated after max_iterations; "
                "the model did not produce a final response]"
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
            args=[{
                "node_id": agent_node_id,
                "session_id": payload.get("session_id", "default"),
                "result": result_payload,
            }],
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )

        # Final lifecycle broadcast — canvas glow goes green + FE
        # consumers of com.machinaos.agent.progress see phase="completed".
        await self._emit_phase(
            agent_node_id, agent_workflow_id, max_iterations, max_iterations,
            phase="completed", status="success",
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
        glows accordingly (executing / success / error)."""
        await workflow.execute_activity(
            "agent.broadcast_progress.v1",
            args=[{
                "node_id": node_id,
                "workflow_id": workflow_id,
                "iteration": iteration,
                "max_iterations": max_iterations,
                "phase": phase,
                **({"status": status} if status else {}),
                **(extra or {}),
            }],
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
            args=[{
                "memory_node_id": memory_node_id,
                "human_text": human_text,
                "assistant_text": assistant_text,
                "window_size": int(payload.get("memory_window_size") or 10),
            }],
            start_to_close_timeout=PERSIST_TURN_TIMEOUT,
            retry_policy=AGENT_ACTIVITY_RETRY,
        )


def _serialise_tool_result(result: Any) -> str:
    """Return a string body for a ``ToolMessage``.

    Mirrors the legacy ``create_tool_node`` in ``services/ai.py``: feed
    the LLM the handler's raw return value (``json.dumps(result,
    default=str)``), NOT the Temporal activity envelope. The F4.A
    per-type activity wraps the handler result as
    ``{"success": bool, "result": {...}, "node_id": ..., "node_type": ...,
    "timestamp": ...}``; we strip the envelope so the LLM doesn't see
    infrastructure metadata.
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
