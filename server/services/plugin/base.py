"""BaseNode — foundation for Wave 11 plugin-first nodes.

Every ActionNode / TriggerNode / ToolNode inherits from here. Invariants:

- ``__init_subclass__`` collects ``@Operation`` methods into ``_operations``
  and registers the class into the four legacy registries via
  ``services.node_registry.register_node``.
- :meth:`execute` enforces the universal handler signature
  ``(node_id, parameters, context) -> Dict[str, Any]`` and orchestrates:
  parameter validation → credential resolve → operation dispatch
  (with optional declarative routing) → result wrap → usage track.
- :meth:`as_activity` produces a Temporal-compatible callable for 11.F.

Subclasses set class attributes rather than pass constructor args —
the class itself *is* the declaration. This keeps node modules flat
and lets the class object function as the plugin manifest.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, ClassVar, Dict, List, Optional, Sequence, Type

from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from core.logging import get_logger, log_context
from services.plugin.connection import Connection
from services.plugin.context import NodeContext
from services.plugin.credential import Credential
from services.plugin.operation import OperationSpec, collect_operations
from services.plugin.routing import execute_routing
from services.plugin.scaling import (
    ACTION_START_TO_CLOSE,
    DEFAULT_HEARTBEAT,
    DEFAULT_RETRY,
    RetryPolicy,
    TaskQueue,
)

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)


class NodeUserError(Exception):
    """Raised by an operation when the failure is *expected and
    user-correctable* (bad ``old_string`` for an edit, missing required
    field, unknown enum value, ...). The framework converts it to a
    structured ``{success: False, error: ...}`` response and logs at
    WARN level without a stack trace — these are not server bugs, they
    are signals the LLM (or user) should retry with different input.

    Use plain ``RuntimeError`` / ``Exception`` only for genuinely
    unexpected failures that warrant a stacktrace in the operator log.
    """


# Sentinel used by Params-less nodes so .model_validate({}) works.
class _EmptyParams(BaseModel):
    pass


class _EmptyOutput(BaseModel):
    pass


# Group memberships that mark a node as auxiliary configuration —
# its panel inherits the parent's main inputs instead of showing
# direct inputs. Centralized here so the frontend doesn't need to
# know any group strings.
_CONFIG_NODE_GROUPS = frozenset({"memory", "tool"})


def _derive_auto_ui_hints(group: Sequence[str]) -> Dict[str, Any]:
    """Auto-derived uiHints based on group membership. Plugin-declared
    ``ui_hints`` override these — explicit always wins."""
    hints: Dict[str, Any] = {}
    if any(g in _CONFIG_NODE_GROUPS for g in group):
        hints["isConfigNode"] = True
    return hints


class BaseNode:
    """Abstract plugin node. Do not instantiate directly — subclass
    :class:`ActionNode`, :class:`TriggerNode`, or :class:`ToolNode`.

    ===== Declaration (class attributes) =====

    ``type``              node type string, matches workflow JSON / registry key
    ``version``           integer, bumped for breaking changes
    ``display_name``      shown in palette + parameter panel header
    ``subtitle``          shown under display_name in the node header
    ``icon``              Wave 10.B wire format: "asset:k" / "lobehub:b" / emoji
    ``color``             hex or dracula token, e.g. "#bd93f9"
    ``group``             palette groupings, e.g. ["search", "tool"]
    ``description``       one-line help
    ``handles``           NodeHandle[] — React Flow topology
    ``visibility``        "all" / "normal" / "dev"
    ``hide_output_handle`` bool — replaces NO_OUTPUT_NODE_TYPES
    ``ui_hints``          dict of flags consumed by parameter panel
    ``annotations``       Pipedream-style: destructive / readonly / open_world

    ``Params``            Pydantic model — user-facing parameters
    ``Output``            Pydantic model — runtime output schema
    ``credentials``       tuple of :class:`Credential` subclasses this node uses

    ``task_queue``        Temporal worker pool (see :class:`TaskQueue`)
    ``retry_policy``      per-node retry knobs
    ``start_to_close_timeout`` / ``heartbeat_timeout``

    ``component_kind``    frontend dispatch key — set by subclass
    ``usable_as_tool``    ActionNode-only — mints a ToolNode adapter

    Operations: methods decorated with ``@Operation("name")``. The
    multi-op dispatcher reads ``parameters.operation``. Single-op nodes
    call the sole operation regardless of the ``operation`` field.
    """

    # ---- declaration (override in subclass) -------------------------------
    type: ClassVar[str] = ""
    version: ClassVar[int] = 1
    display_name: ClassVar[str] = ""
    subtitle: ClassVar[str] = ""
    group: ClassVar[Sequence[str]] = ()
    description: ClassVar[str] = ""
    handles: ClassVar[Sequence[Dict[str, Any]]] = ()
    visibility: ClassVar[str] = "all"
    hide_output_handle: ClassVar[bool] = False
    hide_input_handle: ClassVar[bool] = False
    ui_hints: ClassVar[Dict[str, Any]] = {}
    annotations: ClassVar[Dict[str, Any]] = {}

    Params: ClassVar[Type[BaseModel]] = _EmptyParams
    Output: ClassVar[Type[BaseModel]] = _EmptyOutput
    credentials: ClassVar[Sequence[Type[Credential]]] = ()

    task_queue: ClassVar[str] = TaskQueue.DEFAULT
    retry_policy: ClassVar[RetryPolicy] = DEFAULT_RETRY
    start_to_close_timeout = ACTION_START_TO_CLOSE
    heartbeat_timeout = DEFAULT_HEARTBEAT
    max_concurrent: ClassVar[Optional[int]] = None

    component_kind: ClassVar[str] = "generic"
    usable_as_tool: ClassVar[bool] = False

    # Wave 12 D5: LLM-visible name + description for plugins surfaced as
    # AI tools (ToolNode subclasses, ActionNodes marked usable_as_tool=True,
    # SpecializedAgentBase subclasses).
    #
    # ``tool_name`` is genuinely distinct from ``type`` (camelCase → snake;
    # e.g. ``calculatorTool`` → ``calculator``, ``pythonExecutor`` →
    # ``python_code``); plugins declare it when they want an LLM-facing
    # name that differs from the registry key.
    #
    # ``tool_description`` defaults to falling back to ``cls.description``
    # at resolve time — plugins ONLY override when the LLM-facing variant
    # needs to differ materially from the human-facing description
    # (writeTodos' instruction-heavy prompt, pythonExecutor's available-
    # libraries hint, specialized agents' ONE-SHOT pattern, etc.).
    tool_name: ClassVar[str] = ""
    tool_description: ClassVar[str] = ""

    # Set by __init_subclass__: {op_name: OperationSpec}
    _operations: ClassVar[Dict[str, OperationSpec]] = {}
    # Flag so concrete subclasses auto-register; abstract kinds don't.
    _abstract: ClassVar[bool] = True

    # ---- subclass hook ----------------------------------------------------

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._abstract = abstract
        cls._operations = collect_operations(cls)
        if abstract or not cls.type:
            return
        # Auto-hide default canvas input/output handles for nodes whose
        # primary surface area is the LLM-tool call path. Two cases:
        #   * Pure ToolNodes (component_kind="tool" -- calculator,
        #     duckduckgoSearch, writeTodos, agentBuilder, ...). They
        #     wire through their own output-tool handle; the hardcoded
        #     SquareNode input-main + output-main are visual clutter.
        #   * Dual-purpose ActionNodes with usable_as_tool=True (gmail,
        #     twitter*, brave_search, all 16 android nodes via cascade,
        #     code executors via cascade, ...). Same reasoning: the
        #     LLM-tool path is the dominant use; default handles confuse
        #     the canvas.
        # Subclasses opt out by explicitly setting either flag to False
        # on the class.
        is_tool_oriented = (
            cls.usable_as_tool or cls.component_kind == "tool"
        )
        if is_tool_oriented:
            if "hide_input_handle" not in cls.__dict__:
                cls.hide_input_handle = True
            if "hide_output_handle" not in cls.__dict__:
                cls.hide_output_handle = True
        # Wave 12 D5: auto-derive ``tool_name`` / ``tool_description`` for
        # agents (component_kind=="agent") that don't declare their own.
        # Pattern is parametric — every agent surfaces as
        # ``delegate_to_<type>`` to the parent LLM. Subclasses with a
        # distinct delegation contract (autonomous_agent's Code Mode hint,
        # orchestrator_agent / ai_employee's "Coordinates multiple agents",
        # rlm_agent's REPL note, claude_code_agent's coding note) override
        # ``tool_description`` directly on the class.
        if cls.component_kind == "agent":
            if "tool_name" not in cls.__dict__:
                cls.tool_name = f"delegate_to_{cls.type}"
            if "tool_description" not in cls.__dict__:
                agent_label = cls.display_name or cls.type
                cls.tool_description = (
                    f"ONE-SHOT delegation to {agent_label}. Call ONCE per "
                    f"task, returns task_id immediately. Agent works in "
                    f"background - do NOT re-call."
                )
        # Eager registry write — same four registries as @register_node.
        # ORDER MATTERS: register_node_class MUST precede register_node so
        # that cls._metadata_dict() (evaluated as the metadata argument)
        # can resolve the plugin folder via get_node_class(cls.type) inside
        # get_plugin_icon_path. Without this order the icon falls through
        # to visuals.json — defeating the per-plugin icon.svg endpoint
        # (RFC §6.5 / Phase 6).
        from services.node_registry import register_node, register_node_class
        register_node_class(cls)
        register_node(
            type=cls.type,
            metadata=cls._metadata_dict(),
            input_model=cls.Params if cls.Params is not _EmptyParams else None,
            output_model=cls.Output if cls.Output is not _EmptyOutput else None,
            handler=cls._make_legacy_handler(),
        )

    # ---- metadata projection ---------------------------------------------

    @classmethod
    def _metadata_dict(cls) -> Dict[str, Any]:
        """Project class attributes onto the :data:`NodeMetadata` TypedDict
        expected by the existing node_spec emitter.

        Icon resolution (per RFC §6.5):
        1. Per-plugin ``icon.svg`` co-located with the plugin folder —
           emitted as a URL routed through ``GET /api/schemas/nodes/<type>/icon``.
        2. Fallback to ``visuals.json`` (emoji / ``lobehub:<brand>``).

        Color resolution (per RFC §6.6 / F2):
        1. Per-plugin ``meta.json`` ``color`` field, co-located with the
           plugin folder. Mirrors icon co-location.
        2. Fallback to ``visuals.json`` for legacy entries that have
           not been migrated yet.
        """
        from nodes._visuals import (
            get_icon,
            get_color,
            get_plugin_icon_path,
            get_plugin_meta,
        )

        if get_plugin_icon_path(cls.type) is not None:
            icon = f"/api/schemas/nodes/{cls.type}/icon"
        else:
            icon = get_icon(cls.type)
        color = get_plugin_meta(cls.type, "color") or get_color(cls.type)
        meta: Dict[str, Any] = {
            "displayName": cls.display_name or cls.type,
            "icon": icon,
            "group": list(cls.group),
            "description": cls.description,
            "version": cls.version,
            "componentKind": cls.component_kind,
        }
        if cls.subtitle:
            meta["subtitle"] = cls.subtitle
        if color:
            meta["color"] = color
        if cls.handles:
            meta["handles"] = list(cls.handles)
        if cls.credentials:
            meta["credentials"] = [c.id for c in cls.credentials]
        if cls.hide_output_handle:
            meta["hideOutputHandle"] = True
        if cls.hide_input_handle:
            meta["hideInputHandle"] = True
        if cls.visibility != "all":
            meta["visibility"] = cls.visibility
        ui_hints = _derive_auto_ui_hints(cls.group)
        ui_hints.update(cls.ui_hints)
        if ui_hints:
            meta["uiHints"] = ui_hints
        return meta

    # ---- legacy handler adapter ------------------------------------------

    @classmethod
    def _make_legacy_handler(cls) -> Callable[..., Awaitable[Dict[str, Any]]]:
        """Produce a ``(node_id, node_type, parameters, context) -> dict``
        callable for the existing executor registry. Discard node_type
        (redundant — class is already the dispatch target) and route
        through :meth:`execute`.
        """
        async def _legacy(
            node_id: str,
            node_type: str,
            parameters: Dict[str, Any],
            context: Dict[str, Any],
        ) -> Dict[str, Any]:
            instance = cls()
            ctx = NodeContext.from_legacy(
                node_id=node_id,
                node_type=node_type,
                context=context,
                connection_factory=_make_connection_factory(cls, context),
            )
            return await instance.execute(node_id, parameters, ctx)
        _legacy.__node_class__ = cls       # type: ignore[attr-defined]
        _legacy.__qualname__ = f"{cls.__qualname__}._legacy_handler"
        return _legacy

    # ---- lifecycle --------------------------------------------------------

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        """Universal entry point. Validate params → dispatch op →
        wrap result. Subclasses (TriggerNode, ToolNode) override to
        change the return shape or lifetime.

        The body runs under two ambient contexts:

        - :func:`core.logging.log_context` binds ``node_id`` /
          ``node_type`` / ``workflow_id`` to every log record emitted
          while the operation runs, via ``structlog.contextvars``.
          Survives ``asyncio.gather`` child tasks (stdlib
          ``contextvars`` is task-local).
        - An OpenTelemetry span named ``node.{type}.execute`` so
          per-plugin latency / failures show up in any tracer backend
          without per-plugin instrumentation. Span attributes carry
          the same identifiers as the log context.
        """
        start_time = time.time()
        workflow_id_attr: Optional[str] = None
        if isinstance(context.raw, dict):
            workflow_id_attr = context.raw.get("workflow_id")

        log_fields: Dict[str, Any] = {
            "node_id": node_id,
            "node_type": self.type,
        }
        if workflow_id_attr is not None:
            log_fields["workflow_id"] = workflow_id_attr

        async with log_context(**log_fields):
            with tracer.start_as_current_span(
                f"node.{self.type}.execute",
                attributes={
                    "node.id": node_id,
                    "node.type": self.type,
                    **({"workflow.id": workflow_id_attr} if workflow_id_attr else {}),
                },
            ):
                return await self._execute_body(
                    node_id=node_id,
                    parameters=parameters,
                    context=context,
                    start_time=start_time,
                )

    async def _execute_body(
        self,
        *,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
        start_time: float,
    ) -> Dict[str, Any]:
        """The actual execute pipeline — extracted so :meth:`execute`
        stays a thin shell around the ambient log-context + span. Kept
        method-private; callers should always go through :meth:`execute`."""
        # Stash the raw (pre-validation) parameters dict in context.raw
        # so plugins can recover values the Pydantic extra="ignore" policy
        # would drop — e.g. ``api_key`` injected by node_executor's
        # _inject_api_keys that isn't a declared Params field for AI agent
        # nodes. Plugins that need it: ``ctx.raw["_raw_parameters"]``.
        if isinstance(context.raw, dict):
            context.raw["_raw_parameters"] = parameters

        try:
            params_obj = self._validate_params(parameters)
        except ValidationError as e:
            return self._wrap_error(
                start_time=start_time,
                error=f"Invalid parameters: {e.errors()[0].get('msg', str(e))}",
                error_type="ValidationError",
            )

        op_name = self._pick_operation(parameters)
        op_spec = self._operations.get(op_name)
        if op_spec is None:
            return self._wrap_error(
                start_time=start_time,
                error=f"Unknown operation '{op_name}' for node {self.type}",
                error_type="InvalidParametersError",
            )

        try:
            result = await self._run_operation(op_spec, params_obj, context)
        except PermissionError as e:
            # Credential.resolve() raises PermissionError annotated with
            # .provider / .reason / .auth attributes (see
            # services/plugin/credential.py). When .provider is present,
            # emit a CloudEvents-typed broadcast via
            # ``broadcast_credential_event`` — the existing wire used by
            # every credential mutation. The envelope rides as a
            # WorkflowEvent with type ``credential.{auth}.runtime_failed``
            # so frontend consumers can glob-match ``credential.*.*``
            # without inventing a new wire-frame key. Surface a
            # ``credential`` block in the operation response so the user
            # gets a structured error envelope rather than a raw string.
            provider = getattr(e, "provider", None)
            reason = getattr(e, "reason", "denied")
            auth = getattr(e, "auth", "api_key")
            # Normalize "oauth2" -> "oauth" so the event type aligns with
            # the existing CloudEvents naming (``credential.oauth.connected``,
            # ``credential.oauth.disconnected``, ``credential.oauth.validated``).
            auth_kind = "oauth" if auth == "oauth2" else auth
            workflow_id: Optional[str] = None
            if isinstance(context.raw, dict):
                workflow_id = context.raw.get("workflow_id")
            if provider:
                try:
                    from services.status_broadcaster import get_status_broadcaster
                    broadcaster = get_status_broadcaster()
                    await broadcaster.broadcast_credential_event(
                        event_type=f"credential.{auth_kind}.runtime_failed",
                        provider=provider,
                        workflow_id=workflow_id,
                        reason=reason,
                        node_id=node_id,
                        error=str(e),
                    )
                except Exception:
                    # Broadcast failure must never mask the original error.
                    logger.debug(
                        "[%s] failed to broadcast credential runtime failure for %s",
                        self.type, provider, exc_info=True,
                    )
            extra: Optional[Dict[str, Any]] = None
            if provider:
                extra = {
                    "credential": {
                        "provider": provider,
                        "reason": reason,
                        "remediation": "add_key" if reason == "missing" else "reconnect",
                    }
                }
            return self._wrap_error(
                start_time=start_time,
                error=str(e),
                error_type="PermissionDeniedError",
                extra=extra,
            )
        except NodeUserError as e:
            # Expected, user-correctable: log a single WARN line so it
            # shows up in operator logs, but skip the traceback — the
            # LLM gets the message in the structured response and can
            # retry with corrected input.
            logger.warning("[%s] %s op %s: %s", self.type, op_name, type(e).__name__, e)
            return self._wrap_error(
                start_time=start_time, error=str(e), error_type="NodeUserError"
            )
        except Exception as e:
            logger.exception("[%s] operation %s failed", self.type, op_name)
            return self._wrap_error(start_time=start_time, error=str(e), error_type=type(e).__name__)

        return self._wrap_success(start_time=start_time, result=result)

    # ---- AI-tool invocation path ------------------------------------------

    async def execute_as_tool(
        self,
        tool_args: Dict[str, Any],
        node_params: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        """LLM-invoked tool call. The AI model supplies ``tool_args`` for
        fields it decides to fill; ``node_params`` carries static config
        the user set on the node (e.g. an API endpoint base URL). Merge
        wins for the LLM so it can override.

        Unwraps the :meth:`_wrap_success` envelope to a flat dict —
        tool-call responses fed back into an LLM shouldn't include
        execution_time / timestamp chrome. Errors surface as
        ``{"error": "..."}`` the LLM can reason about.

        ToolNode overrides :meth:`_wrap_success` to return flat, so this
        method is idempotent there. ActionNode+``usable_as_tool`` classes
        get their ``{success, result}`` envelope unwrapped.
        """
        merged = {**node_params, **tool_args}
        envelope = await self.execute(context.node_id, merged, context)
        # ToolNode skips the envelope wrap entirely — its _wrap_success
        # returns the flat Output dict directly. Detect by the absence
        # of the {success, ...} envelope keys.
        if "success" not in envelope:
            return envelope
        if envelope.get("success") is False:
            return {"error": envelope.get("error", "tool execution failed")}
        result = envelope.get("result")
        if isinstance(result, dict):
            return result
        return {"result": result}

    # ---- internals --------------------------------------------------------

    def _validate_params(self, parameters: Dict[str, Any]) -> BaseModel:
        return self.Params.model_validate(parameters)

    def _pick_operation(self, parameters: Dict[str, Any]) -> str:
        """Multi-op nodes read ``parameters['operation']``. Single-op
        nodes return the one registered name regardless."""
        if not self._operations:
            return ""
        if len(self._operations) == 1:
            return next(iter(self._operations))
        return str(parameters.get("operation", ""))

    async def _run_operation(
        self,
        spec: OperationSpec,
        params_obj: BaseModel,
        ctx: NodeContext,
    ) -> Any:
        """Execute either declarative routing or the method body."""
        if spec.routing is not None:
            # Pure-declarative: routing handles everything, method body
            # is expected to be empty.
            if not self.credentials:
                raise RuntimeError(
                    f"Node {self.type} op {spec.name} has routing but no "
                    "credentials declared — routing needs a Connection."
                )
            cred = self.credentials[0]
            conn = ctx.connection(cred.id)
            try:
                return await execute_routing(
                    spec.routing, params=params_obj.model_dump(), connection=conn,
                )
            finally:
                await conn.aclose()

        # Imperative: invoke the method body (bound via descriptor).
        method = spec.method.__get__(self, type(self))
        return await method(ctx, params_obj)

    def _wrap_success(self, *, start_time: float, result: Any) -> Dict[str, Any]:
        """95%-universal return shape. Subclasses (ToolNode) override."""
        if isinstance(result, BaseModel):
            result_data: Any = result.model_dump()
        else:
            result_data = result
        return {
            "success": True,
            "result": result_data,
            "execution_time": round(time.time() - start_time, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _wrap_error(
        self,
        *,
        start_time: float,
        error: str,
        error_type: str = "Error",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        envelope: Dict[str, Any] = {
            "success": False,
            "error": error,
            "error_type": error_type,
            "execution_time": round(time.time() - start_time, 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            envelope.update(extra)
        return envelope

    # ---- Temporal ---------------------------------------------------------

    @classmethod
    def as_activity(cls):
        """Wrap this node as a ``@activity.defn`` callable for Temporal
        worker registration (F4.A). Stable activity name:
        ``node.{type}.v{version}``.

        Accepts the same ``context`` dict shape as the legacy
        ``execute_node_activity`` so the orchestrator can swap by name
        without reshaping the payload. Delegates to
        ``workflow_service.execute_node(...)`` — same execution pipeline
        the WebSocket path uses — so status broadcasts, parameter
        fetching, NodeContext build, and error handling all match. The
        Temporal worker shares the FastAPI process, so direct DI works.

        Returns the decorated async function; the worker collects these
        into ``activities=[...]``.
        """
        from temporalio import activity

        activity_name = f"node.{cls.type}.v{cls.version}"

        @activity.defn(name=activity_name)
        async def _node_activity(context: Dict[str, Any]) -> Dict[str, Any]:
            from datetime import datetime
            from core.container import container
            from services.status_broadcaster import get_status_broadcaster

            node_id = context["node_id"]
            workflow_id = context.get("workflow_id")
            broadcaster = get_status_broadcaster()

            # Pre-executed trigger nodes — return cached output without dispatching.
            if context.get("pre_executed"):
                activity.logger.debug(f"Node {node_id} pre-executed; passthrough")
                result = {
                    "success": True,
                    "node_id": node_id,
                    "node_type": cls.type,
                    "result": context.get("trigger_output", {}),
                    "pre_executed": True,
                    "timestamp": datetime.now().isoformat(),
                }
                await broadcaster.update_node_status(
                    node_id, "success", result, workflow_id=workflow_id,
                )
                return result

            # Disabled nodes — skip.
            node_data = context.get("node_data", {})
            if node_data.get("disabled"):
                activity.logger.debug(f"Node {node_id} disabled; skipping")
                result = {
                    "success": True,
                    "node_id": node_id,
                    "node_type": cls.type,
                    "skipped": True,
                    "reason": "disabled",
                    "timestamp": datetime.now().isoformat(),
                }
                await broadcaster.update_node_status(
                    node_id, "skipped", {"disabled": True}, workflow_id=workflow_id,
                )
                return result

            # Broadcast executing — UI cyan-glow.
            await broadcaster.update_node_status(
                node_id, "executing", {"node_type": cls.type}, workflow_id=workflow_id,
            )

            try:
                # Heartbeat the long-running side of the pipeline.
                activity.heartbeat(f"Executing {cls.type}: {node_id}")

                # Delegate to the same pipeline the WS handler uses. Parameters
                # are read from DB inside execute_node (handler-specific). The
                # legacy execute_node_activity does this via a WS roundtrip;
                # per-type activities skip the loopback because the worker
                # shares the FastAPI process.
                workflow_service = container.workflow_service()
                result = await workflow_service.execute_node(
                    node_id=node_id,
                    node_type=cls.type,
                    parameters=node_data,
                    nodes=context.get("nodes", []),
                    edges=context.get("edges", []),
                    session_id=context.get("session_id", "default"),
                    workflow_id=workflow_id,
                    outputs=context.get("inputs", {}),
                )

                result["node_id"] = node_id
                result["node_type"] = cls.type
                result["timestamp"] = datetime.now().isoformat()

                if result.get("success"):
                    activity.logger.info(f"Node {node_id} succeeded")
                    await broadcaster.update_node_status(
                        node_id, "success", result.get("result", {}),
                        workflow_id=workflow_id,
                    )
                    await broadcaster.update_node_output(
                        node_id, result.get("result", {}), workflow_id=workflow_id,
                    )
                else:
                    activity.logger.warning(
                        f"Node {node_id} failed: {result.get('error')}"
                    )
                    await broadcaster.update_node_status(
                        node_id, "error",
                        {"error": result.get("error")},
                        workflow_id=workflow_id,
                    )

                activity.heartbeat(f"Node {node_id} completed")
                return result

            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                activity.logger.error(f"Node {node_id} crashed: {error_msg}")
                await broadcaster.update_node_status(
                    node_id, "error", {"error": error_msg},
                    workflow_id=workflow_id,
                )
                raise

        return _node_activity


# ---------------------------------------------------------------------------
# Connection factory — avoids circular import with NodeContext.

def _make_connection_factory(
    node_cls: Type[BaseNode],
    context: Dict[str, Any],
) -> Callable[[str], Connection]:
    user_id = context.get("user_id", "owner")
    session_id = context.get("session_id", "default")
    node_id = context.get("node_id")
    # Precompute credential lookup once.
    creds_by_id: Dict[str, Type[Credential]] = {c.id: c for c in node_cls.credentials}

    def factory(credential_id: str) -> Connection:
        cred_cls = creds_by_id.get(credential_id)
        if cred_cls is None:
            raise RuntimeError(
                f"Node {node_cls.type} did not declare credential '{credential_id}' "
                f"but tried to use it. Add it to the `credentials` class attribute."
            )
        return Connection(
            cred_cls, user_id=user_id, session_id=session_id, node_id=node_id,
        )

    return factory
