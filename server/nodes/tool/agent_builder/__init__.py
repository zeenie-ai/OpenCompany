"""Agent Builder: an agent inspects its canvas and adds to it while it runs.

One ToolNode with an ``operation`` discriminator; the LLM sees one tool,
``agent_builder``:

* ``inspect_canvas``: the saved canvas, what is wired to the calling agent,
  and the catalogue of what it may add;
* ``add_tool``: a tool node on the agent's ``input-tools``;
* ``add_skill``: a skill on the agent's Skills node (masterSkill), or on a
  new one;
* ``add_subagent``: a teammate on a team lead's ``input-teammates``;
* ``inspect_node`` / ``search_docs`` / ``read_doc``: live contracts and shipped docs;
* ``plan_update`` / ``apply_update``: validated owner-authorized changes;
* ``create_workflow``: configured employees through the shared Hire pipeline.

Every change goes through ``services.workflow_storage.mutate.apply_graph_additions``,
the server's one path for growing a saved workflow: one transaction,
canonical ids, labels unique against the graph, the handles every
server-side writer uses (services/graph_build.py), real parameter rows, and
an announcement to open editors (``workflow_ops_apply`` with
``persisted: true``). Its ledger is keyed by the tool call, so a retried
call replays the first result instead of adding twice.

The calling agent is the one the runtime names (``invoking_agent_node_id``
on Temporal, ``parent_node_id`` on every path), never guessed from edges:
one Agent Builder node can serve several agents.

A hired employee (the workflow has an employee row) is given things by the
rule Hire applies (services/employees/policy.py): tools from the app
registry and the tools every hire gets, nothing that sends or spends while
the owner asks to be asked first, only apps that are connected and
allowlisted; skills from the owner's library or Settings > Skills'
Discover folder, with their text copied in. What is added goes to the
selected member, with a separate concrete tool instance for each agent. A refusal is one plain
sentence the agent passes on.

When it works: a tool added for the calling agent is bound for the rest of
its run (the agent loop's hot rebind, on by default). A deployed run starts
from the workflow's snapshot, which lacks it until a restart (Apply, for an
employee), so ``add_tool`` for a saved tool this run does not have returns
that node to bind again and saves nothing. A skill merged into an existing
Skills node applies from the next run; a new node or edge waits for the
restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services import workflow_ops
from services.graph_build import (
    SKILL_INPUT,
    TOOLS_INPUT,
    Edge,
    EdgeRemoval,
    GraphAdditions,
    NewNode,
    ParamMerge,
    skill_edge,
    tool_edge,
)
from services.node_registry import get_node_class, registered_node_classes
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode


logger = get_logger(__name__)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

_TEAMMATES_INPUT = "input-teammates"
#: A teammate feeds its lead from its top output, as the editor draws it.
_TEAMMATE_OUTPUT = "output-top"
_MASTER_SKILL_TYPE = "masterSkill"
_AGENT_BUILDER_TYPE = "agentBuilder"
_TASK_MANAGER_TYPE = "taskManager"
_CONTEXT_TYPE = "context"
_TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})
_DENIED_TOOL_TYPES = frozenset({_AGENT_BUILDER_TYPE, _MASTER_SKILL_TYPE, _TASK_MANAGER_TYPE})
_KEY_PARAM_FIELDS = ("provider", "model", "operation", "url", "query")
#: Settings > Skills' Discover folder under server/skills/ (the client's
#: ``DISCOVER_SKILL_FOLDER``, features/home/data/skills.ts).
_DISCOVER_SKILL_FOLDER = "employee"
_APPLY_HINT = "It becomes part of all your work when the owner presses Apply on your page."

# Independent operator switch for shared-pipeline employee creation.
_CREATE_WORKFLOW_ENABLED = True


class _AccessChanged(ValueError):
    pass


# ----------------------------------------------------------------------------
# What an operation works against
# ----------------------------------------------------------------------------


@dataclass
class _Canvas:
    """The saved workflow an operation changes (it may have grown since this
    run started), or this run's own canvas when nothing is saved."""

    workflow_id: Optional[str]
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    saved: bool

    def node(self, node_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((node for node in self.nodes if node_id and node.get("id") == node_id), None)

    def label(self, node_id: str) -> str:
        node = self.node(node_id) or {}
        return str((node.get("data") or {}).get("label") or node.get("type") or node_id)

    def position(self, node_id: str) -> Tuple[float, float]:
        position = (self.node(node_id) or {}).get("position") or {}
        return float(position.get("x") or 0), float(position.get("y") or 0)

    def sources(self, target: str, handle: str) -> List[str]:
        """The nodes wired into ``target``'s ``handle``, in edge order."""
        return [
            str(edge["source"])
            for edge in self.edges
            if edge.get("target") == target and edge.get("targetHandle") == handle and edge.get("source")
        ]

    def source_of_type(self, target: str, handle: str, node_type: str) -> Optional[str]:
        for source in self.sources(target, handle):
            if (self.node(source) or {}).get("type") == node_type:
                return source
        return None


@dataclass
class _Employee:
    """A hired employee's parts, from its row's ``node_roles``."""

    row: Any
    #: The worker and the agent the owner talks to, in the saved graph.
    agents: List[str]
    #: The hire's Skills node, when the graph has it.
    skills: Optional[str]


@dataclass(frozen=True)
class _Skill:
    name: str
    description: str
    #: Empty for a built-in skill the runtime reads from its SKILL.md.
    instructions: str


async def _load_canvas(ctx: NodeContext, database: Any) -> _Canvas:
    """The saved graph (read fresh, so operations in one run see each
    other's additions), else the run's own canvas."""
    if ctx.workflow_id:
        try:
            workflow = await database.get_workflow(ctx.workflow_id)
        except Exception:  # noqa: BLE001 — a failed read falls back to the run's canvas
            logger.warning("Agent Builder could not read the workflow", workflow_id=ctx.workflow_id, exc_info=True)
            workflow = None
        if workflow is not None:
            data = workflow.data or {}
            return _Canvas(ctx.workflow_id, list(data.get("nodes") or []), list(data.get("edges") or []), saved=True)
    return _Canvas(ctx.workflow_id, list(ctx.nodes or []), list(ctx.edges or []), saved=False)


async def _employee_of(database: Any, canvas: _Canvas) -> Optional[_Employee]:
    if not canvas.saved:
        return None
    from services.employees import store
    from services.employees.start import AGENT_ROLES
    from models.approvals import WorkflowRule

    row = await store.get_by_workflow(database, canvas.workflow_id)
    if row is None:
        return None
    # Catalogues and tool defaults must agree with the live rule used at
    # save time even if the hire-time rules still contain the old value.
    async with database.get_session() as session:
        rule = await session.get(WorkflowRule, canvas.workflow_id)
    if rule is not None:
        row = row.model_copy(update={"rules": {**(row.rules or {}), "ask_first": rule.ask_first}})
    roles = row.node_roles or {}
    agents = [value for role, value in roles.items() if (role in AGENT_ROLES or (role.startswith("specialist_") and role.rsplit("_", 1)[-1].isdigit())) and canvas.node(value)]
    skills = roles.get("skills") if canvas.node(roles.get("skills")) else None
    return _Employee(row=row, agents=list(dict.fromkeys(agents)), skills=skills)


def _caller(ctx: NodeContext) -> Optional[str]:
    """The agent that called: both runtimes name it (Temporal as
    ``invoking_agent_node_id``, every path as ``parent_node_id``)."""
    raw = ctx.raw or {}
    caller = raw.get("invoking_agent_node_id") or raw.get("parent_node_id")
    return str(caller) if caller else None


def _mutation_id(ctx: NodeContext, params: BaseModel, caller: Optional[str]) -> str:
    """The ledger key of this call's change: the runtime's tool-call id when
    it has one (stable across retries of the same call), scoped to the run;
    otherwise the request itself."""
    raw = ctx.raw or {}
    if raw.get("pending_builder_mutation"):
        return str(raw["pending_builder_mutation"])
    run = str(ctx.execution_id or "")
    call = str(raw.get("tool_call_id") or "")
    if call:
        return f"agent-builder:{run}:{call}"
    request = json.dumps({"request": params.model_dump(), "caller": caller, "run": run}, sort_keys=True, default=str)
    return f"agent-builder:{hashlib.sha256(request.encode('utf-8')).hexdigest()}"


async def _save(ctx: NodeContext, database: Any, canvas: _Canvas, additions: GraphAdditions, params: BaseModel, caller: Optional[str], *, prepare: Optional[Callable] = None, grant_ids: Sequence[str] = (), read_for_prepare: Optional[Callable] = None) -> Any:
    """Apply ``additions`` (possibly none: the call still claims its ledger
    key, so a retry replays what the first attempt saved). None when the
    workflow is gone; raises ValueError when the graph changed under the
    plan."""
    from services.workflow_storage.mutate import apply_graph_additions
    from services.agent_runtime_policy import prepare_new_agent_additions
    # A new Skill holder can attach to an existing agent. That agent's saved
    # immutable policy, rather than the caller's policy, selects its defaults.
    holder_refs = {node.ref for node in additions.nodes if node.type == "masterSkill"}
    existing_targets = {edge.target for edge in additions.edges if edge.source in holder_refs}
    if prepare is not None and params.operation == "attach_skill":
        existing_targets.update(node["id"] for node in canvas.nodes if node.get("id"))
    existing_parameters = {identifier: await database.get_node_parameters(identifier) or {}
                           for identifier in existing_targets}
    additions = prepare_new_agent_additions(additions, existing_parameters)
    if prepare is not None:
        original_prepare = prepare
        def prepare(graph):
            batch, reused = original_prepare(graph)
            return prepare_new_agent_additions(batch, existing_parameters), reused

    async def authorize(session: Any) -> None:
        from models.employees import EmployeeGrant

        reviewed = ctx.raw.get("reviewed_employee_grant")
        if reviewed or ctx.raw.get("builder_direct_grants"):
            from models.database import Workflow
            from services.employees.talk_team_change import connected_builders

            workflow = await session.get(Workflow, canvas.workflow_id)
            if not workflow or ctx.node_id not in connected_builders(workflow.data or {}, _caller(ctx)):
                raise _AccessChanged("Talk's Agent Builder connection changed before the team change was saved")
        if reviewed and reviewed not in grant_ids:
            raise _AccessChanged("The requested change no longer matches the owner's approval")
        for grant_id in grant_ids:
            grant = await session.get(EmployeeGrant, grant_id)
            direct = ctx.raw.get("builder_direct_grants", {}).get(grant_id)
            if grant is None and direct:
                grant = EmployeeGrant(id=grant_id, workflow_id=canvas.workflow_id, owner_id=ctx.user_id, **direct)
                session.add(grant)
            if grant is None or grant.workflow_id != canvas.workflow_id or grant.owner_id != ctx.user_id or grant.revoked_at is not None:
                raise _AccessChanged("The owner's permission changed. Ask them to allow access again.")
            if not grant.limits.get("approved"):
                from services.employees.permissions import specialist_review_off
                if (not direct or not await specialist_review_off(
                        session, canvas.workflow_id, _caller(ctx), params.operation, grant.member_id, grant.capability)):
                    raise _AccessChanged("The owner's Ask first setting changed. Review the team change in Talk.")
                grant.limits = {**grant.limits, "approved": True}
            limits = {key: value for key, value in grant.limits.items() if key != "approved"}
            scope = [grant.workflow_id, grant.owner_id, grant.capability, grant.member_id, grant.account_id, limits]
            identity = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if identity != grant_id:
                raise _AccessChanged("The approved access scope changed")
            # Consume the pending change with its graph write. A rolled-back
            # write leaves it available for the owner to retry.
            if grant.pending_change and grant.pending_change.get("mutation_id") == _mutation_id(ctx, params, caller):
                grant.pending_change = None
        if read_for_prepare:
            await read_for_prepare(session)

    return await apply_graph_additions(
        database,
        str(canvas.workflow_id),
        additions,
        mutation_id=_mutation_id(ctx, params, caller),
        caller_node_id=caller,
        prepare=prepare,
        authorize=authorize if grant_ids or read_for_prepare or ctx.raw.get("reviewed_employee_grant") else None,
        authorization_grant_ids=grant_ids,
    )


def _unsaved(canvas: _Canvas, targets: Sequence[str]) -> Optional[str]:
    """Why ``targets`` cannot be changed on the saved graph, if they cannot."""
    if not canvas.saved:
        return "Save the workflow first, then ask again."
    if not targets:
        return "Only an agent can ask me to add things."
    if any(canvas.node(target) is None for target in targets):
        return "Save the workflow first: the agent that asked isn't in the saved workflow yet."
    return None


def _refused(operation: str, summary: str) -> "AgentBuilderOutput":
    return AgentBuilderOutput(operation=operation, summary=summary, operations=[], validation_issues=[{"code": "request_refused", "message": summary}])


async def _permission(database: Any, ctx: NodeContext, params: "AgentBuilderParams", capability: str, members: Sequence[str], limits: Dict[str, Any], grant_ids: Optional[List[str]] = None) -> Optional["AgentBuilderOutput"]:
    from services.employees.permissions import require_builder_access
    from services.chat.stream import chat_run_id_of

    pending = []
    refusal = None
    change = None
    run_id = chat_run_id_of(ctx.raw)
    if (run_id and len(members) == 1 and params.operation in {"add_subagent", "add_tool", "add_skill"}
            and (not params.dry_run or ctx.raw.get("save_after_builder_review"))):
        # Persist only validated inputs for this operation. The replay checks
        # the same chat identity, saved connections and exact grant scope.
        fields = {
            "add_subagent": {"operation", "agent_type", "purpose", "tool_types", "tool_parameters", "skill_names", "app_id"},
            "add_tool": {"operation", "node_type", "app_id", "target_member_id", "tool_parameters"},
            "add_skill": {"operation", "skill_name", "target_member_id"},
        }[params.operation]
        change = {"params": params.model_dump(include=fields, exclude_defaults=True), "run_id": run_id,
                  "builder_id": ctx.node_id, "caller_id": _caller(ctx), "mutation_id": _mutation_id(ctx, params, _caller(ctx))}
    for member in members:
        access = await require_builder_access(database, ctx, capability, member_id=member, limits=limits,
                                              pending_change=change, operation=params.operation, preview=params.dry_run)
        if access["authorized"] and access.get("request_id") and grant_ids is not None:
            grant_ids.append(access["request_id"])
            if access.get("grant_on_save"):
                ctx.raw.setdefault("builder_direct_grants", {})[access["request_id"]] = access["grant_on_save"]
            if access.get("pending_mutation_id"):
                ctx.raw["pending_builder_mutation"] = access["pending_mutation_id"]
        if not access["authorized"]:
            refusal = access
            pending.extend(access.get("required_access") or [])
    if refusal:
        return AgentBuilderOutput(operation=params.operation, summary=refusal.get("summary"), operations=[],
                                  request_id=refusal.get("request_id"), required_access=pending, activation_state="blocked", validation_issues=[])
    return None


def _rebind_on(ctx: NodeContext) -> bool:
    """The user's "Auto-Rebind Tools After Canvas Changes" (on by default),
    which the agent loop forwards as ``auto_rebind_tools``."""
    return bool((ctx.raw or {}).get("auto_rebind_tools", True))


def _summary_suffix(ctx: NodeContext) -> str:
    """Whether a new tool is callable in this run (the rebind) or waits."""
    return "Available immediately — call it in your next response." if _rebind_on(ctx) else "Available on your next turn."


def _log_op_entry(op: str, ctx: NodeContext, **fields: Any) -> None:
    """One INFO line per call. The node and workflow ids ride the log context
    BaseNode.execute binds; an empty run canvas here means the runtime did
    not forward it."""
    logger.info(
        "Agent Builder operation",
        operation=op,
        caller=_caller(ctx),
        run_nodes=len(ctx.nodes or []),
        **{key: value for key, value in fields.items() if value not in (None, "")},
    )


# ----------------------------------------------------------------------------
# Catalogues: what the agent may add
# ----------------------------------------------------------------------------


def _allowlist_config() -> Dict[str, Any]:
    """The operator allowlist (server/config/node_allowlist.json), the same
    source the UI palette reads. The service falls back to show-all on a
    missing or malformed file, so this never raises."""
    try:
        from services.node_allowlist import get_node_allowlist_service

        return get_node_allowlist_service().get_config()
    except Exception as exc:  # noqa: BLE001 — defensive: never fail catalogue
        logger.debug("[agentBuilder] node_allowlist read failed: %s", exc)
        return {"disabled_nodes": [], "disabled_groups": [], "disabled_skill_folders": []}


def _is_blocked_by_allowlist(cls: Any, ntype: str, config: Dict[str, Any]) -> bool:
    """``True`` when ``ntype`` or any group in ``cls.group`` is in the
    operator blocklist. Honors both ``disabled_nodes`` (per-type) and
    ``disabled_groups`` (every plugin in the named group)."""
    if ntype in (config.get("disabled_nodes") or ()):
        return True
    disabled_groups = set(config.get("disabled_groups") or ())
    if disabled_groups:
        plugin_groups = set(getattr(cls, "group", ()) or ())
        if plugin_groups & disabled_groups:
            return True
    return False


def _allowed_tool_types() -> set[str]:
    """Tool node types an agent in an editor-built workflow may add: pure
    ToolNodes (``component_kind == 'tool'``) and dual-purpose ActionNodes
    (``usable_as_tool=True``, chat models excluded), minus
    ``_DENIED_TOOL_TYPES`` (no Agent Builder, Skills node or Task Manager)
    and the operator's ``disabled_nodes`` / ``disabled_groups``."""
    config = _allowlist_config()
    out: set[str] = set()
    for ntype, cls in registered_node_classes().items():
        if ntype in _DENIED_TOOL_TYPES:
            continue
        kind = getattr(cls, "component_kind", "")
        is_tool = kind == "tool"
        is_dual_purpose = bool(getattr(cls, "usable_as_tool", False)) and kind != "model"
        if not (is_tool or is_dual_purpose):
            continue
        if _is_blocked_by_allowlist(cls, ntype, config):
            continue
        out.add(ntype)
    return out


def _allowed_subagent_types() -> set[str]:
    """Agent types a team lead may add as teammates, minus the operator's
    ``disabled_nodes`` / ``disabled_groups``."""
    config = _allowlist_config()
    return {
        ntype
        for ntype, cls in registered_node_classes().items()
        if getattr(cls, "component_kind", "") == "agent" and not _is_blocked_by_allowlist(cls, ntype, config)
    }


def _is_team_lead(node_type: str) -> bool:
    return node_type in _TEAM_LEAD_TYPES


def _description(cls: Any) -> str:
    return getattr(cls, "tool_description", "") or getattr(cls, "description", "") or ""


def _catalogue_tools() -> List[Dict[str, Any]]:
    registry = registered_node_classes()
    catalogue: List[Dict[str, Any]] = []
    for ntype in sorted(_allowed_tool_types()):
        cls = registry.get(ntype)
        if cls is None:
            continue
        catalogue.append({"type": ntype, "display_name": getattr(cls, "display_name", "") or ntype, "description": _description(cls)})
    return catalogue


def _catalogue_agents() -> List[Dict[str, Any]]:
    registry = registered_node_classes()
    catalogue: List[Dict[str, Any]] = []
    for ntype in sorted(_allowed_subagent_types()):
        cls = registry.get(ntype)
        if cls is None:
            continue
        catalogue.append(
            {"type": ntype, "display_name": getattr(cls, "display_name", "") or ntype, "description": getattr(cls, "description", "") or ""}
        )
    return catalogue


def _offered_builtins(*, employee: bool) -> Dict[str, Any]:
    """Built-in skills an agent may add, by frontmatter name, from the
    SkillLoader registry (populated at startup). A hired employee gets only
    Settings > Skills' Discover folder; any other agent every skill outside
    the operator's ``disabled_skill_folders`` (matched on any ancestor of
    its SKILL.md directory)."""
    from services.skill_loader import get_skill_loader

    blocked = set(_allowlist_config().get("disabled_skill_folders") or [])
    offered: Dict[str, Any] = {}
    for name, meta in sorted(get_skill_loader().get_registry().items()):
        if meta.path is None:
            continue
        if employee:
            wanted = meta.path.parent.name == _DISCOVER_SKILL_FOLDER and not {parent.name for parent in meta.path.parents} & blocked
        else:
            wanted = not {parent.name for parent in meta.path.parents} & blocked
        if wanted:
            offered[name] = meta
    return offered


async def _library(database: Any) -> List[Dict[str, Any]]:
    """Settings > Skills: every skill in the owner's library that has text
    (on or off for new hires: that switch is about new hires only)."""
    rows = await database.get_all_user_skills(active_only=False)
    return [row for row in rows if str(row.get("name") or "").strip() and str(row.get("instructions") or "").strip()]


async def _catalogue_skills(database: Any, *, employee: bool) -> List[Dict[str, Any]]:
    """The owner's library, then the offered built-ins it does not shadow
    (the order ``_find_skill`` resolves a name in). A hired employee's list
    leaves out the names policy refuses."""
    library = await _library(database)
    names = {str(row["name"]) for row in library}
    catalogue = [{"name": str(row["name"]), "description": str(row.get("description") or "")} for row in library]
    offered = await asyncio.to_thread(_offered_builtins, employee=employee)
    catalogue += [
        {"name": name, "description": meta.description or ""} for name, meta in offered.items() if name not in names
    ]
    if employee:
        from services.employees.policy import check_skill

        catalogue = [entry for entry in catalogue if check_skill(entry["name"]).allowed]
    return catalogue


async def _connected_apps() -> List[str]:
    """The app ids that are connected (services/employees/connections.py)."""
    from services.employees.connections import Connections
    from services.plugin.deps import get_auth_service

    return await Connections(get_auth_service()).connected_app_ids()


async def _owner_apps() -> Mapping[str, Any]:
    """Every app an employee can use: config/employee_apps.json's, then the
    owner's own, such as custom connectors (``Connections.apps``)."""
    from services.employees.connections import Connections
    from services.plugin.deps import get_auth_service

    return await Connections(get_auth_service()).apps()


def _app_field(node_type: str) -> Optional[str]:
    """The parameter naming a node's app, for a type whose app it does not
    say by itself (``BaseNode.app_field``: a custom connector)."""
    return getattr(get_node_class(node_type), "app_field", None)


def _existing_tool(
    canvas: _Canvas, target: str, node_type: str, app: Optional[Tuple[str, str]], parameters: Mapping[str, Mapping[str, Any]]
) -> Optional[str]:
    """The ``node_type`` node on ``target``'s Tools. ``app`` (a parameter
    and the app's id) narrows it to the node naming that app in its saved
    ``parameters``, for a type several apps share."""
    for source in canvas.sources(target, TOOLS_INPUT):
        if (canvas.node(source) or {}).get("type") != node_type:
            continue
        if app is None or (parameters.get(source) or {}).get(app[0]) == app[1]:
            return source
    return None


async def _owner_timezone(database: Any) -> str:
    from services.employees.context import SETTINGS_USER_ID

    settings = await database.get_user_settings(SETTINGS_USER_ID) or {}
    return str(settings.get("profile_timezone") or "UTC")


def _specialist_defaults(agent_type: str) -> set[str]:
    if agent_type == "coding_agent":
        from services.coding_agent_recipe import default_pi_tool_types
        return set(default_pi_tool_types())
    return {"browser", "visionAnalyze"} if agent_type == "browser_agent" else set()


def _validated_tool_settings(node_type: str, supplied: Dict[str, Any]) -> Dict[str, Any]:
    """Validate saved settings separately from arguments supplied later by the tool caller."""
    from services.plugin.base import locked_tool_fields
    from services.workflow_sanitizer import sanitize_runtime_payload

    cls = get_node_class(node_type)
    if not supplied:
        return {}
    if set(supplied) & locked_tool_fields(cls) or sanitize_runtime_payload(supplied) != supplied:
        raise ValueError("Use existing connections and preserve locked tool settings.")
    model = cls.Params
    if issubclass(cls, ToolNode) and cls.tool_input_model() is model:
        model = cls.partial_config_model()
    try:
        settings = model.model_validate(supplied).model_dump(exclude_unset=True)
    except ValueError:
        raise ValueError(f"'{node_type}' needs valid configuration. Inspect its node contract first.") from None
    if set(supplied) - settings.keys():
        raise ValueError(f"'{node_type}' received an unknown setting. Inspect its node contract first.")
    return settings


async def _employee_tool_settings(database: Any, employee: _Employee, node_type: str, owner_type: str,
                                  supplied: Dict[str, Any], connected: Sequence[str], app_id: str, operation: str) -> Tuple[str, Dict[str, Any]]:
    from services.employees.policy import check_tool
    from services.node_allowlist import is_hire_allowed

    # Private recipe tools have their own Hire allowlist entries. Browser
    # itself is an app and goes through its employee access rules below.
    if node_type != "browser" and node_type in _specialist_defaults(owner_type):
        if node_type not in _allowed_tool_types() or not is_hire_allowed(node_type):
            raise ValueError(f"'{node_type}' is disabled for this specialist.")
        return get_node_class(node_type).display_name, supplied
    app = None
    if _app_field(node_type) is not None:
        app = (await _owner_apps()).get(app_id) if app_id else None
        if app is None:
            raise ValueError(f"{operation}: '{node_type}' needs the app_id of its entry in inspect_canvas available_tools.")
    decision = check_tool(node_type, employee=employee.row, connected=connected, app=app)
    if not decision.allowed:
        raise ValueError(decision.reason)
    defaults = dict(decision.params)
    if decision.app is not None and any(key in defaults and value != defaults[key] for key, value in supplied.items()):
        raise ValueError(f"'{node_type}' must preserve this employee's app configuration.")
    if decision.app is None and "timezone" in defaults:
        defaults["timezone"] = await _owner_timezone(database)
    return decision.label, {**defaults, **supplied}


async def _employee_tools(employee: _Employee) -> List[Dict[str, Any]]:
    """What policy lets this employee be given: the tools every hire gets,
    then each app's tools (the registry's, then the owner's own), each in
    the form it would be added. A type several apps share (a custom
    connector) is listed once per app, with the ``app_id`` add_tool takes."""
    from services.employees.policy import BASE_TOOLS, check_tool

    connected = set(await _connected_apps())
    apps = await _owner_apps()
    candidates = [(base.type, None) for base in BASE_TOOLS] + [(tool.type, app) for app in apps.values() for tool in app.tools]
    catalogue: List[Dict[str, Any]] = []
    seen: set = set()
    for node_type, app in candidates:
        shared = app is not None and _app_field(node_type) is not None
        key = (node_type, app.id) if shared else node_type
        if key in seen:
            continue
        decision = check_tool(node_type, employee=employee.row, connected=None, app=app)
        if not decision.allowed:
            continue
        seen.add(key)
        entry: Dict[str, Any] = {"type": node_type, "display_name": decision.label, "description": _description(get_node_class(node_type))}
        if app is not None:
            entry["app"] = app.name
            if shared:
                entry["app_id"] = app.id
            entry["connected"] = app.id in connected
        if decision.read_only:
            entry["read_only"] = True
        catalogue.append(entry)
    return catalogue


async def _find_skill(database: Any, name: str, *, employee: bool) -> Optional[_Skill]:
    """A skill by its frontmatter name: the owner's library first, with its
    text (the runtime's fallback for an entry without text reads files
    only), then the offered built-ins. A built-in keeps no text, so edits to
    its SKILL.md apply, except for a hired employee, whose copy is fixed at
    the time it is added, as Hire fixes a hire's library skills."""
    row = await database.get_user_skill(name)
    if row and str(row.get("instructions") or "").strip():
        return _Skill(name=name, description=str(row.get("description") or ""), instructions=str(row["instructions"]))
    offered = await asyncio.to_thread(_offered_builtins, employee=employee)
    meta = offered.get(name)
    if meta is None:
        return None
    if not employee:
        return _Skill(name=name, description=meta.description or "", instructions="")
    from services.skill_loader import get_skill_loader_async

    loader = await get_skill_loader_async()
    skill = await loader.load_skill_async(name)
    if skill is None or not skill.instructions.strip():
        return None
    return _Skill(name=name, description=meta.description or "", instructions=skill.instructions)


def _key_params(parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {key: parameters[key] for key in _KEY_PARAM_FIELDS if key in parameters}


def _run_has_tool(ctx: NodeContext, node_id: str, caller: str) -> bool:
    """Whether this run's canvas wires ``node_id`` to ``caller`` as a tool,
    so the agent has it bound already."""
    return any(
        edge.get("source") == node_id and edge.get("target") == caller and edge.get("targetHandle") == TOOLS_INPUT for edge in ctx.edges or []
    )


def _added_nodes(result: Any) -> set[str]:
    return {str(op.get("minted_id")) for op in result.operations if op.get("type") == "add_node"}


def _scoped_runtime_operations(operations: Sequence[Mapping[str, Any]], caller: Optional[str]) -> List[Dict[str, Any]]:
    """Editor announcements are complete; a run only gets its own bindings.

    Configured specialists' tools must never be rebound to the lead or Talk
    merely because they were created by that agent's Builder call.
    """
    bindings = {op.get("source") for op in operations if op.get("type") == "add_edge" and op.get("target") == caller and op.get("target_handle") in {TOOLS_INPUT, _TEAMMATES_INPUT}}
    result = []
    for op in operations:
        cls = get_node_class(str(op.get("node_type") or "")) if op.get("type") == "add_node" else None
        callable_node = cls is not None and (getattr(cls, "component_kind", "") in {"tool", "agent"} or getattr(cls, "usable_as_tool", False))
        if callable_node and (op.get("minted_id") or op.get("client_ref")) not in bindings:
            continue
        result.append(dict(op))
    return result


def _tool_summary(ctx: NodeContext, *, employee: bool, label: str, node_id: str, changed: bool, bind: bool) -> str:
    """What ``add_tool`` did, for the agent. An employee's is plain, since
    the agent passes it on to the owner."""
    if not employee:
        if changed:
            return f"Added '{label}' as a tool (node id={node_id}). {_summary_suffix(ctx)}"
        if bind:
            return f"Tool '{label}' is wired to you (node id={node_id}); this run started without it. {_summary_suffix(ctx)}"
        return f"Tool '{label}' is already wired to you (node id={node_id}). Reusing existing instance."
    now = "You can use it now in this conversation." if _rebind_on(ctx) else None
    if changed:
        return " ".join(part for part in (f"Added {label}.", now, _APPLY_HINT) if part)
    if bind:
        return f"You have {label}. {now or _APPLY_HINT}"
    return f"You already have {label}. Use it directly."


# ----------------------------------------------------------------------------
# Params + Output schemas
# ----------------------------------------------------------------------------


class AgentBuilderParams(BaseModel):
    """Multi-op schema. The LLM picks ``operation``, then fills the
    fields whose ``displayOptions`` enable them for that op.
    """

    operation: Literal[
        "inspect_canvas",
        "inspect_node",
        "search_docs",
        "read_doc",
        "plan_update",
        "apply_update",
        "add_tool",
        "add_skill",
        "add_subagent",
        "create_workflow",
    ] = Field(
        default="inspect_canvas",
        description=(
            "Which canvas-mutation to perform. Always call "
            "'inspect_canvas' first to see what's already wired and what "
            "you may add."
        ),
    )

    node_id: str = Field(default="", max_length=256, description="Saved node ID for inspect_node; omit to inspect node_type's live contract.")
    query: str = Field(default="", max_length=1000, description="Words to search in shipped Builder documentation.")
    document_id: str = Field(default="", max_length=500, description="Manifest document ID returned by search_docs or inspect_node. Never a filesystem path.")
    offset: int = Field(default=0, ge=0, description="Character offset for bounded document reads.")
    limit: int = Field(default=12, ge=1, le=50, description="Maximum documentation search results.")
    max_chars: int = Field(default=12000, ge=1, le=24000, description="Maximum characters in a document read.")
    purpose: str = Field(default="", max_length=4000, description="Specialist responsibility and acceptance criteria for add_subagent.")
    target_member_id: str = Field(default="", max_length=256, description="Target team member for a scoped tool or skill change; defaults to the caller. Must belong to this employee.")
    change_operation: Literal["add_tool", "add_skill", "add_subagent", "apply_saved"] = Field(default="apply_saved", description="Change to validate or apply. The same complete configuration and grant scope must be supplied when applying.")
    stop_work: bool = Field(default=False, description="Only set when the owner explicitly requests Stop work and apply.")
    dry_run: bool = Field(default=False, description="Validate configuration and access without saving a graph change.")
    tool_types: List[str] = Field(default_factory=list, max_length=20, description="Scoped specialist tools from available_tools.")
    tool_parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict, description="Per-tool configuration, validated against live schemas; credential secrets and locked fields are forbidden.")
    skill_names: List[str] = Field(default_factory=list, max_length=20, description="Scoped specialist skills from available_skills.")

    # add_tool
    node_type: str = Field(
        default="",
        description="For add_tool: the tool's node type, from inspect_canvas available_tools (e.g. 'httpRequest').",
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_tool"]}}},
    )
    app_id: str = Field(
        default="",
        max_length=256,
        description="For add_tool: the tool's app_id, when its available_tools entry has one (each custom connector is its own app).",
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_tool"]}}},
    )

    # add_skill
    skill_name: str = Field(
        default="",
        description="For add_skill: the skill's name, from inspect_canvas available_skills (e.g. 'book-appointments').",
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_skill"]}}},
    )

    # add_subagent
    agent_type: str = Field(
        default="",
        description=(
            "For add_subagent, choose a specialist from inspect_canvas.available_agents, such as "
            "'coding_agent' for software development or 'browser_agent' for browser work. "
            "A managed employee's Talk contact can add a specialist to its existing lead. "
            "In an editor-built team the caller is the team lead."
        ),
        json_schema_extra={"displayOptions": {"show": {"operation": ["add_subagent"]}}},
    )

    # create_workflow
    workflow_name: str = Field(
        default="",
        description="For create_workflow: display name (non-empty).",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )
    workflow_description: str = Field(
        default="",
        description="For create_workflow: optional one-line description.",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["create_workflow"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class AgentBuilderOutput(BaseModel):
    request_id: Optional[str] = None
    validation_issues: Optional[List[Dict[str, Any]]] = None
    required_access: Optional[List[Dict[str, Any]]] = None
    saved_revision: Optional[str] = None
    activation_state: Optional[str] = None
    binding_results: Optional[List[Dict[str, Any]]] = None
    operation: Optional[str] = None
    summary: Optional[str] = None
    #: For ``add_tool`` / ``add_subagent``: the ops this call saved (as
    #: announced to editors), plus a saved tool the calling agent's run
    #: lacks. The agent loop binds every tool ``add_node`` here for the rest
    #: of the run. Empty for ``add_skill``.
    operations: Optional[List[Dict[str, Any]]] = None
    # inspect_canvas extras
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    you: Optional[Dict[str, Any]] = None
    #: A hired employee's rules: {asks_first, agents}.
    employee: Optional[Dict[str, Any]] = None
    # What the agent may add, each entry with a name and a description.
    available_tools: Optional[List[Dict[str, Any]]] = None
    available_agents: Optional[List[Dict[str, Any]]] = None
    available_skills: Optional[List[Dict[str, Any]]] = None
    # create_workflow extras
    workflow_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


# ----------------------------------------------------------------------------
# Node
# ----------------------------------------------------------------------------


class AgentBuilderNode(ToolNode):
    type = _AGENT_BUILDER_TYPE
    display_name = "Agent Builder"
    subtitle = "Runtime canvas-mutation tool"
    group = ("tool", "ai")
    description = (
        "Inspect the workflow and add tools, skills and specialists. Managed employee Talk can add "
        "specialists to its lead. Editor team leads can also add their own specialists. "
        "Wire to the calling agent's input-tools handle."
    )
    component_kind = "tool"
    # The per-tool activity gets the run's canvas (ctx.nodes / ctx.edges)
    # only for nodes that ask: add_tool compares it with the saved graph to
    # tell whether a saved tool is already bound in this run.
    needs_canvas = True
    tool_name = "agent_builder"
    tool_description = (
        "Inspect and grow the workflow canvas while you run. ALWAYS call "
        "inspect_canvas FIRST: it lists what you may add (available_tools, "
        "available_agents, available_skills) and the current canvas. "
        "Managed employee Talk can call add_subagent to add a specialist to its existing lead. "
        "Supply its purpose and additional tool_types. To equip a connected specialist, use "
        "add_tool or add_skill with its target_member_id. inspect_node reads live contracts; search_docs and "
        "read_doc provide shipped documentation. plan_update validates a "
        "change and access; apply_update applies authorized changes. "
        "create_workflow creates a configured employee team through the shared "
        "Hire pipeline, only from a trusted owner request. When a change is "
        "refused, the summary says why in one plain sentence: pass it on."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {"hideRunButton": True}
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = AgentBuilderParams
    Output = AgentBuilderOutput

    @Operation("search_docs")
    async def search_docs(self, ctx: NodeContext, params: AgentBuilderParams) -> AgentBuilderOutput:
        from services.builder_documentation import BuilderDocumentation

        docs = BuilderDocumentation()
        return AgentBuilderOutput(operation="search_docs", summary="Shipped documentation; live node contracts take precedence.", operations=[],
                                  documents=docs.search(params.query, params.limit), documentation_count=len(docs.documents))

    @Operation("read_doc")
    async def read_doc(self, ctx: NodeContext, params: AgentBuilderParams) -> AgentBuilderOutput:
        from services.builder_documentation import BuilderDocumentation

        try:
            document = BuilderDocumentation().read(params.document_id, params.offset, params.max_chars)
        except ValueError as exc:
            return _refused("read_doc", str(exc))
        return AgentBuilderOutput(operation="read_doc", summary="Read shipped documentation.", operations=[], document=document)

    @Operation("inspect_node")
    async def inspect_node(self, ctx: NodeContext, params: AgentBuilderParams) -> AgentBuilderOutput:
        from services.builder_documentation import BuilderDocumentation
        from services.plugin.base import locked_tool_fields
        from services.plugin.deps import get_database

        canvas = await _load_canvas(ctx, get_database())
        node = canvas.node(params.node_id) if params.node_id else None
        if params.node_id and node is None:
            return _refused("inspect_node", "That node is not in this workflow.")
        node_type = str(node.get("type")) if node else params.node_type
        cls = get_node_class(node_type)
        if cls is None:
            return _refused("inspect_node", "Unknown node type. Inspect the canvas catalogue first.")
        docs = BuilderDocumentation()
        contract = {
            "type": node_type, "metadata": cls._metadata_dict(),
            "parameters_schema": cls.Params.model_json_schema() if cls.Params else {},
            "output_schema": cls.Output.model_json_schema() if cls.Output else {},
            "locked_fields": sorted(locked_tool_fields(cls)),
            "available": not _is_blocked_by_allowlist(cls, node_type, _allowlist_config()),
            "documentation_ids": docs.related(node_type),
            "examples": docs.examples(node_type),
            "precedence": "This live plugin contract overrides conflicting prose. Credential IDs describe requirements; stored secrets are never included.",
        }
        return AgentBuilderOutput(operation="inspect_node", summary="Current registered node contract.", operations=[], contract=contract)

    @Operation("plan_update")
    async def plan_update(self, ctx: NodeContext, params: AgentBuilderParams) -> AgentBuilderOutput:
        if params.change_operation == "apply_saved":
            return AgentBuilderOutput(operation="plan_update", summary="Apply saved capabilities when current work finishes; paused employees stay paused.",
                                      operations=[], activation_state="planned", validation_issues=[])
        planned = params.model_copy(update={"operation": params.change_operation, "dry_run": True})
        result = await getattr(self, params.change_operation)(ctx, planned)
        result.operation = "plan_update"
        return result

    @Operation("apply_update")
    async def apply_update(self, ctx: NodeContext, params: AgentBuilderParams) -> AgentBuilderOutput:
        if params.change_operation != "apply_saved":
            applied = params.model_copy(update={"operation": params.change_operation, "dry_run": False})
            result = await getattr(self, params.change_operation)(ctx, applied)
            result.operation = "apply_update"
            return result
        from services.employees.permissions import trusted_owner_request
        from services.employees.safe_apply import apply_saved_changes
        from services.plugin.deps import get_database

        database = get_database()
        if not await trusted_owner_request(database, ctx):
            return _refused("apply_update", "Ask the owner in Talk to apply this change.")
        if params.stop_work:
            permission = await _permission(database, ctx, params, "stop_work_and_apply", [_caller(ctx) or ""], {"interrupt_current_work": True})
            if permission:
                return permission
        result = await apply_saved_changes(database, str(ctx.workflow_id), owner_id=ctx.user_id, key=_mutation_id(ctx, params, _caller(ctx)), stop_work=params.stop_work)
        return AgentBuilderOutput(operation="apply_update", summary=result.get("summary") or "Saved capabilities are being applied safely.", operations=[], **{key: value for key, value in result.items() if key not in {"operation", "summary", "operations"}})

    # ---- inspect_canvas (read-only) ---------------------------------------

    @Operation("inspect_canvas")
    async def inspect_canvas(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database
        from services.workflow_context_migration import load_node_parameters

        _log_op_entry("inspect_canvas", ctx)
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)
        parameters = await load_node_parameters(database, canvas.nodes)
        type_by_id = {node.get("id"): node.get("type") for node in canvas.nodes}

        node_summaries = [
            {
                "id": node.get("id"),
                "type": node.get("type"),
                "label": (node.get("data") or {}).get("label") or node.get("type"),
                "key_params": _key_params(parameters.get(str(node.get("id")), {})),
            }
            for node in canvas.nodes
        ]
        edge_summaries = [
            {
                "source": edge.get("source"),
                "target": edge.get("target"),
                "source_handle": edge.get("sourceHandle"),
                "target_handle": edge.get("targetHandle"),
            }
            for edge in canvas.edges
        ]
        you = None
        tools: List[Dict[str, Any]] = []
        if caller:
            incoming = [
                {"source_id": edge.get("source"), "source_type": type_by_id.get(edge.get("source")), "target_handle": edge.get("targetHandle")}
                for edge in canvas.edges
                if edge.get("target") == caller
            ]
            outgoing = [
                {"target_id": edge.get("target"), "target_type": type_by_id.get(edge.get("target")), "source_handle": edge.get("sourceHandle")}
                for edge in canvas.edges
                if edge.get("source") == caller
            ]
            you = {"node_id": caller, "incoming": incoming, "outgoing": outgoing}
            tools = [entry for entry in incoming if entry["target_handle"] == TOOLS_INPUT]

        available_skills = await _catalogue_skills(database, employee=employee is not None)
        if employee is None:
            available_tools, available_agents = _catalogue_tools(), _catalogue_agents()
            rules = None
        else:
            from services.employees.policy import asks_first
            from services.node_allowlist import is_hire_allowed

            available_tools = await _employee_tools(employee)
            available_agents = [entry for entry in _catalogue_agents() if not _is_team_lead(entry["type"])
                                and is_hire_allowed(entry["type"])] if employee.row.team_plan else []
            rules = {"asks_first": asks_first(employee.row), "agents": employee.agents}

        parts = [f"{len(canvas.nodes)} nodes"]
        if tools:
            types = ", ".join(sorted({entry["source_type"] or "?" for entry in tools}))
            parts.append(f"{len(tools)} tool(s) wired to you ({types})")
        parts.append(f"{len(available_tools)} tool / {len(available_agents)} agent / {len(available_skills)} skill types you may add")
        return AgentBuilderOutput(
            operation="inspect_canvas",
            summary=", ".join(parts) + ".",
            nodes=node_summaries,
            edges=edge_summaries,
            you=you,
            employee=rules,
            available_tools=available_tools,
            available_agents=available_agents,
            available_skills=available_skills,
        )

    # ---- add_tool ---------------------------------------------------------

    @Operation("add_tool")
    async def add_tool(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database

        _log_op_entry("add_tool", ctx, node_type=params.node_type)
        node_type = (params.node_type or "").strip()
        if not node_type:
            return _refused("add_tool", "add_tool: node_type is required.")
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)

        if employee is None:
            allowed = _allowed_tool_types()
            if node_type not in allowed:
                return _refused(
                    "add_tool",
                    f"add_tool: '{node_type}' is not an allowed tool type. Allowed types: {', '.join(sorted(allowed))}. "
                    "Call inspect_canvas for the full catalogue with descriptions.",
                )
            label = getattr(get_node_class(node_type), "display_name", "") or node_type
            targets = [caller] if caller else []
        else:
            if get_node_class(node_type) is None:
                # Not a node type at all (a display name, say): a hint for
                # the agent, not a refusal to pass on.
                return _refused("add_tool", f"add_tool: '{node_type}' is not a node type. Use a type from inspect_canvas available_tools.")
            target = params.target_member_id or caller
            if target not in employee.agents:
                return _refused("add_tool", "Choose a member of this employee’s team.")
            targets = [str(target)]
        problem = _unsaved(canvas, targets)
        if problem:
            return _refused("add_tool", problem)
        if set(params.tool_parameters) - {node_type}:
            return _refused("add_tool", "Supply settings only for the tool being added.")
        try:
            supplied = _validated_tool_settings(node_type, params.tool_parameters.get(node_type, {}))
            tool_params = supplied
            if employee is not None:
                label, tool_params = await _employee_tool_settings(database, employee, node_type,
                    str(canvas.node(targets[0])["type"]), supplied, await _connected_apps(), params.app_id, params.operation)
        except ValueError as exc:
            return _refused("add_tool", str(exc))
        # A type several apps share is the agent's tool only on the node
        # naming the same app.
        field_name = _app_field(node_type)
        named = (field_name, str(tool_params[field_name])) if field_name and tool_params.get(field_name) else None
        current: Dict[str, Dict[str, Any]] = {}
        if named is not None or supplied:
            for target in targets:
                for source in canvas.sources(target, TOOLS_INPUT):
                    if (canvas.node(source) or {}).get("type") == node_type:
                        current[source] = await database.get_node_parameters(source) or {}
        expanded = []
        for target in targets:
            existing = _existing_tool(canvas, target, node_type, named, current)
            if not existing or any(current.get(existing, {}).get(key) != value for key, value in supplied.items()):
                expanded.append(target)
        grant_ids: List[str] = []
        if employee is not None and expanded:
            permission = await _permission(database, ctx, params, node_type, expanded, {"parameters": tool_params}, grant_ids)
            if permission:
                return permission
        if params.dry_run:
            return AgentBuilderOutput(operation="add_tool", summary=f"Ready to add {label} with validated access.", operations=[], validation_issues=[], required_access=[], activation_state="planned")

        # Parameter reads and ownership decisions use the same mutation
        # transaction. Split legacy shared bindings for this target only.
        saved_tool_parameters: Dict[str, Dict[str, Any]] = {}

        async def read_tool_parameters(session: Any) -> None:
            from models.database import NodeParameter, Workflow
            from sqlmodel import select

            workflow = await session.get(Workflow, canvas.workflow_id)
            if employee is not None:
                from models.employees import Employee
                from services.employees.start import AGENT_ROLES

                current = (await session.execute(select(Employee).where(Employee.workflow_id == canvas.workflow_id))).scalar_one_or_none()
                roles = current.node_roles or {} if current else {}
                members = {value for role, value in roles.items() if role in AGENT_ROLES or
                           (role.startswith("specialist_") and role.rsplit("_", 1)[-1].isdigit())}
                if targets[0] not in members:
                    raise ValueError("The target no longer belongs to this employee")
            ids = [node["id"] for node in (workflow.data or {}).get("nodes", []) if node.get("type") == node_type] if workflow else []
            if ids:
                rows = (await session.execute(select(NodeParameter).where(NodeParameter.node_id.in_(ids)))).scalars().all()
                saved_tool_parameters.update({row.node_id: dict(row.parameters or {}) for row in rows})

        def prepare(graph: Mapping[str, Any]) -> Tuple[GraphAdditions, Mapping[str, str]]:
            live = _Canvas(canvas.workflow_id, list(graph.get("nodes") or []), list(graph.get("edges") or []), True)
            if any(live.node(target) is None for target in targets):
                raise ValueError("The target agent was removed")
            target = targets[0]
            existing = _existing_tool(live, target, node_type, named, saved_tool_parameters)
            previous = saved_tool_parameters.get(existing, {})
            patch = {key: value for key, value in supplied.items() if previous.get(key) != value} if existing else {}
            if employee is not None and target not in expanded and (existing is None or patch):
                raise ValueError("Access must be checked again for the changed binding")
            owners = {str(edge.get("target")) for edge in live.edges if edge.get("source") == existing and
                      (edge.get("targetHandle") or edge.get("target_handle")) == TOOLS_INPUT} if existing else set()
            if existing and owners == {target}:
                return GraphAdditions(merges=(ParamMerge(existing, patch),) if patch else ()), {"tool": existing}
            x, y = live.position(targets[0])
            spread = 170 * len(live.sources(targets[0], TOOLS_INPUT))
            return GraphAdditions(
                nodes=(NewNode("tool", node_type, label, {**previous, **patch} if existing else tool_params, position=(x - 240 + spread, y + 240)),),
                edges=(tool_edge("tool", target),),
                removed_edges=(EdgeRemoval(existing, target, TOOLS_INPUT),) if existing else (),
            ), {}
        try:
            result = await _save(ctx, database, canvas, GraphAdditions(), params, caller, prepare=prepare, grant_ids=grant_ids, read_for_prepare=read_tool_parameters)
        except _AccessChanged as exc:
            return _refused("add_tool", str(exc))
        except ValueError:
            logger.warning("Agent Builder change no longer fits the workflow", workflow_id=canvas.workflow_id, exc_info=True)
            return _refused("add_tool", "The workflow changed while I was adding to it. Try again.")
        if result is None:
            return _refused("add_tool", "Save the workflow first, then ask again.")

        node_id = str(result.node_ids["tool"])
        label = result.labels.get("tool") or canvas.label(node_id)
        operations = _scoped_runtime_operations(result.operations, caller)
        changed = bool(result.operations)
        removed = {op.get("edge_id") for op in result.operations if op.get("type") == "delete_edge"}
        run_types = {node.get("id"): node.get("type") for node in ctx.nodes or []}
        # Another node of a type several apps share names another app: its
        # tools have other names, so it is not replaced.
        replaces_bound_tool = caller in targets and any(
            edge.get("target") == caller and (edge.get("targetHandle") or edge.get("target_handle")) == TOOLS_INPUT and
            (edge.get("id") in removed or (named is None and edge.get("source") != node_id and run_types.get(edge.get("source")) == node_type))
            for edge in ctx.edges or []
        )
        if replaces_bound_tool:
            # Hot rebind appends tools; replacing an admitted same-name tool
            # waits for Apply instead of exposing two callable identities.
            operations = []
        # A saved tool this run started without (it was added after the
        # run's snapshot) is handed back for the agent loop to bind.
        bind = not replaces_bound_tool and caller in targets and node_id not in _added_nodes(result) and not _run_has_tool(ctx, node_id, str(caller))
        if bind:
            saved = await database.get_node_parameters(node_id) or {}
            operations.insert(0, workflow_ops.add_node(node_id, node_type, saved, label=label, minted_id=node_id))
        summary = (f"Added {label} to that team member’s responsibilities. {_APPLY_HINT}" if caller not in targets and changed
                   else _tool_summary(ctx, employee=employee is not None, label=label, node_id=node_id, changed=changed, bind=bind))
        if replaces_bound_tool:
            summary = f"Saved a separate {label} for this team member. {_APPLY_HINT}"
        return AgentBuilderOutput(
            operation="add_tool",
            summary=summary,
            operations=operations,
            request_id=_mutation_id(ctx, params, caller),
            activation_state="saved",
            saved_revision=result.saved_revision,
            binding_results=[{"node_id": node_id, "saved": True, "available_in_run": bool(not replaces_bound_tool and caller in targets and (_rebind_on(ctx) or _run_has_tool(ctx, node_id, str(caller))))}],
        )

    # ---- add_skill --------------------------------------------------------

    @Operation("add_skill")
    async def add_skill(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database

        _log_op_entry("add_skill", ctx, skill_name=params.skill_name)
        name = (params.skill_name or "").strip()
        if not name:
            return _refused("add_skill", "add_skill: skill_name is required.")
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)
        if employee is not None:
            from services.employees.policy import check_skill

            decision = check_skill(name)
            if not decision.allowed:
                return _refused("add_skill", decision.reason)
        skill = await _find_skill(database, name, employee=employee is not None)
        if skill is None:
            if employee is not None:
                return _refused("add_skill", f"There's no skill called '{name}' in the owner's library or in Discover.")
            return _refused("add_skill", f"add_skill: no skill named '{name}'. Call inspect_canvas for the available skills.")
        targets = (employee.agents if employee is not None else []) or ([caller] if caller else [])
        if employee is not None and employee.row.team_plan:
            target = params.target_member_id or caller
            if target not in employee.agents:
                return _refused("add_skill", "Choose a member of this employee’s team.")
            targets = [str(target)]
        problem = _unsaved(canvas, targets)
        if problem:
            return _refused("add_skill", problem)
        skills_of = {target: canvas.source_of_type(target, SKILL_INPUT, _MASTER_SKILL_TYPE) for target in targets}
        expanded = []
        for target, holder in skills_of.items():
            row = (await database.get_node_parameters(holder) or {}) if holder else {}
            if not ((row.get("skills_config") or {}).get(name) or {}).get("enabled"):
                expanded.append(target)
        if employee is not None and employee.row.team_plan and expanded:
            if any(edge.get("source") in {skills_of[target] for target in expanded if skills_of[target]} and edge.get("targetHandle") == SKILL_INPUT and edge.get("target") not in targets for edge in canvas.edges):
                return _refused("add_skill", "This team shares instructions between members. Review those connections before changing one member’s skills.")
        grant_ids: List[str] = []
        if employee is not None and expanded:
            permission = await _permission(database, ctx, params, "skill:" + name, expanded, {"instructions_sha256": hashlib.sha256(skill.instructions.encode()).hexdigest()}, grant_ids)
            if permission:
                return permission
        if params.dry_run:
            return AgentBuilderOutput(operation="add_skill", summary=f"Ready to add the '{name}' skill.", operations=[], validation_issues=[], required_access=[], activation_state="planned")

        entry = {"enabled": True, "instructions": skill.instructions, "isCustomized": False, "description": skill.description}
        # Each target's Skills node gets the skill; a target without one is
        # wired to the shared node (never a second one: two Skills nodes on
        # one agent collide on the Skill tool's own entry), else to a new one.
        bare = [target for target in targets if skills_of[target] is None]
        shared = (employee.skills if employee is not None else None) or next((node for node in skills_of.values() if node), None)
        if employee is not None and employee.row.team_plan:
            shared = next((node for node in skills_of.values() if node), None)
        holders = list(dict.fromkeys([node for node in skills_of.values() if node] + ([shared] if shared and bare else [])))
        merges = []
        for holder in holders:
            row = await database.get_node_parameters(holder) or {}
            if not ((row.get("skills_config") or {}).get(name) or {}).get("enabled"):
                merges.append(ParamMerge(holder, {"skills_config": {name: entry}}))
        nodes: Tuple[NewNode, ...] = ()
        if bare and shared is None:
            from services.employees.policy import SKILL_TOOL_ENTRY, SKILL_TOOL_NAME

            x, y = canvas.position(bare[0])
            config = {SKILL_TOOL_NAME: dict(SKILL_TOOL_ENTRY), name: entry}
            nodes = (
                NewNode(
                    "skills",
                    _MASTER_SKILL_TYPE,
                    "Skills" if employee is not None else "Master Skill",
                    {"skill_folder": "assistant", "skills_config": config},
                    position=(x - 420, y + 240),
                ),
            )
        edges = tuple(skill_edge("skills" if nodes else str(shared), target) for target in bare)
        try:
            result = await _save(ctx, database, canvas, GraphAdditions(nodes=nodes, edges=edges, merges=tuple(merges)), params, caller, grant_ids=grant_ids)
        except _AccessChanged as exc:
            return _refused("add_skill", str(exc))
        except ValueError:
            logger.warning("Agent Builder change no longer fits the workflow", workflow_id=canvas.workflow_id, exc_info=True)
            return _refused("add_skill", "The workflow changed while I was adding to it. Try again.")
        if result is None:
            return _refused("add_skill", "Save the workflow first, then ask again.")

        # No operations back to the agent: a Skills node is nothing to bind,
        # and its merged row (every skill's text) would only grow the stored
        # conversation. Editors got the batch from the save.
        rewired = any(op.get("type") in ("add_node", "add_edge") for op in result.operations)
        holder = result.node_ids.get("skills") or shared or (holders[0] if holders else "")
        if employee is not None:
            if rewired:
                summary = f"Learned '{name}'. {_APPLY_HINT}"
            elif result.operations:
                summary = f"Learned '{name}'. It applies from the next message."
            else:
                summary = f"You already have the skill '{name}'."
        elif rewired:
            summary = (
                f"Added '{name}' to a Master Skill wired to you (node id={holder}). "
                "It applies from your next run (a running deployment needs a restart first)."
            )
        elif result.operations:
            summary = f"Enabled '{name}' on your Master Skill (node id={holder}). It applies from your next run."
        else:
            summary = f"Skill '{name}' is already enabled on your Master Skill (node id={holder}). No change needed."
        return AgentBuilderOutput(operation="add_skill", summary=summary, operations=[], request_id=_mutation_id(ctx, params, caller), saved_revision=result.saved_revision, activation_state="saved")

    # ---- add_subagent -----------------------------------------------------

    async def _equip_existing_member(self, ctx: NodeContext, params: AgentBuilderParams, member: str) -> AgentBuilderOutput:
        """Reuse the member's normal, permission-checked tool and skill mutations."""
        requests = [
            AgentBuilderParams(operation="add_tool", target_member_id=member, node_type=kind,
                               app_id=params.app_id, tool_parameters={kind: params.tool_parameters.get(kind, {})})
            for kind in dict.fromkeys(params.tool_types)
        ] + [
            AgentBuilderParams(operation="add_skill", target_member_id=member, skill_name=name)
            for name in dict.fromkeys(params.skill_names)
        ]
        request_key = _mutation_id(ctx, params, _caller(ctx))
        pending = []
        revision = None
        # Check the whole request before saving any addition. Each write uses
        # its own ledger key and rechecks its approved scope inside the save.
        for planning in (True, False):
            for index, request in enumerate(requests):
                context = replace(ctx, raw={**(ctx.raw or {}), "tool_call_id": f"{request_key}:member:{index}",
                                            "save_after_builder_review": not params.dry_run})
                action = self.add_tool if request.operation == "add_tool" else self.add_skill
                result = await action(context, request.model_copy(update={"dry_run": planning}))
                if planning and result.required_access:
                    pending.extend(result.required_access)
                    continue
                if result.activation_state == "blocked" or result.validation_issues:
                    return result.model_copy(update={"operation": "add_subagent", "member_id": member})
                revision = result.saved_revision or revision
            if pending:
                return AgentBuilderOutput(operation="add_subagent", member_id=member, activation_state="blocked",
                    operations=[], required_access=pending,
                    summary="The specialist is already on this team. The owner must approve the requested capabilities.")
            if params.dry_run:
                return AgentBuilderOutput(operation="add_subagent", member_id=member, activation_state="planned",
                    operations=[], summary="The requested capabilities can be added to this specialist.")
        return AgentBuilderOutput(operation="add_subagent", member_id=member, activation_state="saved",
            operations=[], saved_revision=revision, request_id=request_key,
            summary=f"The specialist ({member}) is on the team with the requested tools and skills. {_APPLY_HINT}")

    @Operation("add_subagent")
    async def add_subagent(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        from services.plugin.deps import get_database

        _log_op_entry("add_subagent", ctx, agent_type=params.agent_type)
        agent_type = (params.agent_type or "").strip()
        if not agent_type:
            return _refused("add_subagent", "add_subagent: agent_type is required.")
        database = get_database()
        caller = _caller(ctx)
        canvas = await _load_canvas(ctx, database)
        employee = await _employee_of(database, canvas)
        if employee is not None and employee.row.team_plan:
            caller = (employee.row.node_roles or {}).get("agent") or caller
        caller_type = str((canvas.node(caller) or {}).get("type") or "")
        if not _is_team_lead(caller_type):
            leads = ", ".join(sorted(_TEAM_LEAD_TYPES))
            return _refused(
                "add_subagent", f"add_subagent: only team-lead agents ({leads}) can spawn delegates. This agent is '{caller_type}'."
            )
        allowed = _allowed_subagent_types()
        if agent_type not in allowed:
            return _refused(
                "add_subagent",
                f"add_subagent: '{agent_type}' is not an allowed agent type. Allowed types: {', '.join(sorted(allowed))}. "
                "Call inspect_canvas for the full catalogue with descriptions.",
            )
        if _is_team_lead(agent_type):
            return _refused("add_subagent", f"add_subagent: cannot spawn another team-lead ('{agent_type}'); pick a specialized agent instead.")
        if employee is not None:
            from services.node_allowlist import is_hire_allowed
            if not is_hire_allowed(agent_type):
                return _refused("add_subagent", f"'{agent_type}' is disabled for hired employees.")
        problem = _unsaved(canvas, [str(caller)])
        if problem:
            return _refused("add_subagent", problem)
        # Custom aiAgent teammates are repeatable (their delegation identity
        # is per node); every other type has one type-wide delegate name.
        existing = None if agent_type == "aiAgent" else canvas.source_of_type(str(caller), _TEAMMATES_INPUT, agent_type)
        if set(params.tool_parameters) - set(params.tool_types):
            return _refused("add_subagent", "Each configured tool must also appear in tool_types.")
        if existing and employee is not None and employee.row.team_plan and (params.tool_types or params.skill_names):
            return await self._equip_existing_member(ctx, params, existing)
        x, y = canvas.position(str(caller))
        label = getattr(get_node_class(agent_type), "display_name", "") or agent_type
        caller_params = await database.get_node_parameters(str(caller)) or {}
        specialist_params = {key: caller_params[key] for key in ("provider", "model", "temperature", "max_tokens") if key in caller_params}
        specialist_params["system_message"] = params.purpose.strip() or (
            f"{getattr(get_node_class(agent_type), 'description', '')} Work only on the lead's assigned mission. "
            "Use the supplied context and acceptance criteria, return evidence and a result for lead review, and never publish a final reply independently."
        )
        nodes = [NewNode("agent", agent_type, label, specialist_params, position=(x + 300, y + 200)),
                 NewNode("context", _CONTEXT_TYPE, "Context", position=(x + 300, y + 20), context_of="agent")]
        edges = [Edge("agent", _TEAMMATE_OUTPUT, str(caller), _TEAMMATES_INPUT)]
        bundled_tool_types: set[str] = set()
        allowed_tools = _allowed_tool_types()
        if agent_type == "browser_agent":
            from services.browser_agent_recipe import BROWSER_AGENT_ROLE, browser_agent_additions
            if not {"browser", "visionAnalyze"}.issubset(allowed_tools) or "web_agent" in _allowlist_config().get("disabled_skill_folders", []):
                return _refused("add_subagent", "Browser tools or instructions are disabled by the operator.")
            specialist_params["system_message"] = BROWSER_AGENT_ROLE + "\n" + specialist_params["system_message"]
            bundle = browser_agent_additions(agent_parameters=specialist_params, position=(x + 300, y + 200))
            nodes = list(bundle.nodes)
            edges.extend(bundle.edges)
            bundled_tool_types.update({"browser", "visionAnalyze"})
        if agent_type == "coding_agent" and not existing:
            from services.coding_agent_recipe import coding_agent_additions, default_pi_tool_types

            bundled_tool_types.update(default_pi_tool_types())
            if not bundled_tool_types.issubset(allowed_tools):
                return _refused("add_subagent", "Pi coding tools are disabled by the operator.")
            bundle = coding_agent_additions(position=(x + 300, y + 200),
                create_agent=False, create_context=False)
            nodes.extend(bundle.nodes)
            edges.extend(bundle.edges)
        tool_settings = {}
        connected = await _connected_apps() if employee is not None else []
        for index, tool_type in enumerate(dict.fromkeys([*params.tool_types, *sorted(bundled_tool_types)])):
            if tool_type not in allowed_tools:
                return _refused("add_subagent", f"The specialist cannot use '{tool_type}'. Inspect available tools first.")
            tool_label = getattr(get_node_class(tool_type), "display_name", "") or tool_type
            try:
                settings = _validated_tool_settings(tool_type, params.tool_parameters.get(tool_type, {}))
                if employee is not None:
                    tool_label, settings = await _employee_tool_settings(database, employee, tool_type, agent_type,
                        settings, connected, params.app_id, params.operation)
            except ValueError as exc:
                return _refused("add_subagent", str(exc))
            if settings:
                tool_settings[tool_type] = settings
            if tool_type not in bundled_tool_types:
                ref = f"tool_{index}"
                nodes.append(NewNode(ref, tool_type, tool_label, settings, position=(x + 160 + index * 170, y + 440)))
                edges.append(tool_edge(ref, "agent"))
        nodes = [replace(node, params={**node.params, **tool_settings[node.type]})
                 if node.type in bundled_tool_types and node.type in tool_settings else node for node in nodes]
        skill_config = {}
        if agent_type == "browser_agent":
            skill_config.update(next(node.params["skills_config"] for node in nodes if node.ref == "skills"))
        for name in dict.fromkeys(params.skill_names):
            if employee is not None:
                from services.employees.policy import check_skill
                decision = check_skill(name)
                if not decision.allowed:
                    return _refused("add_subagent", decision.reason)
            skill = await _find_skill(database, name, employee=employee is not None)
            if skill is None:
                return _refused("add_subagent", f"There is no available skill called '{name}'.")
            skill_config[name] = {"enabled": True, "instructions": skill.instructions, "description": skill.description, "isCustomized": False}
        if skill_config:
            from services.employees.policy import SKILL_TOOL_ENTRY, SKILL_TOOL_NAME

            skill_config[SKILL_TOOL_NAME] = dict(SKILL_TOOL_ENTRY)
            if agent_type == "browser_agent":
                nodes = [replace(node, params={**node.params, "skills_config": skill_config}) if node.ref == "skills" else node for node in nodes]
            else:
                nodes.append(NewNode("skills", _MASTER_SKILL_TYPE, "Specialist skills", {"skill_folder": "assistant", "skills_config": skill_config}, position=(x + 20, y + 200)))
                edges.append(skill_edge("skills", "agent"))
        additions = GraphAdditions(
            nodes=tuple(nodes), edges=tuple(edges),
        )
        grant_ids: List[str] = []
        if employee is not None and not existing:
            permission = await _permission(database, ctx, params, agent_type, [str(caller)], {
                "purpose": specialist_params["system_message"], "tool_types": sorted(set(params.tool_types) | bundled_tool_types),
                "tool_parameters": tool_settings, "skill_names": sorted(set(params.skill_names)),
                "model": specialist_params.get("model"), "provider": specialist_params.get("provider"),
            }, grant_ids)
            if permission:
                return permission
        if params.dry_run:
            return AgentBuilderOutput(operation="add_subagent", summary=f"Ready to add {label} with its own Context and scoped capabilities.", operations=[], validation_issues=[], required_access=[], activation_state="planned")
        def prepare(graph: Mapping[str, Any]) -> Tuple[GraphAdditions, Mapping[str, str]]:
            live = _Canvas(canvas.workflow_id, list(graph.get("nodes") or []), list(graph.get("edges") or []), True)
            if live.node(caller) is None:
                raise ValueError("The lead was removed")
            reused = None if agent_type == "aiAgent" else live.source_of_type(str(caller), _TEAMMATES_INPUT, agent_type)
            if existing and existing != reused:
                raise ValueError("The specialist was removed or replaced")
            return (GraphAdditions(), {"agent": reused}) if reused else (additions, {})
        try:
            result = await _save(ctx, database, canvas, additions, params, caller, prepare=prepare, grant_ids=grant_ids)
        except _AccessChanged as exc:
            return _refused("add_subagent", str(exc))
        except ValueError:
            logger.warning("Agent Builder change no longer fits the workflow", workflow_id=canvas.workflow_id, exc_info=True)
            return _refused("add_subagent", "The workflow changed while I was adding to it. Try again.")
        if result is None:
            return _refused("add_subagent", "Save the workflow first, then ask again.")
        if not any(op.get("type") == "add_node" for op in result.operations):
            member = result.node_ids.get("agent") or existing
            if employee is not None and (params.tool_types or params.skill_names):
                return await self._equip_existing_member(ctx, params, member)
            return AgentBuilderOutput(operation="add_subagent", operations=[], member_id=member, activation_state="saved",
                summary=f"Teammate '{agent_type}' is already wired to the lead (node id={member}).")
        label = result.labels.get("agent") or label
        return AgentBuilderOutput(
            operation="add_subagent",
            member_id=result.node_ids["agent"],
            summary=f"Added '{label}' with its own Context and scoped capabilities. Assign work through Task Manager for lead review. {_summary_suffix(ctx) if _caller(ctx) == caller else _APPLY_HINT}",
            operations=_scoped_runtime_operations(result.operations, _caller(ctx)),
            request_id=_mutation_id(ctx, params, caller), activation_state="saved",
            saved_revision=result.saved_revision,
        )

    # ---- create_workflow --------------------------------------------------

    @Operation("create_workflow")
    async def create_workflow(
        self,
        ctx: NodeContext,
        params: AgentBuilderParams,
    ) -> AgentBuilderOutput:
        _log_op_entry("create_workflow", ctx, workflow_name=params.workflow_name)
        if not _CREATE_WORKFLOW_ENABLED:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary=(
                    "Employee creation is disabled by the operator."
                ),
            )
        name = (params.workflow_name or "").strip()
        if not name:
            return AgentBuilderOutput(
                operation="create_workflow",
                summary="create_workflow: workflow_name is required.",
            )

        from services.employees.control import builder_create

        request_id = _mutation_id(ctx, params, _caller(ctx))
        result = await builder_create(ctx, name, params.workflow_description.strip(), request_id)
        summary = "Employee saved with a configured team." if result.get("success") else result.get("summary") or "The employee could not be created. Ask the owner in Talk and check the required connections."
        return AgentBuilderOutput(
            operation="create_workflow",
            summary=summary, request_id=request_id, operations=[],
            **{key: value for key, value in result.items() if key not in {"operation", "summary", "request_id", "operations"}},
        )
