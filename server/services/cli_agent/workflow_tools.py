"""Per-batch workflow-tool bridge for the FastMCP server.

When an agent batch wires nodes through ``input-tools``, each connected
node is exposed on the FastMCP server as its own
``mcp__opencompany__<node_type>`` entry. The spawned ``claude -p`` sees
those tools on the very first ``tools/list`` and can invoke them
directly — no two-step generic-wrapper indirection.

Schema generation is delegated to FastMCP: we build a function whose
``inspect.signature`` mirrors the plugin's Pydantic ``Params`` field-
for-field, so FastMCP advertises a flat ``inputSchema`` matching the
plugin Params. Per-batch scoping is enforced inside the handler via
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
import inspect
from typing import Any, Dict, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


# node_type -> count of active batches that wired it. Tools are added
# to FastMCP on first wire, removed on last unwire — per-handler scope
# checks ``_require_batch`` against the calling batch's connected_tools.
_active_tool_refcounts: Dict[str, int] = {}


def expose_workflow_tools(connected_tools: List[Dict[str, Any]]) -> None:
    """Add one MCP tool per connected workflow-tool node."""
    mcp = _get_mcp()
    if mcp is None or not connected_tools:
        return
    from services.node_registry import get_node_class

    for entry in connected_tools:
        node_type = entry.get("node_type")
        if not node_type:
            continue
        prev = _active_tool_refcounts.get(node_type, 0)
        _active_tool_refcounts[node_type] = prev + 1
        if prev > 0:
            continue  # already exposed by another concurrent batch
        cls = get_node_class(node_type)
        if cls is None or getattr(cls, "Params", None) is None:
            logger.warning(
                "[CC-Agent MCP] cannot expose %s: class or Params missing",
                node_type,
            )
            continue
        try:
            handler = _build_handler(node_type, cls.Params)
            mcp.add_tool(
                handler,
                name=node_type,
                description=(getattr(cls, "description", None) or f"OpenCompany workflow tool: {node_type}"),
            )
            logger.info("[CC-Agent MCP] exposed mcp__opencompany__%s", node_type)
        except Exception as exc:  # pragma: no cover
            logger.warning("[CC-Agent MCP] add_tool(%s) failed: %s", node_type, exc)
    _schedule_list_changed_notify()


def unexpose_workflow_tools(connected_tools: List[Dict[str, Any]]) -> None:
    """Decrement refcount; remove the MCP tool when no batch references it."""
    mcp = _get_mcp()
    if mcp is None:
        return
    for entry in connected_tools:
        node_type = entry.get("node_type")
        if not node_type:
            continue
        remaining = _active_tool_refcounts.get(node_type, 0) - 1
        if remaining > 0:
            _active_tool_refcounts[node_type] = remaining
            continue
        _active_tool_refcounts.pop(node_type, None)
        try:
            mcp.remove_tool(node_type)
            logger.info("[CC-Agent MCP] removed mcp__opencompany__%s", node_type)
        except Exception as exc:  # pragma: no cover
            logger.debug("[CC-Agent MCP] remove_tool(%s): %s", node_type, exc)
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


def _build_handler(node_type: str, params_cls: type):
    """Build an async handler whose ``inspect.signature`` mirrors the
    plugin Params field-for-field. FastMCP iterates that signature to
    derive the ``inputSchema``; flat fields → flat MCP arguments.
    """
    from pydantic_core import PydanticUndefined

    fields = getattr(params_cls, "model_fields", {}) or {}
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
            (t for t in ctx.connected_tools if t.get("node_type") == node_type),
            None,
        )
        if entry is None:
            return {
                "error": f"tool {node_type!r} not connected to this batch",
                "status": 403,
            }
        # Validate via the plugin's own Params model, then dump back to
        # a plain dict for execute_tool. `exclude_unset=True` drops
        # defaults the agent didn't supply.
        try:
            validated = params_cls(**kwargs)
            args = validated.model_dump(exclude_unset=True)
        except Exception:
            args = dict(kwargs)
        logger.info(
            "[CC-Agent MCP %s] node=%s wf=%s args_keys=%s",
            node_type,
            ctx.node_id,
            ctx.workflow_id,
            list(args.keys()),
        )
        from services.handlers.tools import execute_tool

        config: Dict[str, Any] = {
            "node_type": node_type,
            "node_id": entry.get("node_id"),
            "workflow_id": ctx.workflow_id,
            "execution_id": ctx.execution_id,
            "workspace_dir": str(ctx.workspace_dir),
            "parent_node_id": ctx.node_id,
            "label": entry.get("label") or node_type,
            "parameters": dict(entry.get("parameters") or {}),
        }
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
                "tool_name": node_type,
            },
            workflow_id=ctx.workflow_id,
        )
        await broadcaster.broadcast_agent_capability(
            ctx.node_id,
            capability_kind="tool",
            capability_name=node_type,
            state="started",
            workflow_id=ctx.workflow_id,
            execution_id=ctx.execution_id,
            target_node_id=str(entry.get("node_id") or "") or None,
            invocation_source="native_mcp",
        )
        try:
            result = await execute_tool(node_type, args, config)
        except Exception as exc:
            await broadcaster.update_node_status(
                ctx.node_id,
                "executing",
                {
                    "phase": "tool_completed",
                    "agent_type": "native_cli",
                    "tool_name": node_type,
                    "tool_failed": True,
                },
                workflow_id=ctx.workflow_id,
            )
            await broadcaster.broadcast_agent_capability(
                ctx.node_id,
                capability_kind="tool",
                capability_name=node_type,
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
                "tool_name": node_type,
                "tool_failed": failed,
            },
            workflow_id=ctx.workflow_id,
        )
        await broadcaster.broadcast_agent_capability(
            ctx.node_id,
            capability_kind="tool",
            capability_name=node_type,
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
                node_type,
                entry.get("node_id"),
                result.get("error"),
            )
        else:
            logger.info(
                "[CC-Agent MCP %s] node=%s OK (result_keys=%s)",
                node_type,
                entry.get("node_id"),
                list(result.keys())[:8],
            )
        return result

    _handler.__name__ = node_type
    _handler.__annotations__ = annotations
    _handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters,
        return_annotation=Dict[str, Any],
    )
    return _handler


def _reset_for_tests() -> None:  # pragma: no cover
    """Wipe the refcount registry. ONLY use in tests."""
    _active_tool_refcounts.clear()
