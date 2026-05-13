"""Calculator tool — Wave 11.B reference migration.

Passive tool node connected to an AI Agent's ``input-tools`` handle.
Single file; Params drive the LLM-visible schema via
``ToolNode.as_tool_schema()``. No credentials, no HTTP, no routing —
the simplest possible plugin shape.

Replaces:
- ``server/nodes/tools.py:calculatorTool`` metadata-only registration
- ``server/services/handlers/tools.py:_execute_calculator`` (kept live
  for LLM tool-call path until 11.D unifies dispatch)
"""

from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, Field

from services.plugin import NodeContext, Operation, ToolNode


class CalculatorParams(BaseModel):
    """LLM-visible schema. Keep field names + descriptions explicit —
    the function-calling model reads these."""

    operation: Literal[
        "add", "subtract", "multiply", "divide", "power", "sqrt", "mod", "abs",
    ] = Field(..., description="Math operation to perform.")
    a: float = Field(..., description="First operand (or the sole input for sqrt/abs).")
    b: Optional[float] = Field(
        default=None,
        description="Second operand. Required for add/subtract/multiply/divide/power/mod.",
    )


class CalculatorOutput(BaseModel):
    operation: str
    a: float
    b: Optional[float] = None
    result: float


_OPERATIONS = {
    "add": lambda a, b: a + b,
    "subtract": lambda a, b: a - b,
    "multiply": lambda a, b: a * b,
    "divide": lambda a, b: a / b if b != 0 else float("inf"),
    "power": lambda a, b: math.pow(a, b),
    "mod": lambda a, b: a % b if b != 0 else 0,
    "sqrt": lambda a, _b: math.sqrt(abs(a)),
    "abs": lambda a, _b: abs(a),
}


class CalculatorToolNode(ToolNode):
    type = "calculatorTool"
    display_name = "Calculator"
    subtitle = "Math Operations"
    group = ("tool", "ai")
    description = "Add, subtract, multiply, divide, power, sqrt, mod, abs"
    component_kind = "tool"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top",
         "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": True, "open_world": False}

    Params = CalculatorParams
    Output = CalculatorOutput

    @Operation("calculate")
    async def calculate(
        self, ctx: NodeContext, params: CalculatorParams,
    ) -> CalculatorOutput:
        fn = _OPERATIONS[params.operation]
        b_value = params.b if params.b is not None else 0.0
        result = fn(params.a, b_value)
        return CalculatorOutput(
            operation=params.operation, a=params.a, b=params.b, result=result,
        )
