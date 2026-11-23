"""Bringing an employee hired by an older builder up to the live Ask first
rule (``LIVE_RULE_BUILDER_VERSION``), on Apply, Turn on Talk and Start.

Graphs built before the rule was live followed it as it was at hire:

- an app reply hired with Ask first off has no gate: it gets one, so the
  reply waits for the owner whenever the live rule says so. The reply node
  then reads the gate's text, subject and recipient, and the edge from the
  agent goes to the gate (the one edge this takes out);
- tools of its apps that asking first left out come in whole (each call
  that sends now waits for the owner per the live rule:
  services/approvals/tool_calls.py), and a browser saved read-only is saved
  whole (it reads only per call while they ask first);
- the agent the owner talks to gets its talk tools (generated UI in the
  chat, and sending through the hire's apps).

One saved mutation (``apply_graph_additions``) keyed by the version, so an
editor with the workflow open adopts it and a retry adds nothing twice;
then the employee records the version. ``plan_upgrade`` is pure. Never
imports ``nodes/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from core.logging import get_logger
from services.approvals.contract import APPROVAL_GATE_TYPE, approved_edge_condition, send_condition
from services.employee_team_connection import EMPLOYEE_TEAM_RESOURCE_FIELDS, EmployeeTeamValidationError
from services.employees import store
from services.employees.apps import AppSpec, get_app
from services.employees.builder import (
    GATE_LABEL,
    LIVE_RULE_BUILDER_VERSION,
    _HAS_SUBJECT,
    _MAIL_APPS,
    _REPLY_FIELDS,
    fill_params,
    talk_tools,
)
from services.employees.policy import check_tool
from services.employees.talk import find_talk_line, plan_talk_tools, sources
from services.graph_build import (
    TOOLS_INPUT,
    SKILL_INPUT,
    TOOL_OUTPUT,
    EdgeRemoval,
    GraphAdditions,
    Labels,
    NewNode,
    ParamMerge,
    label_key,
    main_edge,
    ref,
    skill_edge,
    template_key,
    tool_edge,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class Upgrade:
    additions: GraphAdditions = field(default_factory=GraphAdditions)
    #: ``node_roles`` key -> a ref in ``additions``.
    roles: Mapping[str, str] = field(default_factory=dict)


def _nodes(graph: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(node["id"]): node
        for node in graph.get("nodes") or []
        if isinstance(node, Mapping) and isinstance(node.get("id"), str) and isinstance(node.get("type"), str)
    }


def _position(node: Mapping[str, Any]) -> tuple:
    position = node.get("position") if isinstance(node.get("position"), Mapping) else {}
    x, y = position.get("x"), position.get("y")
    return (x if isinstance(x, (int, float)) else 0, y if isinstance(y, (int, float)) else 0)


def _gate(graph_nodes: Mapping[str, Mapping[str, Any]], roles: Mapping[str, str], apps: Sequence[AppSpec], params: Mapping[str, Mapping[str, Any]]):
    """The gate an ungated app reply gets: (nodes, edges, merges, removals,
    roles), or None when there is nothing to gate."""
    agent, trigger, reply = roles.get("agent"), roles.get("trigger"), roles.get("reply")
    if roles.get("gate") or not (agent in graph_nodes and trigger in graph_nodes and reply in graph_nodes):
        return None
    reply_type = str(graph_nodes[reply]["type"])
    fields = _REPLY_FIELDS.get(reply_type)
    app = next((app for app in apps if app.reply is not None and app.reply.type == reply_type), None)
    if fields is None or app is None:
        return None
    recipient_field, name_field, body_field, max_length = fields
    saved = dict(params.get(reply) or {})
    trigger_key, agent_key, gate_key = template_key(graph_nodes[trigger]), template_key(graph_nodes[agent]), label_key(GATE_LABEL)
    gate_params: Dict[str, Any] = {
        "channel": app.name,
        "recipient": saved.get(recipient_field) or "",
        "recipient_label": ref(trigger_key, name_field),
        # What the reply sent, so an edit the owner made to it carries over.
        "draft": saved.get(body_field) or ref(agent_key, "response"),
        "context_excerpt": ref(trigger_key, "subject" if app.id in _MAIL_APPS else "text"),
        "max_length": max_length,
    }
    patch: Dict[str, Any] = {recipient_field: ref(gate_key, "recipient"), body_field: ref(gate_key, "text")}
    if reply_type in _HAS_SUBJECT:
        if saved.get("subject"):
            gate_params["subject"] = saved["subject"]
        patch["subject"] = ref(gate_key, "subject")
    x, y = _position(graph_nodes[reply])
    nodes = [NewNode("gate", APPROVAL_GATE_TYPE, GATE_LABEL, gate_params, (x, y - 160))]
    edges = [main_edge(agent, "gate", send_condition()), main_edge(trigger, "gate"), main_edge("gate", reply, approved_edge_condition())]
    return nodes, edges, [ParamMerge(reply, patch)], [EdgeRemoval(agent, reply)], {"gate": "gate"}


def plan_upgrade(
    graph: Optional[Mapping[str, Any]],
    *,
    employee: Any,
    roles: Mapping[str, str],
    apps: Sequence[AppSpec],
    params: Mapping[str, Mapping[str, Any]],
    owner_values: Mapping[str, str],
    allowed: Callable[[str], bool],
) -> Upgrade:
    """What brings ``graph`` up to the live rule (see the module docstring).
    ``params`` holds the saved parameters of the reply and browser nodes."""
    graph = graph if isinstance(graph, Mapping) else {}
    graph_nodes = _nodes(graph)
    nodes: List[NewNode] = []
    edges: List[Any] = []
    merges: List[ParamMerge] = []
    removals: List[EdgeRemoval] = []
    new_roles: Dict[str, str] = {}

    if allowed(APPROVAL_GATE_TYPE):
        gate = _gate(graph_nodes, roles, apps, params)
        if gate is not None:
            gate_nodes, gate_edges, gate_merges, gate_removals, gate_roles = gate
            nodes += gate_nodes
            edges += gate_edges
            merges += gate_merges
            removals += gate_removals
            new_roles.update(gate_roles)

    agent = roles.get("agent")
    line = find_talk_line(graph)
    talk_agent = roles.get("talk_agent") or (line.agent if line is not None else None)
    if agent in graph_nodes:
        trigger = roles.get("trigger")
        trigger_key = template_key(graph_nodes[trigger]) if trigger in graph_nodes else None
        have = {str(graph_nodes[tool]["type"]) for tool in sources(graph, agent, TOOLS_INPUT) if tool in graph_nodes}
        x, y = _position(graph_nodes[agent])
        for app in apps:
            for tool in app.tools:
                if tool.type in have:
                    continue
                decision = check_tool(tool.type, employee=employee, connected=None, app=app, allowed=allowed)
                tool_params = fill_params(decision.params, trigger_key, owner_values) if decision.allowed else None
                if tool_params is None:
                    continue
                have.add(tool.type)
                name = f"app_tool_{len(nodes)}"
                nodes.append(NewNode(name, tool.type, decision.label, tool_params, (x - 240 + 170 * len(nodes), y + 400)))
                edges.append(tool_edge(name, agent))
                if talk_agent in graph_nodes and talk_agent != agent:
                    edges.append(tool_edge(name, talk_agent))
                if decision.role:
                    new_roles[decision.role] = name

    browser = roles.get("browser")
    if browser in graph_nodes and (params.get(browser) or {}).get("interaction") == "read_only":
        merges.append(ParamMerge(browser, {"interaction": "full"}))

    if talk_agent in graph_nodes:
        plan = plan_talk_tools(graph, talk_agent, talk_tools(apps, owner_values=owner_values, allowed=allowed))
        nodes += list(plan.additions.nodes)
        edges += list(plan.additions.edges)
        new_roles.update(plan.roles)

    return Upgrade(GraphAdditions(nodes=tuple(nodes), edges=tuple(edges), merges=tuple(merges), removed_edges=tuple(removals)), new_roles)


async def upgrade_employee(database: Any, auth_service: Any, workflow_id: str, *, allowed: Optional[Callable[[str], bool]] = None) -> bool:
    """Upgrade the employee behind ``workflow_id`` when an older builder made
    it. A conflicting team configuration is returned to the owner by Start
    or Apply. Other upgrade failures are logged for a subsequent retry."""
    try:
        upgraded = await _upgrade(database, auth_service, workflow_id, allowed)
    except EmployeeTeamValidationError:
        raise
    except Exception:
        logger.warning("Could not upgrade employee graph", workflow_id=workflow_id, exc_info=True)
        return False
    if upgraded:
        logger.info("Upgraded employee graph", workflow_id=workflow_id)
    return upgraded


def _plan_team_resources(graph: Mapping[str, Any], parameters: Mapping[str, Mapping], team: Mapping[str, Any],
                         roles: Mapping[str, str]):
    from services.employee_team_connection import (
        connected_master_skill_ids, connected_task_manager_ids, employee_skill_parameters, employee_team_edge, task_manager_additions,
    )

    lead, talk = team["lead_node_id"], team["talk_node_id"]
    nodes = _nodes(graph)
    if nodes.get(lead, {}).get("type") != "ai_employee" or nodes.get(talk, {}).get("type") != "aiAgent":
        raise EmployeeTeamValidationError("Restore the saved AI Employee lead and Talk contact before assigning their tools.",
                                          code="INVALID_EMPLOYEE_TEAM_CONNECTION", node_id=lead)
    labels = Labels((node.get("data") or {}).get("label") or "" for node in nodes.values())
    new_nodes, removed, reused = [], [], {}
    edges = [employee_team_edge(lead, talk)]

    for role, owner in (("lead", lead), ("talk", talk)):
        x, y = _position(nodes[owner])
        manager_ref = "task_manager" if owner == lead else "talk_task_manager"
        managers = connected_task_manager_ids(graph, owner)
        recorded_manager = team.get(EMPLOYEE_TEAM_RESOURCE_FIELDS[manager_ref]) or roles.get(manager_ref)
        if len(managers) > 1 or (recorded_manager and managers != [recorded_manager]):
            raise EmployeeTeamValidationError("Connect exactly one assigned Task Manager to this agent.",
                                              code="INVALID_EMPLOYEE_TASK_MANAGER", node_id=owner)
        if managers:
            reused[manager_ref] = managers[0]
        else:
            label = "Task Manager" if owner == lead else "Talk Task Manager"
            batch = task_manager_additions(owner, (x, y + 180), ref=manager_ref, label=labels.take(label))
            new_nodes.extend(batch.nodes)
            edges.extend(batch.edges)

        skill_ref = role + "_skills"
        holders = connected_master_skill_ids(graph, owner)
        holder = team.get(EMPLOYEE_TEAM_RESOURCE_FIELDS[skill_ref]) or roles.get(skill_ref)
        if len(set(holders)) != len(holders) or (holder and holder not in holders):
            raise EmployeeTeamValidationError("Restore the assigned Master Skill connection for this agent.",
                                              code="INVALID_EMPLOYEE_MASTER_SKILL", node_id=owner)
        # Additional editor-added skill bundles remain attached. With no
        # recorded role and multiple bundles, allocate an unambiguous one.
        if not holder and len(holders) == 1:
            holder = holders[0]
        if holder:
            links = [edge for edge in graph.get("edges", []) if edge.get("source") == holder]
            # Legacy graphs sometimes gave two agents the same skills. Keep
            # one original; copy its saved instructions for every owner we
            # detach. A third owner retains the original when it uses it too.
            owners = {edge.get("target") for edge in links}
            if len(links) == 1 or (owner == lead and owners == {lead, talk}):
                reused[skill_ref] = holder
                continue
            if (nodes[holder].get("disabled") or (nodes[holder].get("data") or {}).get("disabled")
                    or any((edge.get("sourceHandle") or edge.get("source_handle")) != TOOL_OUTPUT
                           or (edge.get("targetHandle") or edge.get("target_handle")) != SKILL_INPUT for edge in links)):
                raise EmployeeTeamValidationError("Check the shared Master Skill's connections before assigning a private copy.",
                                                  code="INVALID_EMPLOYEE_MASTER_SKILL", node_id=owner)
            removed.append(EdgeRemoval(holder, owner, SKILL_INPUT))
            params = parameters.get(holder, {})
        else:
            params = employee_skill_parameters(role)
        label = labels.take("Team lead skills" if owner == lead else "Talk skills")
        new_nodes.append(NewNode(skill_ref, "masterSkill", label, params, (x - 180, y + 180)))
        edges.append(skill_edge(skill_ref, owner))
    return GraphAdditions(nodes=new_nodes, edges=edges, removed_edges=removed), reused


async def _upgrade(database: Any, auth_service: Any, workflow_id: str, allowed: Optional[Callable[[str], bool]]) -> bool:
    from services.employees.hire import owner_values_for
    from services.node_allowlist import is_hire_allowed
    from services.workflow_storage.mutate import apply_graph_additions

    employee = await store.get_by_workflow(database, workflow_id)
    if employee is None:
        return False
    team = getattr(employee, "team_plan", None) or {}
    if team:
        if team.get("version") != 2 or (team.get("talk_connection_version")
                                        and all(team.get(key) for key in EMPLOYEE_TEAM_RESOURCE_FIELDS.values())):
            return False
        lead = team["lead_node_id"]
        policy = allowed or is_hire_allowed
        if not policy("taskManager") or not policy("masterSkill"):
            raise EmployeeTeamValidationError("Enable Task Manager and Master Skill for this employee before starting work.",
                                              code="EMPLOYEE_TEAM_TOOLS_UNAVAILABLE", node_id=lead)

        try:
            result = await apply_graph_additions(database, workflow_id, GraphAdditions(),
                                                 prepare_employee_resources=_plan_team_resources,
                                                 mutation_id=f"upgrade:{workflow_id}:lead-talk-managers-skills-v1")
        except EmployeeTeamValidationError:
            raise
        except Exception as exc:
            logger.warning("Could not save employee Task Manager and Master Skill connections", workflow_id=workflow_id, exc_info=True)
            raise EmployeeTeamValidationError("The employee's tool connections could not be saved. Try the employee upgrade again.",
                                              code="EMPLOYEE_TEAM_TOOLS_UPGRADE_FAILED", node_id=lead) from exc
        return bool(result and result.applied)
    if int(employee.builder_version or 1) >= LIVE_RULE_BUILDER_VERSION:
        return False
    workflow = await database.get_workflow(workflow_id)
    if workflow is None:
        return False
    roles = dict(employee.node_roles or {})
    apps = [app for app in (get_app(app_id) for app_id in employee.apps or []) if app is not None]
    params = {node_id: dict(await database.get_node_parameters(node_id) or {}) for node_id in (roles.get("reply"), roles.get("browser")) if node_id}
    upgrade = plan_upgrade(
        workflow.data or {},
        employee=employee,
        roles=roles,
        apps=apps,
        params=params,
        owner_values=await owner_values_for(auth_service),
        allowed=allowed or is_hire_allowed,
    )
    if upgrade.additions:
        result = await apply_graph_additions(
            database, workflow_id, upgrade.additions, mutation_id=f"upgrade:{workflow_id}:v{LIVE_RULE_BUILDER_VERSION}"
        )
        if result is None:
            return False
        new_roles = {role: result.node_ids.get(name, name) for role, name in upgrade.roles.items()}
        if new_roles:
            await store.merge_node_roles(database, workflow_id, new_roles)
    await store.update_employee(database, workflow_id, {"builder_version": LIVE_RULE_BUILDER_VERSION})
    return True


__all__ = ["Upgrade", "plan_upgrade", "upgrade_employee"]


def team_approval_topology_error(
    graph: Optional[Mapping[str, Any]], roles: Mapping[str, str], *, params: Optional[Mapping[str, Mapping[str, Any]]] = None,
    team_plan: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """A team lead's app reply must pass through its existing approval gate.

    Chat-only teams have no external reply role. Tools retain the separate
    per-call approval policy. Never repair a team with legacy single-worker
    attachment rules: that would attach every specialist's apps to the lead.
    """
    reply = roles.get("reply")
    if not reply:
        return None
    graph = graph if isinstance(graph, Mapping) else {}
    nodes = _nodes(graph)
    if reply in nodes and nodes[reply]["type"] == "chatReply":
        return None
    gate, lead = roles.get("gate"), roles.get("agent")
    if not gate or gate not in nodes or nodes[gate]["type"] != APPROVAL_GATE_TYPE:
        return "team_approval_topology_invalid"
    incoming = [edge for edge in graph.get("edges") or [] if edge.get("target") == reply]
    if not incoming or any(edge.get("source") != gate for edge in incoming):
        return "team_approval_topology_invalid"
    if any((edge.get("data") or {}).get("condition") != approved_edge_condition() for edge in incoming):
        return "team_approval_topology_invalid"
    delivery = roles.get("job_delivery")
    runtime_delivery = bool(team_plan and team_plan.get("version") in (2, 3))
    if delivery or runtime_delivery:
        saved = (params or {}).get(delivery) or {}
        delivered = (team_plan or {}).get("delivery_node_ids", []) if runtime_delivery else saved.get("delivery_node_ids") or []
        if runtime_delivery:
            if team_plan.get("lead_node_id") != lead or gate not in delivered or reply not in delivered or any(node_id not in nodes for node_id in delivered):
                return "team_approval_topology_invalid"
        elif (delivery not in nodes or nodes[delivery]["type"] != "employeeJob"
                or saved.get("operation") != "deliver" or saved.get("lead_node_id") != lead
                or gate not in delivered or reply not in delivered):
            return "team_approval_topology_invalid"
        if not runtime_delivery and not any(edge.get("source") == lead and edge.get("target") == delivery for edge in graph.get("edges") or []):
            return "team_approval_topology_invalid"
        # Only the job delivery boundary injects the reviewed draft into the
        # gate. A parallel graph edge could publish an assignment acknowledgement.
        if any(edge.get("target") == gate for edge in graph.get("edges") or []):
            return "team_approval_topology_invalid"
        fields = _REPLY_FIELDS.get(str(nodes[reply]["type"]))
        if fields:
            recipient_field = fields[0]
            if ((params or {}).get(reply) or {}).get(recipient_field) != ref(template_key(nodes[gate]), "recipient"):
                return "team_approval_topology_invalid"
    elif not any(edge.get("source") == lead and edge.get("target") == gate for edge in graph.get("edges") or []):
        return "team_approval_topology_invalid"
    return None
