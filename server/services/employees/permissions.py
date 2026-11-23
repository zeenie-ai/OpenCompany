"""Capability authority from owner requests, never public app messages."""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from sqlmodel import select
from models.employees import Employee, EmployeeGrant


async def trusted_owner_request(database: Any, ctx: Any) -> bool:
    """A stored authenticated chat admission is the trust boundary."""
    from services.chat.stream import chat_run_id_of
    from services.chat.ledger import get_run
    from services.employees.store import get_by_workflow
    run_id = chat_run_id_of(ctx.raw)
    if not run_id or not ctx.workflow_id:
        return False
    run = await get_run(database, run_id)
    workflow = await database.get_workflow(str(ctx.workflow_id))
    employee = await get_by_workflow(database, str(ctx.workflow_id))
    owner = employee.owner_id if employee else (workflow.data or {}).get("owner_id", "owner") if workflow else None
    return bool(run and run.workflow_id == ctx.workflow_id and owner == ctx.user_id and run.kind in {"message", "action", "edit"})


async def specialist_review_off(session: Any, workflow_id: str, caller: str, operation: str, member_id: str, capability: str) -> bool:
    """The live Ask first rule governs owner Talk adding or equipping its team."""
    from models.approvals import WorkflowRule
    from services.employees.policy import asks_first

    if operation not in {"add_subagent", "add_tool", "add_skill"}:
        return False
    employee = (await session.execute(select(Employee).where(Employee.workflow_id == workflow_id))).scalar_one_or_none()
    plan = (employee.team_plan or {}) if employee else {}
    if not caller or caller != plan.get("talk_node_id"):
        return False
    if operation == "add_subagent":
        if member_id != plan.get("lead_node_id"):
            return False
    elif member_id not in {m.get("node_id") for m in plan.get("members", [])}:
        return False
    rule = await session.get(WorkflowRule, workflow_id)
    must_ask = rule.ask_first if rule is not None else asks_first(employee)
    if must_ask:
        return False
    revoked = await session.execute(select(EmployeeGrant.id).where(
        EmployeeGrant.workflow_id == workflow_id, EmployeeGrant.owner_id == employee.owner_id,
        EmployeeGrant.member_id == member_id, EmployeeGrant.capability == capability,
        EmployeeGrant.revoked_at.is_not(None)).limit(1))
    return revoked.first() is None


async def require_builder_access(database: Any, ctx: Any, capability: str, *, member_id: str = "", account_id: str = "", limits: dict | None = None, pending_change: dict | None = None, operation: str = "", preview: bool = False) -> dict:
    from services.employees.store import get_by_workflow
    from core.container import container
    if hasattr(container, "settings") and not getattr(container.settings(), "employee_capability_updates_enabled", True):
        return {"authorized": False, "required_access": [], "summary": "Changes to employee capabilities are currently turned off."}
    employee = await get_by_workflow(database, str(ctx.workflow_id))
    if not employee:
        # Existing Dev mode editor operations retain their prior scope.
        return {"authorized": True, "required_access": []}
    if not await trusted_owner_request(database, ctx):
        return {"authorized": False, "required_access": [], "summary": "Ask the owner in Talk to change this employee’s access."}
    limits = limits or {}
    configuration = limits.get("parameters") or limits.get("tool_parameters") or {}
    if not account_id and isinstance(configuration, dict):
        account_id = str(configuration.get("account_id") or configuration.get("credential_id") or configuration.get("sender_account") or "")
    # A request identity includes its complete scope; widening one creates
    # a different review request instead of reusing an earlier permission.
    scope = [employee.workflow_id, employee.owner_id, capability, member_id, account_id, limits]
    request_id = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    reviewed_id = ctx.raw.get("reviewed_employee_grant")
    if reviewed_id and reviewed_id != request_id:
        return {"authorized": False, "required_access": [], "summary": "The team or its settings changed. Ask for a new review in Talk."}
    async with database.reserved_session() as session:
        row = await session.get(EmployeeGrant, request_id)
        if row is not None and row.revoked_at is None and row.limits.get("approved"):
            return {"authorized": True, "request_id": request_id, "required_access": [],
                    "pending_mutation_id": (row.pending_change or {}).get("mutation_id")}
        caller = ctx.raw.get("invoking_agent_node_id") or ctx.raw.get("parent_node_id")
        direct = (row is None or row.revoked_at is None) and await specialist_review_off(session, employee.workflow_id, caller, operation, member_id, capability)
        if direct:
            if preview:
                return {"authorized": True, "required_access": []}
            # The graph transaction rechecks the live rule and creates this
            # grant with its specialist. A failed save leaves no access card.
            return {"authorized": True, "request_id": request_id, "required_access": [],
                    "grant_on_save": {"member_id": member_id, "capability": capability,
                                      "account_id": account_id, "limits": limits},
                    "pending_mutation_id": (row.pending_change or {}).get("mutation_id") if row else None}
        if row is None:
            row = EmployeeGrant(id=request_id, workflow_id=employee.workflow_id, owner_id=employee.owner_id,
                capability=capability, member_id=member_id, account_id=account_id, limits={**limits, "approved": False},
                pending_change=pending_change)
            session.add(row)
            await session.commit()
        elif pending_change and not row.pending_change and not row.revoked_at:
            row.pending_change = pending_change
            await session.commit()
    from services.node_registry import get_node_class
    cls = get_node_class(capability)
    app = getattr(cls, "display_name", capability.replace("_", " ")) if cls else capability.replace("_", " ")
    return {"authorized": False, "request_id": request_id,
        "required_access": [{"request_id": request_id, "app": app, "action": "help with their assigned responsibilities", "member_id": member_id}],
        "summary": f"Allow {employee.role or 'this employee'}’s team to use {app} for their assigned responsibilities?"}


async def list_access(database: Any, workflow_id: str, owner_id: str) -> list[dict]:
    async with database.get_session() as session:
        rows = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.workflow_id == workflow_id, EmployeeGrant.owner_id == owner_id))).scalars().all()
        from services.node_registry import get_node_class
        return [{"id": row.id, "capability": row.capability, "member_id": row.member_id,
                 "change_pending": bool(row.pending_change),
                 "parent_grant_id": row.limits.get("parent_grant_id"),
                 "scope_apps": [getattr(get_node_class(kind), "display_name", kind.replace("_", " ")) for kind in row.limits.get("tool_types", [])],
                 "app": getattr(get_node_class(row.capability), "display_name", row.capability.replace("_", " ")),
                 "action": str((row.limits.get("parameters") or {}).get("operation") or row.limits.get("purpose") or "help with their assigned responsibilities").replace("_", " "),
                 "account_id": row.account_id, "approved": row.limits.get("approved", row.limits.get("initial_hire", False)),
                 "revoked": row.revoked_at is not None} for row in rows]


async def decide_access(database: Any, request_id: str, owner_id: str, allow: bool) -> bool:
    async with database.reserved_session() as session:
        row = await session.get(EmployeeGrant, request_id)
        if row is None or row.owner_id != owner_id:
            return False
        row.limits = {**row.limits, "approved": allow}
        row.revoked_at = None if allow else datetime.now(timezone.utc)
        await session.commit()
        return True


async def _settle_existing_specialist(session, row: EmployeeGrant, graph: dict, params) -> dict | None:
    """A second approval for the same singleton can only acknowledge its saved configuration."""
    from models.database import NodeParameter

    if params.operation != "add_subagent" or row.capability == "aiAgent" or params.agent_type.strip() != row.capability:
        return None
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    def enabled(node_id: str) -> bool:
        node = nodes[node_id]
        return not node.get("disabled") and not (node.get("data") or {}).get("disabled")

    edges = graph.get("edges", [])
    members = [e["source"] for e in edges if e.get("target") == row.member_id and
               e.get("targetHandle") == "input-teammates" and e.get("sourceHandle") == "output-top" and
               nodes.get(e.get("source"), {}).get("type") == row.capability]
    if not members:
        return None
    employee = (await session.execute(select(Employee).where(Employee.workflow_id == row.workflow_id))).scalar_one_or_none()
    plan = (employee.team_plan or {}) if employee else {}
    member_id = members[0]
    member = next((m for m in plan.get("members", []) if m.get("node_id") == member_id), {})
    inputs = {e["source"]: e.get("targetHandle") for e in edges if e.get("target") == member_id
              and e.get("source") in nodes and enabled(e["source"])}
    configs = (await session.execute(select(NodeParameter).where(NodeParameter.node_id.in_([member_id, *inputs])))).scalars()
    settings = {config.node_id: config.parameters or {} for config in configs}
    limits = row.limits
    agent = settings.get(member_id, {})
    required_config = {"system_message": limits.get("purpose"), "provider": limits.get("provider"), "model": limits.get("model")}
    expected_tools = limits.get("tool_parameters", {})
    def has_tool(kind: str) -> bool:
        wanted = expected_tools.get(kind, {})
        return any(handle == "input-tools" and nodes[source]["type"] == kind and
                   all(settings.get(source, {}).get(k) == v for k, v in wanted.items()) for source, handle in inputs.items())

    skills = {name for source, handle in inputs.items() if handle == "input-skill"
              for name, spec in (settings.get(source, {}).get("skills_config") or {}).items()
              if isinstance(spec, dict) and spec.get("enabled")}
    required_skills = set(limits.get("skill_names", []))
    if row.capability == "browser_agent":
        required_skills.add("browser-skill")
    satisfied = (len(members) == 1 and enabled(member_id) and member.get("node_type") == row.capability and
                 nodes.get(member.get("context_node_id"), {}).get("data", {}).get("agentNodeId") == member_id and
                 plan.get("lead_node_id") == row.member_id and
                 all(agent.get(k) == v for k, v in required_config.items()) and
                 all(has_tool(kind) for kind in limits.get("tool_types", [])) and required_skills <= skills)
    if row.pending_change:
        row.pending_change = None
        await session.commit()
    if satisfied:
        return {"success": True, "activation_state": "saved"}
    return {"success": True, "activation_state": "superseded", "message":
            "Another request already added this specialist with a different configuration. Ask Talk to update the existing specialist."}


async def finish_approved_change(database: Any, request_id: str, owner_id: str) -> dict:
    """Finish the exact Builder call held for this owner's Allow action."""
    from models.database import Workflow
    from nodes.tool.agent_builder import AgentBuilderNode, AgentBuilderParams
    from services.employees.talk_team_change import connected_builders
    from services.plugin import NodeContext

    async with database.reserved_session() as session:
        row = await session.get(EmployeeGrant, request_id)
        if not row or row.owner_id != owner_id or row.revoked_at or not row.limits.get("approved"):
            return {"success": False, "error": "not_found"}
        change, workflow_id = row.pending_change, row.workflow_id
        if not change:
            return {"success": True}
        graph = await session.get(Workflow, workflow_id)
        saved = (graph.data or {}) if graph else {}
        caller, builder = change["caller_id"], change["builder_id"]
        if builder not in connected_builders(saved, caller):
            return {"success": False, "error": "builder_disconnected", "message": "The Agent Builder connection changed. Review this request in Talk."}
        params = AgentBuilderParams.model_validate(change["params"])
        if params.operation not in {"add_tool", "add_skill", "add_subagent"}:
            return {"success": False, "error": "invalid_change"}
        settled = await _settle_existing_specialist(session, row, saved, params)
        if settled:
            return settled
    ctx = NodeContext(node_id=builder, node_type="agentBuilder", workflow_id=workflow_id, user_id=owner_id,
                      nodes=saved["nodes"], edges=saved["edges"], raw={"parent_node_id": caller,
                          "run_scope": {"run_id": change["run_id"]}, "reviewed_employee_grant": request_id,
                          "pending_builder_mutation": change["mutation_id"]})
    result = await getattr(AgentBuilderNode(), params.operation)(ctx, params)
    # The graph transaction consumes pending_change; this also recognizes a
    # concurrent retry that already completed this exact change.
    async with database.reserved_session() as session:
        row = await session.get(EmployeeGrant, request_id)
        if row and row.owner_id == owner_id and not row.revoked_at and row.limits.get("approved"):
            if params.operation == "add_subagent" and not result.saved_revision and row.pending_change in (None, change):
                graph = await session.get(Workflow, workflow_id)
                if graph and builder in connected_builders(graph.data or {}, caller):
                    settled = await _settle_existing_specialist(session, row, graph.data or {}, params)
                    if settled:
                        return settled
            if row.pending_change is None:
                return {"success": True, "activation_state": "saved"}
    return {"success": False, "error": "change_not_saved", "message": result.summary or "The team change could not be saved. Review the request in Talk."}


async def assert_runtime_access(database: Any, workflow_id: str, node_id: str, node_type: str, context: dict) -> None:
    """Revocation stops subsequent calls; it never approves an external send."""
    agent_id = str(context.get("parent_node_id") or context.get("agent_node_id") or "")
    async with database.get_session() as session:
        grants = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.workflow_id == workflow_id))).scalars().all()
    indexed = {grant.id: grant for grant in grants}
    def allowed(grant: EmployeeGrant, visited: set[str]) -> bool:
        if grant.id in visited or grant.revoked_at is not None or not grant.limits.get("approved"):
            return False
        parent_id = grant.limits.get("parent_grant_id")
        if not parent_id:
            return True
        parent = indexed.get(parent_id)
        return bool(parent and parent.owner_id == grant.owner_id and allowed(parent, visited | {grant.id}))
    grants = [grant for grant in grants if grant.capability == node_type]
    relevant = [grant for grant in grants if grant.limits.get("tool_node_id") == node_id]
    # A Builder-created specialist is itself a child of the scope that
    # created it. A second lead-level approval cannot substitute for that
    # binding. Tools can also have later member-scoped configuration grants.
    bound_specialist = any(grant.member_id == node_id and grant.limits.get("parent_grant_id") for grant in relevant)
    if agent_id and not bound_specialist:
        relevant.extend(grant for grant in grants if grant.member_id == agent_id and not grant.limits.get("tool_node_id"))
    if relevant and not any(allowed(grant, set()) for grant in relevant):
        from services.plugin.base import NodeUserError
        raise NodeUserError("The owner removed permission to use this app. Ask them to allow access in Settings.", requires_user_action=True)
