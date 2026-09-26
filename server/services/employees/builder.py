"""The graph behind a hired employee. Pure: no I/O; ``hire.py`` gathers the
inputs and saves the result.

One employee is one workflow:

- exactly one trigger: the app that starts the work (a new WhatsApp
  message, a new email), a schedule (cronScheduler), or the owner's Chat;
- one agent (aiAgent) labelled with the employee's name, carrying the
  standing instructions (prompt.py) and a per-run prompt that points at the
  trigger's output;
- tools on the agent: web search, a checklist (writeTodos), a clock and a
  canvas (what the agent puts there shows in Home's Workspace), always; the
  apps' tools, minus anything that sends or spends while "ask me first" is
  on (a tool with ``ask_first_params``, the browser, stays in its read-only
  form instead); Memory when the owner keeps memory across chats;
- the owner's skill library (Settings > Skills): every skill that is on, on
  one Skills node (masterSkill), with its text copied in, so a later edit to
  the library never changes an employee already hired;
- a Context only for owner-facing triggers (schedule, Chat): a public
  trigger talks to many strangers, and one shared conversation would mix
  them;
- delivery: an inbound message from outside is answered through the same
  app, behind the approval gate when "ask me first" is on (no gate, no
  reply: it fails closed); schedule work reports to the owner through the
  app the setup named, else the first connected app that can reach them.
  An answer of exactly NO_REPLY sends nothing;
- an "Activity log" console node that shows every answer in the editor.

The recipient of a reply always comes from this run's trigger (through the
gate when there is one), never from the agent's text; the reply node and
the gate are wired to the trigger so they read this run's output.

Node types come from the app registry and this module, never from the
hire payload, and every one must pass the Hire allowlist. Ids are
canonical (``<workflow_id>:<type>:<n>``) from the start.

Parameter templates: the registry writes ``${trigger.<field>}``,
``${reply_text}``, ``${reply_subject}`` and ``${owner.<field>}``; they
become ``{{<label key>.<field>}}`` references (a node's label, lowercased,
whitespace removed) or the owner's own address. A trigger or delivery whose
owner value is unknown is left out, with a warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.employees.apps import AppSpec, NodeTemplate, ToolTemplate, allowed_when_asking_first, resolve_app
from services.employees.hire_request import HireEmployeeRequest, HireTrigger
from services.employees.llm import LLMChoice
from services.approvals.contract import APPROVAL_GATE_TYPE, NO_REPLY, approved_edge_condition, send_condition
from services.employees.prompt import OwnerProfile, PromptInputs, build_system_message
from services.skill_runtime import is_personality_skill

BUILDER_VERSION = 1

AGENT_TYPE = "aiAgent"
CHAT_TRIGGER_TYPE = "chatTrigger"
SCHEDULE_TYPE = "cronScheduler"
GATE_TYPE = APPROVAL_GATE_TYPE
CONSOLE_TYPE = "console"
CONTEXT_TYPE = "context"
SKILLS_TYPE = "masterSkill"
MEMORY_TYPE = "simpleMemory"
SEARCH_TYPE = "duckduckgoSearch"
TODOS_TYPE = "writeTodos"
CLOCK_TYPE = "currentTimeTool"
CANVAS_TYPE = "canvas"

#: cronScheduler's allowed times and zones (nodes/scheduler/cron_scheduler).
SCHEDULE_TIMES = ("00:00", "02:00", "04:00", "06:00", "08:00", "09:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00")
SCHEDULE_ZONES = ("UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Tokyo", "Asia/Kolkata")
WEEKDAYS = ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")

#: The run did not produce "nothing to send".
SEND_CONDITION: Dict[str, Any] = send_condition()
#: The owner pressed Send on the draft.
APPROVED_CONDITION: Dict[str, Any] = approved_edge_condition()

_PLACEHOLDER = re.compile(r"\$\{([a-z_]+)(?:\.([a-z_]+))?\}")

#: Per reply node: where it keeps the recipient, which trigger field names
#: the person (for the draft card), its message field and that field's
#: length limit for an edited draft.
_REPLY_FIELDS: Dict[str, Tuple[str, str, str, int]] = {
    "whatsappSend": ("phone", "push_name", "message", 4096),
    "whatsappBusinessSend": ("to", "profile_name", "text", 4096),
    "telegramSend": ("chat_id", "from_first_name", "text", 4096),
    "discordSend": ("channel_id", "author_display_name", "message", 2000),
    "googleGmail": ("to", "from", "body", 20000),
    "msMail": ("reply_message_id", "from_name", "comment", 20000),
}
_HAS_SUBJECT = frozenset({"googleGmail"})
_MAIL_APPS = frozenset({"gmail", "outlook", "email"})


class BuildError(ValueError):
    """The employee cannot be built. ``code`` is the hire error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LibrarySkill:
    """A skill from the owner's library that is on for new hires."""

    name: str
    description: str
    instructions: str


#: The Skill tool's own entry on an assistant Skills node (MasterSkillParams).
_SKILL_TOOL_ENTRY: Dict[str, Any] = {"enabled": True, "instructions": "", "isCustomized": False, "required": True}


def _skills_config(skills: Sequence[LibrarySkill], warnings: List[str]) -> Optional[Dict[str, Any]]:
    """The Skills node's ``skills_config``: the Skill tool's entry, then each
    library skill with its text. None when no skill qualifies (no node).

    A skill named ``skill`` would take over the Skill tool's own entry, and a
    ``*-personality`` skill would replace the whole system message (and with
    it the rules prompt.py writes), so neither is given to a hire.
    """
    config: Dict[str, Any] = {"skill": dict(_SKILL_TOOL_ENTRY)}
    for skill in skills:
        name = skill.name.strip()
        if not name or not skill.instructions.strip():
            continue
        if name in config or is_personality_skill(name):
            warnings.append(f"The skill {name!r} can't be given to a hired employee")
            continue
        config[name] = {"enabled": True, "instructions": skill.instructions, "isCustomized": False, "description": skill.description}
    return config if len(config) > 1 else None


@dataclass
class BuildInputs:
    workflow_id: str
    request: HireEmployeeRequest
    #: The hire's apps the registry knows, in the order the setup named them.
    apps: Sequence[AppSpec]
    unsupported_apps: Sequence[str] = ()
    connected_app_ids: Set[str] = field(default_factory=set)
    owner: OwnerProfile = field(default_factory=OwnerProfile)
    #: Values for ${owner.<field>}: google_email, microsoft_email,
    #: email_provider, email_address.
    owner_values: Mapping[str, str] = field(default_factory=dict)
    timezone: str = "UTC"
    llm: Optional[LLMChoice] = None
    memory: bool = True
    #: Settings > Skills: the library skills that are on.
    skills: Sequence[LibrarySkill] = ()
    #: Hire allowlist (services.node_allowlist.is_hire_allowed).
    allowed: Callable[[str], bool] = lambda _node_type: True
    now: Optional[datetime] = None


@dataclass
class BuiltEmployee:
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    parameters: Dict[str, Dict[str, Any]]
    #: {"trigger", "agent", "gate"?, "reply"?, "notify"?, "todos", "console", ...}: node ids.
    node_roles: Dict[str, str]
    #: What starts the work, as stored on the employee: {kind, app?, every?, at?, day?}.
    trigger: Dict[str, Any]
    delivery: str
    delivery_app: Optional[str]
    #: Apps whose nodes the graph uses.
    app_ids: List[str]
    warnings: List[str]


def label_key(label: str) -> str:
    """The template key of a node label (services/parameter_resolver.py)."""
    return re.sub(r"\s+", "", label.lower())


def ref(key: str, field_name: str) -> str:
    return "{{" + f"{key}.{field_name}" + "}}"


class _Unresolved(Exception):
    """A template needs a value nobody supplied."""


def _substitute(value: Any, refs: Mapping[str, str], trigger_key: Optional[str], owner_values: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _substitute(item, refs, trigger_key, owner_values) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, refs, trigger_key, owner_values) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: "re.Match[str]") -> str:
        scope, name = match.group(1), match.group(2)
        if scope == "trigger" and name and trigger_key is not None:
            return ref(trigger_key, name)
        if scope == "owner" and name:
            owned = str(owner_values.get(name) or "").strip()
            if owned:
                return owned
        if scope in refs and not name:
            return refs[scope]
        raise _Unresolved(match.group(0))

    return _PLACEHOLDER.sub(replace, value)


class _Labels:
    """Unique labels, so no two nodes share a template key (they would read
    each other's output)."""

    def __init__(self) -> None:
        self._keys: Set[str] = set()

    def take(self, label: str) -> str:
        candidate, n = label, 2
        while label_key(candidate) in self._keys:
            candidate = f"{label} {n}"
            n += 1
        self._keys.add(label_key(candidate))
        return candidate


class _Graph:
    def __init__(self, workflow_id: str, allowed: Callable[[str], bool]):
        self.workflow_id = workflow_id
        self.allowed = allowed
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.parameters: Dict[str, Dict[str, Any]] = {}
        self._counts: Dict[str, int] = {}

    def add(self, node_type: str, label: str, params: Dict[str, Any], position: Tuple[int, int], data: Optional[Dict[str, Any]] = None) -> str:
        if not self.allowed(node_type):
            raise BuildError("not_allowed", f"{node_type} is not available to hired employees")
        n = self._counts.get(node_type, 0) + 1
        self._counts[node_type] = n
        node_id = f"{self.workflow_id}:{node_type}:{n}"
        self.nodes.append(
            {"id": node_id, "type": node_type, "position": {"x": position[0], "y": position[1]}, "data": {"label": label, **(data or {})}}
        )
        if params:
            self.parameters[node_id] = dict(params)
        return node_id

    def connect(self, source: str, source_handle: str, target: str, target_handle: str, condition: Optional[Dict[str, Any]] = None) -> None:
        edge: Dict[str, Any] = {
            "id": f"e-{source}-{source_handle}-{target}-{target_handle}",
            "source": source,
            "sourceHandle": source_handle,
            "target": target,
            "targetHandle": target_handle,
        }
        if condition:
            edge["data"] = {"condition": dict(condition)}
        self.edges.append(edge)


# ----- the trigger -----


def _app_named(name: Optional[str], inputs: BuildInputs, *, with_trigger: bool) -> Optional[AppSpec]:
    """The hire's app a free-text name means ("email" is the hire's mail app)."""
    if not name:
        return None
    candidates = [app for app in inputs.apps if app.name.lower() == name.strip().lower()]
    match = resolve_app(name, sorted(inputs.connected_app_ids))
    if match is not None:
        candidates.append(match)
    for app in candidates:
        if app.trigger is not None or not with_trigger:
            return app
    return None


def _choose_trigger(inputs: BuildInputs) -> Tuple[str, Optional[AppSpec]]:
    """("app_event", app) | ("schedule", None) | ("manual", None)."""
    request = inputs.request
    requested = request.trigger
    if requested is not None and requested.kind == "app_event":
        app = _app_named(requested.app, inputs, with_trigger=True)
        if app is not None:
            return "app_event", app
    if requested is not None and requested.kind in ("schedule", "manual"):
        return requested.kind, None
    for step in request.steps:
        if step.role == "trigger" and step.app:
            app = _app_named(step.app, inputs, with_trigger=True)
            if app is not None:
                return "app_event", app
    for app in inputs.apps:
        if app.trigger is not None:
            return "app_event", app
    return "manual", None


def _nearest_time(at: Optional[str]) -> str:
    """The allowed time closest to ``at``; between two equally close, the
    earlier one (a briefing an hour early beats one an hour late)."""
    if not at or not re.match(r"^\d{2}:\d{2}$", at):
        return "09:00"
    wanted = int(at[:2]) * 60 + int(at[3:])

    def distance(slot: str) -> Tuple[int, bool]:
        after = (int(slot[:2]) * 60 + int(slot[3:]) - wanted) % (24 * 60)
        gap = min(after, 24 * 60 - after)
        return gap, 0 < after <= 12 * 60

    return min(SCHEDULE_TIMES, key=distance)


def _schedule_zone(zone_name: str, now: datetime) -> Tuple[str, int]:
    """A zone cronScheduler accepts, and the minutes to add to the owner's
    local time to say the same moment there. The owner's own zone when it
    is on the list, else one with the same offset right now, else UTC."""
    if zone_name in SCHEDULE_ZONES:
        return zone_name, 0
    try:
        owner_zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return "UTC", 0
    offset = now.astimezone(owner_zone).utcoffset()
    for candidate in SCHEDULE_ZONES:
        if now.astimezone(ZoneInfo(candidate)).utcoffset() == offset:
            return candidate, 0
    return "UTC", -int(offset.total_seconds() // 60) if offset else 0


def _shift(at: str, minutes: int) -> str:
    total = (int(at[:2]) * 60 + int(at[3:]) + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def schedule_params(trigger: Optional[HireTrigger], zone_name: str, now: datetime) -> Dict[str, Any]:
    """cronScheduler parameters for a hire's schedule, in the owner's zone.
    "Every weekday" runs daily; the instructions tell the employee to rest
    at weekends, since cronScheduler has no weekday-only frequency."""
    every = trigger.every if trigger is not None else None
    zone, shift = _schedule_zone(zone_name, now)
    wanted = trigger.at if trigger is not None and trigger.at else None
    at = _nearest_time(_shift(wanted, shift) if wanted and shift else wanted)
    if every == "hour":
        return {"frequency": "hours", "interval_hours": 1, "timezone": zone}
    if every == "week":
        day = (trigger.day or "").strip().lower() if trigger is not None else ""
        weekday = str(WEEKDAYS.index(day)) if day in WEEKDAYS else "1"
        return {"frequency": "weeks", "weekday": weekday, "weekly_time": at, "timezone": zone}
    if every == "month":
        day = (trigger.day or "").strip() if trigger is not None else ""
        month_day = day if day == "L" or (day.isdigit() and 1 <= int(day) <= 28) else "1"
        return {"frequency": "months", "month_day": month_day, "monthly_time": at, "timezone": zone}
    return {"frequency": "days", "daily_time": at, "timezone": zone}


@dataclass
class _TriggerPlan:
    kind: str
    node_type: str
    label: str
    params: Dict[str, Any]
    prompt: str
    record: Dict[str, Any]
    app: Optional[AppSpec] = None
    public: bool = False

    @property
    def key(self) -> str:
        return label_key(self.label)


def _plan_trigger(inputs: BuildInputs, labels: _Labels, now: datetime, warnings: List[str]) -> _TriggerPlan:
    request = inputs.request
    kind, app = _choose_trigger(inputs)
    if kind == "app_event" and app is not None and app.trigger is not None:
        label = labels.take(app.phrase("when", f"New {app.name} message"))
        try:
            params = _substitute(dict(app.trigger.params), {}, label_key(label), inputs.owner_values)
            prompt = _substitute(app.trigger.prompt, {}, label_key(label), inputs.owner_values) or ref(label_key(label), "text")
        except _Unresolved as missing:
            warnings.append(f"{app.name} cannot start them yet ({missing}); they answer in Chat instead")
        else:
            return _TriggerPlan(
                kind="app_event",
                node_type=app.trigger.type,
                label=label,
                params=params,
                prompt=prompt,
                record={"kind": "app_event", "app": app.id},
                app=app,
                public=app.trigger.audience == "public",
            )
        kind = "manual"
    if kind == "schedule":
        from services.employees.summaries import schedule_text

        requested = request.trigger.model_dump(exclude_none=True) if request.trigger is not None else {}
        label = labels.take("Schedule")
        return _TriggerPlan(
            kind="schedule",
            node_type=SCHEDULE_TYPE,
            label=label,
            params=schedule_params(request.trigger, inputs.timezone, now),
            prompt=f"It is time for your routine ({schedule_text(requested).lower()}). Do it now, then report.",
            record={"kind": "schedule", **{k: v for k, v in requested.items() if k in ("every", "at", "day")}},
        )
    label = labels.take("Chat")
    return _TriggerPlan(
        kind="manual",
        node_type=CHAT_TRIGGER_TYPE,
        label=label,
        params={"session_id": inputs.workflow_id},
        prompt=ref(label_key(label), "message"),
        record={"kind": "manual"},
    )


# ----- delivery -----


@dataclass
class _DeliveryPlan:
    role: str  # "reply" | "notify"
    app: AppSpec
    node_type: str
    label: str
    params: Dict[str, Any]
    gate_label: Optional[str] = None
    gate_params: Optional[Dict[str, Any]] = None


def _reporting_app(inputs: BuildInputs) -> Optional[AppSpec]:
    """The app that carries reports to the owner: the one the setup said it
    sends through, else the first connected app that can reach the owner."""
    via = _app_named(inputs.request.sends_via, inputs, with_trigger=False)
    if via is not None and via.notify_owner is not None:
        return via
    for app in inputs.apps:
        if app.notify_owner is not None and app.id in inputs.connected_app_ids:
            return app
    return None


def _plan_delivery(inputs: BuildInputs, trigger: _TriggerPlan, agent_key: str, labels: _Labels, warnings: List[str]) -> Optional[_DeliveryPlan]:
    if trigger.kind == "manual":
        return None  # the owner reads the answer in Chat
    if trigger.public and trigger.app is not None:
        app = trigger.app
        if app.reply is None:
            reporter = _reporting_app(inputs)
            if reporter is None:
                return None
            return _plan_notify(inputs, reporter, trigger, agent_key, labels, warnings)
        return _plan_reply(inputs, app, app.reply, trigger, agent_key, labels, warnings)
    reporter = _reporting_app(inputs)
    if reporter is None:
        return None
    return _plan_notify(inputs, reporter, trigger, agent_key, labels, warnings)


def _plan_notify(
    inputs: BuildInputs, app: AppSpec, trigger: _TriggerPlan, agent_key: str, labels: _Labels, warnings: List[str]
) -> Optional[_DeliveryPlan]:
    template = app.notify_owner
    assert template is not None
    subject = f"Update from {inputs.request.name}"
    try:
        params = _substitute(
            dict(template.params), {"reply_text": ref(agent_key, "response"), "reply_subject": subject}, trigger.key, inputs.owner_values
        )
    except _Unresolved as missing:
        warnings.append(f"Reports cannot go out through {app.name} yet ({missing}); they stay in the activity log")
        return None
    return _DeliveryPlan(role="notify", app=app, node_type=template.type, label=labels.take(f"Report on {app.name}"), params=params)


def _plan_reply(
    inputs: BuildInputs,
    app: AppSpec,
    template: NodeTemplate,
    trigger: _TriggerPlan,
    agent_key: str,
    labels: _Labels,
    warnings: List[str],
) -> Optional[_DeliveryPlan]:
    fields = _REPLY_FIELDS.get(template.type)
    subject = ref(trigger.key, "subject")
    subject = f"Re: {subject}" if app.id in _MAIL_APPS else f"Reply from {inputs.request.name}"
    if not inputs.request.rules.ask_first:
        try:
            params = _substitute(
                dict(template.params), {"reply_text": ref(agent_key, "response"), "reply_subject": subject}, trigger.key, inputs.owner_values
            )
        except _Unresolved as missing:
            warnings.append(f"Replies cannot go out through {app.name} yet ({missing})")
            return None
        return _DeliveryPlan(role="reply", app=app, node_type=template.type, label=labels.take(f"Reply on {app.name}"), params=params)

    # Asking first: the reply waits behind the gate, or does not happen.
    if fields is None:
        warnings.append(f"{app.name} replies cannot wait for your approval yet, so they are off")
        return None
    recipient_field, name_field, body_field, max_length = fields
    try:
        recipient = _substitute(template.params.get(recipient_field), {}, trigger.key, inputs.owner_values)
    except _Unresolved:
        warnings.append(f"{app.name} replies cannot find who to answer, so they are off")
        return None
    gate_label = labels.take("Check before sending")
    gate_key = label_key(gate_label)
    gate_params: Dict[str, Any] = {
        "channel": app.name,
        "recipient": recipient,
        "recipient_label": ref(trigger.key, name_field),
        "draft": ref(agent_key, "response"),
        "context_excerpt": ref(trigger.key, "subject" if app.id in _MAIL_APPS else "text"),
        "max_length": max_length,
    }
    if template.type in _HAS_SUBJECT:
        gate_params["subject"] = subject
    try:
        params = _substitute(
            dict(template.params),
            {"reply_text": ref(gate_key, "text"), "reply_subject": ref(gate_key, "subject")},
            trigger.key,
            inputs.owner_values,
        )
    except _Unresolved as missing:
        warnings.append(f"Replies cannot go out through {app.name} yet ({missing})")
        return None
    # Who it goes to is what the gate took from this run's trigger.
    params[recipient_field] = ref(gate_key, "recipient")
    params[body_field] = ref(gate_key, "text")
    return _DeliveryPlan(
        role="reply",
        app=app,
        node_type=template.type,
        label=labels.take(f"Reply on {app.name}"),
        params=params,
        gate_label=gate_label,
        gate_params=gate_params,
    )


# ----- app tools -----


@dataclass
class _ToolPlan:
    app: AppSpec
    tool: ToolTemplate
    params: Dict[str, Any]
    #: Attached in its ask-first form (``ask_first_params`` applied).
    read_only: bool = False


def _plan_app_tools(inputs: BuildInputs, trigger: _TriggerPlan, warnings: List[str]) -> List[_ToolPlan]:
    """The apps' tools the agent gets, decided before its instructions are
    written so they can mention them.

    Under ``ask first`` a tool that can send or spend is left out, unless
    the app declares ``ask_first_params`` that make it safe: then it is
    attached with those applied (the browser can read, and hands any page
    change to the owner)."""
    plans: List[_ToolPlan] = []
    for app in inputs.apps:
        for tool in app.tools:
            params = dict(tool.params)
            read_only = False
            if inputs.request.rules.ask_first and not allowed_when_asking_first(tool.side_effects):
                if not tool.ask_first_params:
                    warnings.append(f"{app.name} is left out while they ask before sending anything")
                    continue
                params.update(tool.ask_first_params)
                read_only = True
                warnings.append(f"{app.name} can only read while they ask before sending anything; they hand changes to you")
            try:
                params = _substitute(params, {}, trigger.key, inputs.owner_values)
            except _Unresolved:
                warnings.append(f"{app.name} could not be set up as a tool")
                continue
            plans.append(_ToolPlan(app=app, tool=tool, params=params, read_only=read_only))
    return plans


# ----- the whole graph -----


def build_employee_graph(inputs: BuildInputs) -> BuiltEmployee:
    request = inputs.request
    now = inputs.now or datetime.now(timezone.utc)
    labels = _Labels()
    warnings: List[str] = []

    agent_label = labels.take(request.name)
    agent_key = label_key(agent_label)
    trigger = _plan_trigger(inputs, labels, now, warnings)
    delivery = _plan_delivery(inputs, trigger, agent_key, labels, warnings)

    app_tools = _plan_app_tools(inputs, trigger, warnings)
    browser_tools = [plan for plan in app_tools if plan.tool.role == "browser"]

    delivery_mode = "chat" if trigger.kind == "manual" else ("reply" if delivery is not None and delivery.role == "reply" else "report")
    system_message = build_system_message(
        PromptInputs(
            request=request,
            owner=inputs.owner,
            delivery=delivery_mode,
            delivery_app=delivery.app.name if delivery is not None else None,
            unsupported_apps=list(inputs.unsupported_apps),
            has_memory=inputs.memory,
            has_canvas=True,
            has_browser=bool(browser_tools),
            browser_read_only=bool(browser_tools) and all(plan.read_only for plan in browser_tools),
        )
    )
    if trigger.kind == "schedule" and request.trigger is not None and request.trigger.every == "weekday":
        system_message += f"\n- You work on weekdays only. On a Saturday or Sunday, answer exactly {NO_REPLY}."

    graph = _Graph(inputs.workflow_id, inputs.allowed)
    roles: Dict[str, str] = {}
    used_apps: List[str] = []

    def use(app: Optional[AppSpec]) -> None:
        if app is not None and app.id not in used_apps:
            used_apps.append(app.id)

    roles["trigger"] = graph.add(trigger.node_type, trigger.label, trigger.params, (0, 200))
    use(trigger.app)
    roles["agent"] = graph.add(
        AGENT_TYPE,
        agent_label,
        {
            "prompt": trigger.prompt,
            "system_message": system_message,
            "provider": inputs.llm.provider if inputs.llm is not None else "openai",
            "model": inputs.llm.model if inputs.llm is not None else "",
        },
        (360, 200),
    )
    graph.connect(roles["trigger"], "output-main", roles["agent"], "input-main")

    # Tools.
    tool_x = 120

    def add_tool(node_type: str, label: str, params: Dict[str, Any], role: Optional[str] = None) -> None:
        nonlocal tool_x
        node_id = graph.add(node_type, labels.take(label), params, (tool_x, 440))
        tool_x += 170
        graph.connect(node_id, "output-tool", roles["agent"], "input-tools")
        if role:
            roles[role] = node_id

    add_tool(SEARCH_TYPE, "Web search", {"max_results": 5})
    add_tool(TODOS_TYPE, "Checklist", {}, role="todos")
    add_tool(CLOCK_TYPE, "Clock", {"timezone": inputs.timezone or "UTC"})
    if inputs.memory:
        add_tool(MEMORY_TYPE, "Memory", {}, role="memory")
    add_tool(CANVAS_TYPE, "Canvas", {}, role="canvas")
    for plan in app_tools:
        add_tool(plan.tool.type, plan.tool.label or plan.app.name, plan.params, role=plan.tool.role)
        use(plan.app)

    # Context, for owner-facing triggers only.
    if trigger.kind in ("schedule", "manual"):
        roles["context"] = graph.add(
            CONTEXT_TYPE, labels.take("Context"), {}, (360, 20), data={"systemManaged": True, "agentNodeId": roles["agent"]}
        )
        graph.connect(roles["context"], "output-context", roles["agent"], "input-context")

    # Skills from the owner's library.
    skills_config = _skills_config(inputs.skills, warnings)
    if skills_config is not None:
        roles["skills"] = graph.add(
            SKILLS_TYPE, labels.take("Skills"), {"skill_folder": "assistant", "skills_config": skills_config}, (-60, 440)
        )
        graph.connect(roles["skills"], "output-tool", roles["agent"], "input-skill")

    # Delivery.
    if delivery is not None:
        upstream, condition = roles["agent"], SEND_CONDITION
        if delivery.gate_params is not None and delivery.gate_label is not None:
            roles["gate"] = graph.add(GATE_TYPE, delivery.gate_label, delivery.gate_params, (720, 200))
            graph.connect(roles["agent"], "output-main", roles["gate"], "input-main", SEND_CONDITION)
            # The gate reads this run's trigger for who the reply goes to.
            graph.connect(roles["trigger"], "output-main", roles["gate"], "input-main")
            upstream, condition = roles["gate"], APPROVED_CONDITION
        x = 1080 if "gate" in roles else 720
        roles[delivery.role] = graph.add(delivery.node_type, delivery.label, delivery.params, (x, 200))
        graph.connect(upstream, "output-main", roles[delivery.role], "input-main", condition)
        if delivery.role == "reply":
            # The reply reads this run's trigger (the message it answers); a
            # plain edge adds no condition, so the gate's still decides.
            graph.connect(roles["trigger"], "output-main", roles["reply"], "input-main")
        use(delivery.app)

    # Activity log.
    roles["console"] = graph.add(
        CONSOLE_TYPE,
        labels.take("Activity log"),
        {"log_mode": "field", "field_path": ref(agent_key, "response"), "format": "text"},
        (720, 420),
    )
    graph.connect(roles["agent"], "output-main", roles["console"], "input-main")

    return BuiltEmployee(
        nodes=graph.nodes,
        edges=graph.edges,
        parameters=graph.parameters,
        node_roles=roles,
        trigger=trigger.record,
        delivery=delivery_mode,
        delivery_app=delivery.app.name if delivery is not None else None,
        app_ids=used_apps,
        warnings=list(dict.fromkeys(warnings)),
    )


__all__ = [
    "APPROVED_CONDITION",
    "BUILDER_VERSION",
    "BuildError",
    "BuildInputs",
    "BuiltEmployee",
    "LibrarySkill",
    "SEND_CONDITION",
    "build_employee_graph",
    "label_key",
    "ref",
    "schedule_params",
]
