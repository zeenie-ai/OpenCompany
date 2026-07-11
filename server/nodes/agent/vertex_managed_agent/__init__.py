"""Vertex Managed Agent — Google Gemini Enterprise Agent Platform.

Runs a cloud-hosted managed agent (default: Antigravity) through the
Interactions API and bridges the MachinaOS canvas into it:

- Connected tool nodes are declared as custom ``function`` tools; the
  cloud agent hands control back with ``status == "requires_action"``
  and the pending calls are dispatched through the standard
  ``execute_tool`` path (so tool nodes glow exactly like they do for
  the local aiAgent loop), then answered via ``function_result`` inputs
  on a chained follow-up create.
- Conversation + sandbox continuity rides the connected simpleMemory
  node: ``vertex_interaction_id`` / ``vertex_environment_id`` are stored
  in its params and the turn is appended to ``memory_content``.
- Cloud-side tool usage (sandbox commands, google_search, ...) is
  surfaced LIVE as dynamic ``vertexCloudTool`` canvas nodes via the
  workflow-ops protocol (agentBuilder pattern) — see ``_ops.py``: each
  turn streams SSE step events (``stream_interaction``), minting and
  glowing nodes while the cloud agent is still working, with a post-turn
  sweep as catch-all. The stream is visibility-only; the authoritative
  result always comes from the final non-stream ``interactions.get``.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue
from services.plugin.edge_walker import collect_agent_connections
from services.plugin.scaling import AI_START_TO_CLOSE

from .._handles import STD_AGENT_HINTS, std_agent_handles
from .._vertex import (
    DEFAULT_LOCATION,
    DEFAULT_MANAGED_AGENT,
    build_genai_client,
    is_expired_environment_error,
    raise_as_user_error,
    resolve_api_key_from_context,
    stream_interaction,
)

logger = get_logger(__name__)

# Cloud-side step types surfaced as dynamic canvas nodes. The enterprise
# surface also reports sandbox activity as function_call steps whose
# names were never declared by us (e.g. run_command) — those are keyed
# by name. provision_sandbox is infrastructure noise, not a tool.
_CLOUD_STEP_LABELS = {
    "code_execution_call": "Code Execution",
    "google_search_call": "Google Search",
    "url_context_call": "URL Context",
    "mcp_server_tool_call": "MCP Tool",
}
_CLOUD_NOISE_NAMES = frozenset({"provision_sandbox"})


class VertexManagedAgentParams(BaseModel):
    """Managed-agent tuning surface (aiAgent-parity minimal).

    ``api_key`` is intentionally NOT a field — the gemini credential is
    auto-injected by ``node_executor._inject_api_keys`` and recovered
    from ``_raw_parameters`` (same convention as the other agents).
    """

    prompt: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Enter your prompt or use template variables...",
            "rows": 4,
        },
    )
    agent: str = Field(
        default=DEFAULT_MANAGED_AGENT,
        description=(
            "Managed agent to run: the prebuilt Antigravity agent or a "
            "custom agent id created via the Vertex Agent Admin node."
        ),
    )
    project_id: str = Field(
        default="",
        description=(
            "GCP project id for the Agent Platform surface (auth via "
            "gcloud Application Default Credentials). Leave empty to use "
            "a stored 'AIza' Gemini API key instead."
        ),
        json_schema_extra={"placeholder": "my-gcp-project"},
    )
    # Single-valued on purpose: keeps detect_ai_provider/_inject_api_keys
    # resolving the gemini credential for this node type.
    provider: Literal["gemini"] = "gemini"

    # ---- "Options" group (collapsed by default) ----
    system_instruction: Optional[str] = Field(
        default=None,
        json_schema_extra={"rows": 3, "group": "options"},
    )
    location: str = Field(
        default=DEFAULT_LOCATION,
        json_schema_extra={"group": "options"},
    )
    max_turns: int = Field(
        default=25,
        ge=1,
        le=100,
        description="Cap on requires_action tool round-trips per run.",
        json_schema_extra={"group": "options"},
    )
    visualize_cloud_tools: bool = Field(
        default=True,
        description=(
            "Mint canvas nodes showing which cloud-side tools the "
            "managed agent used (sandbox commands, search, ...)."
        ),
        json_schema_extra={"group": "options"},
    )

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "groups": {
                "options": {
                    "display_name": "Options",
                    "placeholder": "Add Option",
                },
            },
        },
    )


class VertexManagedAgentOutput(BaseModel):
    response: Optional[str] = None
    interaction_id: Optional[str] = None
    environment_id: Optional[str] = None
    status: Optional[str] = None
    agent: Optional[str] = None
    provider: str = "gemini"
    turns: Optional[int] = None
    cloud_tools_used: Optional[List[str]] = None
    usage: Optional[Dict[str, Any]] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VertexManagedAgentNode(ActionNode):
    type = "vertex_managed_agent"
    display_name = "Vertex Agent"
    subtitle = "Managed Agent"
    group = ("agent",)
    description = (
        "Google cloud-hosted managed agent (Antigravity) with sandboxed "
        "code execution. Bridges connected tool nodes into the cloud via "
        "function calling and shows cloud tool usage on the canvas."
    )
    component_kind = "agent"
    tool_name = "delegate_to_vertex_managed_agent"
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY
    # 30m: managed-agent sandbox turns (provision + code execution +
    # multi-round tool bridging) routinely exceed the 10m action default.
    start_to_close_timeout = AI_START_TO_CLOSE
    # Canvas nodes/edges must reach ctx under Temporal per-tool dispatch
    # (tool discovery + workflow-ops minting are canvas-aware).
    needs_canvas = True

    Params = VertexManagedAgentParams
    Output = VertexManagedAgentOutput

    @Operation(
        "execute",
        cost={"service": "vertex_agent", "action": "interact", "count": 1},
    )
    async def execute_op(
        self,
        ctx: NodeContext,
        params: VertexManagedAgentParams,
    ) -> Any:
        from services.plugin.deps import get_database
        from services.status_broadcaster import get_status_broadcaster

        start_time = time.time()
        broadcaster = get_status_broadcaster()
        database = get_database()
        node_id = ctx.node_id
        workflow_id = ctx.workflow_id

        async def phase(name: str, **details: Any) -> None:
            await broadcaster.update_node_status(
                node_id,
                "executing",
                {"phase": name, "agent_type": "vertex_managed", **details},
                workflow_id=workflow_id,
            )

        await phase("initializing", message="Starting Vertex managed agent...")

        memory_data, _, tool_data, input_data, _ = await collect_agent_connections(
            node_id, ctx.raw, database, log_prefix="[Vertex Agent]"
        )

        prompt = params.prompt or self._prompt_from_input(input_data)
        if not prompt:
            raise NodeUserError(
                "vertex_managed_agent: provide a prompt or connect an "
                "input node that produced a message."
            )

        api_key = resolve_api_key_from_context(ctx.raw)
        client = build_genai_client(api_key, params.project_id, params.location)

        # ---- tool bridge: connected tool nodes -> function declarations
        await phase("building_tools")
        declared_tools, tool_configs = await self._build_function_tools(tool_data)

        # ---- live visibility state (shared with the post-turn sweep)
        live_nodes: Dict[str, str] = {}  # cloud_tool_key -> canvas node id
        open_calls: Dict[str, Dict[str, Any]] = {}  # step id -> {node_id, label, arguments}
        on_event = None
        if params.visualize_cloud_tools:
            on_event = self._make_live_handler(
                broadcaster=broadcaster,
                workflow_id=workflow_id,
                agent_node_id=node_id,
                declared_names=frozenset(tool_configs),
                live_nodes=live_nodes,
                open_calls=open_calls,
            )

        # ---- memory bridge: interaction/environment chain ids
        prev_interaction_id: Optional[str] = None
        environment: Any = "remote"
        memory_node_id = (memory_data or {}).get("node_id")
        if memory_node_id:
            await phase("loading_memory")
            mem_params = await database.get_node_parameters(memory_node_id) or {}
            prev_interaction_id = mem_params.get("vertex_interaction_id") or None
            environment = mem_params.get("vertex_environment_id") or "remote"

        create_kwargs: Dict[str, Any] = {
            "agent": params.agent,
            "store": True,
        }
        if params.system_instruction:
            create_kwargs["system_instruction"] = params.system_instruction
        if declared_tools:
            create_kwargs["tools"] = declared_tools

        async def run_turn(**kw: Any) -> Any:
            try:
                return await stream_interaction(
                    client, on_event=on_event, **create_kwargs, **kw
                )
            except Exception as exc:  # noqa: BLE001 — mapped below
                raise_as_user_error(exc, what="Vertex managed agent interaction")

        # ---- turn 1 (with one stale-chain retry)
        await phase("invoking_llm", message=f"Calling {params.agent}...")
        turn = 1
        await broadcaster.broadcast_agent_progress(
            node_id, workflow_id=workflow_id, iteration=turn, max_iterations=params.max_turns
        )
        try:
            interaction = await run_turn(
                input=prompt,
                environment=environment,
                **({"previous_interaction_id": prev_interaction_id} if prev_interaction_id else {}),
            )
        except NodeUserError as exc:
            cause = exc.__cause__
            if prev_interaction_id and cause is not None and is_expired_environment_error(cause):
                logger.warning(
                    "[Vertex Agent] stale interaction chain for %s — retrying fresh", node_id
                )
                if memory_node_id:
                    await self._save_chain_ids(database, memory_node_id, None, None)
                prev_interaction_id = None
                interaction = await run_turn(input=prompt, environment="remote")
            else:
                raise

        # ---- requires_action loop: answer pending local function calls
        while (
            getattr(interaction, "status", None) == "requires_action"
            and turn < params.max_turns
        ):
            results = self._pending_function_results_needed(interaction, tool_configs)
            if not results:
                logger.warning(
                    "[Vertex Agent] requires_action with no answerable "
                    "function calls (node=%s) — stopping",
                    node_id,
                )
                break
            function_results = []
            for call in results:
                await phase("executing_tool", tool_name=call["name"])
                function_results.append(
                    await self._execute_bridged_call(call, tool_configs, ctx)
                )
            turn += 1
            await broadcaster.broadcast_agent_progress(
                node_id, workflow_id=workflow_id, iteration=turn, max_iterations=params.max_turns
            )
            interaction = await run_turn(
                input=function_results,
                previous_interaction_id=interaction.id,
                environment=getattr(interaction, "environment_id", None) or environment,
            )

        response_text = getattr(interaction, "output_text", None) or ""
        status = getattr(interaction, "status", None)
        environment_id = getattr(interaction, "environment_id", None)
        usage = self._usage_dict(interaction)

        # ---- cloud tool visualization: post-turn catch-all sweep.
        # Live streaming already minted+pulsed most usage; this covers
        # steps the stream missed and closes any still-glowing calls.
        final_used = self._collect_cloud_tool_usage(interaction, tool_configs)
        cloud_tools = {
            **{key: self._label_for_key(key) for key in live_nodes},
            **final_used,
        }
        if params.visualize_cloud_tools and workflow_id:
            from ._ops import ensure_cloud_tool_nodes, pulse_node

            try:
                sweep = {
                    key: label
                    for key, label in final_used.items()
                    if key not in live_nodes
                }
                if sweep:
                    resolved = await ensure_cloud_tool_nodes(
                        workflow_id=workflow_id,
                        agent_node_id=node_id,
                        used=sweep,
                    )
                    for swept_id in resolved.values():
                        await pulse_node(swept_id, "executing", workflow_id=workflow_id)
                        await pulse_node(swept_id, "success", workflow_id=workflow_id)
                    await self._record_swept_outputs(interaction, resolved, workflow_id)
                # Calls whose result step never streamed: don't leave them glowing.
                for dangling_id in {c["node_id"] for c in open_calls.values()}:
                    await pulse_node(dangling_id, "success", workflow_id=workflow_id)
                open_calls.clear()
            except Exception:  # noqa: BLE001 — visualization is best-effort
                logger.exception("[Vertex Agent] cloud-tool minting failed")

        # ---- memory persist
        if memory_node_id:
            await phase("saving_memory")
            await self._save_chain_ids(
                database,
                memory_node_id,
                getattr(interaction, "id", None),
                environment_id,
                human=prompt,
                assistant=response_text,
                window_size=int((memory_data or {}).get("window_size") or 10),
            )

        # ---- usage bookkeeping (tokens billed by Google; recorded for stats)
        try:
            await database.save_api_usage_metric(
                {
                    "session_id": ctx.raw.get("session_id", "default"),
                    "node_id": node_id,
                    "workflow_id": workflow_id,
                    "service": "vertex_agent",
                    "operation": "interaction",
                    "endpoint": "interactions.create",
                    "resource_count": int((usage or {}).get("total_tokens") or 1),
                    "cost": 0.0,
                }
            )
        except Exception:  # noqa: BLE001 — metrics are best-effort
            logger.exception("[Vertex Agent] usage metric save failed")

        success = status == "completed"
        await broadcaster.update_node_status(
            node_id,
            "success" if success else "warning",
            {
                "message": f"Vertex agent {status} in {turn} turn(s)",
                "status": status,
                "turns": turn,
            },
            workflow_id=workflow_id,
        )
        logger.info(
            "[Vertex Agent] node=%s status=%s turns=%d elapsed=%.1fs",
            node_id,
            status,
            turn,
            time.time() - start_time,
        )

        return {
            "response": response_text,
            "interaction_id": getattr(interaction, "id", None),
            "environment_id": environment_id,
            "status": status,
            "agent": params.agent,
            "provider": "gemini",
            "turns": turn,
            "cloud_tools_used": sorted(cloud_tools.values()) or None,
            "usage": usage,
            "timestamp": datetime.now().isoformat(),
        }

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prompt_from_input(input_data: Any) -> str:
        if isinstance(input_data, dict):
            for key in ("message", "text", "content"):
                value = input_data.get(key)
                if value:
                    return str(value)
            return str(input_data) if input_data else ""
        return str(input_data) if input_data else ""

    @staticmethod
    async def _build_function_tools(
        tool_data: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """Convert connected tool nodes into Interactions function tools.

        Reuses ``AIService._build_tool_from_node`` so DB ToolSchema
        overrides, plugin ClassVar names, and the delegation schema gate
        all apply exactly as they do for the local agent loop.
        """
        if not tool_data:
            return [], {}
        from services.plugin.deps import get_ai_service

        ai_service = get_ai_service()
        declared: List[Dict[str, Any]] = []
        configs: Dict[str, Dict[str, Any]] = {}
        for tool_info in tool_data:
            structured, config = await ai_service._build_tool_from_node(tool_info)
            if structured is None:
                continue
            if structured.args_schema is not None:
                schema = structured.args_schema.model_json_schema()
            else:
                schema = {"type": "object", "properties": {}}
            # Function-calling APIs reject schema indirection; the plugin
            # contract already forbids $defs in tool Params schemas.
            schema.pop("$defs", None)
            schema.pop("definitions", None)
            declared.append(
                {
                    "type": "function",
                    "name": structured.name,
                    "description": structured.description or structured.name,
                    "parameters": schema,
                }
            )
            configs[structured.name] = config or {}
        return declared, configs

    @staticmethod
    def _pending_function_results_needed(
        interaction: Any,
        tool_configs: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Function calls we declared that have no matching result yet.

        Cloud-internal calls (sandbox provisioning, run_command, ...)
        also surface as function_call steps but are answered server-side
        — only names we declared are ours to execute.
        """
        steps = list(getattr(interaction, "steps", None) or [])
        answered = {
            getattr(step, "call_id", None)
            for step in steps
            if getattr(step, "type", "") == "function_result"
        }
        pending = []
        for step in steps:
            if getattr(step, "type", "") != "function_call":
                continue
            name = getattr(step, "name", "") or ""
            if name not in tool_configs:
                continue
            call_id = getattr(step, "id", None)
            if call_id in answered:
                continue
            pending.append(
                {
                    "name": name,
                    "call_id": call_id,
                    "arguments": getattr(step, "arguments", None) or {},
                }
            )
        return pending

    @staticmethod
    async def _execute_bridged_call(
        call: Dict[str, Any],
        tool_configs: Dict[str, Dict[str, Any]],
        ctx: NodeContext,
    ) -> Dict[str, Any]:
        """Dispatch one bridged function call through execute_tool."""
        from pydantic_core import to_jsonable_python

        from services.handlers.tools import execute_tool
        from services.plugin.deps import get_ai_service, get_database

        config = dict(tool_configs.get(call["name"], {}))
        config["workflow_id"] = ctx.workflow_id
        config["parent_node_id"] = ctx.node_id
        config["ai_service"] = get_ai_service()
        config["database"] = get_database()
        for key in ("nodes", "edges", "workspace_dir", "execution_id"):
            if ctx.raw.get(key) is not None:
                config[key] = ctx.raw[key]

        from ._ops import record_tool_output

        async def record(payload: Dict[str, Any], is_error: bool) -> None:
            # Same Output-panel visibility as a normally-executed node.
            tool_node_id = config.get("node_id")
            if not tool_node_id:
                return
            await record_tool_output(
                tool_node_id,
                {
                    "tool": call["name"],
                    "arguments": dict(call["arguments"]),
                    "result": payload,
                    "is_error": is_error,
                    "timestamp": datetime.now().isoformat(),
                },
                workflow_id=ctx.workflow_id,
            )

        try:
            result = await execute_tool(call["name"], dict(call["arguments"]), config)
            jsonable = to_jsonable_python(result)
            await record(jsonable, is_error=False)
            return {
                "type": "function_result",
                "name": call["name"],
                "call_id": call["call_id"],
                "result": jsonable,
            }
        except Exception as exc:  # noqa: BLE001 — surfaced to the cloud agent
            logger.warning(
                "[Vertex Agent] bridged tool %s failed: %s", call["name"], exc
            )
            await record({"error": str(exc)}, is_error=True)
            return {
                "type": "function_result",
                "name": call["name"],
                "call_id": call["call_id"],
                "result": {"error": str(exc)},
                "is_error": True,
            }

    @staticmethod
    def _collect_cloud_tool_usage(
        interaction: Any,
        tool_configs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, str]:
        """Map of cloud_tool_key -> display label from the turn's steps."""
        used: Dict[str, str] = {}
        for step in getattr(interaction, "steps", None) or []:
            step_type = getattr(step, "type", "") or ""
            if step_type in _CLOUD_STEP_LABELS:
                used[f"type:{step_type}"] = _CLOUD_STEP_LABELS[step_type]
                continue
            if step_type == "function_call":
                name = getattr(step, "name", "") or ""
                if name and name not in tool_configs and name not in _CLOUD_NOISE_NAMES:
                    used[f"fn:{name}"] = name
        return used

    @staticmethod
    def _jsonable(value: Any) -> Any:
        """Best-effort JSON-safe conversion for step payloads."""
        if value is None:
            return None
        try:
            from pydantic_core import to_jsonable_python

            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json", exclude_none=True)
            return to_jsonable_python(value)
        except Exception:  # noqa: BLE001 — display data only
            return str(value)

    async def _record_swept_outputs(
        self,
        interaction: Any,
        resolved: Dict[str, str],
        workflow_id: Optional[str],
    ) -> None:
        """Record invocation outputs for cloud tools the stream missed.

        Pairs each swept key's call step with its ``call_id``-matched
        result step from the FINAL resource, mirroring what the live
        handler records mid-stream.
        """
        from ._ops import record_tool_output

        steps = list(getattr(interaction, "steps", None) or [])
        calls_by_id: Dict[str, Tuple[str, Any]] = {}
        for step in steps:
            step_type = getattr(step, "type", "") or ""
            key = None
            if step_type in _CLOUD_STEP_LABELS:
                key = f"type:{step_type}"
            elif step_type == "function_call":
                name = getattr(step, "name", "") or ""
                if name and name not in _CLOUD_NOISE_NAMES:
                    key = f"fn:{name}"
            if key and key in resolved:
                step_id = getattr(step, "id", None)
                if step_id:
                    calls_by_id[step_id] = (key, step)

        for step in steps:
            step_type = getattr(step, "type", "") or ""
            if not (step_type == "function_result" or step_type.endswith("_result")):
                continue
            entry = calls_by_id.get(getattr(step, "call_id", None))
            if entry is None:
                continue
            key, call_step = entry
            await record_tool_output(
                resolved[key],
                {
                    "tool": self._label_for_key(key),
                    "arguments": self._jsonable(getattr(call_step, "arguments", None)),
                    "result": self._jsonable(getattr(step, "result", None))
                    or self._jsonable(step),
                    "is_error": bool(getattr(step, "is_error", False)),
                    "timestamp": datetime.now().isoformat(),
                },
                workflow_id=workflow_id,
            )

    @staticmethod
    def _label_for_key(key: str) -> str:
        """Recover the display label from a cloud_tool_key."""
        if key.startswith("type:"):
            return _CLOUD_STEP_LABELS.get(key[5:], key[5:])
        if key.startswith("fn:"):
            return key[3:]
        return key

    def _make_live_handler(
        self,
        *,
        broadcaster: Any,
        workflow_id: Optional[str],
        agent_node_id: str,
        declared_names: frozenset,
        live_nodes: Dict[str, str],
        open_calls: Dict[str, str],
    ):
        """Build the per-event SSE callback for live tool visibility.

        Mints a vertexCloudTool node the FIRST time each cloud-side tool
        appears (memoized in ``live_nodes`` — one DB round-trip per key
        per run), pulses it ``executing`` on the call step and ``success``
        on the matching result step (``call_id`` join via ``open_calls``).
        Declared local tools only get an agent phase broadcast here —
        their real execution still happens at requires_action, where
        ``execute_tool`` owns their canvas animation.

        Result steps also RECORD the invocation output on the display
        node (``record_tool_output``) so clicking it shows what the
        cloud tool did — same visibility as a normally-executed node.

        Exceptions are swallowed by ``stream_interaction`` — visibility
        must never kill the turn.
        """
        from ._ops import ensure_cloud_tool_nodes, pulse_node, record_tool_output

        async def phase_tool(label: str) -> None:
            await broadcaster.update_node_status(
                agent_node_id,
                "executing",
                {"phase": "executing_tool", "agent_type": "vertex_managed", "tool_name": label},
                workflow_id=workflow_id,
            )

        async def on_event(event: Any) -> None:
            event_type = getattr(event, "event_type", "") or ""

            if event_type == "interaction.status_update":
                if getattr(event, "status", None) == "requires_action":
                    await phase_tool("local tools")
                return
            if event_type == "error":
                error = getattr(event, "error", None)
                logger.warning(
                    "[Vertex Agent] stream error event: %s",
                    getattr(error, "message", None) or error,
                )
                return
            if event_type != "step.start":
                return

            step = getattr(event, "step", None)
            if step is None:
                return
            step_type = getattr(step, "type", "") or ""

            # Result steps close the matching call's glow and record the
            # invocation output onto the display node.
            if step_type == "function_result" or step_type.endswith("_result"):
                call_id = getattr(step, "call_id", None)
                call = open_calls.pop(call_id, None)
                if call and workflow_id:
                    await pulse_node(call["node_id"], "success", workflow_id=workflow_id)
                    await record_tool_output(
                        call["node_id"],
                        {
                            "tool": call["label"],
                            "arguments": call["arguments"],
                            "result": self._jsonable(getattr(step, "result", None))
                            or self._jsonable(step),
                            "is_error": bool(getattr(step, "is_error", False)),
                            "timestamp": datetime.now().isoformat(),
                        },
                        workflow_id=workflow_id,
                    )
                return

            name = getattr(step, "name", "") or ""
            if step_type in _CLOUD_STEP_LABELS:
                key, label = f"type:{step_type}", _CLOUD_STEP_LABELS[step_type]
            elif step_type == "function_call":
                if name in declared_names:
                    # Ours — bridged at requires_action; phase only.
                    await phase_tool(name)
                    return
                if not name or name in _CLOUD_NOISE_NAMES:
                    return
                key, label = f"fn:{name}", name
            else:
                return

            await phase_tool(label)
            if not workflow_id:
                return
            node_id = live_nodes.get(key)
            if node_id is None:
                resolved = await ensure_cloud_tool_nodes(
                    workflow_id=workflow_id,
                    agent_node_id=agent_node_id,
                    used={key: label},
                )
                node_id = resolved.get(key)
                if node_id is None:
                    return
                live_nodes[key] = node_id
            await pulse_node(node_id, "executing", workflow_id=workflow_id)
            step_id = getattr(step, "id", None)
            if step_id:
                open_calls[step_id] = {
                    "node_id": node_id,
                    "label": label,
                    "arguments": self._jsonable(getattr(step, "arguments", None)),
                }

        return on_event

    @staticmethod
    def _usage_dict(interaction: Any) -> Optional[Dict[str, Any]]:
        usage = getattr(interaction, "usage", None)
        if usage is None:
            return None
        if hasattr(usage, "model_dump"):
            data = usage.model_dump(mode="json", exclude_none=True)
        elif isinstance(usage, dict):
            data = usage
        else:
            return None
        return data or None

    @staticmethod
    async def _save_chain_ids(
        database: Any,
        memory_node_id: str,
        interaction_id: Optional[str],
        environment_id: Optional[str],
        *,
        human: Optional[str] = None,
        assistant: Optional[str] = None,
        window_size: int = 10,
    ) -> None:
        """Persist chain ids (and optionally the turn) onto simpleMemory."""
        from services.memory.markdown import (
            append_to_memory_markdown,
            trim_markdown_window,
        )

        params = await database.get_node_parameters(memory_node_id) or {}
        if interaction_id:
            params["vertex_interaction_id"] = interaction_id
        else:
            params.pop("vertex_interaction_id", None)
        if environment_id:
            params["vertex_environment_id"] = environment_id
        else:
            params.pop("vertex_environment_id", None)

        if human and assistant:
            content = params.get("memory_content") or ""
            content = append_to_memory_markdown(content, "human", human)
            content = append_to_memory_markdown(content, "assistant", assistant)
            content, _removed = trim_markdown_window(content, window_size)
            params["memory_content"] = content

        await database.save_node_parameters(memory_node_id, params)
