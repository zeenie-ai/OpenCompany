"""Owner-reviewed conversion of recognizable hired employee graphs.

The saved graph and parameters are configuration snapshots only: no reset,
conversation deletion, account change, schedule rewrite, or queue clearing.
Ambiguous editor graphs are deliberately surfaced for review.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import uuid
from typing import Any

from sqlmodel import select
from models.database import NodeParameter, Workflow
from models.employees import Employee, EmployeeGrant
from models.employee_conversion import EmployeeConversion
from services.employees.builder import BuildInputs, BuiltEmployee
from services.employees.team_recipe import build_team
from services.graph_build import Labels, NodeIds, graph_node, main_edge, tool_edge


class _OwnershipReviewNeeded(ValueError):
    """Existing differently configured callables need an owner's review."""


def _isolate_conversion_tools(proposed: Any, source: dict, workflow_id: str, original: str, talk: str) -> None:
    """Keep historical nodes while giving each converted callable one owner."""
    nodes = {node["id"]: node for node in proposed.nodes}
    old_ids = {node["id"] for node in source["graph"]["nodes"]}
    old_bindings = [edge for edge in source["graph"]["edges"] if edge.get("targetHandle") == "input-tools"]
    ids = NodeIds(workflow_id, proposed.nodes)
    labels = Labels(node["data"]["label"] for node in proposed.nodes)

    def copy_tool(tool: str) -> str:
        node_type = nodes[tool]["type"]
        copied = ids.next(node_type)
        node = graph_node(copied, node_type, labels.take(nodes[tool]["data"]["label"]), (0, -60))
        proposed.nodes.append(node)
        nodes[copied] = node
        proposed.parameters[copied] = deepcopy(source["parameters"].get(tool, proposed.parameters.get(tool, {})))
        return copied

    memories = {node_id for node_id, node in nodes.items() if node["type"] == "simpleMemory"}
    old_memories = memories & old_ids
    if old_memories:
        current = {agent: next((edge["source"] for edge in proposed.edges if edge.get("target") == agent
                   and edge.get("targetHandle") == "input-tools" and edge["source"] in memories), None)
                   for agent in [proposed.node_roles["agent"], talk, *(member["node_id"] for member in proposed.team_plan["members"])]}
        legacy = {agent: list(dict.fromkeys(edge["source"] for edge in old_bindings
                  if edge["target"] == agent and edge["source"] in old_memories)) for agent in {original, talk}}
        if any(len(bound) > 1 for bound in legacy.values()):
            raise _OwnershipReviewNeeded("Multiple existing memories need review")
        template = next(iter(legacy.get(original) or legacy.get(talk) or sorted(old_memories)))
        proposed.edges = [edge for edge in proposed.edges if not (
            edge.get("targetHandle") == "input-tools" and edge["source"] in memories)]
        used = set()
        # The original shared memory stays with the conversational contact.
        # Existing exclusive worker memory keeps its identity and history too.
        agents = list(dict.fromkeys([talk, original, *current]))
        for agent in agents:
            preferred = next(iter(legacy.get(agent, [])), None)
            memory = preferred if preferred and preferred not in used else current.get(agent)
            if memory in used or memory is None or (memory in old_memories and memory != preferred):
                memory = copy_tool(preferred or template)
            elif memory not in old_ids:
                proposed.parameters[memory] = deepcopy(source["parameters"].get(preferred or template, {}))
            used.add(memory)
            proposed.edges.append(tool_edge(memory, agent).to_dict())

    # The recipe may transfer a legacy shared business tool to a specialist
    # while the reused worker retains its old edge. Keep the specialist's
    # canonical binding rather than reintroducing that shared capability.
    owners: dict[str, list[dict]] = {}
    for edge in proposed.edges:
        if edge.get("targetHandle") == "input-tools":
            owners.setdefault(edge["source"], []).append(edge)
    members = {member["node_id"] for member in proposed.team_plan["members"]}
    remove = set()
    for tool, bindings in owners.items():
        targets = list(dict.fromkeys(edge["target"] for edge in bindings))
        node_type = nodes[tool]["type"]
        if node_type in {"agentBuilder", "chatUi"}:
            # Expansion and owner UI remain on the trusted Talk contact,
            # even when a legacy worker accidentally shared that binding.
            bindings[0]["target"] = talk
            remove.update(id(edge) for edge in bindings[1:])
            continue
        if len(targets) < 2:
            continue
        if node_type in {"currentTimeTool", "writeTodos", "canvas"}:
            keeper = proposed.node_roles["agent"] if proposed.node_roles["agent"] in targets else targets[0]
            for edge in bindings:
                if edge["target"] != keeper:
                    edge["source"] = copy_tool(tool)
        else:
            keeper = next((target for target in targets if target in members and target != original), targets[0])
            for edge in bindings:
                if edge["target"] != keeper:
                    remove.add(id(edge))
    proposed.edges = [edge for edge in proposed.edges if id(edge) not in remove]

    # Reusing a worker also combines its old utilities with recipe utilities.
    # Preserve the existing callable when those configurations are compatible.
    groups: dict[tuple[str, str], list[dict]] = {}
    for edge in proposed.edges:
        if edge.get("targetHandle") == "input-tools":
            groups.setdefault((edge["target"], nodes[edge["source"]]["type"]), []).append(edge)
    remove = set()
    for (agent, _node_type), bindings in groups.items():
        distinct = list(dict.fromkeys(edge["source"] for edge in bindings))
        if len(distinct) < 2:
            continue
        previous = [tool for tool in distinct if tool in old_ids]
        if len(previous) > 1 and any(source["parameters"].get(tool, {}) != source["parameters"].get(previous[0], {}) for tool in previous[1:]):
            raise _OwnershipReviewNeeded("Differently configured existing tools need review")
        keeper = next((edge["source"] for edge in old_bindings if edge["target"] == agent and edge["source"] in distinct), previous[0] if previous else distinct[0])
        remove.update(id(edge) for edge in bindings if edge["source"] != keeper)
    proposed.edges = [edge for edge in proposed.edges if id(edge) not in remove]
    # Generated utilities made redundant by reuse have no historical state.
    # Never remove an original node, or anything still referenced by the
    # graph, Context ownership, parameters or the employee's recorded roles.
    candidates = (set(owners) | memories | {edge["source"] for bindings in groups.values() for edge in bindings}) - old_ids
    connected = {endpoint for edge in proposed.edges for endpoint in (edge["source"], edge["target"])}
    referenced = set(proposed.node_roles.values()) | {node.get("data", {}).get("agentNodeId") for node in proposed.nodes}
    removable = {node_id for node_id in candidates - connected - referenced
                 if not any(node_id in json.dumps(parameters, default=str) for key, parameters in proposed.parameters.items() if key != node_id)}
    proposed.nodes = [node for node in proposed.nodes if node["id"] not in removable]
    proposed.parameters = {key: value for key, value in proposed.parameters.items() if key not in removable}


def _hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def _snapshot(database: Any, workflow: Any, employee: Any) -> dict:
    graph = deepcopy(workflow.data or {})
    return {"graph": graph, "parameters": {node["id"]: await database.get_node_parameters(node["id"]) or {} for node in graph.get("nodes", [])},
            "employee": {"node_roles": dict(employee.node_roles or {}), "team_plan": deepcopy(employee.team_plan),
                         "builder_version": employee.builder_version, "job": employee.job, "role": employee.role,
                         "apps": list(employee.apps or []), "plan": deepcopy(employee.plan), "trigger": deepcopy(employee.trigger),
                         "rules": deepcopy(employee.rules), "llm": deepcopy(employee.llm)}}


async def _parameter(session: Any, node_id: str):
    return (await session.execute(select(NodeParameter).where(NodeParameter.node_id == node_id))).scalar_one_or_none()


def _recognizable(source: dict) -> bool:
    roles = source["employee"]["node_roles"]
    nodes = {node["id"]: node for node in source["graph"].get("nodes", [])}
    required = ("agent", "trigger", "talk_agent", "talk_trigger", "talk_context", "talk_reply")
    if any(roles.get(key) not in nodes for key in required):
        return False
    from services.workspace_capabilities import is_registered_agent
    if nodes[roles["agent"]]["type"] not in {"aiAgent", "chatAgent"}:
        return False
    if any(node["type"] in {"taskManager", "employeeJob", "taskTrigger"} for node in nodes.values()):
        return False
    if {node_id for node_id, node in nodes.items() if is_registered_agent(node["type"])} != {roles["agent"], roles["talk_agent"]}:
        return False
    expected = {(roles["trigger"], roles["agent"]), (roles["talk_trigger"], roles["talk_agent"]), (roles["talk_agent"], roles["talk_reply"])}
    for key in ("gate", "reply", "notify", "console", "report_post"):
        if key in roles:
            expected.add((roles["agent"], roles[key]))
    for key in ("gate", "reply"):
        if key in roles:
            expected.add((roles["trigger"], roles[key]))
    if "gate" in roles and "reply" in roles:
        expected.add((roles["gate"], roles["reply"]))
    return all((edge.get("source"), edge.get("target")) in expected for edge in source["graph"].get("edges", []) if edge.get("targetHandle", "input-main") == "input-main")


def _prepare(source: dict, employee: Any, workflow_id: str, name: str) -> dict:
    from services.employees.apps import get_app
    from services.employees.llm import LLMChoice
    from services.employees.prompt import request_from_employee
    from services.node_allowlist import is_hire_allowed

    original = source["employee"]["node_roles"]["agent"]
    talk = source["employee"]["node_roles"]["talk_agent"]
    built = BuiltEmployee(nodes=deepcopy(source["graph"]["nodes"]), edges=deepcopy(source["graph"]["edges"]),
        parameters=deepcopy(source["parameters"]), node_roles=dict(source["employee"]["node_roles"]),
        trigger={"kind": "manual"}, delivery="talk", delivery_app=None, app_ids=list(employee.apps or []), warnings=[])
    params = source["parameters"][original]
    llm = LLMChoice(provider=str(params.get("provider") or (employee.llm or {}).get("provider") or "openai"),
        model=str(params.get("model") or (employee.llm or {}).get("model") or ""), local=False)
    # Appending the lead through the manual recipe preserves the original
    # worker and its Context. Nonmanual intake is rewired explicitly below.
    proposed = build_team(built, BuildInputs(workflow_id=workflow_id, request=request_from_employee(employee, name),
        apps=[app for app in (get_app(app_id) for app_id in employee.apps or []) if app], llm=llm,
        memory=any(node["type"] == "simpleMemory" for node in built.nodes), allowed=is_hire_allowed, team=True))
    old_context = next((edge["source"] for edge in source["graph"]["edges"] if edge.get("target") == original and edge.get("targetHandle") == "input-context"), None)
    # A separately triggered existing worker can keep doing its established
    # responsibility as a specialist. Its node identity and conversation stay.
    if original != talk:
        members = proposed.team_plan["members"]
        reused = next((member for member in members if member["role"] == "operations"), members[0])
        replaced = reused["node_id"]
        removable = {replaced}
        if old_context:
            removable.add(reused["context_node_id"])
        # New memory belonging to the replaced member is redundant. Preserve
        # the employee's original memory binding for its reused worker.
        for edge in proposed.edges:
            if edge.get("target") == replaced and edge.get("targetHandle") == "input-tools":
                node = next((node for node in proposed.nodes if node["id"] == edge["source"]), None)
                if node and node["type"] == "simpleMemory":
                    removable.add(node["id"])
        proposed.nodes = [node for node in proposed.nodes if node["id"] not in removable]
        proposed.parameters = {key: value for key, value in proposed.parameters.items() if key not in removable}
        proposed.edges = [edge for edge in proposed.edges if edge["source"] not in removable - {replaced} and edge["target"] not in removable - {replaced}]
        for edge in proposed.edges:
            if edge["source"] == replaced:
                edge["source"] = original
            if edge["target"] == replaced:
                edge["target"] = original
        for context_node in proposed.nodes:
            if context_node["type"] == "context" and context_node.get("data", {}).get("agentNodeId") == replaced:
                context_node["data"]["agentNodeId"] = original
        for role, node_id in list(proposed.node_roles.items()):
            if node_id == replaced:
                proposed.node_roles[role] = original
            elif old_context and node_id == reused["context_node_id"]:
                proposed.node_roles[role] = old_context
        reused["node_id"] = original
        reused["node_type"] = next(node["type"] for node in proposed.nodes if node["id"] == original)
        if old_context:
            reused["context_node_id"] = old_context
        reused["tools"] = [node_id for node_id in reused["tools"] if node_id not in removable]
        proposed.parameters[original]["system_message"] += "\nSubmit assigned work to the team lead for review. Never publish a final reply independently."
    # Existing nodes keep their exact model tuning, memory configuration,
    # account IDs and schedule parameters. Only conversational instructions
    # change; new members inherit the employee's resolved model.
    old_ids = {node["id"] for node in source["graph"]["nodes"]}
    _isolate_conversion_tools(proposed, source, workflow_id, original, talk)
    for node_id in old_ids:
        before = source["parameters"].get(node_id, {})
        after = proposed.parameters.get(node_id, {})
        proposed.parameters[node_id] = {**deepcopy(before), **({"system_message": after["system_message"]} if "system_message" in after else {})}
    trigger_record = deepcopy(employee.trigger or {})
    if trigger_record.get("kind") != "manual":
        roles = proposed.node_roles
        destinations = [roles[key] for key in ("gate", "reply", "notify", "report_post") if key in roles]
        proposed.team_plan["delivery_node_ids"] = destinations
        proposed.edges = [edge for edge in proposed.edges if not (
            edge.get("targetHandle") == "input-main" and (
                edge["source"] == roles["trigger"] and edge["target"] in {original, *destinations}
                or edge["source"] == original and edge["target"] in {*destinations, roles.get("console")}
            )
        )]
        proposed.edges.append(main_edge(roles["trigger"], roles["agent"]).to_dict())
    for edge in proposed.edges:
        edge["id"] = f'e-{edge["source"]}-{edge["sourceHandle"]}-{edge["target"]}-{edge["targetHandle"]}'
    # Dedupe bindings restored for old memory without dropping intentional
    # separate custom agents of the same plugin type.
    proposed.edges = list({edge["id"]: edge for edge in proposed.edges}.values())
    for member in proposed.team_plan["members"]:
        member["tools"] = list(dict.fromkeys(edge["source"] for edge in proposed.edges if edge["target"] == member["node_id"] and edge["targetHandle"] == "input-tools"))
    next(node for node in proposed.nodes if node["id"] == proposed.team_plan["lead_node_id"])["data"]["employee_team_plan"] = deepcopy(proposed.team_plan)
    for node in proposed.nodes:
        proposed.parameters.setdefault(node["id"], {})
    return {"graph": {**deepcopy(source["graph"]), "nodes": proposed.nodes, "edges": proposed.edges}, "parameters": proposed.parameters,
            "employee": {**deepcopy(source["employee"]), "node_roles": proposed.node_roles, "team_plan": proposed.team_plan}}


async def plan_conversion(database: Any, workflow_id: str, owner_id: str, *, enabled: bool = False) -> dict:
    if not enabled:
        return {"success": False, "error": "conversion_not_enabled"}
    from services.employees.store import get_by_workflow
    from services.workflow_validator import validate_workflow
    workflow = await database.get_workflow(workflow_id)
    employee = await get_by_workflow(database, workflow_id)
    if not workflow or not employee or employee.owner_id != owner_id or (workflow.data or {}).get("owner_id", "owner") != owner_id:
        return {"success": False, "error": "not_found"}
    if employee.team_plan:
        return {"success": True, "already_has_team": True}
    source = await _snapshot(database, workflow, employee)
    if not _recognizable(source):
        return {"success": False, "error": "needs_dev_review", "review_required": True,
                "message": "This employee has a custom setup. Review it in Dev mode before adding a team."}
    try:
        proposed = _prepare(source, employee, workflow_id, workflow.name)
    except _OwnershipReviewNeeded:
        return {"success": False, "error": "needs_dev_review", "review_required": True,
                "message": "This employee has separately configured tools. Review their responsibilities in Dev mode before adding a team."}
    except ValueError:
        return {"success": False, "error": "team_unavailable"}
    report = await validate_workflow(nodes=proposed["graph"]["nodes"], edges=proposed["graph"]["edges"], parameters_by_id=proposed["parameters"])
    from services.employees.upgrade import team_approval_topology_error
    approval_error = team_approval_topology_error(proposed["graph"], proposed["employee"]["node_roles"], params=proposed["parameters"], team_plan=proposed["employee"]["team_plan"])
    if approval_error:
        return {"success": False, "error": "needs_dev_review", "review_required": True,
                "message": "Review this employee's sending approval setup in Dev mode before adding a team."}
    if report.get("errors"):
        return {"success": False, "error": "invalid_changes", "validation_issues": report["errors"]}
    review = EmployeeConversion(id=uuid.uuid4().hex, workflow_id=workflow_id, owner_id=owner_id,
        source_hash=_hash(source), source=source, proposed=proposed)
    async with database.reserved_session() as session:
        session.add(review)
        await session.commit()
    return {"success": True, "review_id": review.id,
            "explanation": "They will ask helpers to do parts of the job and check the result before replying. Their conversations, connected apps, hours and approval rule stay in place.",
            "team": [{"responsibility": member["responsibility"]} for member in proposed["employee"]["team_plan"]["members"]],
            "apps": list(employee.apps or []), "requires_review": True}


async def apply_conversion(database: Any, review_id: str, owner_id: str, *, enabled: bool = False, stop_work: bool = False) -> dict:
    if not enabled:
        return {"success": False, "error": "conversion_not_enabled"}
    from services.employees.team_runtime import team_runtime_error
    from services.employees.safe_apply import apply_saved_changes
    readiness = team_runtime_error()
    if readiness:
        return {"success": False, "error": readiness}
    async with database.reserved_session() as session:
        review = await session.get(EmployeeConversion, review_id)
        if not review or review.owner_id != owner_id:
            return {"success": False, "error": "not_found"}
        workflow = await session.get(Workflow, review.workflow_id)
        employee = (await session.execute(select(Employee).where(Employee.workflow_id == review.workflow_id))).scalar_one_or_none()
        if not workflow or not employee or employee.owner_id != owner_id:
            return {"success": False, "error": "not_found"}
        if review.state not in {"saved", "applied"}:
            parameters = {}
            for node in (workflow.data or {}).get("nodes", []):
                parameter = await _parameter(session, node["id"])
                parameters[node["id"]] = deepcopy(parameter.parameters) if parameter else {}
            current = {"graph": workflow.data, "parameters": parameters, "employee": {key: deepcopy(getattr(employee, key)) for key in review.source["employee"]}}
            if _hash(current) != review.source_hash:
                return {"success": False, "error": "review_stale", "message": "Their setup changed. Review the team again."}
            workflow.data = deepcopy(review.proposed["graph"])
            for node_id, value in review.proposed["parameters"].items():
                parameter = await _parameter(session, node_id)
                if parameter is None:
                    session.add(NodeParameter(node_id=node_id, parameters=deepcopy(value)))
                else:
                    parameter.parameters = deepcopy(value)
            employee.node_roles = deepcopy(review.proposed["employee"]["node_roles"])
            employee.team_plan = deepcopy(review.proposed["employee"]["team_plan"])
            for member in employee.team_plan["members"]:
                for tool_id in member["tools"]:
                    node_type = next(node["type"] for node in workflow.data["nodes"] if node["id"] == tool_id)
                    session.add(EmployeeGrant(id=uuid.uuid4().hex, workflow_id=review.workflow_id, owner_id=owner_id,
                        capability=node_type, member_id=member["node_id"], limits={"review_id": review.id, "approved": True,
                            "tool_node_id": tool_id, "parameters": deepcopy(review.proposed["parameters"].get(tool_id, {}))}))
            review.state = "saved"
            await session.commit()
        workflow_id = review.workflow_id
    result = await apply_saved_changes(database, workflow_id, owner_id=owner_id, key="conversion-" + review_id, stop_work=stop_work)
    if result.get("success") and result.get("activation_state") != "waiting":
        async with database.reserved_session() as session:
            review = await session.get(EmployeeConversion, review_id)
            review.state = "applied"
            await session.commit()
    return {**result, "review_id": review_id, "workflow_id": workflow_id}


async def rollback_conversion(database: Any, review_id: str, owner_id: str) -> dict:
    from services.employees.safe_apply import apply_saved_changes
    async with database.reserved_session() as session:
        review = await session.get(EmployeeConversion, review_id)
        if not review or review.owner_id != owner_id or review.state not in {"saved", "applied"}:
            return {"success": False, "error": "not_found"}
        workflow = await session.get(Workflow, review.workflow_id)
        employee = (await session.execute(select(Employee).where(Employee.workflow_id == review.workflow_id))).scalar_one_or_none()
        if not workflow or not employee or employee.owner_id != owner_id:
            return {"success": False, "error": "not_found"}
        from models.employees import EmployeeApply
        pending = await session.get(EmployeeApply, f"apply:{review.workflow_id}:conversion-{review_id}")
        if pending and pending.state == "applying":
            return {"success": False, "error": "busy", "message": "Their new team is being applied. Wait for that to finish before restoring the previous setup."}
        if pending and pending.state == "waiting":
            pending.state = "cancelled"
        # Do not overwrite later capability edits under an old snapshot.
        parameters = {}
        for node in (workflow.data or {}).get("nodes", []):
            parameter = await _parameter(session, node["id"])
            parameters[node["id"]] = deepcopy(parameter.parameters) if parameter else {}
        current = {"graph": workflow.data, "parameters": parameters, "employee": {key: deepcopy(getattr(employee, key)) for key in review.proposed["employee"]}}
        if _hash(current) != _hash(review.proposed):
            return {"success": False, "error": "rollback_needs_review"}
        workflow.data = deepcopy(review.source["graph"])
        for node_id, parameters in review.source["parameters"].items():
            row = await _parameter(session, node_id)
            if row:
                row.parameters = deepcopy(parameters)
        employee.node_roles = deepcopy(review.source["employee"]["node_roles"])
        employee.team_plan = deepcopy(review.source["employee"]["team_plan"])
        employee.builder_version = review.source["employee"]["builder_version"]
        grants = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.workflow_id == review.workflow_id))).scalars().all()
        from datetime import datetime, timezone
        for grant in grants:
            if grant.limits.get("review_id") == review_id:
                grant.revoked_at = datetime.now(timezone.utc)
        review.state = "rolled_back"
        await session.commit()
    return await apply_saved_changes(database, review.workflow_id, owner_id=owner_id, key="rollback-" + review_id)
