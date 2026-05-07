"""Shared base for code-executor plugins.

Python / JavaScript / TypeScript executors all need ``connected_outputs``
(injected by the executor's _NEEDS_CONNECTED_OUTPUTS path). Each
delegates to its language-specific handler.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class CodeExecutorParams(BaseModel):
    code: str = Field(..., min_length=1, json_schema_extra={"editor": "code"})
    timeout: int = Field(default=30, ge=1, le=600)

    model_config = ConfigDict(extra="allow")


class CodeExecutorOutput(BaseModel):
    output: Optional[Any] = None
    console_output: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CodeExecutorBase(ActionNode, abstract=True):
    """Subclass and set type / display_name / handler import.

    Visual metadata (icon + color) lives in ``server/nodes/visuals.json``
    keyed by individual plugin type. The ``_visuals.py`` resolver picks
    each entry up at NodeSpec emit time; no class-level ClassVars needed.
    """

    group = ("code", "tool")
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    task_queue = TaskQueue.CODE_EXEC
    usable_as_tool = True
    ui_hints = {"hasCodeEditor": True}

    Params = CodeExecutorParams
    Output = CodeExecutorOutput
