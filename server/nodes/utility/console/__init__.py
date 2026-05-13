"""Console — Wave 11.D.10 inlined.

Sink node that logs upstream outputs to the Console panel. Reads
``connected_outputs`` + ``source_nodes`` injected by
``NodeExecutor._dispatch`` (any plugin needing upstream data declares
its type in ``_NEEDS_CONNECTED_OUTPUTS``).

Supports three log modes — ``all`` (dump the merged input),
``field`` (dot-path navigation with ``items[0]`` array indexing),
``expression`` (use the already-resolved parameter value). Output is
formatted as JSON (pretty / compact), plain text, or an ASCII table
for lists of dicts.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


def _navigate_field_path(data: Any, path: str) -> Any:
    if not path:
        return data
    current = data
    for part in path.split("."):
        if current is None:
            return None
        m = re.match(r"^(\w+)\[(\d+)\]$", part)
        if m:
            field, idx = m.group(1), int(m.group(2))
            if isinstance(current, dict) and field in current:
                current = current[field]
            else:
                return None
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    return current


def _format_console_output(data: Any, format_type: str) -> str:
    match format_type:
        case "json":
            try:
                return json.dumps(data, indent=2, default=str, ensure_ascii=False)
            except Exception:
                return str(data)
        case "json_compact":
            try:
                return json.dumps(data, default=str, ensure_ascii=False)
            except Exception:
                return str(data)
        case "text":
            return str(data)
        case "table":
            if isinstance(data, list) and data and isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = [[str(r.get(h, "")) for h in headers] for r in data]
                widths = [max(len(h), max((len(r[i]) for r in rows), default=0))
                          for i, h in enumerate(headers)]
                header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
                sep = "-+-".join("-" * w for w in widths)
                body = ["\n" .join(
                    " | ".join(r[i].ljust(widths[i]) for i in range(len(headers)))
                    for r in rows
                )]
                return "\n".join([header_line, sep, *body])
            return json.dumps(data, indent=2, default=str, ensure_ascii=False)
        case _:
            return str(data)


class ConsoleParams(BaseModel):
    label: str = ""
    log_mode: Literal["all", "field", "expression"] = Field(default="all")
    field_path: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"log_mode": ["field"]}}},
    )
    expression: str = Field(
        default="",
        json_schema_extra={"displayOptions": {"show": {"log_mode": ["expression"]}}},
    )
    format: Literal["json", "json_compact", "text", "table"] = "json"

    model_config = ConfigDict(extra="ignore")


class ConsoleOutput(BaseModel):
    label: Optional[str] = None
    logged_at: Optional[str] = None
    format: Optional[str] = None
    data: Optional[Any] = None
    formatted: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ConsoleNode(ActionNode):
    type = "console"
    display_name = "Console"
    subtitle = "Debug Logger"
    group = ("utility",)
    description = "Log data to console panel for debugging during execution"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
    )
    hide_output_handle = True
    ui_hints = {"isConsoleSink": True}
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = ConsoleParams
    Output = ConsoleOutput

    @Operation("log")
    async def log(self, ctx: NodeContext, params: ConsoleParams) -> Dict[str, Any]:
        from services.status_broadcaster import get_status_broadcaster

        label = params.label
        log_mode = params.log_mode
        format_type = params.format
        field_path = params.field_path
        expression = params.expression

        connected_outputs = ctx.raw.get("connected_outputs") or {}
        source_nodes = ctx.raw.get("source_nodes") or []

        input_data: Dict[str, Any] = {}
        for _node_type, output in connected_outputs.items():
            if isinstance(output, dict):
                input_data.update(output)
            else:
                input_data["value"] = output

        source_info = source_nodes[0] if source_nodes else None

        log_value: Any = None
        match log_mode:
            case "all":
                log_value = input_data
            case "field":
                if field_path:
                    # Decide between navigation and resolved-scalar by
                    # checking whether the first segment of field_path
                    # matches a top-level key in input_data. If yes,
                    # treat as a path; if no, the resolver already
                    # replaced {{...}} with a scalar value and we log
                    # it as-is. Content-based check is more robust than
                    # structural heuristics — handles arbitrary text
                    # content including strings with dots / slashes /
                    # brackets that the previous structural check
                    # misclassified as path expressions.
                    first_segment = field_path.split(".")[0].split("[")[0]
                    if first_segment and first_segment in input_data:
                        log_value = _navigate_field_path(input_data, field_path)
                    else:
                        log_value = field_path
                else:
                    log_value = input_data
            case "expression":
                log_value = expression if expression else input_data
            case _:
                log_value = input_data

        formatted_output = _format_console_output(log_value, format_type)

        await get_status_broadcaster().broadcast_console_log({
            "node_id": ctx.node_id,
            "label": label or f"Console ({ctx.node_id[:8]})",
            "timestamp": datetime.now().isoformat(),
            "data": log_value,
            "formatted": formatted_output,
            "format": format_type,
            "workflow_id": ctx.workflow_id,
            "source_node_id": source_info.get("id") if source_info else None,
            "source_node_type": source_info.get("type") if source_info else None,
            "source_node_label": source_info.get("label") if source_info else None,
        })

        # Pass through original input for downstream nodes; explicit keys
        # take precedence over upstream keys of the same name.
        return {
            **input_data,
            "label": label or f"Console ({ctx.node_id[:8]})",
            "logged_at": datetime.now().isoformat(),
            "format": format_type,
            "data": log_value,
            "formatted": formatted_output,
        }
