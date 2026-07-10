"""Browser Harness — browser-use/browser-harness integration (Wave 19).

Drives the user's REAL Chrome over raw CDP via the ``browser-harness``
CLI (PyPI, alpha). The upstream philosophy is "the LLM writes Python
against ~25 pre-imported helpers" — so the primary operation here is
``run_python`` (code piped verbatim to the CLI stdin, the documented
driving model), with a few convenience operations for structured
workflow use. The companion skill
(``server/skills/web_agent/browser-harness-skill/``) teaches agents the
helper API and the screenshot -> click_at_xy -> wait_for_load loop.

Sibling to the ``browser`` node (agent-browser), NOT a replacement:
that node's accessibility-tree ``@eN`` model stays the stable default;
this one trades structure for raw-CDP freedom on the user's own Chrome
profile/session state.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue
from services.plugin.base import NodeUserError
from services.plugin.shutdown_hooks import register_shutdown_hook

from ._service import shutdown_browser_harness_service

logger = get_logger(__name__)

register_shutdown_hook("browser_harness", shutdown_browser_harness_service)


def _code_for(params: "BrowserHarnessParams") -> str:
    """Translate convenience operations into harness helper one-liners.

    ``repr()`` embeds user strings safely (quotes/backslashes) since the
    generated text is executed as Python by the harness.
    """
    op = params.operation
    if op == "run_python":
        return params.code
    if op == "goto":
        url = (params.url or "").strip()
        if not url:
            raise NodeUserError("url is required for the goto operation")
        return f"goto_url({url!r})\nwait_for_load()\nprint(page_info())"
    if op == "screenshot":
        return f"p = capture_screenshot(full={bool(params.full_page)})\nprint(p)"
    if op == "js":
        expr = (params.expression or "").strip()
        if not expr:
            raise NodeUserError("expression is required for the js operation")
        return f"print(js({expr!r}))"
    if op == "tabs":
        return "print(list_tabs())"
    raise NodeUserError(f"Unknown operation: {op}")


class BrowserHarnessParams(BaseModel):
    operation: Literal["run_python", "goto", "screenshot", "js", "tabs", "doctor"] = Field(
        default="run_python",
        description=(
            "run_python executes a Python snippet against the harness "
            "helpers (goto_url, click_at_xy, capture_screenshot, js, "
            "fill_input, wait_for_load, ...). The other operations are "
            "single-helper shortcuts; doctor diagnoses the Chrome/CDP "
            "connection."
        ),
    )
    code: str = Field(
        default="",
        description=(
            "Python for run_python. Helpers are pre-imported. Print a "
            "JSON object as the final line for structured output."
        ),
        json_schema_extra={
            "editor": "code",
            "rows": 8,
            "displayOptions": {"show": {"operation": ["run_python"]}},
        },
    )
    url: str = Field(
        default="",
        description="URL to open.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["goto"]}}},
    )
    expression: str = Field(
        default="",
        description="JavaScript to evaluate in the page.",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"operation": ["js"]}},
        },
    )
    full_page: bool = Field(
        default=False,
        description="Capture the full scrollable page.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["screenshot"]}}},
    )
    timeout: int = Field(
        default=60,
        ge=5,
        le=600,
        description="Script timeout in seconds.",
    )

    model_config = ConfigDict(extra="ignore")


class BrowserHarnessOutput(BaseModel):
    operation: Optional[str] = None
    data: Optional[Any] = None

    model_config = ConfigDict(extra="allow")


class BrowserHarnessNode(ActionNode):
    type = "browserHarness"
    display_name = "Browser Harness"
    subtitle = "Raw-CDP Chrome control"
    group = ("browser", "tool")
    description = "Drive your real Chrome over raw CDP via browser-use/browser-harness (alpha)"
    component_kind = "square"
    tool_name = "browser_harness"
    tool_description = (
        "Control the user's real Chrome by writing Python against pre-imported "
        "CDP helpers. Loop: capture_screenshot() to SEE the page, "
        "click_at_xy(x, y) to interact, wait_for_load(), then js(...) for "
        "DOM reads. Prefer operation=run_python with a short script; print a "
        "JSON object as the last line for structured results. Use "
        "operation=doctor if the browser seems unreachable."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.BROWSER
    usable_as_tool = True

    Params = BrowserHarnessParams
    Output = BrowserHarnessOutput

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: BrowserHarnessParams) -> BrowserHarnessOutput:
        from ._service import get_browser_harness_service

        svc = get_browser_harness_service()
        if not svc:
            raise NodeUserError(
                "browser-harness could not be installed — uv is required on "
                "PATH (https://docs.astral.sh/uv/). It installs automatically "
                "on first use once uv is available."
            )

        if params.operation == "doctor":
            data = await svc.doctor()
        else:
            data = await svc.run_code(_code_for(params), timeout=params.timeout)

        return BrowserHarnessOutput(operation=params.operation, data=data)
