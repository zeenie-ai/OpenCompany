"""Admission requirements for employees with an explicit durable team plan."""
from __future__ import annotations
from typing import Any, Optional


def employee_review_input(context: dict) -> dict | None:
    """Read a single current-run task event before consulting saved parameters."""
    from services.plugin.edge_walker import extract_task_event_payload
    trigger_ids = {node.get("id") for node in context.get("nodes", []) if node.get("type") == "taskTrigger"}
    outputs = {**(context.get("inputs") or {}), **(context.get("outputs") or {})}
    events = [event for node_id, output in outputs.items() if node_id in trigger_ids
              and (event := extract_task_event_payload(output))]
    if len({str(event["task_id"]) for event in events}) > 1:
        raise ValueError("The employee review task is ambiguous")
    return events[0] if events else None


async def attach_employee_job(database: Any, context: dict, review: dict | None = None):
    """Attach exact intake/task evidence; a shared generation is not a job ID."""
    from models.employees import EmployeeJob
    from models.database import TeamTask
    from sqlmodel import select
    workflow_id, caller = context.get("workflow_id"), context.get("node_id")
    identifier = context.get("employee_job_id")
    outputs = {**(context.get("inputs") or {}), **(context.get("outputs") or {})}
    intake_ids = {node.get("id") for node in context.get("nodes", []) if node.get("type") == "employeeJob"}
    candidates = {str(value["job_id"]) for key, value in outputs.items()
                  if key in intake_ids and isinstance(value, dict) and value.get("job_id")}
    if not identifier and not review and not candidates:
        return None
    async with database.get_session() as session:
        if identifier:
            job = await session.get(EmployeeJob, str(identifier))
        elif review:
            job = (await session.execute(select(EmployeeJob).where(
                EmployeeJob.workflow_id == workflow_id, EmployeeJob.team_id == review.get("team_id"),
                EmployeeJob.lead_node_id == caller))).scalar_one_or_none()
            if job is None:
                return None  # Ordinary teams retain their existing task-trigger handling.
            task = await session.get(TeamTask, str(review.get("task_id") or ""))
            if not task or task.workflow_id != workflow_id or task.team_id != review.get("team_id"):
                raise ValueError("The review task does not belong to this workflow and team")
        else:
            if len(candidates) > 1:
                raise ValueError("The lead intake job is ambiguous")
            job = await session.get(EmployeeJob, next(iter(candidates))) if candidates else None
            if job and (job.origin_execution_id != context.get("execution_id") or job.lead_node_id != caller):
                raise ValueError("The intake job does not belong to this lead execution")
        if job is None:
            if identifier:
                raise ValueError("The admitted employee job is unavailable")
            return None
        if job.workflow_id != workflow_id:
            raise ValueError("The employee job does not belong to this workflow")
        if review and identifier:
            task = await session.get(TeamTask, str(review.get("task_id") or ""))
            if not task or task.workflow_id != workflow_id or task.team_id != job.team_id or job.team_id != review.get("team_id"):
                raise ValueError("The review task does not belong to this employee job")
        if caller != job.lead_node_id:
            from services.employees.job_source import authorize_employee_job_source
            await authorize_employee_job_source(database, {**context, "employee_job_id": job.id,
                "parent_node_id": caller})
        context["employee_job_id"] = job.id
        if caller == job.lead_node_id:
            context["_employee_job_team_id"] = job.team_id
            if job.team_id:
                from models.database import AgentTeam
                team = await session.get(AgentTeam, job.team_id)
                if team:
                    context["team_execution_id"] = team.execution_id
        # Captured email data wins over mutable session storage; current-run
        # task/intake outputs remain authoritative for their own node IDs.
        captured = dict((job.source or {}).get("outputs") or {})
        intake_id = (job.source or {}).get("intake_node_id")
        if not intake_id and (job.source or {}).get("team_plan", {}).get("version") != 2:
            snapshot = (job.source or {}).get("parameters") or {}
            possible = [node.get("id") for node in (job.source or {}).get("nodes", [])
                if node.get("type") == "employeeJob"
                and (snapshot.get(node.get("id")) or {}).get("operation") == "intake"
                and (snapshot.get(node.get("id")) or {}).get("lead_node_id") == job.lead_node_id]
            if len(possible) > 1:
                raise ValueError("The captured employee intake is ambiguous")
            intake_id = possible[0] if possible else None
        if intake_id:
            from services.employees.email_input import project_stored_mission
            captured[intake_id] = {"job_id": job.id, "mission": project_stored_mission(job.mission, job.source or {}), "state": job.state}
        context["outputs"] = {**captured, **outputs}
        return job


async def employee_lead_prompt(database: Any, context: dict, prompt: str) -> str:
    """Preserve custom v1 lead instructions and project historical bodies on read."""
    job = await attach_employee_job(database, context)
    if not job or job.lead_node_id != context.get("node_id") or context.get("parent_node_id"):
        return prompt
    if job.origin_execution_id != context.get("execution_id"):
        return prompt
    from services.employees.email_input import project_stored_mission
    source = job.source or {}
    plan = source.get("team_plan") or {}
    if plan.get("version") == 2:
        return project_stored_mission(job.mission, source)
    if len(prompt) <= 20000:
        return prompt
    original = (source.get("email_input") or {}).get("original_mission", job.mission)
    admitted_mission = project_stored_mission(job.mission, source)
    if original and admitted_mission in prompt:
        prompt = prompt.replace(admitted_mission, original)
    return project_stored_mission(prompt, source)


def temporal_job_runtime(context: dict) -> dict:
    """Validate workflow metadata against the actual activity, never tool arguments."""
    from temporalio import activity
    if not activity.in_activity():
        return {}
    info = activity.info()
    runtime = context.get("_employee_runtime") or {}
    if runtime:
        if (runtime.get("namespace") != info.workflow_namespace
            or runtime.get("lead_workflow_id") != info.workflow_id
            or runtime.get("lead_initial_run_id") != info.workflow_run_id):
            raise ValueError("Employee runtime identity does not match its activity execution")
        # The immediate parent is the graph/job execution that started this lead.
        return {"version": 1, "namespace": info.workflow_namespace,
            "context_execution_id": context.get("execution_id"),
            "root_workflow_id": runtime.get("parent_workflow_id") or info.workflow_id,
            "root_run_id": runtime.get("parent_run_id") or info.workflow_run_id,
            **{key: runtime[key] for key in ("lead_workflow_id", "lead_initial_run_id", "lead_first_execution_run_id") if runtime.get(key)}}
    return {"version": 1, "namespace": info.workflow_namespace,
        "context_execution_id": context.get("execution_id"),
        "root_workflow_id": info.workflow_id, "root_run_id": info.workflow_run_id}


async def capture_employee_runtime(database: Any, context: dict) -> None:
    if not context.get("employee_job_id") or context.get("parent_node_id"):
        return
    runtime = temporal_job_runtime(context)
    if not runtime:
        return
    from models.employees import EmployeeJob
    async with database.reserved_session() as session:
        job = await session.get(EmployeeJob, str(context["employee_job_id"]))
        if not job or job.workflow_id != context.get("workflow_id") or job.lead_node_id != context.get("node_id"):
            raise ValueError("Runtime identity does not belong to the admitted lead")
        if job.origin_execution_id != context.get("execution_id"):
            return  # Review runs must not replace initial lead evidence.
        existing = (job.source or {}).get("runtime") or {}
        if existing.get("lead_workflow_id"):
            return  # Preserve the initial run and first-execution identity across CAN.
        job.source = {**job.source, "runtime": {**existing, **runtime}}
        await session.commit()


def team_runtime_error(*, settings: Any = None, temporal_client: Any = None, worker_manager: Any = None) -> Optional[str]:
    if settings is None or temporal_client is None:
        from core.container import container
        settings = settings if settings is not None else container.settings()
        temporal_client = temporal_client if temporal_client is not None else container.temporal_client()
    if not getattr(settings, "temporal_enabled", False):
        return "team_temporal_required"
    if not getattr(settings, "temporal_agent_workflow_enabled", False):
        return "team_agent_workflow_required"
    if not getattr(temporal_client, "is_connected", False):
        return "team_runtime_not_ready"
    if worker_manager is None and hasattr(temporal_client, "worker_manager"):
        worker_manager = temporal_client.worker_manager
        if worker_manager is None:
            return "team_runtime_not_ready"
    if worker_manager is not None and not getattr(worker_manager, "is_running", False):
        return "team_runtime_not_ready"
    return None


async def employee_job_mission(database: Any, context: dict) -> Optional[str]:
    """Read an admitted lead's mission from its durable job, never user params.

    Child agents inherit job identity for traceability but keep their own
    invocation. Task-trigger review runs likewise retain their review prompt.
    """
    job_id = context.get("employee_job_id")
    if not job_id:
        return None
    from models.employees import EmployeeJob
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, str(job_id))
    if job is None or job.workflow_id != context.get("workflow_id"):
        raise ValueError("The employee job does not belong to this workflow")
    if job.lead_node_id != context.get("node_id"):
        return None
    if job.origin_execution_id != context.get("execution_id"):
        return None
    from services.employees.email_input import project_stored_mission
    return project_stored_mission(job.mission, getattr(job, "source", None) or {})


async def employee_job_parameters(database: Any, context: dict) -> dict:
    if not context.get("employee_job_id"):
        return context.get("parameter_snapshot") or {}
    from models.employees import EmployeeJob
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, str(context["employee_job_id"]))
    if job is None or job.workflow_id != context.get("workflow_id"):
        raise ValueError("The employee job does not belong to this workflow")
    if context.get("agent_instance_id"):
        from services.employees.job_source import authorize_employee_job_source
        from services.agent_instances.service import AgentInstanceService
        await authorize_employee_job_source(database, {**context, "parent_node_id": context.get("node_id")})
        instance = await AgentInstanceService(database).get_instance(str(context["agent_instance_id"]))
        frozen = instance.get("config") or {}
        template = frozen.get("template") or {}
        return {**dict(job.source.get("parameters") or {}),
                **dict((frozen.get("runtime_context") or {}).get("parameter_snapshot") or {}),
                str(instance["template_node_id"]): dict(template.get("parameters") or {})}
    return dict(job.source.get("parameters") or {})
def validate_managed_assignment(args, scope):
    """Require reviewable assignments only in server-admitted employee jobs."""
    if not scope.get("employee_job_id"):
        return
    if not isinstance(args.get("mission", args.get("task")), str) or not args.get("mission", args.get("task", "")).strip():
        raise ValueError("Employee job assignment requires a nonempty mission")
    if not isinstance(args.get("context"), dict):
        raise ValueError("Employee job assignment requires context as an object")
    if not isinstance(args.get("acceptance_criteria"), dict) or not args["acceptance_criteria"]:
        raise ValueError("Employee job assignment requires nonempty acceptance_criteria; specify how submitted work will be reviewed")
    dependencies = args.get("depends_on", [])
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list) or any(not isinstance(item, str) or not item.strip() for item in dependencies):
        raise ValueError("Employee job assignment depends_on must be a list of resolved task IDs")


async def admit_team_plan(database, workflow_id: str, nodes: list, edges: list | None = None, *, captured_plan: dict | None = None) -> list:
    """Put the employee's team plan on its lead in a graph being admitted.

    The saved graph cannot hold it (``workflow_sanitizer`` keeps node data to
    UI fields), so it is saved on the employee row beside the graph
    (``store.commit_hire``, ``workflow_storage.mutate``). Start and Apply copy
    it into the generation, which then runs the plan it was admitted with.
    """
    from copy import deepcopy
    if captured_plan is None:
        from services.employees.store import get_by_workflow
        employee = await get_by_workflow(database, str(workflow_id))
        plan = (employee.team_plan if employee else None) or {}
    else:
        # Re-arming an admitted generation must never adopt later saved edits.
        plan = captured_plan
    if plan.get("version") != 2:
        return nodes
    from services.employee_team_connection import validate_employee_team_connection
    validate_employee_team_connection({"nodes": nodes, "edges": edges or []}, plan)
    return [{**node, "data": {**(node.get("data") or {}), "employee_recipe_version": 2, "employee_team_plan": deepcopy(plan)}}
            if node.get("id") == plan.get("lead_node_id") else node for node in nodes]


async def admitted_graph(database, context):
    """The whole graph the run's generation admitted: its control snapshot.

    A run holds only the nodes its trigger reaches, never a job's delivery
    nodes and, for a Talk run, not the lead.
    """
    generation = int(context.get("generation") or 0)
    if generation <= 0 or not context.get("workflow_id"):
        return None
    control = await database.get_workflow_control_generation(str(context["workflow_id"]), generation)
    return control.graph_snapshot if control else None


def team_plan_in(graph, node_id):
    """The v2 plan an admitted graph carries, for its lead or Talk contact."""
    nodes = graph.get("nodes") or []
    lead = next((node for node in nodes if (node.get("data") or {}).get("employee_recipe_version") == 2), None)
    if not lead:
        return None
    from copy import deepcopy
    plan = deepcopy((lead.get("data") or {}).get("employee_team_plan") or {})
    if plan.get("version") != 2 or node_id not in {plan.get("lead_node_id"), plan.get("talk_node_id")}:
        return None
    ids = {node.get("id") for node in nodes}
    referenced = {plan.get("lead_node_id"), plan.get("talk_node_id"), *plan.get("delivery_node_ids", []), *plan.get("talk_delivery_node_ids", []), *(member.get("node_id") for member in plan.get("members", []))}
    if not referenced <= ids or plan.get("lead_node_id") != lead.get("id"):
        raise ValueError("Employee runtime plan references nodes outside its admitted graph")
    from services.employee_team_connection import validate_employee_team_connection
    validate_employee_team_connection(graph, plan)
    return plan


async def employee_runtime_plan(database, context):
    """Resolve the off-canvas boundary from the generation's admitted graph."""
    graph = await admitted_graph(database, context)
    return team_plan_in(graph, context.get("node_id")) if graph else None


async def admit_employee_runtime_job(database, context, plan, mission, review=None):
    if not plan or context.get("node_id") != plan.get("lead_node_id"):
        return
    if review:
        await attach_employee_job(database, context, review)
        return
    if context.get("employee_job_id") or context.get("parent_node_id"):
        return
    from services.employees.jobs import create_job
    from services.plugin import NodeContext
    admission = {**context, "outputs": context.get("outputs") or context.get("inputs") or {}}
    job = await create_job(database, NodeContext.from_legacy(context["node_id"], "ai_employee", admission), mission=mission,
        lead_node_id=context["node_id"], delivery_node_ids=plan["delivery_node_ids"], dispatch=False)
    context["employee_job_id"] = job.id
