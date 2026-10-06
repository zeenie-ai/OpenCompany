"""Bounded, owner-facing work snapshots, without model or tool payloads.

Read the existing ledgers and live phase cache only: polling must never
dispatch work, reconcile Temporal, or fetch a task's execution trace.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import case
from sqlmodel import select

from core.logging import get_logger
from models.chat import ChatRun, LIVE_STATES
from models.database import AgentTeam, TeamMember, TeamTask
from models.employees import EmployeeJob
from services.employees.graph_index import index_graph

logger = get_logger(__name__)
MAX_STEPS = 20
ACTIVE_TASKS = ("pending", "queued", "blocked", "running", "submitted")
ACTIVE_JOBS = ("queued", "running", "working", "delivering", "delivery_queued")
_STATES = {"pending": "queued", "blocked": "waiting", "submitted": "reviewing",
           "accepted": "done", "completed": "done", "finished": "done", "success": "done",
           "delivered": "done", "working": "running", "executing": "running",
           "error": "failed", "stopped": "cancelled", "skipped": "cancelled",
           "delivering": "reviewing", "delivery_queued": "queued", "retry_wait": "waiting"}


def _state(value: str) -> str:
    value = _STATES.get(value, value)
    return value if value in {"queued", "running", "waiting", "reviewing", "done", "failed", "cancelled", "stopping"} else "running"


def _moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return None


def _iso(value: Any) -> str | None:
    moment = _moment(value)
    return moment.isoformat() if moment else None


def _label(value: Any, fallback: str) -> str:
    # Labels are presentation fields, never descriptions, missions or results.
    return " ".join(value.split())[:160] if isinstance(value, str) and value.strip() else fallback


def _step(step_id: str, label: str, member: str, status: str, *, node_id: str | None = None,
          started_at: Any = None, updated_at: Any = None, finished_at: Any = None) -> dict:
    return {"id": step_id, "label": label, "member": member, "node_id": node_id,
            "status": _state(status), "started_at": _iso(started_at), "updated_at": _iso(updated_at),
            "finished_at": _iso(finished_at)}


def _live_steps(workflow_id: str, graph: Mapping, now: datetime, control: Any = None) -> list[dict]:
    from services.status_broadcaster import get_status_broadcaster
    from services.node_registry import get_node_class, registered_node_classes

    index = index_graph(graph)
    tool_members: dict[str, set[str]] = {}
    for edge in graph.get("edges", []):
        if isinstance(edge, Mapping) and edge.get("target") in index.agent_ids:
            tool_members.setdefault(edge.get("source"), set()).add(edge["target"])
    broadcaster = get_status_broadcaster()
    loop_time = asyncio.get_running_loop().time()
    aliases: dict[str, list] = {}
    for cls in registered_node_classes().values():
        name = getattr(cls, "tool_name", None)
        if isinstance(name, str) and name:
            aliases.setdefault(name, []).append(cls)
    steps = []
    for node in graph.get("nodes", []):
        if not isinstance(node, Mapping) or not node.get("id") or (node.get("data") or {}).get("disabled"):
            continue
        node_id = node["id"]
        # Perpetual app/schedule listeners do not represent an accepted job.
        if node_id in index.trigger_ids:
            continue
        cached = broadcaster.get_node_status(node_id)
        if not isinstance(cached, Mapping) or cached.get("workflow_id") != workflow_id or cached.get("status") not in {"executing", "waiting"}:
            continue
        data = cached.get("data") or {}
        if data.get("generation") is not None and getattr(control, "generation", None) is not None and data["generation"] != control.generation:
            continue
        phase = data.get("phase")
        status = "waiting" if cached["status"] == "waiting" else "running"
        if phase == "retry_wait":
            status = "waiting"
        member = _label(index.labels.get(node_id), "Employee") if node_id in index.agent_ids else "Employee"
        if node_id not in index.agent_ids and len(tool_members.get(node_id, set())) == 1:
            member = _label(index.labels.get(next(iter(tool_members[node_id]))), "Employee")
        cls = get_node_class(index.node_types.get(node_id, ""))
        label = _label(index.labels.get(node_id) or getattr(cls, "display_name", None), "Using a connected app")
        if node_id in index.agent_ids:
            label = {"invoking_llm": "Thinking through the next step", "initializing": "Getting ready",
                     "building_tools": "Getting ready", "loading_memory": "Loading saved context",
                     "compacting_context": "Organizing saved context", "retry_wait": "Waiting to try again",
                     "tool_completed": "Checking the result", "executing_tool": "Using a connected app"}.get(
                         phase, "Waiting before continuing" if status == "waiting" else "Working on the request")
            # Resolve only known callable identities; never forward arbitrary
            # model strings, status messages, arguments or capability payloads.
            tool = data.get("tool_name") if phase == "executing_tool" else None
            tool_cls = get_node_class(tool) if isinstance(tool, str) else None
            if tool_cls is None and isinstance(tool, str) and len(aliases.get(tool, [])) == 1:
                tool_cls = aliases[tool][0]
            if tool_cls is not None:
                label = "Using " + _label(getattr(tool_cls, "display_name", None), "a connected app")
        elif node_id in index.gate_ids:
            label = "Waiting for your approval"
            status = "waiting"
        stamp = cached.get("timestamp")
        updated = now - timedelta(seconds=max(0, loop_time - stamp)) if isinstance(stamp, (int, float)) else None
        steps.append(_step(f"node:{node_id}", label, member, status, node_id=node_id, started_at=updated, updated_at=updated))
        if len(steps) > MAX_STEPS:
            break
    return steps


async def work_progress(database: Any, workflow: Any, *, control: Any = None,
                        pending_approvals: int = 0, browser_request: dict | None = None,
                        job_status: Mapping | None = None) -> dict | None:
    """Team jobs, legacy chat and scheduled node work through one safe view.

    Select the active job before a newer completed job, and scope its tasks
    to that exact team. Old teams must not appear in a newer job's review.
    """
    now = datetime.now(timezone.utc)
    graph = getattr(workflow, "data", None) or {}
    snapshot = getattr(control, "graph_snapshot", None)
    if getattr(control, "status", None) in {"running", "starting", "pausing", "paused", "resuming"} and isinstance(snapshot, Mapping) and snapshot.get("nodes"):
        graph = snapshot
    index = index_graph(graph)
    steps: list[dict] = []
    starts: list[datetime] = []
    updates: list[datetime] = []
    state = None
    truncated = False
    job = None
    chats = []
    unavailable = False
    try:
        async with database.get_session() as session:
            job = (await session.execute(select(EmployeeJob.team_id, EmployeeJob.state, EmployeeJob.updated_at).where(EmployeeJob.workflow_id == workflow.id)
                .order_by(case((EmployeeJob.state.in_(ACTIVE_JOBS), 0), else_=1), EmployeeJob.updated_at.desc()).limit(1))).first()
            team = None
            if job and job.team_id:
                team = (await session.execute(select(AgentTeam.id, AgentTeam.created_at).where(AgentTeam.id == job.team_id, AgentTeam.workflow_id == workflow.id))).first()
            elif not job:
                team = (await session.execute(select(AgentTeam.id, AgentTeam.created_at).where(AgentTeam.workflow_id == workflow.id, AgentTeam.status == "active")
                    .order_by(AgentTeam.created_at.desc()).limit(1))).first()
            if job:
                state = _state(job.state)
                updates.append(_moment(job.updated_at))
                if not team:
                    # Older job rows have no admission timestamp; while queued
                    # this is the persisted creation time, otherwise a lower-
                    # precision last transition time, never a fabricated date.
                    starts.append(_moment(job.updated_at))
            if team:
                starts.append(_moment(team.created_at))
                members = (await session.execute(select(TeamMember.agent_node_id, TeamMember.agent_label)
                    .where(TeamMember.team_id == team.id).limit(100))).all()
                member_labels = {member.agent_node_id: member.agent_label for member in members}
                tasks = (await session.execute(select(TeamTask.id, TeamTask.title, TeamTask.assigned_to, TeamTask.status,
                    TeamTask.cancellation_requested, TeamTask.created_at, TeamTask.started_at, TeamTask.completed_at).where(TeamTask.team_id == team.id)
                    .where((TeamTask.workflow_id == workflow.id) | (TeamTask.workflow_id.is_(None)))
                    .order_by(case((TeamTask.status.in_(ACTIVE_TASKS), 0), else_=1), TeamTask.created_at)
                    .limit(MAX_STEPS + 1))).all()
                truncated = len(tasks) > MAX_STEPS
                for task in tasks[:MAX_STEPS]:
                    node_id = task.assigned_to
                    status = "stopping" if task.cancellation_requested and task.status in ACTIVE_TASKS else task.status
                    steps.append(_step(f"task:{task.id}", _label(task.title, "Assigned task"),
                        _label(index.labels.get(node_id) or member_labels.get(node_id), "Team member"), status, node_id=node_id,
                        started_at=task.started_at or task.created_at, updated_at=task.completed_at or task.started_at or task.created_at,
                        finished_at=task.completed_at))
                    updates.append(_moment(task.completed_at or task.started_at or task.created_at))
            chats = (await session.execute(select(ChatRun.run_id, ChatRun.state, ChatRun.created_at, ChatRun.started_at, ChatRun.steps)
                .where(ChatRun.workflow_id == workflow.id, ChatRun.state.in_(LIVE_STATES))
                .order_by(ChatRun.created_at).limit(5))).all()
        truncated = truncated or len(chats) > 4
        from services.chat.hub import get_chat_hub
        for run in chats[:4]:
            state = _state(run.state) if state not in {"running", "waiting", "reviewing", "stopping"} else state
            starts.append(_moment(run.started_at or run.created_at))
            updates.append(_moment(run.started_at or run.created_at))
            agent_id = index.talk.agent_node_id
            member = _label(index.labels.get(agent_id), "Employee")
            steps.append(_step(f"chat:{run.run_id}", "Answering your message" if run.state == "running" else "Waiting to answer your message",
                member, run.state, node_id=agent_id, started_at=run.started_at or run.created_at, updated_at=run.started_at or run.created_at))
            live = get_chat_hub().live_snapshot(run.run_id)
            saved = live.get("steps", run.steps) if isinstance(live, Mapping) and live.get("workflow_id") == workflow.id else run.steps
            truncated = truncated or len(saved) > MAX_STEPS
            for tool in saved[-MAX_STEPS:]:
                if not isinstance(tool, Mapping):
                    continue
                steps.append(_step(f"chat:{run.run_id}:{tool.get('step_id')}", _label(tool.get("name"), "Using a connected app"), member,
                    tool.get("state", "running"), node_id=agent_id))
    except Exception:
        # Progress is a courtesy; unavailable ledgers must not break controls
        # or make a saved employee look as though creation failed.
        logger.warning("Could not read employee work progress", workflow_id=workflow.id, exc_info=True)
        unavailable = True
    try:
        live_steps = [] if job and job.state in {"delivered", "failed", "cancelled"} and not chats else _live_steps(workflow.id, graph, now, control)
    except Exception:
        live_steps = []
    steps = live_steps + steps
    if live_steps:
        state = "waiting" if all(step["status"] == "waiting" for step in live_steps) else "running"
    if pending_approvals:
        state = "waiting"
        steps.insert(0, _step("approval", "Waiting for your approval", "Employee", "waiting"))
    elif browser_request:
        state = "waiting"
        steps.insert(0, _step("browser", "Waiting for your help in the browser", "Employee", "waiting", node_id=browser_request.get("node_id")))
    job_message = None
    if job_status and job_status.get("state") in {"failed", "cancelled", "waiting_for_approval", "delivery_needs_review"}:
        state = _state(job_status["state"]) if job_status["state"] in {"failed", "cancelled"} else "waiting"
        job_message = job_status.get("message")
    paused = getattr(control, "status", None) in {"paused", "pausing"} and (steps or state in {"queued", "running", "waiting", "reviewing", "stopping"})
    if paused:
        state = "waiting"
        for step in steps:
            if step["status"] in {"queued", "running", "reviewing", "stopping"}:
                step["status"] = "waiting"
    active = [step["status"] for step in steps if step["status"] not in {"done", "failed", "cancelled"}]
    if not state and steps:
        statuses = {step["status"] for step in steps}
        state = next((candidate for candidate in ("stopping", "running", "waiting", "reviewing", "queued", "failed", "cancelled", "done") if candidate in statuses), None)
    if state == "running" and active and all(status in {"waiting", "reviewing", "queued"} for status in active):
        state = "reviewing" if "reviewing" in active else "waiting" if "waiting" in active else "queued"
    elif state == "running" and active and all(status == "stopping" for status in active):
        state = "stopping"
    if not state and not steps and not unavailable:
        return None
    state = state or ("unavailable" if unavailable else "running")
    messages = {"queued": "Your request is saved and waiting to start.", "running": "Working on your request.",
                "waiting": "Waiting before continuing.", "reviewing": "Checking the work before sending the result.",
                "done": "The work is finished.", "failed": "The work could not finish. Ask them to try again.",
                "cancelled": "This work was stopped.", "stopping": "Stopping the current work.",
                "unavailable": "Work progress could not be loaded. Try refreshing."}
    updates.extend(datetime.fromisoformat(step["updated_at"]) for step in live_steps if step["updated_at"])
    if not any(starts):
        starts.extend(datetime.fromisoformat(step["started_at"]) for step in live_steps if step["started_at"])
    return {"state": state, "message": "Work is paused. Resume when you are ready." if paused else job_message or
            (messages["unavailable"] if unavailable and not steps else
            "Waiting for your approval before sending." if pending_approvals else
            "Waiting for your help in the browser." if browser_request else messages[state]),
            "started_at": _iso(min(filter(None, starts), default=None)),
            "updated_at": _iso(max(filter(None, updates), default=None)),
            "steps": steps[:MAX_STEPS], "truncated": truncated or len(steps) > MAX_STEPS}
