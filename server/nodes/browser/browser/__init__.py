"""Browser — a real Chrome the agent drives, and the owner can watch and take over.

OpenCompany launches a pinned Chrome for Testing per browser profile and the
agent drives it through the browser-use CLI: it reads pages through the
accessibility tree (``snapshot`` returns ``[e12]``-style refs), acts on refs
(``click``, ``type``, ``select``), calls the tools a page offers through
WebMCP (``webmcp_list`` / ``webmcp_call``), and hands the page to the owner
with ``request_user`` when it needs a login, a CAPTCHA or a decision. The
live view and take-over run over ``/ws/browser`` (``_stream.py``).

Two schemas: ``BrowserToolInput`` is all a model may send;
``BrowserParams`` adds the operator's settings (profile, read-only mode,
WebMCP policy, network access, timeouts, and the code of the workflow-only
``evaluate`` / ``run_python``). Those settings are read from the node's saved
parameters on every tool call, never from the call itself: on the Temporal
path the call arguments arrive merged over the saved parameters, so a model
(or a page talking to it) could otherwise pick them.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.logging import get_logger
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode
from services.plugin.base import NodeUserError
from services.plugin.scaling import RetryPolicy

logger = get_logger(__name__)

ModelOperation = Literal[
    "navigate",
    "snapshot",
    "click",
    "hover",
    "type",
    "press",
    "select",
    "scroll",
    "screenshot",
    "tabs",
    "back",
    "forward",
    "reload",
    "page_text",
    "page_info",
    "wait",
    "webmcp_list",
    "webmcp_call",
    "request_user",
    "diagnose",
]
NodeOperation = Literal[
    "navigate",
    "snapshot",
    "click",
    "hover",
    "type",
    "press",
    "select",
    "scroll",
    "screenshot",
    "tabs",
    "back",
    "forward",
    "reload",
    "page_text",
    "page_info",
    "wait",
    "webmcp_list",
    "webmcp_call",
    "request_user",
    "diagnose",
    "evaluate",
    "run_python",
    "close",
]

#: Operations that can change a page or a site. Refused in read-only mode,
#: and never re-run by a retry (the first attempt's outcome is unknown).
MUTATING = frozenset({"click", "type", "press", "select", "back", "forward", "reload", "webmcp_call", "evaluate", "run_python"})
#: Operations with a target element.
_TARGETED = {"click", "hover", "type", "select", "scroll", "page_text"}
#: Only workflow nodes may run these; they are not in the model's schema.
NODE_ONLY = frozenset({"evaluate", "run_python", "close"})

_TOOL_CALL_BUDGET = 510.0  # under the agent's 10-minute tool step, including installs and waits
_TOOL_REQUEST_WAIT = 480.0


def _show(*ops: str) -> Dict[str, Any]:
    return {"displayOptions": {"show": {"operation": list(ops)}}}


def _coerce_json_list(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return [stripped]
        return parsed if isinstance(parsed, list) else [str(parsed)]
    return value


class BrowserToolInput(BaseModel):
    """What a model may send. Nothing here can pick a profile, a binary,
    network access or code to run on the host."""

    operation: ModelOperation = Field(
        default="snapshot",
        description=(
            "snapshot first: it lists the page's controls as [eN] refs. Then act on a ref (click, type, select), "
            "navigate to a URL, read with page_text, or call a tool the page offers (webmcp_list, webmcp_call). "
            "Use request_user when a person must act (login, CAPTCHA, two-factor, a final confirmation)."
        ),
    )
    url: str = Field(default="", description="http(s) URL for navigate, or for tabs with tab_action=new.", json_schema_extra=_show("navigate", "tabs"))
    ref: str = Field(default="", description="An element ref from the last snapshot, e.g. e12.", json_schema_extra=_show(*_TARGETED))
    selector: str = Field(default="", description="A CSS selector, when no ref fits.", json_schema_extra=_show(*_TARGETED))
    x: Optional[float] = Field(default=None, description="Viewport x in CSS pixels, for click/hover without a ref.")
    y: Optional[float] = Field(default=None, description="Viewport y in CSS pixels, for click/hover without a ref.")
    text: str = Field(default="", description="Text to type.", json_schema_extra=_show("type"))
    clear: bool = Field(default=True, description="Clear the field before typing.", json_schema_extra=_show("type"))
    submit: bool = Field(default=False, description="Press Enter after typing.", json_schema_extra=_show("type"))
    key: str = Field(default="", description="Key to press, e.g. Enter, Tab, Escape, ArrowDown, a.", json_schema_extra=_show("press"))
    modifiers: int = Field(default=0, ge=0, le=15, description="Bit mask: 1 Alt, 2 Ctrl, 4 Meta, 8 Shift.", json_schema_extra=_show("press"))
    values: Optional[List[str]] = Field(default=None, description="Option values or labels to select.", json_schema_extra=_show("select"))
    direction: Literal["up", "down", "left", "right"] = Field(default="down", json_schema_extra=_show("scroll"))
    amount: int = Field(default=600, ge=1, le=5000, description="Pixels to scroll.", json_schema_extra=_show("scroll"))
    tab_action: Literal["list", "new", "switch", "close"] = Field(default="list", json_schema_extra=_show("tabs"))
    tab_id: str = Field(default="", description="Tab id from tabs, for switch and close.", json_schema_extra=_show("tabs"))
    full_page: bool = Field(default=False, description="Capture the whole scrollable page.", json_schema_extra=_show("screenshot"))
    wait_for: Literal["load", "network_idle", "selector", "text", "time"] = Field(default="load", json_schema_extra=_show("wait"))
    wait_value: str = Field(default="", description="Selector, text, or seconds for wait.", json_schema_extra=_show("wait"))
    max_chars: int = Field(default=20000, ge=1000, le=100000, description="Longest snapshot or page_text to return.")
    interactive_only: bool = Field(default=True, description="snapshot: list only controls and headings.", json_schema_extra=_show("snapshot"))
    webmcp_tool: str = Field(default="", description="Name of a tool from webmcp_list.", json_schema_extra=_show("webmcp_call"))
    webmcp_input: Optional[Union[Dict[str, Any], str]] = Field(
        default=None, description="Input object for the WebMCP tool, matching its input_schema.", json_schema_extra=_show("webmcp_call")
    )
    frame_id: str = Field(default="", description="Only when webmcp_list shows the same tool in several frames.", json_schema_extra=_show("webmcp_call"))
    reason: Literal["login", "captcha", "two_factor", "confirm", "other"] = Field(default="other", json_schema_extra=_show("request_user"))
    message: str = Field(default="", description="What the owner should do in the browser.", json_schema_extra=_show("request_user"))

    model_config = ConfigDict(extra="ignore")

    @field_validator("values", mode="before")
    @classmethod
    def _coerce_values(cls, value: Any) -> Any:
        return _coerce_json_list(value)

    @field_validator("webmcp_input", mode="before")
    @classmethod
    def _coerce_input(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            return parsed
        return value


class BrowserParams(BrowserToolInput):
    """The saved node: everything above plus the operator's settings."""

    operation: NodeOperation = Field(default="navigate", description=BrowserToolInput.model_fields["operation"].description)
    profile_id: str = Field(
        default="",
        description="Browser profile (its logins). Empty uses this workflow's own profile.",
        json_schema_extra={"loadOptionsMethod": "browserProfiles"},
    )
    interaction: Literal["full", "read_only"] = Field(
        default="full",
        description="read_only: the agent may browse and read, and must hand anything that changes a page to you.",
    )
    webmcp_mode: Literal["disabled", "read_only", "all"] = Field(
        default="read_only", description="Which tools a web page offers (WebMCP) the agent may call."
    )
    allowed_domains: str = Field(default="", description="Comma-separated sites the browser may open. Empty allows every public site.")
    allow_private_network: bool = Field(
        default=False,
        description="Allow localhost and your local network (e.g. to test a local app). OpenCompany's own ports and cloud metadata stay blocked.",
    )
    op_timeout_s: int = Field(default=45, ge=5, le=300, description="Seconds one browser step may take.")
    request_user_timeout_s: int = Field(default=600, ge=60, le=1800, description="How long to wait for you when the agent asks for help.")
    expression: str = Field(default="", description="JavaScript to evaluate in the page.", json_schema_extra={"rows": 4, **_show("evaluate")})
    code: str = Field(
        default="",
        description="Python run by the browser-use CLI with its helpers (goto_url, click_at_xy, js, cdp, ...). Set `result` to return a value.",
        json_schema_extra={"rows": 10, "editor": "code", **_show("run_python")},
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        """Saved parameters from the retired agent-browser / browserHarness nodes
        (immutable deployment snapshots never went through the load-time migration)."""
        if isinstance(data, dict) and (
            str(data.get("operation") or "") in {"fill", "get_text", "get_html", "eval", "console", "errors", "batch", "goto", "js", "doctor"}
            or any(k in data for k in ("session", "headed", "executable_path", "chrome_profile", "commands", "action_delay"))
        ):
            from services.workflow_migrations import migrate_legacy_browser_params

            legacy_type = "browserHarness" if str(data.get("operation") or "") in {"goto", "js", "doctor"} else "browser"
            migrated, _ = migrate_legacy_browser_params(data, legacy_type=legacy_type)
            return migrated
        return data


class BrowserOutput(BaseModel):
    """Small by contract: a node result is persisted, broadcast and replayed
    into the model's context. Screenshots are FileRefs, never bytes."""

    operation: Optional[str] = None
    session_id: Optional[str] = None
    profile: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    tab_id: Optional[str] = None
    snapshot: Optional[str] = None
    truncated: Optional[bool] = None
    text: Optional[str] = None
    screenshot: Optional[Dict[str, Any]] = None
    tabs: Optional[List[Dict[str, Any]]] = None
    webmcp_tools: Optional[List[Dict[str, Any]]] = None
    webmcp_result: Optional[Dict[str, Any]] = None
    handback: Optional[Dict[str, Any]] = None
    data: Optional[Any] = None
    notice: Optional[str] = None

    model_config = ConfigDict(extra="allow")


def _attempt() -> int:
    try:
        from temporalio import activity

        if activity.in_activity():
            return int(activity.info().attempt)
    except Exception:  # noqa: BLE001
        pass
    return 1


def _is_tool_call(ctx: NodeContext) -> bool:
    return ctx.raw.get("_split_tool_schema") is True or isinstance(ctx.raw.get("tool_args"), dict)


async def _config(ctx: NodeContext, params: BaseModel) -> BrowserParams:
    """The operator's settings for this run.

    A tool call reads them from the node's saved parameters only; a workflow
    run's parameters already are the operator's.
    """
    if not _is_tool_call(ctx):
        return params if isinstance(params, BrowserParams) else BrowserParams.model_validate(params.model_dump())
    from services.plugin.deps import get_database

    saved = await get_database().get_node_parameters(ctx.node_id)
    if saved is None:
        raise NodeUserError("This Browser node's settings could not be loaded. Save the workflow and try again.")
    return BrowserParams.model_validate(saved)


async def _profile_for(ctx: NodeContext, cfg: BrowserParams):
    from services.plugin.deps import get_database

    from .._profiles import ProfileError, ProfileStore

    store = ProfileStore(get_database())
    owner = ctx.user_id or "owner"
    try:
        if cfg.profile_id:
            return await store.get(owner, cfg.profile_id)
        if ctx.workflow_id:
            saved = await get_database().get_workflow(ctx.workflow_id)
            name = getattr(saved, "name", None) if saved is not None else None
            if not name and isinstance(saved, dict):
                name = saved.get("name")
            return await store.default_for_workflow(owner, ctx.workflow_id, str(name or "Browser"))
        shared = [p for p in await store.list(owner) if p.kind == "shared" and p.name.lower() == "default"]
        return shared[0] if shared else await store.create(owner, "Default")
    except ProfileError as exc:
        raise NodeUserError(str(exc)) from exc


def _read_only_refusal(op: str) -> str:
    return (
        f"This browser is read-only for this agent, so '{op}' is not allowed. Read the page, then call "
        "request_user with a message saying exactly what the owner should do."
    )


class BrowserNode(ToolNode):
    type = "browser"
    display_name = "Browser"
    subtitle = "Web Browser"
    group = ("browser", "tool")
    description = "A real Chrome the agent drives through its accessibility tree and a site's WebMCP tools; watch and take over live."
    component_kind = "square"
    tool_name = "browser"
    tool_schema_locked = True
    tool_description = (
        "Use a real web browser. Start with snapshot: it lists the page's controls as [eN] refs, plus any tools the page "
        "offers through WebMCP. Act on refs (click, type with submit, select), navigate to URLs, read with page_text, and "
        "prefer webmcp_call when the page offers a tool for the job. Take a new snapshot after the page changes. When a "
        "person must act (log in, a CAPTCHA, two-factor, a final purchase or send), call request_user with clear "
        "instructions and wait; never ask for passwords in chat."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    hide_input_handle = False
    hide_output_handle = False
    ui_hints = {"isBrowserPanel": True, "isConfigNode": False}
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.BROWSER
    retry_policy = RetryPolicy(maximum_attempts=3)
    # Long enough for request_user on a workflow node (up to 30 min); also
    # turns on the base 30 s heartbeat loop while a step runs.
    start_to_close_timeout = timedelta(minutes=35)
    server_controlled_fields = frozenset(
        {"profile_id", "interaction", "webmcp_mode", "allowed_domains", "allow_private_network", "op_timeout_s", "request_user_timeout_s", "code", "expression"}
    )

    Params = BrowserParams
    ToolInput = BrowserToolInput
    Output = BrowserOutput

    @classmethod
    async def reset_execution_state(cls, *, node_id: str, workflow_id: str, execution_id: str, generation: int, graph: Dict[str, Any], database: Any) -> Dict[str, Any]:
        from .._runtime import peek_browser_runtime

        runtime = peek_browser_runtime()
        if runtime is None:
            return {"reset": False}
        session = runtime.find_session(workflow_id, node_id)
        if session is None:
            return {"reset": False}
        controller = runtime.controller(session.profile_id)
        if controller is not None:
            await controller.release_lease(session.session_id)
        return {"reset": True}

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: Any) -> BrowserOutput:
        from .._netpolicy import parse_allowed_domains, url_block_reason
        from .._runtime import get_browser_runtime
        from .._session import BrowserSession, SessionKey

        started = time.monotonic()
        tool_call = _is_tool_call(ctx)
        cfg = await _config(ctx, params)
        op = str(params.operation)
        call = params if isinstance(params, BrowserToolInput) else cfg

        if tool_call and op in NODE_ONLY:
            raise NodeUserError(f"'{op}' is not available to agents.")
        if cfg.interaction == "read_only" and op in MUTATING:
            raise NodeUserError(_read_only_refusal(op))
        if op in MUTATING and _attempt() > 1:
            raise NodeUserError(
                "The previous attempt of this browser step did not report back, so it may have happened already. "
                "Take a snapshot to see the page before trying again."
            )

        runtime = get_browser_runtime()
        profile = await _profile_for(ctx, cfg)
        owner = ctx.user_id or "owner"
        workflow_key = ctx.workflow_id or f"unsaved:{ctx.execution_id or 'run'}"
        policy = runtime.base_policy(
            allow_private_network=cfg.allow_private_network, allowed_domains=parse_allowed_domains(cfg.allowed_domains)
        )
        label = next(
            (str((n.get("data") or {}).get("label") or "") for n in ctx.nodes or [] if n.get("id") == ctx.node_id),
            "",
        ) or "a Browser node"
        session = runtime.register_session(
            BrowserSession(key=SessionKey(owner, workflow_key, ctx.node_id), profile_id=profile.id, label=label, policy=policy)
        )
        out = BrowserOutput(operation=op, session_id=session.session_id, profile=profile.name)
        budget = _TOOL_CALL_BUDGET if tool_call else 3600.0

        def remaining() -> float:
            return max(5.0, budget - (time.monotonic() - started))

        if op in ("navigate", "tabs") and call.url and (op == "navigate" or call.tab_action == "new"):
            reason = url_block_reason(call.url, policy)
            if reason:
                raise NodeUserError(f"Cannot open {call.url}: {reason}.")
        if op == "navigate" and not call.url:
            raise NodeUserError("url is required for navigate")

        if op == "close":
            await runtime.stop_profile(profile.id, reason="closed by the workflow")
            out.notice = "The browser was closed."
            return out

        if op == "diagnose":
            out.data = await _diagnose(runtime, profile)
            return out

        prt = await runtime.open(profile, wait=min(remaining(), 300.0))
        controller = prt.controller

        if op == "request_user":
            wait = min(float(cfg.request_user_timeout_s), _TOOL_REQUEST_WAIT) if tool_call else float(cfg.request_user_timeout_s)
            message = call.message or "Please finish this step in the browser, then hand it back."
            result = await controller.request_user(session, reason=call.reason, message=message, timeout=float(cfg.request_user_timeout_s), wait=wait)
            out.handback = result
            _fill_page(out, controller)
            if result.get("status") == "still_waiting":
                out.notice = "The owner has not answered yet. Call request_user again to keep waiting."
            return out

        async with controller.agent_op(session, interrupt=prt.cli.interrupt):
            if op == "webmcp_list":
                tools = prt.webmcp.tools(controller.active_target_id)
                out.webmcp_tools = [dict(t, callable=_webmcp_callable(t, cfg)) for t in tools]
                _fill_page(out, controller)
                return out
            if op == "webmcp_call":
                if not call.webmcp_tool:
                    raise NodeUserError("webmcp_tool is required; see webmcp_list")
                mode = "read_only" if cfg.interaction == "read_only" else cfg.webmcp_mode
                tool_input = call.webmcp_input if isinstance(call.webmcp_input, dict) else {}
                out.webmcp_result = await prt.webmcp.invoke(
                    controller.active_target_id, call.webmcp_tool, tool_input, frame_id=call.frame_id or None, mode=mode
                )
                _fill_page(out, controller)
                return out
            return await _run_cli_op(ctx, prt, controller, op, call, cfg, out, timeout=min(float(cfg.op_timeout_s), remaining()))


def _webmcp_callable(tool: Dict[str, Any], cfg: BrowserParams) -> bool:
    mode = "read_only" if cfg.interaction == "read_only" else cfg.webmcp_mode
    return mode == "all" or (mode == "read_only" and bool(tool.get("read_only")))


def _fill_page(out: BrowserOutput, controller: Any) -> None:
    tab = controller.tabs.get(controller.active_target_id or "") or {}
    out.tab_id = controller.active_target_id
    out.url = out.url or tab.get("url")
    out.title = out.title or tab.get("title")


def _cli_args(op: str, call: BaseModel, cfg: BrowserParams, controller: Any) -> Dict[str, Any]:
    args: Dict[str, Any] = {"_target_id": controller.active_target_id}
    ref = (getattr(call, "ref", "") or "").strip()
    selector = (getattr(call, "selector", "") or "").strip()
    if not ref and selector.startswith("@e") and selector[2:].isdigit():
        ref, selector = selector[1:], ""
    if ref:
        refs = controller.refs.get(controller.active_target_id or "", {})
        backend = refs.get(ref if ref.startswith("e") else f"e{ref}")
        if backend is None:
            raise NodeUserError(f"No element {ref} on this page; take a new snapshot and use a ref from it.")
        args["backend_node_id"] = backend
    elif selector:
        args["selector"] = selector
    for name in ("x", "y", "text", "clear", "submit", "key", "modifiers", "values", "direction", "amount", "tab_action", "tab_id", "url", "full_page", "wait_for", "wait_value", "max_chars", "interactive_only"):
        if hasattr(call, name):
            args[name] = getattr(call, name)
    if op == "evaluate":
        args["expression"] = cfg.expression
    if op == "run_python":
        args["code"] = cfg.code
    return args


async def _run_cli_op(ctx: NodeContext, prt: Any, controller: Any, op: str, call: BaseModel, cfg: BrowserParams, out: BrowserOutput, *, timeout: float) -> BrowserOutput:
    from .._cli import BrowserUnavailable

    script_op = {"back": "history", "forward": "history", "reload": "history"}.get(op, op)
    if op in ("evaluate", "run_python") and not (cfg.expression if op == "evaluate" else cfg.code).strip():
        raise NodeUserError(f"{'expression' if op == 'evaluate' else 'code'} is required for {op}")
    args = _cli_args(op, call, cfg, controller)
    if script_op == "history":
        args["history_action"] = op
    shot: Optional[Path] = None
    if op == "screenshot":
        shot = prt.cli.dirs()["tmp"] / f"shot-{uuid.uuid4().hex[:12]}.png"
        args["path"] = str(shot)

    try:
        result = await prt.cli.run(script_op, args, timeout=timeout)
    except BrowserUnavailable:
        if op in MUTATING:
            raise
        # A read can safely run again once the CLI's daemon is replaced.
        prt.cli.stop_daemon()
        result = await prt.cli.run(script_op, args, timeout=timeout)

    if result.page and result.page.get("target_id"):
        controller.active_target_id = result.page["target_id"]
        tab = controller.tabs.setdefault(result.page["target_id"], {"target_id": result.page["target_id"]})
        tab.update(url=result.page.get("url"), title=result.page.get("title"))
        await controller.emit("page", dict(result.page))
    if result.page:
        out.url, out.title, out.tab_id = result.page.get("url"), result.page.get("title"), result.page.get("target_id")

    if not result.ok:
        raise NodeUserError(result.error or f"The browser step '{op}' failed")

    value = result.value
    if op == "snapshot" and isinstance(value, dict):
        refs = {str(k): int(v) for k, v in (value.get("refs") or {}).items() if isinstance(v, (int, float))}
        controller.refs[controller.active_target_id or ""] = refs
        text = str(value.get("text") or "")
        tools = prt.webmcp.count(controller.active_target_id)
        if tools:
            text += f"\n\n[this page offers {tools} WebMCP tool{'s' if tools != 1 else ''}: call webmcp_list]"
        out.snapshot, out.truncated = text or "(no controls found on this page)", bool(value.get("truncated"))
    elif op == "screenshot":
        from .._screenshots import persist_screenshot_file

        ref = persist_screenshot_file(str(shot), ctx, contained_under=prt.cli.dirs()["tmp"]) if shot else None
        if shot is not None:
            try:
                shot.unlink()
            except OSError:
                pass
        if ref is None:
            out.notice = "The screenshot was taken but could not be saved to the workspace."
        out.screenshot = ref
    elif op == "page_text" and isinstance(value, dict):
        out.text, out.truncated = value.get("text"), bool(value.get("truncated"))
    elif op == "tabs" and isinstance(value, dict):
        out.tabs = value.get("tabs")
    else:
        out.data = value
    if result.output and op == "run_python":
        out.text = result.output[-20000:]
    return out


async def _diagnose(runtime: Any, profile: Any) -> Dict[str, Any]:
    from .._host import in_container, is_linux_root, sandbox_disabled, shm_small, userns_restricted

    report: Dict[str, Any] = {
        "runtime": runtime.status(),
        "host": {
            "container": in_container(),
            "linux_root": is_linux_root(),
            "userns_restricted": userns_restricted(),
            "small_dev_shm": shm_small(),
            "sandbox": dict(zip(("disabled", "why"), sandbox_disabled(str(getattr(runtime._settings(), "browser_sandbox", "auto"))))),
        },
        "profile": profile.to_wire(),
    }
    running = runtime.running(profile.id)
    if running is not None:
        try:
            report["cli"] = await running.cli.doctor()
        except Exception as exc:  # noqa: BLE001 - a diagnosis reports failures
            report["cli"] = {"healthy": False, "error": str(exc)}
        report["webmcp_tools_on_page"] = running.webmcp.count(running.controller.active_target_id)
    return report


__all__ = ["BrowserNode", "BrowserOutput", "BrowserParams", "BrowserToolInput", "MUTATING", "NODE_ONLY"]
