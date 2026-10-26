"""Per-batch workflow-tool bridge for the FastMCP server.

When an agent batch wires nodes through ``input-tools``, each connected
node is exposed on the FastMCP server as its own
``mcp__opencompany__<node_type>`` entry. The spawned ``claude -p`` sees
those tools on the very first ``tools/list`` and can invoke them
directly — no two-step generic-wrapper indirection.

Schema generation is delegated to FastMCP: we build a function whose
``inspect.signature`` mirrors the canonical ``AgentToolSpec.args_schema``
field-for-field, so FastMCP advertises the same flat ``ToolInput`` contract
as native agents. Per-batch scoping is enforced inside the handler via
``_require_batch()`` so concurrent batches sharing the same tool name
are isolated; refcounts (``add_tool`` on first wire, ``remove_tool`` on
last unwire) keep the FastMCP registry tidy.

Public API:
  - :func:`expose_workflow_tools(connected_tools)` — call from
    ``mcp_server.register_batch``
  - :func:`unexpose_workflow_tools(connected_tools)` — call from
    ``mcp_server.unregister_batch``
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from typing import Any, Dict, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


# canonical tool name -> count of active batches that wired it. Tools are added
# to FastMCP on first wire, removed on last unwire — per-handler scope
# checks ``_require_batch`` against the calling batch's connected_tools.
_active_tool_refcounts: Dict[str, int] = {}
_active_tool_schema_fingerprints: Dict[str, str] = {}


def _connected_tool_name(entry: Dict[str, Any]) -> str:
    """Return the canonical builder name, or the legacy raw node type.

    Production agent nodes enrich every entry through
    ``AIService._build_tool_from_node`` before registering a batch.  Keeping
    raw entries keyed by ``node_type`` preserves the low-level MCP extension
    contract for callers that deliberately register a ``BatchContext``
    directly; it also avoids running a second, incomplete name resolver here.
    """
    name = str(entry.get("_agent_tool_name") or "")
    if name:
        return name
    return str(entry.get("node_type") or "")


def _entry_input_model(entry: Dict[str, Any]) -> Optional[type]:
    """Resolve the current batch entry's canonical invocation model."""

    input_model = entry.get("_agent_tool_input_model")
    if input_model is not None:
        return input_model
    from services.node_registry import get_node_class

    cls = get_node_class(str(entry.get("node_type") or ""))
    if cls is None:
        return None
    model_factory = getattr(cls, "tool_input_model", None)
    return (
        model_factory()
        if callable(model_factory)
        else getattr(cls, "Params", None)
    )


def _entry_schema_fingerprint(
    entry: Dict[str, Any],
    input_model: type,
) -> str:
    schema = entry.get("_agent_tool_schema")
    if not isinstance(schema, dict):
        schema = input_model.model_json_schema()
    encoded = json.dumps(
        schema,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def expose_workflow_tools(connected_tools: List[Dict[str, Any]]) -> None:
    """Add one MCP tool per connected workflow-tool node."""
    mcp = _get_mcp()
    if mcp is None or not connected_tools:
        return
    from services.node_registry import get_node_class

    # FastMCP owns one global handler per visible name. Preflight the entire
    # batch before mutating that registry so concurrent workflows can share a
    # name only when they expose the exact same schema.
    prepared: List[tuple[Dict[str, Any], str, type, str]] = []
    pending_fingerprints: Dict[str, str] = {}
    for entry in connected_tools:
        node_type = entry.get("node_type")
        if not node_type:
            continue
        tool_name = _connected_tool_name(entry)
        cls = get_node_class(node_type)
        input_model = _entry_input_model(entry)
        if (cls is None and node_type != "_builtin_check_delegated_tasks") or input_model is None:
            logger.warning(
                "[CC-Agent MCP] cannot expose %s: class or ToolInput missing",
                tool_name,
            )
            continue
        fingerprint = _entry_schema_fingerprint(entry, input_model)
        active_fingerprint = _active_tool_schema_fingerprints.get(tool_name)
        batch_fingerprint = pending_fingerprints.get(tool_name)
        if (
            active_fingerprint is not None
            and active_fingerprint != fingerprint
        ) or (
            batch_fingerprint is not None
            and batch_fingerprint != fingerprint
        ):
            raise ValueError(
                "Concurrent MCP tool schema conflict for canonical name "
                f"{tool_name!r}"
            )
        pending_fingerprints[tool_name] = fingerprint
        prepared.append((entry, tool_name, input_model, fingerprint))

    for entry, tool_name, input_model, fingerprint in prepared:
        prev = _active_tool_refcounts.get(tool_name, 0)
        if prev > 0:
            _active_tool_refcounts[tool_name] = prev + 1
            continue  # identical schema already exposed by another batch
        node_type = str(entry["node_type"])
        try:
            handler = _build_handler(tool_name, node_type, input_model)
            mcp.add_tool(
                handler,
                name=tool_name,
                description=(
                    entry.get("_agent_tool_description")
                    or getattr(cls, "description", None)
                    or f"OpenCompany workflow tool: {tool_name}"
                ),
            )
            _active_tool_refcounts[tool_name] = 1
            _active_tool_schema_fingerprints[tool_name] = fingerprint
            logger.info("[CC-Agent MCP] exposed mcp__opencompany__%s", tool_name)
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "[CC-Agent MCP] add_tool(%s) failed: %s",
                tool_name,
                exc,
            )
            raise
    _schedule_list_changed_notify()


def unexpose_workflow_tools(connected_tools: List[Dict[str, Any]]) -> None:
    """Decrement refcount; remove the MCP tool when no batch references it."""
    mcp = _get_mcp()
    if mcp is None:
        return
    for entry in connected_tools:
        tool_name = _connected_tool_name(entry)
        if not tool_name:
            continue
        remaining = _active_tool_refcounts.get(tool_name, 0) - 1
        if remaining > 0:
            _active_tool_refcounts[tool_name] = remaining
            continue
        _active_tool_refcounts.pop(tool_name, None)
        _active_tool_schema_fingerprints.pop(tool_name, None)
        try:
            mcp.remove_tool(tool_name)
            logger.info("[CC-Agent MCP] removed mcp__opencompany__%s", tool_name)
        except Exception as exc:  # pragma: no cover
            logger.debug("[CC-Agent MCP] remove_tool(%s): %s", tool_name, exc)
    _schedule_list_changed_notify()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _get_mcp() -> Optional[Any]:
    """Late import to avoid a circular import with ``mcp_server``."""
    from services.cli_agent import mcp_server

    return mcp_server._mcp_singleton


def _schedule_list_changed_notify() -> None:
    """Fire-and-forget ``notifications/tools/list_changed`` to the
    connected MCP client.

    FastMCP does NOT emit this automatically on ``add_tool`` /
    ``remove_tool`` (verified at
    ``mcp/server/fastmcp/tools/tool_manager.py`` — both methods only
    mutate the ``_tools`` dict, with no notification dispatch). Without
    this manual notify, tools registered after the agent's first
    ``tools/list`` request stay invisible until reconnect.

    Best-effort: requires a running asyncio loop (always true in the
    ``service.run_batch`` call path). Failures log at WARN and don't
    abort the batch.
    """
    mcp = _get_mcp()
    if mcp is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — sync test path

    async def _do_notify() -> None:
        try:
            session = getattr(mcp, "session", None) or getattr(getattr(mcp, "_mcp_server", None), "session", None)
            if session is not None and hasattr(session, "send_tool_list_changed"):
                await session.send_tool_list_changed()
        except Exception as exc:
            logger.warning(
                "[CC-Agent workflow_tools] tools/list_changed notify failed: %s",
                exc,
            )

    loop.create_task(_do_notify())


def _build_handler(
    tool_name: str,
    node_type: str | type,
    input_model: Optional[type] = None,
):
    """Build an async handler whose ``inspect.signature`` mirrors the
    canonical ToolInput field-for-field. FastMCP iterates that signature to
    derive the ``inputSchema``; flat fields → flat MCP arguments.
    """
    from pydantic_core import PydanticUndefined

    # Private two-argument compatibility for existing extension/tests:
    # _build_handler(node_type, Params). Production passes the canonical
    # three-argument form.
    if input_model is None:
        input_model = node_type  # type: ignore[assignment]
        node_type = tool_name

    fields = getattr(input_model, "model_fields", {}) or {}
    parameters: List[inspect.Parameter] = []
    annotations: Dict[str, Any] = {"return": Dict[str, Any]}
    for fname, finfo in fields.items():
        annotations[fname] = finfo.annotation
        if finfo.is_required():
            default: Any = inspect.Parameter.empty
        elif finfo.default_factory is not None:  # type: ignore[truthy-function]
            try:
                default = finfo.default_factory()  # materialize once for the signature
            except Exception:
                default = None
        elif finfo.default is PydanticUndefined:
            default = None
        else:
            default = finfo.default
        parameters.append(
            inspect.Parameter(
                fname,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=finfo.annotation,
                default=default,
            )
        )

    async def _handler(**kwargs: Any) -> Dict[str, Any]:
        from services.cli_agent.mcp_server import _require_batch

        ctx = _require_batch()
        entry = next(
            (
                t
                for t in ctx.connected_tools
                if _connected_tool_name(t) == tool_name
            ),
            None,
        )
        if entry is None:
            return {
                "error": f"tool {tool_name!r} not connected to this batch",
                "status": 403,
            }
        # Resolve from the authenticated current batch, not the first workflow
        # that happened to install this global FastMCP handler.
        current_input_model = _entry_input_model(entry)
        if current_input_model is None:
            return {
                "error": "Canonical ToolInput is unavailable",
                "status": 500,
            }
        try:
            validated = current_input_model.model_validate(kwargs)
            args = validated.model_dump(mode="json", exclude_unset=True)
        except Exception as exc:
            return {
                "error": "Invalid tool arguments",
                "details": str(exc),
            }
        logger.info(
            "[CC-Agent MCP %s] node=%s wf=%s args_keys=%s",
            tool_name,
            ctx.node_id,
            ctx.workflow_id,
            list(args.keys()),
        )
        from services.handlers.tools import execute_tool

        config: Dict[str, Any] = {
            **dict(entry.get("_agent_tool_execution") or {}),
            **dict(entry.get("_pi_inline_scope") or {}),
            "node_type": entry.get("node_type") or node_type,
            "node_id": entry.get("node_id"),
            "workflow_id": ctx.workflow_id,
            "execution_id": ctx.execution_id,
            "user_id": (entry.get("_pi_inline_scope") or {}).get("user_id", ctx.user_id),
            "workspace_dir": str(ctx.workspace_dir),
            "parent_node_id": ctx.node_id,
            "label": entry.get("label") or tool_name,
            "parameters": dict(entry.get("parameters") or {}),
            **({"pi_harness_version": 1} if entry.get("pi_harness_version") == 1 else {}),
        }
        # Every call gets the request's identity: a stateful tool's idempotency
        # key, and a held call's draft key (services/approvals/tool_calls.py).
        # Request IDs are local to one client. The stateful HTTP transport
        # validates its server-issued session ID before calling this tool, so
        # parallel CLI sessions need that namespace as well.
        mcp = _get_mcp()
        if mcp is not None:
            try:
                request_context = mcp.get_context().request_context
                request = request_context.request
                session_id = request.headers.get("mcp-session-id") if request is not None else None
                if session_id:
                    from services.temporal.pi_protocol import canonical_digest
                    config["tool_call_id"] = "mcp:" + canonical_digest({
                        "session": session_id, "request": request_context.request_id,
                    })
            except ValueError:
                pass  # The admission guard, or the draft, reports the missing identity.
        if config.get("node_type") in {"coding_agent", "taskManager", "_builtin_check_delegated_tasks"} and config.get("pi_harness_version") == 1:
            from services.plugin.deps import get_ai_service, get_database
            config["ai_service"] = get_ai_service()
            config["database"] = get_database()
        broadcaster = ctx.broadcaster
        if broadcaster is None:
            from services.status_broadcaster import get_status_broadcaster

            broadcaster = get_status_broadcaster()
        await broadcaster.update_node_status(
            ctx.node_id,
            "executing",
            {
                "phase": "executing_tool",
                "agent_type": "native_cli",
                "tool_name": tool_name,
            },
            workflow_id=ctx.workflow_id,
        )
        await broadcaster.broadcast_agent_capability(
            ctx.node_id,
            capability_kind="tool",
            capability_name=tool_name,
            state="started",
            workflow_id=ctx.workflow_id,
            execution_id=ctx.execution_id,
            target_node_id=str(entry.get("node_id") or "") or None,
            invocation_source="native_mcp",
        )
        try:
            result = await execute_tool(tool_name, args, config)
        except Exception as exc:
            await broadcaster.update_node_status(
                ctx.node_id,
                "executing",
                {
                    "phase": "tool_completed",
                    "agent_type": "native_cli",
                    "tool_name": tool_name,
                    "tool_failed": True,
                },
                workflow_id=ctx.workflow_id,
            )
            await broadcaster.broadcast_agent_capability(
                ctx.node_id,
                capability_kind="tool",
                capability_name=tool_name,
                state="failed",
                workflow_id=ctx.workflow_id,
                execution_id=ctx.execution_id,
                target_node_id=str(entry.get("node_id") or "") or None,
                invocation_source="native_mcp",
                error_code=type(exc).__name__,
            )
            raise
        if not isinstance(result, dict):
            result = {"result": result}
        failed = "error" in result
        await broadcaster.update_node_status(
            ctx.node_id,
            "executing",
            {
                "phase": "tool_completed",
                "agent_type": "native_cli",
                "tool_name": tool_name,
                "tool_failed": failed,
            },
            workflow_id=ctx.workflow_id,
        )
        await broadcaster.broadcast_agent_capability(
            ctx.node_id,
            capability_kind="tool",
            capability_name=tool_name,
            state="failed" if failed else "completed",
            workflow_id=ctx.workflow_id,
            execution_id=ctx.execution_id,
            target_node_id=str(entry.get("node_id") or "") or None,
            invocation_source="native_mcp",
            error_code="TOOL_RETURNED_ERROR" if failed else None,
        )
        if failed:
            logger.warning(
                "[CC-Agent MCP %s] node=%s ERROR: %s",
                tool_name,
                entry.get("node_id"),
                result.get("error"),
            )
        else:
            logger.info(
                "[CC-Agent MCP %s] node=%s OK (result_keys=%s)",
                tool_name,
                entry.get("node_id"),
                list(result.keys())[:8],
            )
        return result

    _handler.__name__ = tool_name
    _handler.__annotations__ = annotations
    _handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters,
        return_annotation=Dict[str, Any],
    )
    return _handler


def _reset_for_tests() -> None:  # pragma: no cover
    """Wipe the refcount registry. ONLY use in tests."""
    _active_tool_refcounts.clear()
    _active_tool_schema_fingerprints.clear()
