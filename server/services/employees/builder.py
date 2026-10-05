"""The graph behind a hired employee. Pure: no I/O; ``hire.py`` gathers the
inputs and saves the result.

One employee is one workflow:

- one trigger for the work: the app that starts it (a new WhatsApp
  message, a new email), a schedule (cronScheduler), or the owner's Chat;
  the schedule is recorded as it will run, in the owner's time, and a
  warning says so when that is not what they asked for;
- one agent (aiAgent) labelled with the employee's name, carrying the
  standing instructions (prompt.py) and a per-run prompt that points at the
  trigger's output;
- tools on the agent: web search, a checklist (writeTodos), a clock and a
  canvas (what the agent puts there shows in Home's Workspace), always; the
  apps' tools: one whose plugin declares an approval spec is attached as it
  is (while "ask me first" is on, each call that sends waits as a draft and
  the browser runs read-only); one without a spec that sends or spends is
  left out while "ask me first" is on, unless the app declares
  ``ask_first_params`` that make it safe; Memory when the owner keeps memory
  across chats. Which tools and skills a hire may have is policy.py's rule,
  the same one that governs what is added to an employee later;
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
- an "Activity log" console node that shows every answer in the editor;
- Talk, where the owner talks to them on Home (talk.py): a Chat hire's
  agent answers there through "Reply in Chat"; any other hire gets a talk
  line beside its work (a "Talk" trigger, a "Talk with <name>" agent on the
  same model sharing the worker's tools and skills, its own Context, and
  its reply), and a schedule worker's reports also go to Talk ("Post to
  Talk"). The agent that answers the owner gets the Agent Builder tool, to
  add tools and skills when the owner asks (a worker strangers write to
  never has it).

The recipient of a reply always comes from this run's trigger (through the
gate when there is one), never from the agent's text; the reply node and
the gate are wired to the trigger so they read this run's output.

Node types come from the app registry and this module, never from the
hire payload, and every one must pass the Hire allowlist. Ids are
canonical (``<workflow_id>:<type>:<n>``) from the start; labels, ids and
edges come from services/graph_build.py, shared with every server-side
graph writer.

Parameter templates: the registry writes ``${trigger.<field>}``,
``${reply_text}``, ``${reply_subject}`` and ``${owner.<field>}``; they
become ``{{<label key>.<field>}}`` references (a node's label, lowercased,
whitespace removed) or the owner's own address. The owner's address for an
app comes from connecting that app, so a trigger, delivery or tool whose
owner value is unknown is left out with a warning that says to connect it.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.employees.apps import AppSpec, NodeTemplate, ToolTemplate, resolve_app
from services.employees.hire_request import HireEmployeeRequest, HireTrigger
from services.employees.llm import LLMChoice
from services.approvals.contract import APPROVAL_GATE_TYPE, NO_REPLY, approved_edge_condition, send_condition
from services.employees.policy import BASE_TOOLS, SKILL_TOOL_ENTRY, SKILL_TOOL_NAME, check_skill, check_tool
from services.employees.prompt import Delivery, OwnerProfile, PromptInputs, build_system_message
from services.employees.talk import TalkAgent, TalkTool, plan_talk_line, talk_agent_label, talk_state
from services.graph_build import (
    CONTEXT_TYPE,
    Edge,
    GraphAdditions,
    Labels,
    NodeIds,
    add_to_graph,
    context_data,
    context_edge,
    graph_node,
    label_key,
    main_edge,
    ref,
    skill_edge,
    tool_edge,
)

BUILDER_VERSION = 4
#: The first builder whose graphs follow the live Ask first rule (every app
#: reply behind a gate, sending tools attached and held per call). Older
#: graphs need Apply for a changed rule to take full effect.
LIVE_RULE_BUILDER_VERSION = 3
#: Generated UI in the chat: a talk tool every hire's talk agent gets.
CHAT_UI_TYPE = "chatUi"
CHAT_UI_LABEL = "Show in chat"
#: The approval gate's label in front of an app reply.
GATE_LABEL = "Check before sending"

AGENT_TYPE = "aiAgent"
CHAT_TRIGGER_TYPE = "chatTrigger"
SCHEDULE_TYPE = "cronScheduler"
GATE_TYPE = APPROVAL_GATE_TYPE
CONSOLE_TYPE = "console"
SKILLS_TYPE = "masterSkill"
MEMORY_TYPE = "simpleMemory"
CLOCK_TYPE = "currentTimeTool"

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


def _skills_config(skills: Sequence[LibrarySkill], warnings: List[str]) -> Optional[Dict[str, Any]]:
    """The Skills node's ``skills_config``: the Skill tool's entry, then each
    library skill with its text. None when no skill qualifies (no node): a
    node holding only the Skill tool's entry would give the agent a Skill
    tool whose one skill is the instructions for using it.

    A skill policy.check_skill refuses (``skill``, the Skill tool's own
    entry; a ``*-personality`` skill, which would replace the whole system
    message) is not given to a hire, and neither is a second skill of a
    name already given.
    """
    config: Dict[str, Any] = {SKILL_TOOL_NAME: dict(SKILL_TOOL_ENTRY)}
    for skill in skills:
        name = skill.name.strip()
        if not name or not skill.instructions.strip():
            continue
        if name in config or not check_skill(name).allowed:
            warnings.append(f'The skill "{name}" can\'t be given to a hired employee')
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
    #: Opt-in template team. Legacy graphs remain available for compatibility.
    team: bool = False


@dataclass
class BuiltEmployee:
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    parameters: Dict[str, Dict[str, Any]]
    #: {"trigger", "agent", "gate"?, "reply"?, "notify"?, "todos", "console",
    #: ..., "talk_trigger", "talk_agent", "talk_context", "talk_reply",
    #: "builder", "report_post"?}: node ids. A Chat hire's talk trigger,
    #: agent and Context are its trigger, agent and Context.
    node_roles: Dict[str, str]
    #: What starts the work, as stored on the employee: {kind, app?, every?,
    #: at?, day?}; a schedule's as it runs, in the owner's time.
    trigger: Dict[str, Any]
    delivery: str
    delivery_app: Optional[str]
    #: Apps whose nodes the graph uses.
    app_ids: List[str]
    warnings: List[str]
    team_plan: Optional[Dict[str, Any]] = None


class _Unresolved(Exception):
    """A template needs a value nobody supplied."""


def _connect(app: AppSpec) -> str:
    """What an owner does so ``app``'s ``${owner.<field>}`` values resolve:
    the owner's own address for an app comes from their connection to it."""
    return f"connect {app.name} in Settings > Connectors"


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


class _Graph:
    def __init__(self, workflow_id: str, allowed: Callable[[str], bool]):
        self.workflow_id = workflow_id
        self.allowed = allowed
        self.ids = NodeIds(workflow_id)
        self.nodes: List[Dict[str, Any]] = []
        self.edges: List[Dict[str, Any]] = []
        self.parameters: Dict[str, Dict[str, Any]] = {}

    def _check(self, node_type: str) -> None:
        if not self.allowed(node_type):
            raise BuildError("not_allowed", f"{node_type} is not available to hired employees")

    def add(self, node_type: str, label: str, params: Dict[str, Any], position: Tuple[int, int], data: Optional[Dict[str, Any]] = None) -> str:
        self._check(node_type)
        node_id = self.ids.next(node_type)
        self.nodes.append(graph_node(node_id, node_type, label, position, data))
        if params:
            self.parameters[node_id] = dict(params)
        return node_id

    def connect(self, edge: Edge) -> None:
        self.edges.append(edge.to_dict())

    @property
    def data(self) -> Dict[str, Any]:
        return {"nodes": self.nodes, "edges": self.edges}

    def place(self, additions: GraphAdditions) -> Dict[str, str]:
        """Add a batch planned against this graph (graph_build.add_to_graph,
        as additions to a saved workflow are placed). Returns ref -> id."""
        placed = add_to_graph(self.workflow_id, self.data, additions)
        for node in placed.nodes:
            self._check(node["type"])
        for node in placed.nodes:
            self.nodes.append(node)
            if placed.parameters[node["id"]]:
                self.parameters[node["id"]] = placed.parameters[node["id"]]
        self.edges.extend(placed.edges)
        return placed.node_ids


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


def _owner_zone(zone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _as_run(params: Mapping[str, Any], zone_name: str, now: datetime) -> Dict[str, str]:
    """When cronScheduler ``params`` run, in the owner's time today: the
    ``at`` and ``day`` of a schedule record (nothing for an hourly one)."""
    frequency = params.get("frequency")
    time_key = {"days": "daily_time", "weeks": "weekly_time", "months": "monthly_time"}.get(str(frequency))
    if time_key is None:
        return {}
    run_zone = ZoneInfo(params["timezone"])
    hour, minute = (int(part) for part in str(params[time_key]).split(":"))
    date = now.astimezone(run_zone).date()
    if frequency == "weeks":
        # cronScheduler counts weekdays from Sunday (0), Python from Monday.
        date += timedelta(days=(int(params["weekday"]) - (date.weekday() + 1)) % 7)
    elif frequency == "months":
        last = calendar.monthrange(date.year, date.month)[1]
        date = date.replace(day=last if params["month_day"] == "L" else int(params["month_day"]))
    local = datetime.combine(date, time(hour, minute), tzinfo=run_zone).astimezone(_owner_zone(zone_name))
    record = {"at": local.strftime("%H:%M")}
    if frequency == "weeks":
        record["day"] = WEEKDAYS[(local.weekday() + 1) % 7]
    elif frequency == "months":
        record["day"] = str(params["month_day"]) if local.date() == date else str(local.day)
    return record


def _schedule_phrase(trigger: Mapping[str, Any]) -> str:
    """"every weekday at 09:00", to go mid-sentence."""
    from services.employees.summaries import schedule_text

    text = schedule_text(trigger)
    return text[:1].lower() + text[1:]


def schedule_params(trigger: Optional[HireTrigger], zone_name: str, now: datetime) -> Dict[str, Any]:
    """cronScheduler parameters for a hire's schedule, in the owner's zone.
    Generated weekday schedules use the scheduler's deterministic filter."""
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
    params = {"frequency": "days", "daily_time": at, "timezone": zone}
    if every == "weekday":
        params["weekday_only"] = True
    return params


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


def _plan_trigger(inputs: BuildInputs, labels: Labels, now: datetime, warnings: List[str]) -> _TriggerPlan:
    request = inputs.request
    kind, app = _choose_trigger(inputs)
    if kind == "app_event" and app is not None and app.trigger is not None:
        label = labels.take(app.phrase("when", f"New {app.name} message"))
        try:
            params = _substitute(dict(app.trigger.params), {}, label_key(label), inputs.owner_values)
            prompt = _substitute(app.trigger.prompt, {}, label_key(label), inputs.owner_values) or ref(label_key(label), "text")
        except _Unresolved:
            warnings.append(f"{app.name} can't start their work until you {_connect(app)}. Until then they work when you message them.")
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
        asked = request.trigger.model_dump(include={"every", "at", "day"}, exclude_none=True) if request.trigger is not None else {}
        params = schedule_params(request.trigger, inputs.timezone, now)
        # What the card shows is what runs: the schedule as it will run, in
        # the owner's time, which can differ from what they asked for.
        record = {"kind": "schedule", **asked, **_as_run(params, inputs.timezone, now)}
        if any(str(asked[key]).lower() != str(record[key]).lower() for key in ("at", "day") if key in asked):
            warnings.append(
                f"Schedules run only at set times, so they'll work {_schedule_phrase(record)} (you asked for {_schedule_phrase(asked)})."
            )
        label = labels.take("Schedule")
        return _TriggerPlan(
            kind="schedule",
            node_type=SCHEDULE_TYPE,
            label=label,
            params=params,
            prompt=f"It is time for your routine ({_schedule_phrase(record)}). Do it now, then report.",
            record=record,
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


def _plan_delivery(inputs: BuildInputs, trigger: _TriggerPlan, agent_key: str, labels: Labels, warnings: List[str]) -> Optional[_DeliveryPlan]:
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
    inputs: BuildInputs, app: AppSpec, trigger: _TriggerPlan, agent_key: str, labels: Labels, warnings: List[str]
) -> Optional[_DeliveryPlan]:
    template = app.notify_owner
    assert template is not None
    subject = f"Update from {inputs.request.name}"
    try:
        params = _substitute(
            dict(template.params), {"reply_text": ref(agent_key, "response"), "reply_subject": subject}, trigger.key, inputs.owner_values
        )
    except _Unresolved:
        # A schedule worker's reports also go to Talk ("Post to Talk").
        meanwhile = " Until then you'll find them in Talk." if trigger.kind == "schedule" else ""
        warnings.append(f"Reports can't go out through {app.name} until you {_connect(app)}.{meanwhile}")
        return None
    return _DeliveryPlan(role="notify", app=app, node_type=template.type, label=labels.take(f"Report on {app.name}"), params=params)


def _plan_reply(
    inputs: BuildInputs,
    app: AppSpec,
    template: NodeTemplate,
    trigger: _TriggerPlan,
    agent_key: str,
    labels: Labels,
    warnings: List[str],
) -> Optional[_DeliveryPlan]:
    fields = _REPLY_FIELDS.get(template.type)
    subject = ref(trigger.key, "subject")
    subject = f"Re: {subject}" if app.id in _MAIL_APPS else f"Reply from {inputs.request.name}"
    # Every reply that can wait for the owner goes through the gate, which
    # reads the live Ask first rule; one that cannot is sent as written only
    # while the owner does not ask first.
    if fields is None and not inputs.request.rules.ask_first:
        try:
            params = _substitute(
                dict(template.params), {"reply_text": ref(agent_key, "response"), "reply_subject": subject}, trigger.key, inputs.owner_values
            )
        except _Unresolved:
            warnings.append(f"Replies can't go out through {app.name} until you {_connect(app)}.")
            return None
        return _DeliveryPlan(role="reply", app=app, node_type=template.type, label=labels.take(f"Reply on {app.name}"), params=params)

    # The reply waits behind the gate while the owner asks first, or does not happen.
    if fields is None:
        warnings.append(f"{app.name} replies cannot wait for your approval yet, so they are off")
        return None
    recipient_field, name_field, body_field, max_length = fields
    try:
        recipient = _substitute(template.params.get(recipient_field), {}, trigger.key, inputs.owner_values)
    except _Unresolved:
        warnings.append(f"{app.name} replies cannot find who to answer, so they are off")
        return None
    gate_label = labels.take(GATE_LABEL)
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
    except _Unresolved:
        warnings.append(f"Replies can't go out through {app.name} until you {_connect(app)}.")
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

    policy.check_tool decides: a tool whose plugin declares an approval
    spec is attached as it is (each call that sends is decided when it is
    made). Under ``ask first`` a tool without a spec that can send or spend
    is left out, unless the app declares ``ask_first_params`` that make it
    safe: then it is attached with those applied. An app the owner has not
    connected yet keeps its tools; the card asks them to connect it."""
    plans: List[_ToolPlan] = []
    for app in inputs.apps:
        for tool in app.tools:
            decision = check_tool(tool.type, employee=inputs.request, connected=None, app=app, allowed=inputs.allowed)
            if decision.code == "asks_first":
                warnings.append(f"{app.name} is left out while they ask before sending anything")
                continue
            if not decision.allowed:
                raise BuildError(decision.code, decision.reason)
            if decision.read_only:
                warnings.append(f"{app.name} can only read while they ask before sending anything; they hand changes to you")
            try:
                params = _substitute(dict(decision.params), {}, trigger.key, inputs.owner_values)
            except _Unresolved:
                warnings.append(f"They can't use {app.name} until you {_connect(app)}.")
                continue
            plans.append(_ToolPlan(app=app, tool=tool, params=params, read_only=decision.read_only))
    return plans


# ----- what only the agent the owner talks to gets -----


def talk_tools(
    apps: Sequence[AppSpec],
    *,
    owner_values: Mapping[str, str],
    allowed: Callable[[str], bool],
    warnings: Optional[List[str]] = None,
) -> List[TalkTool]:
    """Generated UI in the chat, and a way to send through each of the
    hire's apps when the owner asks (``talk_send``). Every send waits for the
    owner while they ask first, so asking first never takes them away."""
    tools: List[TalkTool] = []
    if allowed(CHAT_UI_TYPE):
        tools.append(TalkTool(CHAT_UI_TYPE, CHAT_UI_LABEL))
    for app in apps:
        template = app.talk_send
        if template is None or not allowed(template.type):
            continue
        params = fill_params(template.params, None, owner_values)
        if params is None:
            if warnings is not None:
                warnings.append(f"They can't send on {app.name} for you until you {_connect(app)}.")
            continue
        tools.append(TalkTool(template.type, template.label or f"Send on {app.name}", params))
    return tools


def fill_params(params: Mapping[str, Any], trigger_key: Optional[str], owner_values: Mapping[str, str]) -> Optional[Dict[str, Any]]:
    """A template's parameters with their placeholders filled in (``${trigger.*}``
    from the trigger node ``trigger_key`` names, ``${owner.*}`` from the owner's
    own addresses); None when one cannot be."""
    try:
        return _substitute(dict(params), {}, trigger_key, owner_values)
    except _Unresolved:
        return None


def _plan_talk_tools(inputs: BuildInputs, warnings: List[str]) -> List[TalkTool]:
    return talk_tools(inputs.apps, owner_values=inputs.owner_values, allowed=inputs.allowed, warnings=warnings)


# ----- the whole graph -----


def _build_single_employee_graph(inputs: BuildInputs) -> BuiltEmployee:
    request = inputs.request
    now = inputs.now or datetime.now(timezone.utc)
    labels = Labels()
    warnings: List[str] = []

    agent_label = labels.take(request.name)
    agent_key = label_key(agent_label)
    trigger = _plan_trigger(inputs, labels, now, warnings)
    delivery = _plan_delivery(inputs, trigger, agent_key, labels, warnings)

    app_tools = _plan_app_tools(inputs, trigger, warnings)
    browser_tools = [plan for plan in app_tools if plan.tool.role == "browser"]
    talk_tools = _plan_talk_tools(inputs, warnings)
    sends = any(tool.type != CHAT_UI_TYPE for tool in talk_tools)

    def instructions(delivery_mode: Delivery, *, delivery_app: Optional[str] = None, has_builder: bool = False) -> str:
        return build_system_message(
            PromptInputs(
                request=request,
                owner=inputs.owner,
                delivery=delivery_mode,
                delivery_app=delivery_app,
                unsupported_apps=list(inputs.unsupported_apps),
                has_memory=inputs.memory,
                has_canvas=True,
                has_browser=bool(browser_tools),
                browser_read_only=bool(browser_tools) and all(plan.read_only for plan in browser_tools),
                has_builder=has_builder,
                has_send_tools=delivery_mode == "talk" and sends,
            )
        )

    # A Chat hire's agent answers the owner in Talk itself.
    talks_itself = trigger.kind == "manual"
    delivery_mode: Delivery = "talk" if talks_itself else ("reply" if delivery is not None and delivery.role == "reply" else "report")
    system_message = instructions(delivery_mode, delivery_app=delivery.app.name if delivery is not None else None, has_builder=talks_itself)
    if trigger.kind == "schedule" and request.trigger is not None and request.trigger.every == "weekday":
        system_message += f"\n- You work on weekdays only. On a Saturday or Sunday, answer exactly {NO_REPLY}."
    model = {
        "provider": inputs.llm.provider if inputs.llm is not None else "openai",
        "model": inputs.llm.model if inputs.llm is not None else "",
    }

    graph = _Graph(inputs.workflow_id, inputs.allowed)
    roles: Dict[str, str] = {}
    used_apps: List[str] = []

    def use(app: Optional[AppSpec]) -> None:
        if app is not None and app.id not in used_apps:
            used_apps.append(app.id)

    roles["trigger"] = graph.add(trigger.node_type, trigger.label, trigger.params, (0, 200))
    use(trigger.app)
    roles["agent"] = graph.add(AGENT_TYPE, agent_label, {"prompt": trigger.prompt, "system_message": system_message, **model}, (360, 200))
    graph.connect(main_edge(roles["trigger"], roles["agent"]))

    # Tools.
    tool_x = 120

    def add_tool(node_type: str, label: str, params: Dict[str, Any], role: Optional[str] = None) -> None:
        nonlocal tool_x
        node_id = graph.add(node_type, labels.take(label), params, (tool_x, 440))
        tool_x += 170
        graph.connect(tool_edge(node_id, roles["agent"]))
        if role:
            roles[role] = node_id

    for base in BASE_TOOLS:
        if base.type == MEMORY_TYPE and not inputs.memory:
            continue
        params = {"timezone": inputs.timezone or "UTC"} if base.type == CLOCK_TYPE else dict(base.params)
        add_tool(base.type, base.label, params, role=base.role)
    for plan in app_tools:
        add_tool(plan.tool.type, plan.tool.label or plan.app.name, plan.params, role=plan.tool.role)
        use(plan.app)

    # Context, for owner-facing triggers only.
    if trigger.kind in ("schedule", "manual"):
        roles["context"] = graph.add(CONTEXT_TYPE, labels.take("Context"), {}, (360, 20), data=context_data(roles["agent"]))
        graph.connect(context_edge(roles["context"], roles["agent"]))

    # Skills from the owner's library.
    skills_config = _skills_config(inputs.skills, warnings)
    if skills_config is not None:
        roles["skills"] = graph.add(
            SKILLS_TYPE, labels.take("Skills"), {"skill_folder": "assistant", "skills_config": skills_config}, (-60, 440)
        )
        graph.connect(skill_edge(roles["skills"], roles["agent"]))

    # Delivery.
    if delivery is not None:
        upstream, condition = roles["agent"], SEND_CONDITION
        if delivery.gate_params is not None and delivery.gate_label is not None:
            roles["gate"] = graph.add(GATE_TYPE, delivery.gate_label, delivery.gate_params, (720, 200))
            graph.connect(main_edge(roles["agent"], roles["gate"], SEND_CONDITION))
            # The gate reads this run's trigger for who the reply goes to.
            graph.connect(main_edge(roles["trigger"], roles["gate"]))
            upstream, condition = roles["gate"], APPROVED_CONDITION
        x = 1080 if "gate" in roles else 720
        roles[delivery.role] = graph.add(delivery.node_type, delivery.label, delivery.params, (x, 200))
        graph.connect(main_edge(upstream, roles[delivery.role], condition))
        if delivery.role == "reply":
            # The reply reads this run's trigger (the message it answers); a
            # plain edge adds no condition, so the gate's still decides.
            graph.connect(main_edge(roles["trigger"], roles["reply"]))
        use(delivery.app)

    # Activity log.
    roles["console"] = graph.add(
        CONSOLE_TYPE,
        labels.take("Activity log"),
        {"log_mode": "field", "field_path": ref(agent_key, "response"), "format": "text"},
        (720, 420),
    )
    graph.connect(main_edge(roles["agent"], roles["console"]))

    # Talk, placed the way Turn on Talk places it on a saved workflow.
    state = talk_state(graph.data, worker=roles["agent"])
    talk_agent = None
    if state.line is None:
        talk_agent = TalkAgent(talk_agent_label(agent_label), {"system_message": instructions("talk", has_builder=True), **model})
    plan = plan_talk_line(
        graph.data,
        state,
        workflow_id=inputs.workflow_id,
        agent=talk_agent,
        hired=True,
        report_from=roles["agent"] if trigger.kind == "schedule" else None,
        talk_tools=talk_tools,
    )
    roles.update(plan.role_ids(graph.place(plan.additions)))

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


def build_employee_graph(inputs: BuildInputs) -> BuiltEmployee:
    built = _build_single_employee_graph(inputs)
    if inputs.team:
        from services.employees.team_recipe import build_team

        return build_team(built, inputs)
    return built


__all__ = [
    "APPROVED_CONDITION",
    "BUILDER_VERSION",
    "CHAT_UI_TYPE",
    "GATE_LABEL",
    "LIVE_RULE_BUILDER_VERSION",
    "BuildError",
    "BuildInputs",
    "BuiltEmployee",
    "LibrarySkill",
    "SEND_CONDITION",
    "build_employee_graph",
    "fill_params",
    "talk_tools",
    "label_key",
    "ref",
    "schedule_params",
]
