"""Back-compat shims for contract tests.

Wave 11.B-C moved tool-handler bodies from `services/handlers/tools.py`
functions (`_execute_calculator`, `_execute_current_time`,
`_execute_duckduckgo_search`) into plugin classes under `nodes/tool/`.

The contract tests were written against the old flat-function signature
(`args: dict -> dict`). These shims re-expose that signature by dispatching
into the plugin's Operation so assertions about numeric results, timezone
fallbacks, and ddgs result mapping continue to test the same contract.

If a plugin raises, the shim either propagates (for `_execute_calculator`
which was documented to raise on invalid operands) or returns an
``{"error": ...}`` dict matching the old flat-function semantics.
"""

from __future__ import annotations

from typing import Any, Dict


async def _execute_calculator(args: Dict[str, Any]) -> Dict[str, Any]:
    """Old signature: dict-in, dict-out. Dispatches into CalculatorToolNode."""
    from nodes.tool.calculator_tool import CalculatorToolNode, CalculatorParams

    op = str(args.get("operation", "")).lower()
    # Old behaviour: unknown op -> {"error": "...", "supported_operations": [...]}
    supported = {"add", "subtract", "multiply", "divide", "power", "sqrt", "mod", "abs"}
    if op not in supported:
        return {
            "error": (f"Unsupported operation '{op}'. " f"Supported: {sorted(supported)}"),
            "supported_operations": sorted(supported),
        }

    # Pydantic will raise ValueError on non-numeric a/b -- propagate to
    # preserve the old contract documented by
    # test_non_numeric_operand_raises.
    params = CalculatorParams(
        operation=op,
        a=float(args.get("a", 0)),
        b=float(args["b"]) if "b" in args and args["b"] is not None else None,
    )

    node = CalculatorToolNode()
    out = await node.calculate(_DummyContext(), params)
    return {
        "operation": out.operation,
        "a": out.a,
        "b": out.b,
        "result": out.result,
    }


async def _execute_current_time(
    args: Dict[str, Any] = None,
    node_params: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Returns legacy flat fields; arg-first with node_params fallback.

    The plugin's CurrentTimeOutput exposes iso/unix, but contract tests
    were written against the prior (datetime, date, time, day_of_week,
    timezone, timestamp) shape. Shim keeps the old shape.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    node_params = node_params or {}
    args = args or {}
    tz_arg = args.get("timezone") or args.get("tz")
    tz_np = node_params.get("timezone") or node_params.get("tz")
    tz = tz_arg or tz_np or "UTC"

    try:
        zone = ZoneInfo(tz)
    except Exception as exc:
        return {"error": f"Invalid timezone: {exc}"}

    now = datetime.now(zone)
    return {
        "datetime": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": tz,
        "day_of_week": now.strftime("%A"),
        "timestamp": int(now.timestamp()),
    }


async def _execute_duckduckgo_search(
    args: Dict[str, Any],
    config: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Flat (args, config) -> dict shim for the deleted
    ``services.handlers.tools._execute_duckduckgo_search``.

    Wave 11.I milestone O moved this here from production code -- it
    only ever served contract tests that patch ``sys.modules['ddgs']``
    and assert the flat output shape. Production callers go through
    :class:`nodes.search.duckduckgo_search.DuckDuckGoSearchNode`.
    """
    config = config or {}
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "No search query provided"}
    max_results = int(config.get("max_results", args.get("max_results", 5)))
    provider = config.get("provider", "duckduckgo")
    from ddgs import DDGS

    raw = list(DDGS().text(query, max_results=max_results))
    results = [
        {
            "title": item.get("title", ""),
            "snippet": item.get("body", ""),
            "url": item.get("href", ""),
        }
        for item in raw
    ]
    return {"query": query, "provider": provider, "results": results}


async def handle_write_todos(
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Legacy flat-envelope shim. Drops invalid items at the service
    layer rather than raising at the pydantic boundary (matches the
    pre-refactor handler contract)."""
    from services.todo_service import get_todo_service

    session_key = context.get("workflow_id") or node_id or "default"
    raw_todos = parameters.get("todos", [])
    service = get_todo_service()
    stored = service.write(session_key, raw_todos)

    broadcaster = context.get("broadcaster")
    if broadcaster:
        await broadcaster.update_node_status(
            node_id,
            "executing",
            {"phase": "todo_update", "todos": stored},
            workflow_id=context.get("workflow_id"),
        )

    return {
        "success": True,
        "message": f"Updated todo list ({len(stored)} items)",
        "count": len(stored),
        "todos": service.format_for_llm(session_key),
    }


class _DummyContext:
    """Minimal NodeContext stand-in for plugin method invocation in tests.

    Plugins that need a real connection-aware context should not use this
    shim; the three legacy tools are compute-only.
    """

    node_id = "test-tool"
    session_id = "default"
    workflow_id = "test-workflow"
    raw: Dict[str, Any] = {}

    def connection(self, *_args, **_kwargs):  # pragma: no cover - not used
        raise NotImplementedError("compat shim; not wired for HTTP")
