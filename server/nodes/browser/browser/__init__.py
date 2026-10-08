"""Browser — a real Chrome the agent drives, and the owner can watch and take over.

OpenCompany launches installed Chrome/Edge/Chromium per dedicated browser profile and the
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

import asyncio
import json
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

from core.logging import get_logger
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode
from services.plugin.approval import ApprovalSpec
from services.plugin.base import NodeUserError
from services.plugin.scaling import RetryPolicy

from .._controls import is_retry_safe, is_site_action

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
    "credential_fill",
    "credential_bindings",
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
    "credential_fill",
    "credential_bindings",
    "evaluate",
    "run_python",
    "close",
]

#: Permission policy. Retry safety and traffic accounting are separate:
#: navigation can be permitted in read-only mode but cannot be blindly replayed.
MUTATING = frozenset({"click", "type", "press", "select", "back", "forward", "reload", "webmcp_call", "evaluate", "run_python", "credential_fill"})
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
    credential_binding_id: str = Field(default="", description="Opaque approved website-login binding ID. Never pass a password or 1Password reference.", json_schema_extra=_show("credential_fill"))
    username_ref: str = Field(default="", description="Username input ref from the current snapshot.", json_schema_extra=_show("credential_fill"))
    password_ref: str = Field(default="", description="Password input ref from the current snapshot.", json_schema_extra=_show("credential_fill"))
    submit_ref: str = Field(default="", description="A separately selected submit-button ref. Required for configured automatic login.", json_schema_extra=_show("credential_fill"))

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
    allowed_domains: str = Field(default="", description="Comma-separated sites the browser may open. Empty allows every public site and localhost.")
    allow_private_network: bool = Field(
        default=False,
        description="Allow your private local network. Localhost is already allowed. OpenCompany's own ports and cloud metadata stay blocked.",
    )
    op_timeout_s: int = Field(default=45, ge=5, le=300, description="Seconds one browser step may take.")
    request_user_timeout_s: int = Field(default=600, ge=60, le=1800, description="How long to wait for you when the agent asks for help.")
    min_action_interval_ms: int = Field(default=1000, ge=0, le=60000, description="Minimum time between agent actions on the same site, shared across browser profiles. Human control is unaffected.")
    max_actions_per_minute: int = Field(default=30, ge=1, le=300, description="Maximum agent actions per site in 60 seconds, shared across profiles. Reading snapshots does not consume this budget.")
    max_repeat_actions: int = Field(default=3, ge=1, le=20, description="Maximum identical agent actions on a page in 60 seconds before the agent must stop and inspect it.")
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
    success: Optional[bool] = None
    error_type: Optional[str] = None
    error: Optional[str] = None
    retry_after: Optional[float] = None
    next_action: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def _wire(self, handler):
        payload = handler(self)
        # ToolNode successes are flat payloads with no success key. A null
        # success field would otherwise turn every successful activity into
        # a failure when its result is interpreted by Temporal.
        if payload.get("success") is None:
            payload.pop("success", None)
        return payload


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
    snapshot = ctx.raw.get("parameter_snapshot") or {}
    frozen = snapshot.get(ctx.node_id) if isinstance(snapshot, dict) else None
    versioned = ctx.raw.get("native_workspace_version") == 1 or ctx.raw.get("browser_runtime_version") == 1 or ctx.raw.get("browser_routing_version") == 1
    cfg = BrowserParams.model_validate(frozen if versioned and isinstance(frozen, dict) else saved)
    # This is the adapter's validated server configuration, never tool_args.
    # An employee's Ask first restriction must survive reloading saved settings.
    trusted = ctx.raw.get("_tool_config")
    trusted_interaction = trusted.interaction if isinstance(trusted, BrowserParams) else trusted.get("interaction") if isinstance(trusted, dict) else None
    if saved.get("interaction") == "read_only" or trusted_interaction == "read_only":
        cfg = cfg.model_copy(update={"interaction": "read_only"})
    return cfg


async def _profile_for(ctx: NodeContext, cfg: BrowserParams):
    from services.plugin.deps import get_database

    from .._profiles import ProfileError, ProfileStore

    store = ProfileStore(get_database())
    owner = ctx.user_id or "owner"
    try:
        binding = ctx.raw.get("_browser_owner")
        if isinstance(binding, dict) and binding.get("profile_id"):
            return await store.get(owner, str(binding["profile_id"]))
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
    usable_as_tool = True
    tool_name = "browser"
    tool_schema_locked = True
    tool_error_fields = frozenset({"success", "error_type", "retry_after", "next_action", "session_id", "profile", "url", "title", "tab_id"})
    tool_description = (
        "Use a real web browser. Start with snapshot: it lists the page's controls as [eN] refs, plus any tools the page "
        "offers through WebMCP. Act on refs (click, type with submit, select), navigate to URLs, read with page_text, and "
        "prefer webmcp_call when the page offers a tool for the job. Take a new snapshot after the page changes. When a "
        "person must act (log in, a CAPTCHA, two-factor, a final purchase or send), call request_user with clear "
        "instructions and wait; never ask for passwords in chat. Respect retry_after when rate_limited; do not repeat "
        "a blocked action. On outcome_unknown, inspect a fresh snapshot before deciding what remains to do."
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
    # A page can do anything its logins allow; while the owner asks first
    # it only reads, and hands changes to them (request_user).
    approval = ApprovalSpec(
        channel="Web browser",
        action="Use the web browser",
        restrict_while_asking={"interaction": "read_only"},
    )
    retry_policy = RetryPolicy(maximum_attempts=3)
    # Long enough for request_user on a workflow node (up to 30 min); also
    # turns on the base 30 s heartbeat loop while a step runs.
    start_to_close_timeout = timedelta(minutes=35)
    server_controlled_fields = frozenset(
        {"profile_id", "interaction", "webmcp_mode", "allowed_domains", "allow_private_network", "op_timeout_s", "request_user_timeout_s", "min_action_interval_ms", "max_actions_per_minute", "max_repeat_actions", "code", "expression"}
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
            if controller.task_id is not None:
                # The owning agent's reset hook carries its exact task token.
                # A legacy Browser hook must not release an unrelated task.
                return {"reset": False, "managed_by_agent": True}
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
        if is_site_action(op, call.tab_action) and _attempt() > 1:
            raise NodeUserError(
                "The previous attempt of this browser step did not report back, so it may have happened already. "
                "Take a snapshot to see the page before trying again."
            )

        runtime = get_browser_runtime()
        profile = await _profile_for(ctx, cfg)
        owner = ctx.user_id or "owner"
        task_id = str(ctx.raw.get("_browser_task_id") or ctx.execution_id or "")
        from services.browser_owners import assert_owner, bind_profile, _persistent
        from services.plugin.deps import get_database
        if ctx.raw.get("_browser_owner") or _persistent(get_database()):
            binding = ctx.raw.get("_browser_owner") or await bind_profile(get_database(), profile.id, owner)
            await assert_owner(get_database(), binding, owner, task_id)
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
        controller_lookup = getattr(runtime, "controller", None)
        existing_controller = controller_lookup(profile.id) if callable(controller_lookup) else controller_lookup
        if existing_controller is not None:
            if existing_controller.task_id is not None and existing_controller.task_id != task_id:
                return _failure(out, "BrowserBusy", "This browser profile is already assigned to another task.", next_action="wait")
            if existing_controller.sensitive_login and op not in {"request_user", "close", "credential_bindings"}:
                return _failure(out, "sensitive_login", "Browser observations are paused during a protected login. Close and reopen this browser to finish login manually.", next_action="request_user")
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

        if op == "credential_bindings":
            from core.container import container
            from .._credentials import scoped_bindings
            out.data = {"bindings": await scoped_bindings(ctx, container.auth_service(), profile.id)}
            return out

        if op == "diagnose":
            out.data = await _diagnose(runtime, profile)
            return out

        prt = await runtime.open(profile, wait=min(remaining(), 300.0))
        controller = prt.controller
        if controller.task_id is not None and controller.task_id != task_id:
            return _failure(out, "BrowserBusy", "This browser profile is already assigned to another task.", next_action="wait")
        await controller.restore_assistance(session)
        if controller.sensitive_login and op != "request_user":
            return _failure(out, "sensitive_login", "Browser observations are paused during a protected login. Ask the owner to finish login and hand the browser back.", next_action="request_user")
        if op == "credential_fill":
            from .._credentials import fill_credentials
            if controller.needs_observation:
                return _failure(out, "outcome_unknown", "Inspect a fresh snapshot before attempting login.", next_action="snapshot")
            async with controller.agent_op(session, interrupt=prt.cli.interrupt):
                decision = runtime.action_guard.admit(origin=_action_origin(op, call, controller), profile_id=profile.id,
                    operation=op, arguments={"binding": call.credential_binding_id, "username_ref": call.username_ref,
                    "password_ref": call.password_ref, "submit_ref": call.submit_ref},
                    min_interval_ms=cfg.min_action_interval_ms, max_actions_per_minute=cfg.max_actions_per_minute,
                    max_repeat_actions=cfg.max_repeat_actions)
                if decision is not None:
                    return _failure(out, decision.error_type, decision.error, retry_after=decision.retry_after, next_action=decision.next_action)
                result = await fill_credentials(ctx, prt, call, timeout=min(float(cfg.op_timeout_s), remaining()))
            if result.get("success"):
                out.data = {"status": "authenticated"}
                out.notice = "Configured website login completed. Take a fresh snapshot."
                return out
            return _failure(out, result.get("error_type", "login_required"), result.get("error", "Please finish login manually."), next_action="request_user")

        if op == "request_user":
            wait = min(float(cfg.request_user_timeout_s), _TOOL_REQUEST_WAIT) if tool_call else float(cfg.request_user_timeout_s)
            message = call.message or "Please finish this step in the browser, then hand it back."
            result = await controller.request_user(session, reason=call.reason, message=message, timeout=float(cfg.request_user_timeout_s), wait=wait)
            out.handback = result
            _fill_page(out, controller)
            if result.get("status") == "still_waiting":
                out.notice = "The owner has not answered yet. Call request_user again to keep waiting."
            return out

        if controller.challenge_required:
            _fill_page(out, controller)
            return _failure(out, "challenge_required", "This browser is paused for a site challenge. The owner must take control and explicitly hand it back.", next_action="request_user")
        if controller.needs_observation and is_site_action(op, call.tab_action):
            return _failure(out, "outcome_unknown", "The previous action may already have happened. Inspect a fresh snapshot before taking another action.", next_action="snapshot")

        async with controller.agent_op(session, interrupt=prt.cli.interrupt):
            if controller.needs_observation and is_site_action(op, call.tab_action):
                return _failure(out, "outcome_unknown", "The previous action may already have happened. Inspect a fresh snapshot before taking another action.", next_action="snapshot")
            if is_site_action(op, call.tab_action):
                decision = runtime.action_guard.admit(
                    origin=_action_origin(op, call, controller), profile_id=profile.id, operation=op,
                    arguments=_action_arguments(op, call, cfg, controller), min_interval_ms=cfg.min_action_interval_ms,
                    max_actions_per_minute=cfg.max_actions_per_minute, max_repeat_actions=cfg.max_repeat_actions,
                )
                if decision is not None:
                    _fill_page(out, controller)
                    return _failure(out, decision.error_type, decision.error, retry_after=decision.retry_after, next_action=decision.next_action)
            if op == "webmcp_list":
                tools = prt.webmcp.tools(controller.active_target_id)
                out.webmcp_tools = [dict(t, callable=_webmcp_callable(t, cfg)) for t in tools]
                _fill_page(out, controller)
                return out
            if op == "webmcp_call":
                from .._webmcp import WebMcpOutcomeUnknown
                if not call.webmcp_tool:
                    raise NodeUserError("webmcp_tool is required; see webmcp_list")
                # Page-provided tools pass the same challenge gate as UI input.
                checked = await _run_cli_op(ctx, prt, controller, "page_info", BrowserToolInput(operation="page_info"), cfg, out, timeout=min(float(cfg.op_timeout_s), remaining()))
                if checked.success is False:
                    return checked
                mode = cfg.webmcp_mode
                tool_input = call.webmcp_input if isinstance(call.webmcp_input, dict) else {}
                try:
                    out.webmcp_result = await prt.webmcp.invoke(
                        controller.active_target_id, call.webmcp_tool, tool_input, frame_id=call.frame_id or None, mode=mode,
                        timeout=min(float(cfg.op_timeout_s), remaining()),
                    )
                except WebMcpOutcomeUnknown as exc:
                    controller.needs_observation = True
                    return _failure(out, "outcome_unknown", str(exc), next_action="snapshot")
                except asyncio.CancelledError:
                    controller.needs_observation = True
                    raise
                _fill_page(out, controller)
                return out
            return await _run_cli_op(ctx, prt, controller, op, call, cfg, out, timeout=min(float(cfg.op_timeout_s), remaining()))


def _webmcp_callable(tool: Dict[str, Any], cfg: BrowserParams) -> bool:
    if cfg.interaction == "read_only" or cfg.webmcp_mode == "disabled":
        return False
    mode = cfg.webmcp_mode
    return mode == "all" or (mode == "read_only" and bool(tool.get("read_only")))


def _failure(out: BrowserOutput, kind: str, message: str, *, retry_after: Optional[float] = None, next_action: str = "snapshot") -> BrowserOutput:
    out.success, out.error_type, out.error = False, kind, message
    out.retry_after, out.next_action = retry_after, next_action
    return out


def _action_origin(op: str, call: BrowserToolInput, controller: Any) -> str:
    tab = controller.tabs.get(controller.active_target_id or "") or {}
    url = call.url if op == "navigate" or (op == "tabs" and call.tab_action == "new") else str(tab.get("url") or "")
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{(parts.hostname or '').lower()}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def _action_arguments(op: str, call: BrowserToolInput, cfg: BrowserParams, controller: Any) -> Dict[str, Any]:
    # Only values actually used by this operation participate. Changing an
    # irrelevant field such as max_chars cannot evade repeat suppression.
    fields = {
        "navigate": ("url",), "click": ("ref", "selector", "x", "y"), "hover": ("ref", "selector", "x", "y"),
        "type": ("ref", "selector", "text", "clear", "submit"), "select": ("ref", "selector", "values"),
        "press": ("key", "modifiers"), "scroll": ("ref", "selector", "direction", "amount"),
        "tabs": ("tab_action", "tab_id", "url"), "webmcp_call": ("webmcp_tool", "webmcp_input", "frame_id"),
    }.get(op, ())
    args = {name: getattr(call, name) for name in fields}
    if op == "evaluate":
        args["expression"] = cfg.expression
    if op == "run_python":
        args["code"] = cfg.code
    args["target"] = controller.active_target_id
    # Navigation counts the destination, not its changing source page.
    if op != "navigate":
        args["page"] = (controller.tabs.get(controller.active_target_id or "") or {}).get("url")
    return args


def _fill_page(out: BrowserOutput, controller: Any) -> None:
    if getattr(controller, "sensitive_login", False):
        return
    tab = controller.tabs.get(controller.active_target_id or "") or {}
    out.tab_id = controller.active_target_id
    out.url = out.url or tab.get("url")
    out.title = out.title or tab.get("title")


def _cli_args(op: str, call: BaseModel, cfg: BrowserParams, controller: Any) -> Dict[str, Any]:
    args: Dict[str, Any] = {"_target_id": controller.active_target_id, "_guard_actions": is_site_action(op, getattr(call, "tab_action", "list"))}
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
    from .._cli import BrowserStepOutcomeUnknown, BrowserStepTimeout, BrowserUnavailable

    script_op = {"back": "history", "forward": "history", "reload": "history"}.get(op, op)
    if op in ("evaluate", "run_python") and not (cfg.expression if op == "evaluate" else cfg.code).strip():
        raise NodeUserError(f"{'expression' if op == 'evaluate' else 'code'} is required for {op}")
    args = _cli_args(op, call, cfg, controller)
    args["timeout"] = max(1.0, timeout - 1.0)
    retry_safe = is_retry_safe(op, getattr(call, "tab_action", "list"))
    if script_op == "history":
        args["history_action"] = op
    shot: Optional[Path] = None
    if op == "screenshot":
        shot = prt.cli.dirs()["tmp"] / f"shot-{uuid.uuid4().hex[:12]}.png"
        args["path"] = str(shot)

    try:
        try:
            result = await prt.cli.run(script_op, args, timeout=timeout)
        except BrowserUnavailable:
            if not retry_safe:
                raise
            # Only an observation may be replayed after losing its reply.
            prt.cli.stop_daemon()
            result = await prt.cli.run(script_op, args, timeout=timeout)
    except (BrowserUnavailable, BrowserStepOutcomeUnknown) as exc:
        if not retry_safe:
            prt.cli.stop_daemon()
            controller.needs_observation = True
            return _failure(out, "outcome_unknown", str(exc), next_action="snapshot")
        kind = "timeout" if isinstance(exc, BrowserStepTimeout) else "browser_unavailable" if isinstance(exc, BrowserUnavailable) else "outcome_unknown"
        return _failure(out, kind, str(exc), next_action="snapshot")
    except asyncio.CancelledError:
        if not retry_safe:
            controller.needs_observation = True
        raise

    if result.page and result.page.get("target_id"):
        controller.active_target_id = result.page["target_id"]
        tab = controller.tabs.setdefault(result.page["target_id"], {"target_id": result.page["target_id"]})
        if tab.get("url") != result.page.get("url") or op in ("navigate", "back", "forward", "reload"):
            controller.refs.pop(result.page["target_id"], None)
        tab.update(url=result.page.get("url"), title=result.page.get("title"))
        await controller.emit("page", dict(result.page))
    if result.page:
        out.url, out.title, out.tab_id = result.page.get("url"), result.page.get("title"), result.page.get("target_id")

    if not result.ok:
        kind = result.error_type or "script"
        message = result.error or f"The browser step '{op}' failed"
        if kind == "challenge_required":
            await controller.pause_for_challenge(controller.lease_session, message=message, timeout=float(cfg.request_user_timeout_s))
            return _failure(out, kind, message, next_action="request_user")
        if not retry_safe and kind in ("timeout", "cdp", "script"):
            controller.needs_observation = True
            return _failure(out, "outcome_unknown", message + " Inspect a fresh snapshot before another action.", next_action="snapshot")
        return _failure(out, kind, message, next_action="snapshot")

    if op in ("snapshot", "page_info", "page_text"):
        controller.needs_observation = False

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
