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

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, ClassVar, Dict, Optional, Sequence, Type

from opentelemetry import trace
from pydantic import BaseModel, ValidationError

from core.logging import get_logger, log_context
from services.media.limits import (
    TEMPORAL_PAYLOAD_ERROR_BYTES,
    TEMPORAL_PAYLOAD_WARN_BYTES,
)
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


def _icon_fingerprint(path: Any) -> str:
    """Short content hash used to version a plugin icon URL.

    Content-based rather than mtime-based so a fresh checkout of the same
    bytes keeps the same URL (and the same browser cache entry). Truncated
    to 12 hex chars — cache-busting, not integrity. Unreadable files fall
    back to a constant so registration never breaks over an icon.
    """
    import hashlib

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "0"


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

    # Mutable state owned by a node belongs to an execution scope, never to
    # deployment orchestration. Plugins that keep state outside normal node
    # outputs override this hook; stateless nodes inherit the no-op contract.
    @classmethod
    async def reset_execution_state(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        execution_id: str,
        generation: int,
        graph: Dict[str, Any],
        database: Any,
    ) -> Dict[str, Any]:
        return {"reset": False}

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
    # Explicit agent capability used by graph validation/migration. Rendering
    # kind and palette group are intentionally not proxies: several
    # non-agents render with componentKind="agent", while Codex-style agents
    # have historically been missing from central type lists.
    requires_context: ClassVar[bool] = False

    # Whether this plugin requires the parent workflow's full canvas
    # (``nodes`` + ``edges`` arrays) in its NodeContext. Default ``False``
    # — most tools execute against their own params alone. Set ``True``
    # on plugins that walk or mutate the canvas (the F4.B AgentWorkflow
    # tool-dispatch path reads this attribute to decide whether to
    # forward the parent workflow's canvas into the per-tool activity
    # context). Currently only :class:`AgentBuilderNode` opts in.
    needs_canvas: ClassVar[bool] = False

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
        is_tool_oriented = cls.usable_as_tool or cls.component_kind == "tool"
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
            get_plugin_icon_ref,
            get_plugin_meta,
        )

        # Co-located SVG, then the plugin's own meta.json (library
        # reference), then the central visuals.json. Same shape as the
        # color lookup below: plugin folder first, central registry as the
        # legacy fallback.
        icon_path = get_plugin_icon_path(cls.type)
        if icon_path is not None:
            # Fingerprinted URL — standard asset-cache-busting. The icon
            # route serves `Cache-Control: max-age=86400` on a URL that
            # otherwise never changes, so replacing an SVG left every
            # browser showing the old artwork for up to a day. A content
            # hash in the URL makes the long cache correct instead of
            # harmful: changed bytes mint a new URL, identical bytes keep
            # the cached one. Computed once at registration, like every
            # other field in this dict.
            icon = f"/api/schemas/nodes/{cls.type}/icon?v={_icon_fingerprint(icon_path)}"
        else:
            icon = get_plugin_icon_ref(cls.type) or get_icon(cls.type)
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
        handles = [dict(handle) for handle in cls.handles]
        is_invokable_tool = cls.usable_as_tool or (
            cls.component_kind == "tool"
            and cls.ui_hints.get("isMasterSkillEditor") is not True
        )
        if is_invokable_tool and not any(
            handle.get("name") == "output-tool"
            and handle.get("kind") == "output"
            for handle in handles
        ):
            # The backend NodeSpec is the topology source of truth. Before
            # generic handle rendering, SquareNode synthesized this endpoint
            # from frontend group membership, which left dual-purpose plugin
            # specs incomplete and made their tool connection disappear.
            handles.append(
                {
                    "name": "output-tool",
                    "kind": "output",
                    "position": "top",
                    "label": "Tool",
                    "role": "tools",
                }
            )
        if handles:
            meta["handles"] = handles
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
        if cls.requires_context:
            ui_hints["requiresContext"] = True
        # How long this node may legitimately run, so the client can size its
        # request budget instead of keeping its own list of "slow" node types.
        #
        # This is the ONLY honest signal for it. ``componentKind`` is a
        # rendering key -- socialSend/socialReceive declare "agent" purely to
        # get the multi-handle layout while being plain ActionNodes -- and
        # ``group`` disagrees the other way (vertex_agent_admin is group
        # "agent" but a REST-API square). The declared timeout is exactly the
        # 19 genuinely long-running nodes, with no heuristic.
        #
        # Rides uiHints deliberately: ``node_spec.get_node_spec`` copies a
        # fixed tuple of top-level keys, so a new top-level field would need a
        # second edit there to reach the wire.
        ui_hints.setdefault(
            "executionTimeoutMs",
            int(cls.start_to_close_timeout.total_seconds() * 1000),
        )
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
            # An LLM-invoked tool call carries its unmerged model arguments
            # in ``context["tool_args"]`` (Temporal per-type activity path).
            # ToolNodes must take execute_as_tool so a split ToolInput schema
            # validates the model's arguments — plain execute validates
            # against Params, whose extra="ignore" silently drops them (a
            # Simple Memory remember degraded to a no-op list this way).
            # Dual-purpose ActionNodes keep their documented merged-params
            # contract unchanged.
            from services.plugin.tool import ToolNode

            tool_args = context.get("tool_args")
            if isinstance(tool_args, dict) and isinstance(instance, ToolNode):
                return await instance.execute_as_tool(
                    tool_args,
                    parameters,
                    ctx,
                )
            return await instance.execute(node_id, parameters, ctx)

        _legacy.__node_class__ = cls  # type: ignore[attr-defined]
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

        # Split-schema ToolNodes arrive here only through execute_as_tool,
        # which has already validated model arguments against ToolInput and
        # persisted configuration against Params. Reuse that exact object so
        # the operation cannot accidentally observe a merged payload.
        validated_tool_input = (
            context.raw.get("_validated_tool_input")
            if context.raw.get("_split_tool_schema") is True
            else None
        )
        if isinstance(validated_tool_input, BaseModel):
            params_obj = validated_tool_input
        else:
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
                        self.type,
                        provider,
                        exc_info=True,
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
            return self._wrap_error(start_time=start_time, error=str(e), error_type="NodeUserError")
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
        """LLM-invoked tool call with separate input/config validation.

        The AI model supplies ``tool_args`` while ``node_params`` carries
        persisted configuration. ToolNode subclasses may declare a distinct
        ``ToolInput`` model. The two payloads are validated independently and
        made available to the operation as follows:

        * Default/legacy tools (``ToolInput is Params``) receive the combined
          Params object. Model arguments retain their historical precedence,
          except fields explicitly listed in ``server_controlled_fields``.
        * Split-schema tools receive the validated ToolInput object; validated
          configuration is available only through
          ``ctx.raw["_tool_config"]``. It is never copied into model input.

        Trusted workflow/node scope always remains on NodeContext and is never
        sourced from model arguments.

        Unwraps the :meth:`_wrap_success` envelope to a flat dict —
        tool-call responses fed back into an LLM shouldn't include
        execution_time / timestamp chrome. Errors surface as
        ``{"error": "..."}`` the LLM can reason about.

        ToolNode overrides :meth:`_wrap_success` to return flat, so this
        method is idempotent there. ActionNode+``usable_as_tool`` classes
        get their ``{success, result}`` envelope unwrapped.
        """
        from services.plugin.tool import ToolNode

        is_tool_node = isinstance(self, ToolNode)
        if not is_tool_node:
            # Dual-purpose ActionNodes retain the established tool contract.
            # ToolInput is a ToolNode extension and must not change them.
            envelope = await self.execute(
                context.node_id,
                {**node_params, **tool_args},
                context,
            )
            if "success" not in envelope:
                return envelope
            if envelope.get("success") is False:
                return {
                    "error": envelope.get("error", "tool execution failed")
                }
            result = envelope.get("result")
            return result if isinstance(result, dict) else {"result": result}

        input_model = type(self).tool_input_model()

        try:
            validated_input = input_model.model_validate(tool_args)
            # Only arguments the model actually supplied participate in the
            # compatibility merge. Otherwise ToolInput defaults would
            # overwrite an operator's persisted value even though the model
            # never attempted to set that field.
            input_payload = validated_input.model_dump(exclude_unset=True)

            if input_model is self.Params:
                # Validate the persisted subset on its own before combining
                # it with invocation arguments. Required Params fields are
                # often model-supplied on legacy tools, hence the derived
                # partial model rather than treating an omitted argument as a
                # malformed node configuration.
                type(self).partial_config_model().model_validate(node_params)
                # Preserve the legacy contract for existing plugins, while
                # allowing a plugin to lock specific persisted settings.
                effective = {**node_params, **input_payload}
                for field_name in getattr(type(self), "server_controlled_fields", ()):
                    if field_name in node_params:
                        effective[field_name] = node_params[field_name]
                validated_config = self.Params.model_validate(effective)
                invocation_payload = validated_config.model_dump()
                split_schema = False
            else:
                validated_config = self.Params.model_validate(node_params)
                invocation_payload = input_payload
                split_schema = True
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            return {
                "error": f"Invalid tool input/configuration: {first.get('msg', str(e))}",
                "error_type": "ValidationError",
            }

        # A NodeContext belongs to one invocation, but preserve pre-existing
        # escape-hatch values for tests/adapters that deliberately reuse one.
        marker = object()
        previous_config = context.raw.get("_tool_config", marker)
        previous_input = context.raw.get("_validated_tool_input", marker)
        previous_split = context.raw.get("_split_tool_schema", marker)
        context.raw["_tool_config"] = validated_config
        context.raw["_validated_tool_input"] = validated_input
        context.raw["_split_tool_schema"] = split_schema
        try:
            envelope = await self.execute(context.node_id, invocation_payload, context)
        finally:
            for key, previous in (
                ("_tool_config", previous_config),
                ("_validated_tool_input", previous_input),
                ("_split_tool_schema", previous_split),
            ):
                if previous is marker:
                    context.raw.pop(key, None)
                else:
                    context.raw[key] = previous
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
                    f"Node {self.type} op {spec.name} has routing but no " "credentials declared — routing needs a Connection."
                )
            cred = self.credentials[0]
            conn = ctx.connection(cred.id)
            try:
                return await execute_routing(
                    spec.routing,
                    params=params_obj.model_dump(),
                    connection=conn,
                )
            finally:
                await conn.aclose()

        # Imperative: invoke the method body (bound via descriptor).
        method = spec.method.__get__(self, type(self))
        return await method(ctx, params_obj)

    @classmethod
    def interpret_result(cls, result: Dict[str, Any]) -> tuple[bool, Any, Optional[str]]:
        """Unify success/error semantics across node base classes.

        Returns ``(success, payload, error_message)``. The F4.A activity
        wrapper calls this so broadcast plumbing stays plugin-agnostic.

        Default contract: the standard ``{success, result, error?, …}``
        envelope produced by :meth:`_wrap_success` / :meth:`_wrap_error`.
        :class:`ToolNode` overrides — its :meth:`_wrap_success` returns
        a flat dict (the LLM-feedable shape).
        """
        if result.get("success"):
            return True, result.get("result", {}), None
        return False, None, result.get("error")

    def _serialize_result(self, result: Any) -> Any:
        """Enforce the declared ``Output`` contract at the serialization
        boundary — the same semantics FastAPI applies to ``response_model``
        (validate, coerce, serialize). ``model_dump(mode="json")`` guarantees
        the payload is JSON-compatible (datetimes → ISO strings, enums →
        values) before it reaches node_outputs persistence and the WS
        broadcast. Dict results from plugins without a declared ``Output``
        pass through untouched.

        Raises ``pydantic.ValidationError`` (or
        ``pydantic_core.PydanticSerializationError``) when the operation
        returned data violating its own declared contract — a plugin bug
        that must fail loudly instead of silently corrupting downstream
        stores (callers convert it to the standard error envelope).
        """
        if isinstance(result, BaseModel):
            payload = result.model_dump(mode="json")
        elif isinstance(result, dict) and self.Output is not _EmptyOutput:
            # ``exclude_unset`` preserves the producer's exact key set —
            # declared-but-absent Optional fields must not materialise as
            # ``None`` keys in the payload (validate + coerce + serialize,
            # without reshaping what the operation chose to return).
            payload = self.Output.model_validate(result).model_dump(
                mode="json", exclude_unset=True
            )
        else:
            payload = result

        self._check_result_size(payload)
        return payload

    def _check_result_size(self, payload: Any) -> None:
        """Warn on a large node result; refuse one Temporal cannot carry.

        A node result is not stored once. It is written to ``node_outputs``
        three times, broadcast twice, retained in the status cache, aggregated
        into the workflow result, copied into **every downstream activity's
        input**, and — when the node is ``usable_as_tool`` — serialized into an
        LLM message.

        Two thresholds, and the difference between them matters:

        * At the **warning** threshold this only logs. Some existing nodes
          legitimately return hundreds of KB (a parsed document, a long
          transcript) and breaking them would be a regression.
        * At the **error** threshold it raises. That is not a new failure: a
          payload over Temporal's limit is rejected by the converter anyway.
          What changes is *how* it fails — ``NodeUserError`` is already in
          ``NON_RETRYABLE_ERROR_TYPES``, so instead of three attempts that
          re-run the work (and re-bill whatever produced it) before reporting
          a generic converter error, the run stops immediately with a message
          naming the node and the size.

        This is also why no Temporal-internal error type had to be named: the
        payload never reaches the converter. The installed SDK enforces the
        limit in its Rust core and exposes no Python class to add to a
        non-retryable list, so catching it there was not possible anyway.
        """
        if payload is None:
            return
        try:
            import orjson

            size = len(orjson.dumps(payload, default=str))
        except Exception:
            # Sizing is diagnostics; never fail a result over it.
            return

        if size >= TEMPORAL_PAYLOAD_ERROR_BYTES:
            raise NodeUserError(
                f"{self.type} returned {size // 1024} KB, over the "
                f"{TEMPORAL_PAYLOAD_ERROR_BYTES // (1024 * 1024)} MB the "
                "workflow engine can carry. Large data belongs in the "
                "workspace: write it to a file and return a reference "
                "(see services/media for the audio case)."
            )
        if size >= TEMPORAL_PAYLOAD_WARN_BYTES:
            logger.warning(
                "large node result",
                node_type=self.type,
                size_bytes=size,
                limit_bytes=TEMPORAL_PAYLOAD_ERROR_BYTES,
            )

    def _wrap_success(self, *, start_time: float, result: Any) -> Dict[str, Any]:
        """95%-universal return shape. Subclasses (ToolNode) override."""
        from pydantic_core import PydanticSerializationError

        try:
            result_data = self._serialize_result(result)
        except (ValidationError, PydanticSerializationError) as e:
            logger.exception(
                "[%s] operation result violates declared Output contract",
                self.type,
            )
            return self._wrap_error(
                start_time=start_time,
                error=f"Output contract violation: {e}",
                error_type="OutputValidationError",
            )
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
            from temporalio.exceptions import ApplicationError
            from core.container import container
            from services.status_broadcaster import get_status_broadcaster

            node_id = context["node_id"]
            workflow_id = context.get("workflow_id")
            execution_id = context.get("execution_id")
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
                    "execution_id": execution_id,
                    "timestamp": datetime.now().isoformat(),
                }
                await broadcaster.update_node_status(
                    node_id,
                    "success",
                    result,
                    workflow_id=workflow_id,
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
                    "execution_id": execution_id,
                    "timestamp": datetime.now().isoformat(),
                }
                await broadcaster.update_node_status(
                    node_id,
                    "skipped",
                    {"disabled": True, "execution_id": execution_id},
                    workflow_id=workflow_id,
                )
                return result

            # Broadcast executing — UI cyan-glow.
            await broadcaster.update_node_status(
                node_id,
                "executing",
                {"node_type": cls.type, "execution_id": execution_id},
                workflow_id=workflow_id,
            )

            # Wave 17.6: periodic heartbeat DURING the body. The bracketing
            # beats below only fire at start + completion — a browser /
            # CLI-agent body running 5+ minutes between them exceeds the
            # heartbeat_timeout (2 min default) with no beat, so a laptop
            # sleep mid-body wasn't detected until start_to_close (10 min).
            # A 30s background beat keeps detection within one
            # heartbeat_timeout window. Skipped when the plugin's
            # start_to_close fits inside heartbeat_timeout (nothing to gain).
            beat_task: Optional[asyncio.Task] = None
            if cls.start_to_close_timeout > cls.heartbeat_timeout:

                async def _beat_loop() -> None:
                    while True:
                        await asyncio.sleep(30)
                        activity.heartbeat(f"Still executing {cls.type}: {node_id}")

                beat_task = asyncio.create_task(_beat_loop())

            structured_failure_broadcasted = False
            try:
                # Heartbeat the long-running side of the pipeline.
                activity.heartbeat(f"Executing {cls.type}: {node_id}")

                # Delegate to the same pipeline the WS handler uses. Parameters
                # are read from DB inside execute_node (handler-specific). The
                # legacy execute_node_activity does this via a WS roundtrip;
                # per-type activities skip the loopback because the worker
                # shares the FastAPI process.
                workflow_service = container.workflow_service()
                # Forward any non-standard context keys (e.g.
                # ``auto_rebind_tools`` for agentBuilder) through the
                # ``extras`` channel so they land in NodeContext.raw.
                extras: Dict[str, Any] = {}
                for key in (
                    "auto_rebind_tools",
                    "invoking_agent_node_id",
                    "agent_iteration",
                    "tool_call_index",
                    "tool_call_id",
                    "parent_node_id",
                    "team_lead_node_id",
                    "root_execution_id",
                    # The model's unmerged arguments for an LLM-invoked tool
                    # call; routes ToolNodes through execute_as_tool in the
                    # legacy handler so split-schema ToolInput validation
                    # applies on the Temporal path too.
                    "tool_args",
                ):
                    if key in context:
                        extras[key] = context[key]
                result = await workflow_service.execute_node(
                    node_id=node_id,
                    node_type=cls.type,
                    parameters=node_data,
                    nodes=context.get("nodes", []),
                    edges=context.get("edges", []),
                    session_id=context.get("session_id", "default"),
                    execution_id=context.get("execution_id"),
                    workflow_id=workflow_id,
                    outputs=context.get("inputs", {}),
                    extras=extras or None,
                    user_id=str(context.get("user_id") or "owner"),
                )

                result["node_id"] = node_id
                result["node_type"] = cls.type
                result.setdefault("execution_id", execution_id)
                result["timestamp"] = datetime.now().isoformat()

                # Polymorphic dispatch — each node base class owns its
                # result contract. ToolNode returns flat dicts; ActionNode
                # / TriggerNode return the {success, result} envelope.
                # cls.interpret_result() normalizes both into (success,
                # payload, error_message).
                success, payload, error = cls.interpret_result(result)
                if success:
                    correlated_payload = (
                        {**payload, "execution_id": execution_id}
                        if isinstance(payload, dict)
                        else {"result": payload, "execution_id": execution_id}
                    )
                    activity.logger.info(f"Node {node_id} succeeded")
                    await broadcaster.update_node_status(
                        node_id,
                        "success",
                        correlated_payload,
                        workflow_id=workflow_id,
                    )
                    await broadcaster.update_node_output(
                        node_id,
                        correlated_payload,
                        workflow_id=workflow_id,
                    )
                    activity.heartbeat(f"Node {node_id} completed")
                    return result
                else:
                    activity.logger.warning(f"Node {node_id} failed: {error}")
                    await broadcaster.update_node_status(
                        node_id,
                        "error",
                        {"error": error, "execution_id": execution_id},
                        workflow_id=workflow_id,
                    )
                    structured_failure_broadcasted = True
                    # Translate the structured failure envelope into a
                    # Temporal-visible typed failure so the configured
                    # RetryPolicy can classify it (e.g. NodeUserError is
                    # non-retryable, RuntimeError is retryable).
                    error_type = result.get("error_type", type(error).__name__ if error else "Error")
                    raise ApplicationError(
                        error or "Activity failed",
                        type=error_type,
                    )

            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                activity.logger.error(f"Node {node_id} crashed: {error_msg}")
                if not structured_failure_broadcasted:
                    await broadcaster.update_node_status(
                        node_id,
                        "error",
                        {"error": error_msg, "execution_id": execution_id},
                        workflow_id=workflow_id,
                    )
                raise
            finally:
                if beat_task is not None:
                    beat_task.cancel()

        return _node_activity


# ---------------------------------------------------------------------------
# Connection factory — avoids circular import with NodeContext.


def _make_connection_factory(
    node_cls: Type[BaseNode],
    context: Dict[str, Any],
) -> Callable[[str], Connection]:
    from constants import DEFAULT_CREDENTIAL_CUSTOMER_ID

    # Credentials are scoped by ``credential_customer_id``, NOT by the
    # tenancy principal. Reading ``user_id`` here is what made a real
    # authenticated subject break every OAuth-backed node: tokens are
    # stored under the installation's customer id, not the logged-in user's.
    user_id = context.get(
        "credential_customer_id", DEFAULT_CREDENTIAL_CUSTOMER_ID
    )
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
            cred_cls,
            user_id=user_id,
            session_id=session_id,
            node_id=node_id,
        )

    return factory
