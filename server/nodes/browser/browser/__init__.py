"""Browser — Wave 11.E.3 inlined.

Interactive browser automation via the agent-browser CLI. The plugin
maps the high-level operation enum to CLI argv, resolves the browser
binary (system Chrome / Edge / Chromium / bundled), and delegates the
subprocess invocation to ``_service`` (the plugin-private service).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue
from services.plugin.base import NodeUserError

logger = get_logger(__name__)


# PATH-based names for shutil.which() (Linux/macOS/Windows where on PATH).
_BROWSER_PATH_NAMES = {
    "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
    "edge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
    "chromium": ["chromium", "chromium-browser"],
}

# Windows registry App Paths keys (how Selenium/Playwright find browsers).
_BROWSER_REGISTRY_KEYS = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "chromium": "chrome.exe",
}


def _find_browser_via_registry(exe_name: str) -> Optional[str]:
    """Find a browser executable via the Windows App Paths registry.

    HKLM/HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\<exe>
    """
    try:
        import winreg

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                key = winreg.OpenKey(
                    hive,
                    rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}",
                )
                path, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if path:
                    return path
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _resolve_browser(selection: str, custom_path: str) -> Optional[str]:
    """Resolve a browser dropdown selection to an executable path."""
    if selection in ("bundled", "bundled_explicit") or not selection:
        return None
    if selection == "custom":
        return custom_path or None

    for name in _BROWSER_PATH_NAMES.get(selection, []):
        path = shutil.which(name)
        if path:
            return path

    if sys.platform == "win32":
        reg_name = _BROWSER_REGISTRY_KEYS.get(selection)
        if reg_name:
            path = _find_browser_via_registry(reg_name)
            if path:
                return path
    return None


def _req(p: Dict[str, Any], key: str) -> str:
    v = (p.get(key) or "").strip()
    if not v:
        raise NodeUserError(f"{key} is required")
    return v


def _req_sel(s: str) -> str:
    if not s:
        raise NodeUserError("selector is required")
    return s


def _build_args(op: str, p: Dict[str, Any]) -> List[str]:
    """Map an operation name + parameters to agent-browser CLI arguments."""
    s = (p.get("selector") or "").strip()
    match op:
        case "navigate":
            return ["open", _req(p, "url")]
        case "click":
            return ["click", _req_sel(s)]
        case "type":
            return ["type", _req_sel(s), p.get("text") or ""]
        case "fill":
            return ["fill", _req_sel(s), p.get("value") or ""]
        case "screenshot":
            args = ["screenshot"]
            if p.get("full_page"):
                args.append("--full")
            if p.get("annotate"):
                args.append("--annotate")
            fmt = p.get("screenshot_format", "png")
            if fmt and fmt != "png":
                args.extend(["--screenshot-format", fmt])
                quality = p.get("screenshot_quality")
                if quality and fmt == "jpeg":
                    args.extend(["--screenshot-quality", str(quality)])
            return args
        case "snapshot":
            return ["snapshot", "-i"]
        case "get_text":
            return ["get", "text", _req_sel(s)]
        case "get_html":
            return ["get", "html", _req_sel(s)]
        case "eval":
            return ["eval", _req(p, "expression")]
        case "wait":
            return ["wait", _req_sel(s)]
        case "scroll":
            return ["scroll", p.get("direction") or "down", str(p.get("amount") or 500)]
        case "select":
            return ["select", _req_sel(s), p.get("value") or ""]
        case "console":
            return ["console"]
        case "errors":
            return ["errors"]
        case _:
            raise ValueError(f"Unknown operation: {op}")


class BrowserParams(BaseModel):
    """Browser automation parameters — full baseline schema.

    Organized into operation-scoped fields (drive the LLM tool schema) and
    runtime/stealth config (always visible in the workflow panel, used for
    browser binary selection, profiles, proxy, user agent, etc.).
    """

    operation: Literal[
        "navigate",
        "click",
        "type",
        "fill",
        "screenshot",
        "snapshot",
        "get_text",
        "get_html",
        "eval",
        "wait",
        "scroll",
        "select",
        "console",
        "errors",
        "batch",
    ] = Field(
        default="navigate",
        description=("Browser operation. Typical flow: navigate -> snapshot -> " "interact using @eN refs -> snapshot."),
    )

    # Operation-scoped fields
    url: str = Field(
        default="",
        description="URL to open.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["navigate"]}}},
    )
    selector: str = Field(
        default="",
        description="CSS selector or @eN ref from snapshot.",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": [
                        "click",
                        "type",
                        "fill",
                        "get_text",
                        "get_html",
                        "wait",
                        "select",
                    ]
                }
            },
        },
    )
    text: str = Field(
        default="",
        description="Text to type keystroke-by-keystroke.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["type"]}}},
    )
    value: str = Field(
        default="",
        description="Value to fill (fill) or option value to select (select).",
        json_schema_extra={"displayOptions": {"show": {"operation": ["fill", "select"]}}},
    )
    expression: str = Field(
        default="",
        description="JavaScript to execute in page context.",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"operation": ["eval"]}},
        },
    )
    direction: Literal["up", "down", "left", "right"] = Field(
        default="down",
        json_schema_extra={"displayOptions": {"show": {"operation": ["scroll"]}}},
    )
    amount: int = Field(
        default=500,
        ge=1,
        le=20000,
        description="Pixels to scroll.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["scroll"]}}},
    )
    commands: str = Field(
        default="[]",
        description="JSON array of batch commands (see agent-browser batch docs).",
        json_schema_extra={
            "rows": 6,
            "displayOptions": {"show": {"operation": ["batch"]}},
        },
    )

    # Screenshot options
    full_page: bool = Field(
        default=False,
        description="Capture full scrollable page.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["screenshot"]}}},
    )
    annotate: bool = Field(
        default=False,
        description="Overlay element boxes on screenshot.",
        json_schema_extra={"displayOptions": {"show": {"operation": ["screenshot"]}}},
    )
    screenshot_format: Literal["png", "jpeg"] = Field(
        default="png",
        json_schema_extra={"displayOptions": {"show": {"operation": ["screenshot"]}}},
    )
    screenshot_quality: int = Field(
        default=85,
        ge=1,
        le=100,
        description="JPEG quality (1-100).",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": ["screenshot"],
                    "screenshot_format": ["jpeg"],
                }
            },
        },
    )

    # Session
    session: str = Field(
        default="",
        description="Browser session name for state sharing across chained nodes (auto-derived when empty).",
    )

    # Browser binary selection
    browser: Literal["chrome", "edge", "chromium", "bundled_explicit", "custom"] = Field(
        default="chrome",
        description="Which browser binary to drive.",
    )
    executable_path: str = Field(
        default="",
        description="Full path to browser executable.",
        json_schema_extra={"displayOptions": {"show": {"browser": ["custom"]}}},
    )

    # Runtime config
    headed: bool = Field(default=True, description="Show browser window (false = headless).")
    new_window: bool = Field(
        default=True,
        description="Open a new browser window for this run.",
    )
    auto_connect: bool = Field(
        default=False,
        description="Reuse an already-running browser with CDP enabled.",
    )
    chrome_profile: str = Field(
        default="",
        description="Chrome user-profile name (e.g. 'Default').",
    )
    user_agent: str = Field(
        default="",
        description="Custom User-Agent string.",
    )
    proxy: str = Field(
        default="",
        description="Proxy URL (e.g. http://user:pass@host:port).",
    )
    action_delay: int = Field(
        default=0,
        ge=0,
        le=60000,
        description="Delay before each action in milliseconds (for stealth).",
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=600,
        description="Per-action timeout in seconds.",
    )

    model_config = ConfigDict(extra="ignore")


class BrowserOutput(BaseModel):
    operation: Optional[str] = None
    data: Optional[Any] = None
    session: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class BrowserNode(ActionNode):
    type = "browser"
    display_name = "Browser"
    subtitle = "Browser Automation"
    group = ("browser", "tool")
    description = "Interactive browser automation via agent-browser CLI"
    component_kind = "square"
    tool_name = "browser"
    tool_description = "Control a web browser interactively. Use snapshot to see the page (returns accessibility tree with @eN refs). Then click/type/fill with those refs. Workflow: navigate -> snapshot -> interact -> snapshot. Operations: navigate, click, type, fill, screenshot, snapshot, get_text, get_html, eval, wait, scroll, select, batch."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.BROWSER
    usable_as_tool = True

    Params = BrowserParams
    Output = BrowserOutput

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: BrowserParams) -> BrowserOutput:
        from .._service import get_browser_service

        svc = get_browser_service()
        if not svc:
            raise RuntimeError(
                "agent-browser not installed. Run: pnpm install && npx agent-browser install",
            )

        op = params.operation
        session = params.session.strip() or f"machina_{ctx.raw.get('execution_id', 'default')}"
        timeout = params.timeout
        browser_sel = params.browser or "chrome"
        if browser_sel == "bundled":
            browser_sel = "chrome"
        executable_path = _resolve_browser(browser_sel, params.executable_path.strip())
        logger.info("[Browser] browser=%s executable=%s", browser_sel, executable_path)

        run_kw = dict(
            headed=params.headed,
            user_agent=params.user_agent.strip() or None,
            proxy=params.proxy.strip() or None,
            executable_path=executable_path,
            auto_connect=params.auto_connect,
            chrome_profile=params.chrome_profile.strip() or None,
            new_window=params.new_window and executable_path is not None,
        )

        if params.action_delay > 0:
            await svc.run(["wait", str(params.action_delay)], session, timeout, **run_kw)

        p = params.model_dump()
        if op == "batch":
            cmds = json.loads(params.commands or "[]")
            data = await svc.run(
                ["batch", "--json"],
                session,
                timeout,
                stdin=json.dumps(cmds).encode(),
                **run_kw,
            )
        else:
            data = await svc.run(_build_args(op, p), session, timeout, **run_kw)

        return BrowserOutput(operation=op, data=data, session=session)
