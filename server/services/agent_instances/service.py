"""Transactional lifecycle and inbox operations for reusable agents.

Signals are wakeup hints. All work, fences and receipts survive independently
in this store; callers must acknowledge a committed receipt before dispatch.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from datetime import timedelta
from typing import Any

from sqlalchemy import func, tuple_, update
from sqlmodel import select

from models.agent_instances import (
    AgentDelivery, AgentInstance, AgentInstanceConversation, AgentInstanceMutation,
    AgentMessageChain, AgentReconcileLease, AgentRun, now,
)
from models.database import AgentMessage, AgentTeam, TeamMember, TeamTask, TeamTaskAttempt
from models.employees import EmployeeJob
from services.llm.protocol import Message, MessageWire, message_to_wire

PEER_MESSAGE_BYTES = 8192
PENDING_MESSAGES = 256
PENDING_BYTES = 1024 * 1024
CHAIN_TURNS = 8
MAX_BATCH_MESSAGES = 16
MAX_BATCH_BYTES = 32768
RESULT_PREVIEW_CHARS = 4000
MAX_OWNER_DECISIONS = 32
MAX_WAITING_TASKS = 512
TERMINAL_DELIVERY = {"consumed", "cancelled", "recipient_closed"}
MANAGER_OPERATIONS = {"spawn", "assign_task", "modify_task", "request_changes", "accept_task", "retry_task", "cancel_task", "reassign", "answer_decision", "escalate_decision"}


class InstanceError(ValueError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _id(prefix: str, *values: Any) -> str:
    return prefix + _hash(values)[:40]


def _wire(row: AgentDelivery) -> MessageWire:
    sender = row.sender_id or "runtime"
    prefix = f"[Agent message from {sender}; this is not owner approval]" if row.kind == "peer" else f"[{row.kind}]"
    return message_to_wire(Message(role="user", content=f"{prefix}\n{row.content}"))


def task_ref(task: TeamTask | dict) -> str:
    get = task.get if isinstance(task, dict) else lambda key: getattr(task, key)
    data = [1, get("team_id"), get("id"), get("revision"), get("current_attempt"), get("status")]
    return "taskref_" + base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def decode_task_ref(value: str) -> list:
    try:
        if not value.startswith("taskref_"):
            raise ValueError()
        raw = value[8:]
        decoded = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        if not isinstance(decoded, list) or len(decoded) != 6 or decoded[0] != 1:
            raise ValueError()
        return decoded
    except Exception as exc:
        raise InstanceError("invalid_task_ref", "Use the task_ref returned by get_task or list_tasks.") from exc


async def outer_assignment(session, job: EmployeeJob) -> TeamTask | None:
    """Resolve Chat's unique assignment from the job's admitted hierarchy."""
    query = select(TeamTask).where(TeamTask.team_id == job.team_id, TeamTask.parent_agent_id == job.lead_node_id,
        TeamTask.assigned_to == job.source["team_plan"]["orchestrator_node_id"], TeamTask.assigning_run_id.is_not(None))
    tasks = (await session.execute(query.limit(2))).scalars().all()
    return tasks[0] if len(tasks) == 1 else None


def _task_actions(task: TeamTask) -> list[str]:
    actions = {
        "blocked": ["modify_task", "cancel_task"], "queued": ["modify_task", "cancel_task"],
        "running": ["cancel_task"], "submitted": ["accept_task", "request_changes", "retry_task", "reassign"],
        "failed": ["retry_task", "reassign"], "cancelled": ["retry_task", "reassign"],
    }.get(task.status, [])
    if task.assigning_run_id and task.status in {"submitted", "failed"}:
        actions.append("cancel_task")
    return actions


def _task_dict(task: TeamTask) -> dict:
    result = task.model_dump(mode="json")
    result["depends_on"] = _dependency_ids(task)
    result["task_ref"] = task_ref(task)
    result["result"] = _result_preview(task.result, task.id)
    result["allowed_actions"] = _task_actions(task)
    return result


def _dependency_ids(task: TeamTask) -> list[str]:
    value = task.depends_on or {}
    return list(value.get("task_ids", [])) if isinstance(value, dict) else list(value)


def _public_instance(instance: AgentInstance) -> dict:
    """Model-visible identity, never provider session state or credentials."""
    result = {key: getattr(instance, key) for key in (
        "id", "template_node_id", "display_name", "role", "state", "active_run_id", "halt_reason")}
    result["capabilities"] = (instance.config.get("template") or {}).get("capabilities")
    result["runtime_capabilities"] = (instance.config.get("template") or {}).get("runtime_capabilities") or {}
    return result


def _public_run(run: AgentRun) -> dict:
    result = {key: getattr(run, key) for key in ("id", "status", "task_id", "attempt", "error")}
    result["result"] = _result_preview(run.result, run.id)
    return result


def _public_attempt(attempt: TeamTaskAttempt) -> dict:
    result = attempt.model_dump(mode="json")
    result["result"] = _result_preview(attempt.result, attempt.id)
    if result.get("input_snapshot"):
        result["input_snapshot"] = {key: value for key, value in result["input_snapshot"].items() if key != "configuration"}
    return result


def _result_preview(value: Any, identity: str) -> Any:
    rendered = json.dumps(value, ensure_ascii=False, default=str) if value is not None else ""
    return value if len(rendered) <= RESULT_PREVIEW_CHARS else {
        "summary": rendered[:RESULT_PREVIEW_CHARS], "truncated": True, "total_chars": len(rendered),
        "result_ref": identity, "hint": "Use inspect_task to page or search the retained full result."}


def _inspect_results(task: TeamTask, attempts: list[TeamTaskAttempt], args: dict) -> dict:
    """Bounded pages/search over full stored results, independent of Temporal."""
    query = str(args.get("query") or "") if args.get("detail") == "search" else ""
    document = json.dumps({"task_id": task.id, "attempts": [
        {"attempt": attempt.attempt_number, "result": attempt.result} for attempt in attempts
    ]}, ensure_ascii=False, default=str, indent=2)
    offset = 0
    cursor = str(args.get("cursor") or "")
    if cursor.startswith("result_"):
        try:
            raw = cursor[7:]
            value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            if value[:4] != [1, task.id, task.revision, query] or len(value) != 5:
                raise ValueError()
            offset = int(value[4])
            if offset < 0 or offset > len(document):
                raise ValueError()
        except Exception as exc:
            raise InstanceError("stale_reference", "Read the current task and restart result inspection.") from exc
    if query:
        match = re.search(re.escape(query), document[offset:], flags=re.IGNORECASE)
        if match:
            position = offset + match.start()
            start, end = max(offset, position - 500), min(len(document), position + 3500)
            text = document[start:end]
            next_offset = min(len(document), offset + match.end())
        else:
            start, text, next_offset = offset, "", len(document)
    else:
        start, next_offset = offset, min(len(document), offset + RESULT_PREVIEW_CHARS)
        text = document[start:next_offset]
    next_cursor = None
    if next_offset < len(document):
        payload = json.dumps([1, task.id, task.revision, query, next_offset], separators=(",", ":")).encode()
        next_cursor = "result_" + base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return {"text": text, "offset": start, "total_chars": len(document), "truncated": next_cursor is not None,
            "next_cursor": next_cursor, **({"query": query, "matched": bool(text)} if query else {})}


class AgentInstanceService:
    def __init__(self, database, *, limits: dict | None = None):
        from core.env_defaults import env_int
        self.database = database
        limits = {
            "peer_message_bytes": env_int("AGENT_PEER_MESSAGE_BYTES"),
            "pending_messages": env_int("AGENT_PENDING_MESSAGES"),
            "pending_bytes": env_int("AGENT_PENDING_BYTES"),
            "chain_turns": env_int("AGENT_PEER_CHAIN_TURNS"),
            **(limits or {}),
        }
        self.peer_message_bytes = max(1, int(limits.get("peer_message_bytes", PEER_MESSAGE_BYTES)))
        self.pending_messages = max(1, int(limits.get("pending_messages", PENDING_MESSAGES)))
        self.pending_bytes = max(1, int(limits.get("pending_bytes", PENDING_BYTES)))
        self.chain_turns = max(1, int(limits.get("chain_turns", CHAIN_TURNS)))

    @staticmethod
    def _scope(scope: dict) -> dict:
        value = dict(scope)
        value["job_id"] = str(value.get("job_id") or value.get("employee_job_id") or value.get("execution_id") or "")
        value["lead_node_id"] = str(value.get("lead_node_id") or value.get("team_lead_node_id") or value.get("node_id") or "")
        value["generation"] = int(value.get("generation") or value.get("run_generation") or 0)
        for key in ("workflow_id", "team_id", "job_id", "lead_node_id"):
            if not value.get(key):
                raise InstanceError("invalid_scope", f"Trusted {key} is required.")
        return value

    async def _authorize(self, session, scope: dict, *, lead: bool = False, operation: str = "") -> AgentTeam:
        team = await session.get(AgentTeam, scope["team_id"])
        if team is None or team.workflow_id != scope["workflow_id"] or team.team_lead_node_id != scope["lead_node_id"]:
            raise InstanceError("invalid_scope")
        if scope.get("execution_id") and team.execution_id != scope["execution_id"]:
            raise InstanceError("invalid_scope")
        job = await session.get(EmployeeJob, scope["job_id"])
        plan = (job.source.get("team_plan") or {}) if job else {}
        scope.pop("_report_ids", None)
        if plan.get("version") == 3:
            if (job.team_id != team.id or job.workflow_id != team.workflow_id or
                    int(job.source.get("generation") or 0) != scope["generation"]):
                raise InstanceError("invalid_scope")
            scope["_report_ids"] = set()
        actor_id = scope.get("agent_instance_id")
        if actor_id:
            actor = await self._instance(session, str(actor_id), scope)
            if "_report_ids" in scope:
                scope["_actor_node"] = actor.template_node_id
                scope["_report_ids"] = {m["node_id"] for m in plan["members"] if m.get("parent_node_id") == actor.template_node_id}
                if (actor.role == "lead" and operation in {"spawn", "assign_task", "reassign"}):
                    raise InstanceError("invalid_target", "Review the original Orchestrator assignment on this job.")
            if actor.state == "closed":
                raise InstanceError("recipient_closed")
            if lead and actor.role != "lead" and not (operation in MANAGER_OPERATIONS and scope.get("_report_ids")):
                raise InstanceError("lead_required")
        elif operation and "_report_ids" in scope:
            raise InstanceError("invalid_scope", "An admitted employee instance is required.")
        return team

    async def _instance(self, session, instance_id: str, scope: dict | None = None) -> AgentInstance:
        instance = await session.get(AgentInstance, instance_id)
        if instance is None:
            raise InstanceError("agent_not_found")
        if scope and any(getattr(instance, key) != scope[key] for key in ("workflow_id", "generation", "team_id", "job_id")):
            raise InstanceError("agent_not_found", "The agent is outside this job or generation.")
        return instance

    async def _receipt(self, session, key: str, fingerprint: str) -> dict | None:
        prior = await session.get(AgentInstanceMutation, key)
        if prior:
            if prior.fingerprint != fingerprint:
                raise InstanceError("idempotency_conflict")
            return deepcopy(prior.result)
        return None

    async def _commit(self, session, key: str, fingerprint: str, result: dict) -> dict:
        if result.get("task_outcome"):
            result["task_event_receipt_id"] = key
        session.add(AgentInstanceMutation(id=key, fingerprint=fingerprint, result=deepcopy(result),
                                          task_event_pending=bool(result.get("task_outcome"))))
        await session.commit()
        return result

    async def pending_task_events(self, limit: int) -> list[dict]:
        async with self.database.get_session() as session:
            query = select(AgentInstanceMutation.result).where(AgentInstanceMutation.task_event_pending.is_(True))
            return list((await session.scalars(query.order_by(AgentInstanceMutation.created_at).limit(limit))).all())

    async def acknowledge_task_event(self, receipt_id: str) -> None:
        async with self.database.reserved_session() as session:
            await session.execute(update(AgentInstanceMutation).where(
                AgentInstanceMutation.id == receipt_id).values(task_event_pending=False))
            await session.commit()

    async def _template(self, session, scope: dict, template: dict, *, lead: bool = False) -> dict:
        template = deepcopy(template)
        from services.agent_instances.capabilities import runtime_capabilities
        template["runtime_capabilities"] = runtime_capabilities(str(template.get("node_type") or "aiAgent"))
        node_id = str(template.get("node_id") or "")
        members = (await session.execute(select(TeamMember).where(
            TeamMember.team_id == scope["team_id"], TeamMember.agent_node_id == node_id))).scalars().all()
        if lead:
            if node_id != scope["lead_node_id"]:
                raise InstanceError("invalid_target")
        elif not any(member.role == "teammate" and member.status != "offline" for member in members):
            raise InstanceError("invalid_target", "Choose a connected teammate template.")
        elif "_report_ids" in scope and node_id not in scope["_report_ids"]:
            raise InstanceError("invalid_target", "Choose a direct report of the assigning employee.")
        return template

    async def _new_instance(self, session, scope: dict, template: dict, identity: str, *, role: str = "teammate") -> AgentInstance:
        instance = AgentInstance(
            id=identity, workflow_id=scope["workflow_id"], generation=scope["generation"],
            team_id=scope["team_id"], job_id=scope["job_id"], template_node_id=template["node_id"],
            parent_instance_id=scope.get("agent_instance_id"), role=role,
            display_name=str(template.get("label") or template.get("node_type") or "Agent")[:255],
            config={"template": deepcopy(template),
                "runtime_context": deepcopy(template.get("runtime_context") or scope.get("runtime_context") or {}),
                "scope": {key: scope.get(key) for key in (
                "workflow_id", "generation", "team_id", "job_id", "execution_id", "root_execution_id", "lead_node_id")}},
        )
        session.add(instance)
        session.add(AgentInstanceConversation(instance_id=identity))
        await session.flush()
        return instance

    async def ensure_lead(self, *, scope: dict, template: dict, request_id: str) -> dict:
        scope = self._scope(scope)
        identity = _id("agent_", scope["team_id"], scope["job_id"], scope["generation"], "lead")
        async with self.database.reserved_session() as session:
            await self._authorize(session, scope)
            existing = await session.get(AgentInstance, identity)
            if existing:
                return existing.model_dump(mode="json")
            template = await self._template(session, scope, template, lead=True)
            instance = await self._new_instance(session, scope, template, identity, role="lead")
            if template.get("initial_instructions"):
                await self._enqueue(session, instance, identity=_id("delivery_", identity, "initial"),
                    sender_id=None, kind="handoff", content=str(template["initial_instructions"]))
            await session.commit()
            return instance.model_dump(mode="json")

    async def get_instance(self, instance_id: str, *, scope: dict | None = None) -> dict:
        async with self.database.get_session() as session:
            instance = await self._instance(session, instance_id, self._scope(scope) if scope else None)
            return instance.model_dump(mode="json")

    async def get_transcript(self, instance_id: str) -> dict:
        async with self.database.get_session() as session:
            row = await session.get(AgentInstanceConversation, instance_id)
            if row is None:
                raise InstanceError("agent_not_found")
            return {"messages": deepcopy(row.messages), "revision": row.revision, "owning_run_id": row.owning_run_id}

    async def get_run(self, instance_id: str, run_id: str) -> dict:
        async with self.database.get_session() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.instance_id != instance_id:
                raise InstanceError("stale_run")
            return run.model_dump(mode="json")

    async def record_run_permit(self, instance_id: str, run_id: str, *, lease: dict) -> dict:
        """Reserve cleanup provenance before a capacity acquisition can occur."""
        required = {"lease_id", "root_execution_id", "workflow_id", "workflow_run_id"}
        if set(lease) != required or any(not isinstance(value, str) or not value for value in lease.values()):
            raise InstanceError("invalid_permit_identity")
        async with self.database.reserved_session() as session:
            instance, run = await self._owned_run(session, instance_id, run_id)
            if instance.state != "running":
                raise InstanceError("stale_run")
            scope = instance.config.get("scope") or {}
            if lease["root_execution_id"] != str(scope.get("root_execution_id") or scope.get("execution_id") or ""):
                raise InstanceError("invalid_permit_identity")
            prior = next((item for item in run.permit_leases if item["lease_id"] == lease["lease_id"]), None)
            if prior:
                if {key: prior[key] for key in required} != lease:
                    raise InstanceError("idempotency_conflict")
                return prior
            entry = {**lease, "released": False}
            run.permit_leases = [*run.permit_leases, entry]
            run.permits_pending = True
            await session.commit()
            return entry

    async def release_run_permit(self, instance_id: str, run_id: str, *, lease_id: str) -> None:
        """Acknowledge release even after completion/fencing of the executor."""
        async with self.database.reserved_session() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or run.instance_id != instance_id:
                raise InstanceError("stale_run")
            run.permit_leases = [{**entry, "released": True} if entry["lease_id"] == lease_id else entry
                                for entry in run.permit_leases]
            run.permits_pending = any(not entry.get("released") for entry in run.permit_leases)
            await session.commit()

    async def list_pending_permit_runs(self, limit: int = 100) -> list[dict]:
        async with self.database.reserved_session() as session:
            rows = (await session.execute(select(AgentRun).where(AgentRun.permits_pending == True)
                .order_by(AgentRun.last_permit_reconcile_at.asc().nullsfirst(), AgentRun.created_at, AgentRun.id)
                .limit(max(1, limit)))).scalars().all()
            checked_at = now()
            for row in rows:
                row.last_permit_reconcile_at = checked_at
            await session.commit()
            return [row.model_dump(mode="json") for row in rows]

    async def fence_orphan_run(self, instance_id: str, run_id: str) -> None:
        """After exact Temporal-owner loss, preserve a draining recovery record."""
        async with self.database.reserved_session() as session:
            instance = await self._instance(session, instance_id)
            run = await session.get(AgentRun, run_id)
            if run is None or run.instance_id != instance_id or instance.active_run_id != run_id:
                return
            if run.status == "draining":
                return
            if instance.state != "closed":
                await self._control(session, instance, "stop_agent")
            else:
                instance.fence += 1
                run.status, run.error = "draining", "interrupted"
            await session.commit()

    async def admission_state(self, instance_id: str, run_id: str) -> dict:
        """Restore the error budget before an opaque activity admits requests."""
        async with self.database.get_session() as session:
            instance = await self._instance(session, instance_id)
            run = await session.get(AgentRun, run_id)
            if run is None or run.instance_id != instance_id:
                raise InstanceError("stale_run")
            reason = ""
            if run.status != "running" or instance.active_run_id != run.id or instance.fence != run.fence:
                reason = "stale_run"
            elif instance.state in {"halted", "closed"}:
                reason = instance.halt_reason or instance.state
            return {"invalid_count": run.invalid_requests, "invalid_ids": list(run.invalid_request_ids or []),
                    "max_invalid": 3, "reason": reason}

    async def _dependencies(self, session, scope: dict, values: list, *, task_id: str | None = None) -> tuple[list[str], bool]:
        ids, accepted = [], True
        for value in values:
            identity = decode_task_ref(value)[2] if str(value).startswith("taskref_") else str(value)
            if identity == task_id:
                raise InstanceError("invalid_dependencies", "A task cannot depend on itself.")
            task = await session.get(TeamTask, identity)
            if task is None or task.team_id != scope["team_id"]:
                raise InstanceError("invalid_dependencies")
            if task.assigned_instance_id:
                await self._instance(session, task.assigned_instance_id, scope)
            else:
                raise InstanceError("invalid_dependencies", "Dependencies must belong to this instance protocol job.")
            if scope.get("_actor_node") and not await self._same_assigning_attempt(session, scope, task):
                raise InstanceError("invalid_dependencies", "Depend only on work assigned for this task attempt.")
            if identity not in ids:
                ids.append(identity)
            accepted = accepted and task.status == "accepted"
        if task_id:
            pending, seen = list(ids), set()
            while pending:
                identity = pending.pop()
                if identity == task_id:
                    raise InstanceError("invalid_dependencies", "Task dependencies must not contain a cycle.")
                if identity in seen:
                    continue
                seen.add(identity)
                dep = await session.get(TeamTask, identity)
                pending.extend(_dependency_ids(dep) if dep else [])
        return ids, accepted

    async def _enqueue(self, session, instance: AgentInstance, *, identity: str, sender_id: str | None,
                       kind: str, content: str, payload: dict | None = None, task: TeamTask | None = None,
                       chain_id: str | None = None) -> AgentDelivery:
        fingerprint = _hash([sender_id, instance.id, kind, content, payload, task.id if task else None,
                             task.current_attempt if task else None, chain_id])
        old = await session.get(AgentDelivery, identity)
        if old:
            if old.fingerprint != fingerprint:
                raise InstanceError("idempotency_conflict")
            return old
        if instance.state == "closed":
            raise InstanceError("recipient_closed")
        if kind == "peer":
            if len(content.encode()) > self.peer_message_bytes:
                raise InstanceError("message_too_large")
            pending = (await session.execute(select(AgentDelivery).where(
                AgentDelivery.recipient_id == instance.id, AgentDelivery.state.in_(["queued", "admitted", "held"]),
                AgentDelivery.kind == "peer"))).scalars().all()
            if len(pending) >= self.pending_messages or sum(len(row.content.encode()) for row in pending) + len(content.encode()) > self.pending_bytes:
                raise InstanceError("inbox_full", "Recipient inbox is full; wait for existing messages to be consumed.")
        instance.sequence += 1
        instance.revision += 1
        instance.updated_at = now()
        delivery = AgentDelivery(
            id=identity, recipient_id=instance.id, sender_id=sender_id, team_id=instance.team_id,
            generation=instance.generation, sequence=instance.sequence, kind=kind, content=content,
            payload=deepcopy(payload or {}), fingerprint=fingerprint, task_id=task.id if task else None,
            attempt=task.current_attempt if task else None, chain_id=chain_id,
        )
        session.add(delivery)
        return delivery

    async def enqueue_input(self, instance_id: str, *, content: str, request_id: str,
                            kind: str = "handoff", payload: dict | None = None) -> dict:
        """Trusted runtime input (owner handoff/review), not a public send API."""
        async with self.database.reserved_session() as session:
            instance = await self._instance(session, instance_id)
            delivery = await self._enqueue(session, instance, identity=_id("delivery_", instance_id, request_id),
                                          sender_id=None, kind=kind, content=content, payload=payload)
            parked = await self._parked_wait(session, instance)
            eligible = not parked or await self._matches_parked_delivery(session, instance, parked, delivery)
            wake = [instance_id] if eligible else []
            await session.commit()
            return {"delivery": delivery.model_dump(mode="json"), "instance_wake": wake}

    async def _attempt(self, session, task: TeamTask, instance: AgentInstance) -> TeamTaskAttempt:
        snapshot = {"instructions": (task.acceptance_criteria or {}).get("instructions", task.mission), "context": deepcopy(task.context or {}),
                    "feedback": deepcopy(task.review_feedback or []), "dependencies": _dependency_ids(task),
                    "acceptance_criteria": deepcopy(task.acceptance_criteria or {}), "revision": task.revision,
                    "configuration": deepcopy(instance.config)}
        attempt = TeamTaskAttempt(id=f"{task.id}:attempt:{task.current_attempt}", task_id=task.id,
            team_id=task.team_id, attempt_number=task.current_attempt, assignee_node_id=instance.template_node_id,
            assigned_instance_id=instance.id, status=task.status, input_snapshot=snapshot)
        session.add(attempt)
        return attempt

    def _task_input(self, task: TeamTask) -> str:
        content = str((task.acceptance_criteria or {}).get("instructions", task.mission) or "")
        for index, feedback in enumerate(task.review_feedback or [], 1):
            content += f"\n\nReview feedback {index}:\n{feedback}"
        return content

    async def _queue_task(self, session, task: TeamTask, instance: AgentInstance, *, chain_id: str | None = None):
        return await self._enqueue(session, instance, identity=_id("delivery_", task.id, task.current_attempt, task.revision),
            sender_id=None, kind="task_assignment", content=self._task_input(task), task=task, chain_id=chain_id)

    async def _new_task(self, session, scope: dict, instance: AgentInstance, args: dict, key: str, *, intake: bool = False) -> TeamTask:
        if instance.role != "teammate":
            raise InstanceError("invalid_target", "Assign reviewed work to a spawned teammate.")
        team = await session.get(AgentTeam, instance.team_id)
        if team and team.status == "completed":
            raise InstanceError("invalid_transition", "This team has already finished its reviewed work.")
        instructions = str(args.get("instructions") or "")
        if not instructions.strip() or len(instructions) > (20000 if intake else 10000):
            raise InstanceError("invalid_tool_input", "instructions must contain between 1 and 10000 characters.")
        if instance.state in {"closed", "halted"}:
            raise InstanceError("agent_unavailable")
        if "_report_ids" in scope and instance.template_node_id not in scope["_report_ids"]:
            raise InstanceError("invalid_target", "Assign work only to a direct report.")
        assigner = scope.get("_actor_node") or scope["lead_node_id"]
        dependencies, accepted = await self._dependencies(session, scope, list(args.get("after") or []))
        task = TeamTask(id=_id("task_", key), team_id=scope["team_id"], workflow_id=scope["workflow_id"],
            execution_id=scope.get("execution_id"), root_execution_id=scope.get("root_execution_id"),
            parent_agent_id=assigner, title=instructions.strip().splitlines()[0][:160],
            description=instructions[:5000], mission=instructions[:10000], context={},
            acceptance_criteria={"instructions": instructions}, created_by=assigner,
            assigning_run_id=scope.get("agent_run_id") if "_report_ids" in scope else None,
            assigned_to=instance.template_node_id, assigned_instance_id=instance.id,
            depends_on={"task_ids": dependencies}, status="queued" if accepted else "blocked", current_attempt=0,
            review_feedback=[], protocol_version=1, queue_sequence=instance.sequence + 1)
        session.add(task)
        await session.flush()
        await self._attempt(session, task, instance)
        await self._queue_task(session, task, instance, chain_id=scope.get("_chain_id"))
        return task

    async def _read_task(self, session, scope: dict, value: str, *, mutate: bool = False) -> TeamTask:
        ref = decode_task_ref(value) if str(value).startswith("taskref_") else None
        if mutate and ref is None:
            raise InstanceError("invalid_task_ref", "Use the task_ref from the latest task read.")
        identity = ref[2] if ref else value
        task = await session.get(TeamTask, identity)
        if task is None or task.team_id != scope["team_id"] or task.protocol_version != 1:
            raise InstanceError("task_not_found")
        await self._instance(session, task.assigned_instance_id, scope)
        if ref and ref[1] != task.team_id:
            raise InstanceError("task_not_found")
        if mutate and ref[3:] != [task.revision, task.current_attempt, task.status]:
            raise InstanceError("stale_reference", "Task state changed; get_task and use its latest task_ref.")
        if mutate and "_report_ids" in scope and not await self._same_assigning_attempt(session, scope, task):
            raise InstanceError("invalid_target", "Review only work assigned by this employee for its current task.")
        return task

    @staticmethod
    async def _same_assigning_attempt(session, scope, task):
        assigner = await session.get(AgentRun, task.assigning_run_id) if task.assigning_run_id else None
        run = await session.get(AgentRun, scope["agent_run_id"])
        return (assigner and run and assigner.instance_id == run.instance_id
                and (assigner.task_id, assigner.attempt) == (run.task_id, run.attempt))

    @staticmethod
    def _review_instructions(task: TeamTask) -> str:
        prompt = f"Task {task.id} is {task.status}. Read get_task with this ID and inspect the retained result."
        if task.status == "accepted" or task.reviewed_blocker:
            return prompt + " The review decision is recorded. Write the final answer for the user, including any blocker and completed work."
        if task.status in {"failed", "submitted"}:
            if task.status == "submitted":
                action = "Use accept_task for satisfactory work or request_changes with specific feedback."
            elif task.current_attempt < task.max_retries:
                action = "Use retry_task if the same assignment can succeed on another attempt."
            else:
                action = "This assignment has exhausted its retry budget."
            return (f"{prompt} {action} If a blocker requires owner action, use cancel_task with the blocker as the reason. "
                    "Make the decision with the current task_ref before ending your turn.")
        if task.status == "cancelled" and task.current_attempt < task.max_retries:
            return prompt + " Use retry_task with the current task_ref to resume this assignment if appropriate."
        return prompt

    async def _remind_review(self, session, reviewer, child, *, parent_task, chain_id):
        # One corrective turn for this exact result, even across restarts.
        # The session claims the queued notice after releasing its current slot.
        identity = _id("delivery_", "review_reminder", reviewer.id, task_ref(child))
        existing = await session.get(AgentDelivery, identity)
        if existing:
            return existing.state == "queued"
        await self._enqueue(session, reviewer, identity=identity, sender_id=None, kind="task_review",
            task=parent_task, chain_id=chain_id,
            content=self._review_instructions(child),
            payload={"task_id": child.id, "attempt": child.current_attempt, "revision": child.revision,
                     "status": child.status, "task_ref": task_ref(child), "review_reminder": True})
        return True

    async def _task_notice(self, session, task: TeamTask, *, chain_id: str | None = None) -> list[str]:
        owner = await self._instance(session, task.assigned_instance_id)
        parent_task = None
        if task.assigning_run_id:
            assigner = await session.get(AgentRun, task.assigning_run_id)
            reviewer = await self._instance(session, assigner.instance_id)
            if reviewer.state == "closed":
                return []
            if assigner.task_id:
                parent_task = await session.get(TeamTask, assigner.task_id)
                if (not parent_task or parent_task.current_attempt != assigner.attempt
                        or parent_task.assigned_instance_id != reviewer.id or parent_task.status != "running"):
                    return []
            leads = [reviewer]
        else:
            leads = (await session.execute(select(AgentInstance).where(AgentInstance.team_id == task.team_id,
                AgentInstance.job_id == owner.job_id, AgentInstance.generation == owner.generation,
                AgentInstance.role == "lead", AgentInstance.state != "closed"))).scalars().all()
        wake = []
        for lead in leads:
            await self._enqueue(session, lead, identity=_id("delivery_", task.id, task.current_attempt, task.revision, task.status),
                sender_id=task.assigned_instance_id, kind="task_review", task=parent_task,
                content=(self._review_instructions(task) if task.assigning_run_id else
                         f"Task {task.id} is {task.status}. Inspect its result and choose a review action."),
                payload={"task_id": task.id, "attempt": task.current_attempt, "revision": task.revision,
                         "status": task.status, "task_ref": task_ref(task)}, chain_id=chain_id)
            wake.append(lead.id)
        return wake

    async def _children_of_attempt(self, session, task):
        query = select(TeamTask).join(AgentRun, TeamTask.assigning_run_id == AgentRun.id).where(
            AgentRun.instance_id == task.assigned_instance_id, AgentRun.task_id == task.id,
            AgentRun.attempt == task.current_attempt)
        return (await session.execute(query)).scalars().all()

    async def _open_child_ids(self, session, task: TeamTask | None) -> list[str]:
        if task is None:
            return []
        children = await self._children_of_attempt(session, task)
        open_ids = sorted(child.id for child in children if child.status not in {"accepted", "cancelled"})
        if len(open_ids) > MAX_WAITING_TASKS:
            raise InstanceError("too_many_waiting_tasks", "This task has more open child assignments than one turn can track.")
        return open_ids

    @staticmethod
    def _owner_decision_matches(record: dict, decision_id: str, instance: AgentInstance,
                                job: EmployeeJob, task: TeamTask | None,
                                statuses: set[str]) -> bool:
        task_id, attempt = (task.id, task.current_attempt) if task else (None, None)
        return bool(
            isinstance(record, dict)
            and record.get("decision_id") == decision_id
            and record.get("status") in statuses
            and instance.role == "lead"
            and record.get("instance_id") == instance.id
            and record.get("job_id") == job.id
            and record.get("workflow_id") == job.workflow_id
            and record.get("team_id") == job.team_id
            and int(record.get("generation") or 0) == instance.generation
            and record.get("task_id") == task_id
            and record.get("attempt") == attempt
        )

    @staticmethod
    def _pending_owner_decision_ids(job: EmployeeJob, actor: AgentInstance,
                                    task: TeamTask | None) -> list[str]:
        """Keep other owner decisions for this Chat task attempt in the wait set.

        Chat may receive several blocking questions from the same outer
        Orchestrator attempt. Answering one must not strand the others on an
        older, already parked Chat run.
        """
        records = (job.delivery or {}).get("owner_decisions") or {}
        if not isinstance(records, dict):
            return []
        task_id, attempt = (task.id, task.current_attempt) if task else (None, None)
        return sorted(str(identity) for identity, record in records.items()
            if isinstance(record, dict) and record.get("status") == "pending"
            and record.get("instance_id") == actor.id and record.get("job_id") == job.id
            and record.get("workflow_id") == job.workflow_id and record.get("team_id") == job.team_id
            and int(record.get("generation") or 0) == actor.generation
            and record.get("task_id") == task_id and record.get("attempt") == attempt)

    async def _pending_source_decision_ids(self, session, actor: AgentInstance,
                                           task: TeamTask | None) -> list[str]:
        """Return this task attempt's unanswered requests to its assigner.

        A resumed turn may use a new AgentRun while the TeamTask attempt stays
        the same. The task attempt and source instance therefore own the
        decision; the original run remains evidence of where it was asked.
        """
        if task is None:
            return []
        rows = (await session.execute(select(AgentDelivery).where(
            AgentDelivery.team_id == actor.team_id,
            AgentDelivery.generation == actor.generation,
            AgentDelivery.kind == "decision_request"))).scalars().all()
        pending = []
        for row in rows:
            payload = row.payload or {}
            decision_id = payload.get("decision_id")
            if (not decision_id
                    or payload.get("decision_state") not in {"pending", "escalated"}
                    or payload.get("source_instance_id") != actor.id
                    or payload.get("source_task_id") != task.id
                    or payload.get("source_attempt") != task.current_attempt):
                continue
            source_run = await session.get(AgentRun, str(payload.get("source_run_id") or ""))
            if (source_run is None or source_run.instance_id != actor.id
                    or source_run.task_id != task.id or source_run.attempt != task.current_attempt
                    or source_run.status not in {"running", "parked", "completed"}):
                continue
            if source_run.status == "running" and actor.active_run_id != source_run.id:
                continue
            pending.append(str(decision_id))
        return sorted(set(pending))

    async def _waiting_after_task_change(self, session, scope: dict, task: TeamTask) -> list[str]:
        run = await session.get(AgentRun, str(scope.get("agent_run_id") or ""))
        parent_task = await session.get(TeamTask, run.task_id) if run and run.task_id else None
        if parent_task:
            return await self._open_child_ids(session, parent_task)
        actor = await session.get(AgentInstance, str(scope.get("agent_instance_id") or ""))
        if (actor and actor.role == "lead" and task.parent_agent_id == actor.template_node_id
                and task.team_id == actor.team_id and task.status not in {"accepted", "cancelled"}):
            return [task.id]
        return []

    async def _cancel_branch(self, session, parent, stops):
        """Fence all still-open work induced by the invalidated parent attempt."""
        for task in await self._children_of_attempt(session, parent):
            if task.status in {"accepted", "cancelled"}:
                continue
            await self._cancel_branch(session, task, stops)
            task.status, task.cancellation_requested = "cancelled", True
            task.cancellation_reason, task.completed_at = "parent_attempt_closed", now()
            task.revision += 1
            instance = await self._instance(session, task.assigned_instance_id)
            if instance.active_run_id:
                active = await session.get(AgentRun, instance.active_run_id)
                if active and active.task_id == task.id and active.attempt == task.current_attempt:
                    active.status, active.error = "draining", "parent_attempt_closed"
                    instance.fence += 1
                    stops.append(instance.id)
            attempt = await session.get(TeamTaskAttempt, f"{task.id}:attempt:{task.current_attempt}")
            if attempt:
                attempt.status, attempt.error, attempt.completed_at = "cancelled", task.cancellation_reason, task.completed_at
            await session.execute(update(AgentDelivery).where(AgentDelivery.task_id == task.id,
                AgentDelivery.attempt == task.current_attempt, AgentDelivery.state.in_(["queued", "held"]))
                .values(state="cancelled", hold_reason="parent_attempt_closed"))

    @staticmethod
    def _task_outcome(task, instance, reviewer_id):
        runtime = instance.config.get("runtime_context") or {}
        return {"task_id": task.id, "attempt": task.current_attempt, "revision": task.revision, "status": task.status,
                "workflow_id": instance.workflow_id, "generation": instance.generation, "team_id": instance.team_id,
                "job_id": instance.job_id, "controller_workflow_id": runtime.get("controller_workflow_id"),
                "execution_id": task.execution_id, "root_execution_id": task.root_execution_id,
                "agent_instance_id": instance.id, "agent_node_id": instance.template_node_id, "agent_name": instance.display_name,
                "parent_node_id": task.parent_agent_id, "assigning_run_id": task.assigning_run_id,
                "reviewer_instance_id": reviewer_id, "result": _result_preview(task.result, task.id), "error": task.error}

    async def _record_notice(self, session, instance: AgentInstance, reason: str, identity: str) -> None:
        """Visible outside the mailbox, so a halted lead can still report it."""
        event_id = _id("instance_notice_", instance.id, reason, identity)
        prior = (await session.execute(select(AgentMessage.id).where(AgentMessage.event_id == event_id))).first()
        if prior is None:
            session.add(AgentMessage(event_id=event_id, team_id=instance.team_id, from_agent=instance.id,
                message_type="agent_notice", content=f"{instance.display_name}: {reason}. Explicit recovery is required.",
                extra_data={"agent_instance_id": instance.id, "job_id": instance.job_id,
                            "generation": instance.generation, "reason": reason}))

    async def _refresh_member(self, session, instance: AgentInstance) -> None:
        active = (await session.execute(select(AgentInstance.id).where(
            AgentInstance.team_id == instance.team_id, AgentInstance.template_node_id == instance.template_node_id,
            AgentInstance.active_run_id.is_not(None)).limit(1))).first()
        members = (await session.execute(select(TeamMember).where(TeamMember.team_id == instance.team_id,
            TeamMember.agent_node_id == instance.template_node_id))).scalars().all()
        for member in members:
            if member.status != "offline":
                member.status = "working" if active else "idle"

    @staticmethod
    def _vnext_plan(job: EmployeeJob | None) -> dict:
        plan = (job.source.get("team_plan") or {}) if job else {}
        if plan.get("version") != 3 or plan.get("delegation_runtime_version") != 1:
            raise InstanceError("invalid_operation", "Decision operations require the admitted event-driven team recipe.")
        return plan

    async def _route_to_assigner(self, session, job, plan, task: TeamTask) -> tuple[AgentInstance, AgentRun, TeamTask | None]:
        """Resolve the exact immediate assigner captured by this task."""
        generation = int(job.source.get("generation") or 0)
        parent_task = None
        if task.assigning_run_id:
            parent_run = await session.get(AgentRun, task.assigning_run_id)
            if parent_run is None:
                raise InstanceError("decision_route_missing")
            parent = await self._instance(session, parent_run.instance_id, {
                "workflow_id": task.workflow_id or job.workflow_id, "generation": generation,
                "team_id": job.team_id, "job_id": job.id})
            # The fixed Chat -> Orchestrator assignment is created by a
            # taskless admission turn that completes immediately. Later Chat
            # reviews use a new taskless run, so route a blocking question to
            # that active or parked review run while retaining the original
            # assigning run as the durable relationship.
            if (parent.role == "lead" and task.parent_agent_id == job.lead_node_id
                    and task.assigned_to == plan.get("orchestrator_node_id")
                    and task.assigned_instance_id):
                current = await session.get(AgentRun, parent.active_run_id) if parent.active_run_id else None
                if current and current.instance_id == parent.id and current.task_id is None and current.status == "running":
                    parent_run = current
                elif parent.active_run_id is None:
                    parked_runs = (await session.execute(select(AgentRun).where(
                        AgentRun.instance_id == parent.id, AgentRun.status == "parked", AgentRun.task_id.is_(None))
                        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()))).scalars().all()
                    waiting_run = next((candidate for candidate in parked_runs
                        if task.id in self._waiting_ids(candidate)), None)
                    if waiting_run is not None:
                        parent_run = waiting_run
        else:
            # The fixed intake assignment is created while Chat completes its
            # taskless admission turn; older admitted rows may not record that
            # run on the TeamTask. Resolve only the one exact outer assignment
            # and the matching lead instance/run from the same job.
            assignee = await session.get(AgentInstance, task.assigned_instance_id) if task.assigned_instance_id else None
            if (task.parent_agent_id != job.lead_node_id or task.team_id != job.team_id
                    or task.assigned_to != plan.get("orchestrator_node_id")
                    or assignee is None or assignee.job_id != job.id or assignee.generation != generation
                    or assignee.template_node_id != plan.get("orchestrator_node_id")):
                raise InstanceError("decision_route_missing", "This task has no recorded immediate assigning run.")
            leads = (await session.execute(select(AgentInstance).where(
                AgentInstance.job_id == job.id, AgentInstance.team_id == job.team_id,
                AgentInstance.generation == generation, AgentInstance.role == "lead",
                AgentInstance.template_node_id == job.lead_node_id))).scalars().all()
            if len(leads) != 1:
                raise InstanceError("decision_route_missing", "The admitted Chat lead is missing or ambiguous.")
            parent = leads[0]
            parent_run = await session.get(AgentRun, parent.active_run_id) if parent.active_run_id else None
            if parent_run is None:
                parked_runs = (await session.execute(select(AgentRun).where(
                    AgentRun.instance_id == parent.id, AgentRun.status == "parked", AgentRun.task_id.is_(None))
                    .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()))).scalars().all()
                parent_run = next((candidate for candidate in parked_runs
                    if task.id in self._waiting_ids(candidate)), None)
            if parent_run is None:
                completed_runs = (await session.execute(select(AgentRun).where(
                    AgentRun.instance_id == parent.id, AgentRun.status == "completed", AgentRun.task_id.is_(None))
                    .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(20))).scalars().all()
                parent_run = next((candidate for candidate in completed_runs
                    if (candidate.result or {}).get("task_id") == task.id
                    and (candidate.result or {}).get("status") == "queued"), None)
            if parent_run is None:
                raise InstanceError("decision_route_missing", "The taskless Chat lead has no matching admitted run.")
        if parent.template_node_id != task.parent_agent_id or parent.team_id != task.team_id:
            raise InstanceError("decision_route_missing", "The assigning run no longer matches this task's manager.")
        if parent_run.task_id:
            parent_task = await session.get(TeamTask, parent_run.task_id)
            if (parent_run.status not in {"running", "parked", "completed"} or parent_run.attempt is None
                    or parent_task is None or parent_task.current_attempt != parent_run.attempt
                    or parent_task.assigned_instance_id != parent.id or parent_task.status != "running"):
                raise InstanceError("stale_decision", "The immediate manager's task attempt is no longer active.")
            if parent_task.assigned_run_id != parent_run.id:
                current = await session.get(AgentRun, parent_task.assigned_run_id) if parent_task.assigned_run_id else None
                if (current is None or current.instance_id != parent.id
                        or current.task_id != parent_task.id or current.attempt != parent_task.current_attempt):
                    raise InstanceError("stale_decision", "The immediate manager has moved to another task attempt.")
                if current.status == "running" and parent.active_run_id == current.id:
                    parent_run = current
                elif (current.status == "parked" and parent.active_run_id is None
                        and task.id in self._waiting_ids(current)):
                    parent_run = current
                elif current.status not in {"running", "parked"}:
                    raise InstanceError("stale_decision", "The immediate manager has moved to another task attempt.")
            if parent_run.status == "running" and parent.active_run_id != parent_run.id:
                raise InstanceError("stale_decision", "The immediate manager's active turn has changed.")
            if parent_run.status == "parked" and (parent.active_run_id is not None
                    or task.id not in self._waiting_ids(parent_run)):
                raise InstanceError("stale_decision", "The immediate manager is no longer waiting on this child attempt.")
        else:
            # The fixed Chat -> Orchestrator intake run is completed once it
            # creates the outer task. It remains the authoritative parent for
            # a blocking question from that outer task.
            outer = (parent.role == "lead" and task.parent_agent_id == job.lead_node_id
                     and task.assigned_to == plan.get("orchestrator_node_id")
                     and task.assigned_instance_id is not None
                     and (await self._instance(session, task.assigned_instance_id)).template_node_id == plan.get("orchestrator_node_id"))
            if not outer or parent_run.status not in {"running", "parked", "completed"}:
                raise InstanceError("stale_decision", "The taskless parent is not the admitted Chat lead for this outer assignment.")
        return parent, parent_run, parent_task

    async def _decision_request_for(self, session, actor: AgentInstance, decision_id: str) -> AgentDelivery:
        rows = (await session.execute(select(AgentDelivery).where(
            AgentDelivery.recipient_id == actor.id, AgentDelivery.kind == "decision_request")
            .order_by(AgentDelivery.created_at.desc(), AgentDelivery.sequence.desc()))).scalars().all()
        row = next((item for item in rows if (item.payload or {}).get("decision_id") == decision_id), None)
        if row is None or row.state not in {"admitted", "consumed"} or not row.run_id:
            raise InstanceError("decision_not_admitted", "Use the decision ID from a request delivered to this manager.")
        admitted_run = await session.get(AgentRun, row.run_id)
        payload = row.payload or {}
        if (admitted_run is None or admitted_run.instance_id != actor.id
                or (admitted_run.task_id, admitted_run.attempt) !=
                   (payload.get("response_task_id"), payload.get("response_attempt"))):
            raise InstanceError("stale_decision", "The decision request belongs to a different manager task attempt.")
        return row

    async def _validate_decision_turn(self, session, actor: AgentInstance, run: AgentRun,
                                      request: AgentDelivery) -> tuple[TeamTask | None, dict]:
        payload = request.payload or {}
        response_task_id, response_attempt = payload.get("response_task_id"), payload.get("response_attempt")
        if (payload.get("response_instance_id") != actor.id
                or (run.task_id, run.attempt) != (response_task_id, response_attempt)):
            raise InstanceError("stale_decision", "This decision is not addressed to the current task attempt.")
        task = None
        if response_task_id:
            task = await session.get(TeamTask, response_task_id)
            if (task is None or task.current_attempt != response_attempt or task.assigned_instance_id != actor.id
                    or task.assigned_run_id != run.id or task.status != "running"):
                raise InstanceError("stale_decision", "The manager's task attempt is no longer active.")
        elif actor.role != "lead" or run.task_id is not None:
            raise InstanceError("stale_decision", "Only the taskless Chat lead can resolve an owner decision.")
        state = payload.get("decision_state", "pending")
        if state == "answered":
            raise InstanceError("decision_already_answered")
        if state == "escalated" and not payload.get("upstream_answer_delivery_id"):
            raise InstanceError("decision_already_escalated", "This decision is waiting for its parent or the owner.")
        return task, payload

    async def _decision_source(self, session, scope, payload: dict) -> tuple[AgentInstance, TeamTask, AgentRun]:
        source_id, task_id = payload.get("source_instance_id"), payload.get("source_task_id")
        source = await self._instance(session, str(source_id or ""), scope)
        task = await session.get(TeamTask, str(task_id or ""))
        if (task is None or task.team_id != source.team_id or task.current_attempt != payload.get("source_attempt")
                or task.assigned_instance_id != source.id or task.status != "running"):
            raise InstanceError("stale_decision", "The requesting task attempt has ended or changed.")
        source_run = await session.get(AgentRun, str(payload.get("source_run_id") or ""))
        if (source_run is None or source_run.instance_id != source.id or source_run.task_id != task.id
                or source_run.attempt != task.current_attempt
                or source_run.status not in {"running", "parked", "completed"}):
            raise InstanceError("stale_decision", "The requesting execution is no longer available.")
        if source_run.status == "running" and source.active_run_id != source_run.id:
            raise InstanceError("stale_decision", "The requesting execution has been fenced.")
        if source_run.status in {"parked", "completed"} and source.active_run_id is not None:
            # A same-attempt resumed turn can already be draining inbox work.
            active = await session.get(AgentRun, source.active_run_id)
            if not active or active.task_id != task.id or active.attempt != task.current_attempt:
                raise InstanceError("stale_decision", "The requesting execution has moved to another task attempt.")
        return source, task, source_run

    async def _request_decision(self, session, scope, actor, run, job, plan, args, key) -> dict:
        node_type = (actor.config.get("template") or {}).get("node_type")
        if node_type in {"claude_code_agent", "codex_agent", "rlm_agent", "gemini_cli_agent"}:
            raise InstanceError("unsupported_decision", "This worker cannot checkpoint for a mid-turn decision.")
        task = await session.get(TeamTask, run.task_id) if run.task_id else None
        if (task is None or task.current_attempt != run.attempt or task.assigned_instance_id != actor.id
                or task.assigned_run_id != run.id or task.status != "running"):
            raise InstanceError("decision_requires_task", "A blocking decision must belong to the current running task attempt.")
        parent, parent_run, parent_task = await self._route_to_assigner(session, job, plan, task)
        decision_id = _id("decision_", actor.id, task.id, task.current_attempt, run.id, key)
        identity = _id("delivery_", decision_id, "request", parent.id)
        question, context = str(args["question"]).strip(), str(args.get("context") or "").strip()
        if not question:
            raise InstanceError("invalid_tool_input", "The decision question must not be blank.")
        content = f"Blocking decision {decision_id}\nQuestion: {question}"
        if context:
            content += f"\nContext: {context}"
        payload = {
            "decision_id": decision_id, "decision_state": "pending", "question": question, "context": context,
            "source_instance_id": actor.id, "source_task_id": task.id,
            "source_attempt": task.current_attempt, "source_run_id": run.id,
            "response_instance_id": parent.id, "response_run_id": parent_run.id,
            "response_task_id": parent_task.id if parent_task else None,
            "response_attempt": parent_task.current_attempt if parent_task else None,
            "response_delivery_id": None,
        }
        delivery = await self._enqueue(session, parent, identity=identity, sender_id=actor.id,
            kind="decision_request", content=content, payload=payload, task=parent_task)
        waiting = await self._open_child_ids(session, task)
        return {"success": True, "status": "queued", "decision_id": decision_id,
                "delivery_id": delivery.id, "instance_wake": [parent.id],
                "waiting_task_ids": waiting[:MAX_WAITING_TASKS], "decision_ids": [decision_id]}

    async def _answer_decision(self, session, scope, actor, run, job, plan, args) -> dict:
        decision_id = str(args["decision_id"])
        request = await self._decision_request_for(session, actor, decision_id)
        current_task, request_payload = await self._validate_decision_turn(session, actor, run, request)
        source, source_task, source_run = await self._decision_source(session, scope, request_payload)

        owner_decision = (job.delivery.get("owner_decisions") or {}).get(decision_id)
        if actor.role == "lead" and owner_decision and owner_decision.get("status") == "answered":
            if (owner_decision.get("instance_id") != actor.id or owner_decision.get("job_id") != job.id
                    or owner_decision.get("task_id") != (current_task.id if current_task else None)
                    or owner_decision.get("attempt") != (current_task.current_attempt if current_task else None)):
                raise InstanceError("stale_decision", "The owner answer belongs to another Chat task attempt.")
        answer = str(args["answer"]).strip()
        if not answer:
            raise InstanceError("invalid_tool_input", "The decision answer must not be blank.")
        answer_identity = _id("delivery_", decision_id, "answer", request.id)
        answer_payload = {
            "decision_id": decision_id, "response_delivery_id": request.id,
            "source_instance_id": actor.id, "source_task_id": current_task.id if current_task else None,
            "source_attempt": current_task.current_attempt if current_task else None, "source_run_id": run.id,
            "response_instance_id": source.id, "response_task_id": source_task.id,
            "response_attempt": source_task.current_attempt, "response_run_id": source_run.id,
            "answer": answer, "owner_answer": bool(owner_decision and owner_decision.get("status") == "answered"),
        }
        content = f"Decision answer {decision_id}\n{answer}"
        delivery = await self._enqueue(session, source, identity=answer_identity, sender_id=actor.id,
            kind="decision_answer", content=content, payload=answer_payload, task=source_task)
        request.payload = {**request_payload, "decision_state": "answered", "answer_delivery_id": delivery.id,
                           "answer": answer, "answered_by_instance_id": actor.id, "answered_by_run_id": run.id}
        if request_payload.get("response_delivery_id"):
            upstream = await session.get(AgentDelivery, request_payload["response_delivery_id"])
            if (upstream is None or upstream.kind != "decision_request" or upstream.recipient_id != source.id
                    or (upstream.payload or {}).get("decision_id") != decision_id
                    or (upstream.payload or {}).get("decision_state") != "escalated"):
                raise InstanceError("stale_decision", "The escalated request no longer matches its original manager turn.")
            upstream.payload = {**(upstream.payload or {}), "decision_state": "pending",
                "upstream_answer_delivery_id": delivery.id, "upstream_answer": answer,
                "upstream_answered_by_instance_id": actor.id}
        waiting = await self._open_child_ids(session, current_task) if current_task else [source_task.id]
        pending_owner_decisions = self._pending_owner_decision_ids(job, actor, current_task)
        return {"success": True, "status": "answered", "decision_id": decision_id,
                "delivery_id": delivery.id, "instance_wake": [source.id],
                "waiting_task_ids": waiting[:MAX_WAITING_TASKS],
                "decision_ids": pending_owner_decisions[:MAX_OWNER_DECISIONS]}

    async def _escalate_decision(self, session, scope, actor, run, job, plan, args) -> dict:
        decision_id = str(args["decision_id"])
        request = await self._decision_request_for(session, actor, decision_id)
        current_task, request_payload = await self._validate_decision_turn(session, actor, run, request)
        note = str(args.get("note") or "").strip()
        question = str(request_payload.get("question") or request.content)
        context = str(request_payload.get("context") or "").strip()
        if note:
            addition = f"Additional context from {actor.display_name}: {note}"
            context = f"{context}\n{addition}".strip()
        context = context[:10000]

        if actor.role == "lead":
            records = dict((job.delivery or {}).get("owner_decisions") or {})
            existing = records.get(decision_id)
            if existing:
                if (existing.get("instance_id") != actor.id or existing.get("run_id") != run.id
                        or existing.get("request_delivery_id") != request.id):
                    raise InstanceError("decision_already_escalated")
                return {"success": True, "status": existing.get("status", "pending"),
                        "decision_id": decision_id, "owner_decision": deepcopy(existing),
                        "instance_wake": [], "decision_ids": [decision_id],
                        "waiting_task_ids": (await self._open_child_ids(session, current_task)
                                             if current_task else [str(request_payload.get("source_task_id"))])}
            records = {identity: value for identity, value in records.items()
                       if value.get("status") == "pending"}
            if len(records) >= MAX_OWNER_DECISIONS:
                raise InstanceError("owner_decisions_full", "Resolve an existing owner decision before escalating another.")
            entry = {
                "decision_id": decision_id, "status": "pending", "job_id": job.id,
                "workflow_id": job.workflow_id, "team_id": job.team_id,
                "generation": int(job.source.get("generation") or 0),
                "instance_id": actor.id, "run_id": run.id,
                "task_id": current_task.id if current_task else None,
                "attempt": current_task.current_attempt if current_task else None,
                "request_delivery_id": request.id, "question": question, "context": context,
                "requested_by": {key: request_payload.get(key) for key in (
                    "source_instance_id", "source_task_id", "source_attempt", "source_run_id")},
                "created_at": now().isoformat(),
            }
            records[decision_id] = entry
            job.delivery = {**(job.delivery or {}), "owner_decisions": records}
            request.payload = {**request_payload, "decision_state": "escalated", "owner_decision_id": decision_id}
            waiting = await self._open_child_ids(session, current_task) if current_task else [str(request_payload.get("source_task_id"))]
            return {"success": True, "status": "pending_owner", "decision_id": decision_id,
                    "owner_decision": deepcopy(entry), "instance_wake": [],
                    "waiting_task_ids": waiting[:MAX_WAITING_TASKS], "decision_ids": [decision_id]}

        if current_task is None or current_task.assigned_run_id != run.id or current_task.status != "running":
            raise InstanceError("decision_requires_task", "Only a manager working on an assigned task can escalate to its parent.")
        parent, parent_run, parent_task = await self._route_to_assigner(session, job, plan, current_task)
        identity = _id("delivery_", decision_id, "escalation", request.id, parent.id)
        response_delivery_id = request.id
        payload = {
            "decision_id": decision_id, "decision_state": "pending", "question": question,
            "context": context,
            "source_instance_id": actor.id, "source_task_id": current_task.id,
            "source_attempt": current_task.current_attempt, "source_run_id": run.id,
            "response_instance_id": parent.id, "response_run_id": parent_run.id,
            "response_task_id": parent_task.id if parent_task else None,
            "response_attempt": parent_task.current_attempt if parent_task else None,
            "response_delivery_id": response_delivery_id,
            "escalated_from_delivery_id": request.id,
        }
        delivery = await self._enqueue(session, parent, identity=identity, sender_id=actor.id,
            kind="decision_request", content=f"Blocking decision {decision_id} escalated\nQuestion: {question}"
                + (f"\nContext: {context}" if context else ""),
            payload=payload, task=parent_task)
        request.payload = {**request_payload, "decision_state": "escalated",
                           "escalation_delivery_id": delivery.id, "escalated_by_run_id": run.id}
        waiting = await self._open_child_ids(session, current_task)
        return {"success": True, "status": "escalated", "decision_id": decision_id,
                "delivery_id": delivery.id, "instance_wake": [parent.id],
                "waiting_task_ids": waiting, "decision_ids": [decision_id]}

    async def execute(self, operation: str, *, scope: dict, arguments: dict, request_id: str,
                      template: dict | None = None) -> dict:
        scope = self._scope(scope)
        if not request_id:
            raise InstanceError("invalid_request_identity")
        key = _id("mutation_", scope["team_id"], scope["job_id"], scope.get("agent_instance_id"), request_id)
        fingerprint = _hash([operation, arguments, template])
        reads = {"list_agents", "get_agent", "wait_for_agents", "list_tasks", "get_task", "inspect_task"}
        async with self.database.reserved_session() as session:
            await self._authorize(session, scope,
                lead=operation not in reads | {"send_message", "request_decision"}, operation=operation)
            prior = await self._receipt(session, key, fingerprint) if operation not in reads else None
            if prior is not None:
                return prior
            scope.pop("_chain_id", None)
            if scope.get("agent_instance_id") and operation not in reads:
                actor = await self._instance(session, scope["agent_instance_id"], scope)
                if actor.state == "halted":
                    raise InstanceError("agent_halted", "This agent requires explicit recovery before admitting more work.")
                if "_report_ids" in scope and not scope.get("agent_run_id"):
                    raise InstanceError("stale_run")
                if scope.get("agent_run_id"):
                    run = await session.get(AgentRun, str(scope["agent_run_id"]))
                    if (run is None or run.instance_id != actor.id or actor.active_run_id != run.id
                            or run.status != "running" or run.fence != actor.fence):
                        raise InstanceError("stale_run", "This execution no longer owns the agent.")
                    scope["_chain_id"] = run.chain_id
                    if "_report_ids" in scope and actor.role != "lead" and operation in MANAGER_OPERATIONS and not run.task_id:
                        raise InstanceError("invalid_target", "A manager can assign or review work only in its active task.")
            result: dict = {"success": True}
            if operation in {"spawn", "assign_task"}:
                if operation == "spawn":
                    target = await self._template(session, scope, template or {})
                    instance = await self._new_instance(session, scope, target, _id("agent_", key))
                else:
                    instance = await self._instance(session, str(arguments.get("agent") or ""), scope)
                task = await self._new_task(session, scope, instance, arguments, key)
                result.update(agent=_public_instance(instance), task=_task_dict(task), instance_wake=[instance.id],
                              waiting_task_ids=[task.id])
            elif operation == "send_message":
                sender = await self._instance(session, str(scope.get("agent_instance_id") or ""), scope)
                if sender.state in {"halted", "closed"}:
                    raise InstanceError("agent_unavailable")
                recipient = await self._instance(session, str(arguments.get("agent") or ""), scope)
                if sender.id == recipient.id:
                    raise InstanceError("invalid_target", "Send messages to another agent.")
                content = str(arguments.get("message") or "")
                if not content.strip():
                    raise InstanceError("invalid_tool_input", "message must not be blank.")
                run = await session.get(AgentRun, sender.active_run_id) if sender.active_run_id else None
                chain_id = run.chain_id if run and run.chain_id else _id("chain_", sender.id, run.id if run else key)
                if run and not run.chain_id:
                    run.chain_id = chain_id
                delivery = await self._enqueue(session, recipient, identity=_id("delivery_", key), sender_id=sender.id,
                                               kind="peer", content=content, chain_id=chain_id)
                parked = await self._parked_wait(session, recipient)
                result.update(delivery=delivery.model_dump(mode="json"), status=delivery.state,
                              instance_wake=[] if parked else [recipient.id])
            elif operation in {"request_decision", "answer_decision", "escalate_decision"}:
                job = await session.get(EmployeeJob, scope["job_id"])
                plan = self._vnext_plan(job)
                actor = await self._instance(session, scope.get("agent_instance_id"), scope)
                run = await session.get(AgentRun, str(scope.get("agent_run_id") or ""))
                if run is None or run.instance_id != actor.id or actor.active_run_id != run.id or run.status != "running":
                    raise InstanceError("stale_run")
                if operation == "request_decision":
                    result.update(await self._request_decision(session, scope, actor, run, job, plan, arguments, key))
                elif operation == "answer_decision":
                    result.update(await self._answer_decision(session, scope, actor, run, job, plan, arguments))
                else:
                    result.update(await self._escalate_decision(session, scope, actor, run, job, plan, arguments))
            elif operation in {"list_agents", "wait_for_agents"}:
                agents = (await session.execute(select(AgentInstance).where(AgentInstance.team_id == scope["team_id"],
                    AgentInstance.job_id == scope["job_id"], AgentInstance.generation == scope["generation"]).order_by(AgentInstance.created_at, AgentInstance.id))).scalars().all()
                selected = set(arguments.get("agents") or [])
                if operation == "wait_for_agents":
                    known = {item.id for item in agents}
                    if selected - known or scope.get("agent_instance_id") in selected:
                        raise InstanceError("invalid_target", "Wait for other agent IDs returned by list_agents.")
                    if not selected:
                        agents = [item for item in agents if item.id != scope.get("agent_instance_id")]
                result["agents"] = [_public_instance(item) for item in agents if not selected or item.id in selected]
                result["current_agent"] = scope.get("agent_instance_id")
                if operation == "wait_for_agents":
                    selected_ids = [item.id for item in agents if not selected or item.id in selected]
                    pending = (await session.execute(select(AgentDelivery.id).where(
                        AgentDelivery.recipient_id.in_(selected_ids), AgentDelivery.state.in_(["queued", "admitted", "held"]))
                        .limit(1))).first() if selected_ids else None
                    result["status"] = "pending" if pending or any(item.active_run_id for item in agents if item.id in selected_ids) else "idle"
            elif operation == "get_agent":
                instance = await self._instance(session, str(arguments.get("agent") or ""), scope)
                result["agent"] = _public_instance(instance)
                runs = (await session.execute(select(AgentRun).where(AgentRun.instance_id == instance.id)
                    .order_by(AgentRun.created_at.desc()).limit(10))).scalars().all()
                result["runs"] = [_public_run(run) for run in runs]
            elif operation == "list_tasks":
                tasks = (await session.execute(select(TeamTask).join(AgentInstance, TeamTask.assigned_instance_id == AgentInstance.id)
                    .where(TeamTask.team_id == scope["team_id"], TeamTask.protocol_version == 1,
                    AgentInstance.job_id == scope["job_id"], AgentInstance.generation == scope["generation"])
                    .order_by(TeamTask.created_at, TeamTask.id))).scalars().all()
                status = arguments.get("status") or arguments.get("status_filter")
                if "_report_ids" in scope:
                    own_runs = select(AgentRun.id).where(AgentRun.instance_id == scope["agent_instance_id"])
                    own_run_ids = set((await session.execute(own_runs)).scalars())
                    tasks = [task for task in tasks if task.assigned_instance_id == scope["agent_instance_id"]
                             or task.assigning_run_id in own_run_ids]
                result["tasks"] = [_task_dict(task) for task in tasks if not status or task.status == status]
            elif operation in {"get_task", "inspect_task"}:
                task = await self._read_task(session, scope, str(arguments.get("task_ref") or arguments.get("task") or arguments.get("task_id") or ""))
                attempts = (await session.execute(select(TeamTaskAttempt).where(TeamTaskAttempt.task_id == task.id)
                    .order_by(TeamTaskAttempt.attempt_number))).scalars().all()
                result.update(task=_task_dict(task), attempts=[_public_attempt(attempt) for attempt in attempts])
                if operation == "inspect_task":
                    result["result_excerpt"] = _inspect_results(task, attempts, arguments)
            elif operation in {"stop_agent", "resume_agent", "close_agent"}:
                instance = await self._instance(session, str(arguments.get("agent") or ""), scope)
                result.update(await self._control(session, instance, operation))
            elif operation == "finish_team":
                tasks = (await session.execute(select(TeamTask).join(AgentInstance, TeamTask.assigned_instance_id == AgentInstance.id)
                    .where(TeamTask.team_id == scope["team_id"], AgentInstance.job_id == scope["job_id"],
                    AgentInstance.generation == scope["generation"]))).scalars().all()
                if "_report_ids" in scope:
                    outer = [task for task in tasks if task.parent_agent_id == scope["lead_node_id"]]
                    if len(outer) != 1 or not (outer[0].status == "accepted" or outer[0].reviewed_blocker):
                        raise InstanceError("invalid_transition", "Record a review decision on the Orchestrator's result before finishing.")
                if any(task.status not in {"accepted", "cancelled"} for task in tasks):
                    raise InstanceError("invalid_transition", "Review all outstanding tasks before finishing the team.")
                result["status"] = "reviewed"
            elif operation in {"modify_task", "request_changes", "accept_task", "retry_task", "cancel_task", "reassign"}:
                task = await self._read_task(session, scope, str(arguments.get("task_ref") or ""), mutate=True)
                result.update(await self._mutate_task(session, scope, task, operation, arguments, template, key))
            else:
                raise InstanceError("invalid_operation")
            return result if operation in reads else await self._commit(session, key, fingerprint, result)

    async def answer_owner_decision(self, workflow_id: str, job_id: str, decision_id: str, answer: str,
                                    *, request_id: str) -> dict:
        """Commit an owner answer to the exact pending Chat instance turn.

        The authenticated owner-facing handler calls this after validating the
        user. The job record remains the source of truth for which instance,
        task attempt, and decision the answer belongs to.
        """
        if not request_id or not decision_id:
            raise InstanceError("invalid_request_identity")
        answer = str(answer or "").strip()
        if not answer or len(answer) > 10000:
            raise InstanceError("invalid_tool_input", "The owner answer must contain between 1 and 10000 characters.")
        key = _id("mutation_", job_id, "owner_decision_answer", decision_id, request_id)
        fingerprint = _hash([workflow_id, decision_id, answer])
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            job = await session.get(EmployeeJob, job_id)
            if job is None or job.workflow_id != workflow_id or not job.team_id:
                raise InstanceError("job_not_found")
            if job.state in {"delivered", "completed", "cancelled", "failed"}:
                raise InstanceError("stale_decision", "The original employee job has already ended.")
            plan = self._vnext_plan(job)
            record = (job.delivery or {}).get("owner_decisions", {}).get(decision_id)
            if not record or record.get("decision_id") != decision_id or record.get("status") != "pending":
                raise InstanceError("decision_not_pending", "This owner decision is missing, stale, or already answered.")
            if (record.get("job_id") != job.id or record.get("workflow_id") != job.workflow_id
                    or record.get("team_id") != job.team_id
                    or int(record.get("generation") or 0) != int(job.source.get("generation") or 0)):
                raise InstanceError("stale_decision")
            instance = await session.get(AgentInstance, str(record.get("instance_id") or ""))
            if (instance is None or instance.role != "lead" or instance.job_id != job.id
                    or instance.team_id != job.team_id or instance.generation != int(record.get("generation") or 0)
                    or instance.template_node_id != plan.get("lead_node_id") or instance.state in {"halted", "closed"}):
                raise InstanceError("stale_decision", "The original Chat lead is no longer available.")
            run = await session.get(AgentRun, str(record.get("run_id") or ""))
            task = await session.get(TeamTask, str(record.get("task_id") or "")) if record.get("task_id") else None
            if (run is None or run.instance_id != instance.id
                    or (run.task_id, run.attempt) != (record.get("task_id"), record.get("attempt"))):
                raise InstanceError("stale_decision", "The original Chat turn no longer matches this decision.")
            if task:
                if (task.team_id != job.team_id or task.current_attempt != record.get("attempt")
                        or task.assigned_instance_id != instance.id or task.status != "running"):
                    raise InstanceError("stale_decision", "The Chat task attempt has ended or changed.")
            elif run.task_id is not None or record.get("attempt") is not None:
                raise InstanceError("stale_decision")
            if run.status == "parked":
                parked = run.result or {}
                if (instance.active_run_id is not None or parked.get("reason") not in {"decision", "child_work"}
                        or decision_id not in (parked.get("decision_ids") or [])):
                    raise InstanceError("stale_decision", "The Chat lead is no longer waiting on this owner decision.")
                if task and task.assigned_run_id != run.id:
                    raise InstanceError("stale_decision")
            elif run.status == "running":
                if instance.active_run_id != run.id or (task and task.assigned_run_id != run.id):
                    raise InstanceError("stale_decision")
            else:
                raise InstanceError("stale_decision", "The original Chat turn has already ended.")
            request = await session.get(AgentDelivery, str(record.get("request_delivery_id") or ""))
            request_payload = (request.payload or {}) if request else {}
            if (request is None or request.kind != "decision_request" or request.recipient_id != instance.id
                    or request_payload.get("decision_id") != decision_id
                    or request_payload.get("decision_state") != "escalated"
                    or request_payload.get("response_instance_id") != instance.id
                    or (request_payload.get("response_task_id"), request_payload.get("response_attempt"))
                       != (record.get("task_id"), record.get("attempt"))):
                raise InstanceError("stale_decision", "The owner request no longer matches its original Chat turn.")
            identity = _id("delivery_", decision_id, "owner_answer", instance.id, request.id)
            payload = {"decision_id": decision_id, "response_delivery_id": request.id,
                       "response_instance_id": instance.id, "response_task_id": task.id if task else None,
                       "response_attempt": task.current_attempt if task else None,
                       "owner_answer": True, "answer": answer}
            delivery = await self._enqueue(session, instance, identity=identity, sender_id=None,
                kind="decision_answer", content=f"Owner answer {decision_id}\n{answer}",
                payload=payload, task=task)
            request.payload = {**request_payload, "decision_state": "pending",
                "upstream_answer_delivery_id": delivery.id, "upstream_answer": answer,
                "upstream_answered_by_owner": True}
            records = dict((job.delivery or {}).get("owner_decisions") or {})
            records[decision_id] = {**record, "status": "answered", "answer": answer,
                "answer_delivery_id": delivery.id, "answered_at": now().isoformat(), "answer_request_id": request_id}
            job.delivery = {**(job.delivery or {}), "owner_decisions": records}
            result = {"success": True, "status": "answered", "job_id": job.id,
                "decision_id": decision_id, "delivery_id": delivery.id,
                "instance_wake": [instance.id],
                "waiting_task_ids": [task.id] if task else [], "decision_ids": [decision_id]}
            return await self._commit(session, key, fingerprint, result)

    async def _mutate_task(self, session, scope, task, operation, args, template, key):
        if operation not in _task_actions(task):
            raise InstanceError("invalid_transition", f"{operation} is unavailable while task is {task.status}.")
        instance = await self._instance(session, task.assigned_instance_id, scope)
        wake, stops = [], []
        task.revision += 1
        if operation == "modify_task":
            if not any(key in args for key in ("instructions", "after")):
                raise InstanceError("invalid_tool_input", "Provide instructions or after to modify queued work.")
            if "instructions" in args:
                value = str(args["instructions"] or "")
                if not value.strip() or len(value) > 10000:
                    raise InstanceError("invalid_tool_input")
                task.mission, task.description, task.title = value, value[:5000], value.strip().splitlines()[0][:160]
                task.acceptance_criteria = {"instructions": value}
            deps, accepted = await self._dependencies(session, scope, list(args.get("after", _dependency_ids(task)) or []), task_id=task.id)
            task.depends_on, task.status = {"task_ids": deps}, "queued" if accepted else "blocked"
            pending = (await session.execute(select(AgentDelivery).where(AgentDelivery.task_id == task.id,
                AgentDelivery.attempt == task.current_attempt, AgentDelivery.state.in_(["queued", "held"])))).scalars().all()
            for delivery in pending:
                delivery.state, delivery.hold_reason = "cancelled", "contract_revised"
            await self._queue_task(session, task, instance, chain_id=scope.get("_chain_id"))
            attempt = await session.get(TeamTaskAttempt, f"{task.id}:attempt:{task.current_attempt}")
            if attempt:
                attempt.input_snapshot = {**(attempt.input_snapshot or {}), "instructions": task.mission,
                    "dependencies": deps, "revision": task.revision, "acceptance_criteria": task.acceptance_criteria}
                attempt.status = task.status
            wake.append(instance.id)
            waiting_task_ids = [task.id]
        elif operation == "accept_task":
            task.status = "accepted"
            if args.get("auto_confidence") is not None:
                # Accepted by a calibrated judgment, not a model turn
                # (auto_review.py). A model's tool input cannot carry this:
                # the review tools forbid extra fields (tools.Input).
                task.context = {**(task.context or {}), "auto_accepted": {
                    "attempt": task.current_attempt, "confidence": round(float(args["auto_confidence"]), 4)}}
            dependents = (await session.execute(select(TeamTask).where(TeamTask.team_id == task.team_id,
                TeamTask.status == "blocked"))).scalars().all()
            for dependent in dependents:
                if task.id not in _dependency_ids(dependent):
                    continue
                owner = await session.get(AgentInstance, dependent.assigned_instance_id) if dependent.assigned_instance_id else None
                if owner is None or owner.job_id != scope["job_id"] or owner.generation != scope["generation"]:
                    continue
                _, accepted = await self._dependencies(session, scope, _dependency_ids(dependent))
                if accepted:
                    dependent.status = "queued"
                    dependent.revision += 1
                    wake.append(dependent.assigned_instance_id)
            actor_run = await session.get(AgentRun, str(scope.get("agent_run_id") or ""))
            parent_task = await session.get(TeamTask, actor_run.task_id) if actor_run and actor_run.task_id else None
            waiting_task_ids = await self._open_child_ids(session, parent_task)
        elif operation == "cancel_task":
            if task.assigning_run_id and task.status in {"submitted", "failed"}:
                reason = str(args.get("reason") or "").strip()
                if not reason:
                    raise InstanceError("invalid_tool_input", "Give the reason this reviewed work cannot be completed.")
                task.context = {**(task.context or {}), "reviewed_blocker_attempt": task.current_attempt}
            if task.assigning_run_id:
                await self._cancel_branch(session, task, stops)
            task.status, task.cancellation_requested = "cancelled", True
            task.cancellation_reason = str(args.get("reason") or "Cancelled by lead").strip()[:2000]
            if instance.active_run_id:
                run = await session.get(AgentRun, instance.active_run_id)
                if run and run.task_id == task.id:
                    run.status = "draining"
                    run.error = "task_cancelled"
                    instance.fence += 1
                    stops.append(instance.id)
            pending = (await session.execute(select(AgentDelivery).where(AgentDelivery.task_id == task.id,
                AgentDelivery.attempt == task.current_attempt, AgentDelivery.state.in_(["queued", "held"])))).scalars().all()
            for delivery in pending:
                delivery.state = "cancelled"
            attempt = await session.get(TeamTaskAttempt, f"{task.id}:attempt:{task.current_attempt}")
            if attempt:
                attempt.status = "cancelled"
                attempt.error = attempt.error or task.cancellation_reason
                attempt.completed_at = attempt.completed_at or now()
            waiting_task_ids = []
        else:
            if instance.active_run_id:
                active = await session.get(AgentRun, instance.active_run_id)
                if instance.state == "halted" or (active and active.task_id == task.id and active.attempt == task.current_attempt):
                    raise InstanceError("agent_draining", "Wait for the interrupted execution to drain before retrying.")
            if task.current_attempt >= task.max_retries:
                raise InstanceError("retry_limit", "Task attempt budget exhausted; create explicitly revised work.")
            if task.assigning_run_id:
                await self._cancel_branch(session, task, stops)
            if operation == "request_changes":
                feedback = str(args.get("feedback") or "")
                if not feedback.strip() or len(feedback) > 10000:
                    raise InstanceError("invalid_tool_input", "feedback must contain between 1 and 10000 characters.")
                task.review_feedback = [*(task.review_feedback or []), feedback]
            if operation == "reassign":
                target = await self._template(session, scope, template or {})
                instance = await self._new_instance(session, scope, target, _id("agent_", key))
                task.assigned_instance_id, task.assigned_to = instance.id, instance.template_node_id
            if instance.state == "closed":
                raise InstanceError("recipient_closed")
            if not instance.active_run_id:
                instance.state, instance.halt_reason = "idle", None
            task.current_attempt += 1
            task.retry_count += 1
            task.result, task.error, task.usage = None, None, None
            task.cancellation_requested, task.cancellation_reason = False, None
            task.started_at, task.completed_at = None, None
            task.child_workflow_id = task.child_run_id = task.runner_workflow_id = task.runner_run_id = None
            task.assigned_run_id = None
            _, accepted = await self._dependencies(session, scope, _dependency_ids(task))
            task.status = "queued" if accepted else "blocked"
            await self._attempt(session, task, instance)
            await self._queue_task(session, task, instance, chain_id=scope.get("_chain_id"))
            wake.append(instance.id)
            waiting_task_ids = [task.id] if task.status in {"queued", "blocked"} else []
        if operation != "accept_task":
            waiting_task_ids = await self._waiting_after_task_change(session, scope, task)
        return {"task": _task_dict(task), "agent": _public_instance(instance),
                "instance_wake": sorted(set(filter(None, wake))), "instance_stop": stops,
                "waiting_task_ids": waiting_task_ids}

    async def _control(self, session, instance, operation):
        if operation == "stop_agent":
            instance.state, instance.halt_reason = "halted", "interrupted"
            instance.fence += 1
            if instance.active_run_id:
                run = await session.get(AgentRun, instance.active_run_id)
                if run:
                    run.status, run.error = "draining", "interrupted"
                    if run.task_id:
                        task = await session.get(TeamTask, run.task_id)
                        if task and not task.assigning_run_id and task.current_attempt == run.attempt and task.status == "running":
                            task.status, task.error = "failed", "interrupted"
                            task.revision += 1
                            await self._task_notice(session, task, chain_id=run.chain_id)
        elif operation == "resume_agent":
            if instance.state == "closed":
                raise InstanceError("recipient_closed")
            if instance.active_run_id:
                raise InstanceError("agent_draining")
            instance.state, instance.halt_reason = "idle", None
            held = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == instance.id,
                AgentDelivery.state == "held"))).scalars().all()
            for delivery in held:
                if delivery.hold_reason == "owner_review":
                    continue
                delivery.state, delivery.hold_reason = "queued", None
                if delivery.chain_id:
                    chain = await session.get(AgentMessageChain, delivery.chain_id)
                    if chain:
                        chain.allowance = max(chain.allowance, chain.induced_turns + self.chain_turns)
        else:
            tasks = (await session.execute(select(TeamTask).where(TeamTask.assigned_instance_id == instance.id,
                TeamTask.status.notin_(["accepted", "cancelled"])))).scalars().all()
            pending = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == instance.id,
                AgentDelivery.state.in_(["queued", "admitted", "held"])))).scalars().first()
            if instance.active_run_id or tasks or pending:
                raise InstanceError("agent_busy", "Resolve assigned work and pending messages before closing.")
            instance.state, instance.halt_reason = "closed", None
            instance.fence += 1
        instance.revision += 1
        instance.updated_at = now()
        return {"agent": _public_instance(instance), "status": "stop_requested" if operation == "stop_agent" and instance.active_run_id else instance.state,
                "instance_wake": [instance.id] if operation == "resume_agent" else []}

    async def reserve_reconciliation(self, token: str, *, lease_seconds: int) -> bool:
        """Use the database clock and serialize first acquisition across workers."""
        async with self.database.reserved_session() as session:
            clock = (await session.execute(select(func.current_timestamp()))).scalar_one()
            row = (await session.execute(select(AgentReconcileLease,
                AgentReconcileLease.available_at > clock).where(AgentReconcileLease.id == 1))).first()
            if row and row[1]:
                return False
            deadline = clock + timedelta(seconds=lease_seconds)
            if row is None:
                session.add(AgentReconcileLease(id=1, token=token, available_at=deadline))
            else:
                slot = row[0]
                slot.token, slot.available_at = token, deadline
            await session.commit()
            return True

    async def finish_reconciliation(self, token: str, *, delay_seconds: int) -> None:
        async with self.database.reserved_session() as session:
            clock = (await session.execute(select(func.current_timestamp()))).scalar_one()
            # A delayed old worker cannot release a newer worker's claim.
            await session.execute(update(AgentReconcileLease).where(
                AgentReconcileLease.id == 1, AgentReconcileLease.token == token
            ).values(available_at=clock + timedelta(seconds=delay_seconds)))
            await session.commit()

    @staticmethod
    def _waiting_ids(run: AgentRun) -> dict[str, int | None]:
        values = (run.result or {}).get("waiting_task_ids") or []
        items: dict[str, int | None] = {}
        for value in values:
            if isinstance(value, dict) and value.get("task_id"):
                items[str(value["task_id"])] = int(value["attempt"]) if value.get("attempt") is not None else None
            elif value:
                items[str(value)] = None
        return items

    async def _parked_wait(self, session, instance: AgentInstance) -> dict | None:
        """Return the frozen vNext wait state that gates inbox admission."""
        if instance.active_run_id:
            return None
        job = await session.get(EmployeeJob, instance.job_id)
        plan = (job.source.get("team_plan") or {}) if job else {}
        if plan.get("version") != 3 or plan.get("delegation_runtime_version") != 1:
            return None
        runs = (await session.execute(select(AgentRun).where(AgentRun.instance_id == instance.id,
            AgentRun.status == "parked").order_by(AgentRun.created_at.desc(), AgentRun.id.desc()))).scalars().all()
        for run in runs:
            result = run.result or {}
            reason = result.get("reason")
            if reason not in {"child_work", "decision"}:
                continue
            if run.task_id:
                task = await session.get(TeamTask, run.task_id)
                if (task is None or run.attempt is None or task.current_attempt != run.attempt
                        or task.assigned_instance_id != instance.id or task.assigned_run_id != run.id
                        or task.status != "running"):
                    continue
                waiting = self._waiting_ids(run)
                decisions = set(map(str, result.get("decision_ids") or []))
                children = await self._children_of_attempt(session, task)
                current = {child.id: child for child in children if child.status not in {"accepted", "cancelled"}}
                waiting = {identity: attempt for identity, attempt in waiting.items()
                           if (identity == task.id and reason == "decision") or
                           (identity in current and (attempt is None or attempt == current[identity].current_attempt))}
                if not waiting and not decisions:
                    continue
                return {"run": run, "task": task, "job": job, "plan": plan,
                        "reason": reason, "waiting_task_ids": waiting, "decision_ids": decisions}
            elif instance.role == "lead":
                waiting = self._waiting_ids(run)
                decisions = set(map(str, result.get("decision_ids") or []))
                owner = (job.delivery or {}).get("owner_decisions") or {}
                decisions = {identity for identity in decisions
                             if self._owner_decision_matches(owner.get(identity), identity, instance, job,
                                                             None, {"pending", "answered"})}
                outer = {}
                for identity, attempt in waiting.items():
                    task = await session.get(TeamTask, identity)
                    target = await session.get(AgentInstance, task.assigned_instance_id) if task and task.assigned_instance_id else None
                    if (task and task.team_id == job.team_id and task.workflow_id == job.workflow_id
                            and task.parent_agent_id == job.lead_node_id
                            and task.assigned_to == plan.get("orchestrator_node_id")
                            and target and target.job_id == job.id and target.generation == instance.generation
                            and target.template_node_id == plan.get("orchestrator_node_id")
                            and (attempt is None or attempt == task.current_attempt)
                            and task.status not in {"accepted", "cancelled"}):
                        outer[task.id] = attempt
                waiting = outer
                if not waiting and not decisions:
                    continue
                return {"run": run, "task": None, "job": job, "plan": plan,
                        "reason": reason, "waiting_task_ids": waiting, "decision_ids": decisions}
        return None

    async def _matches_parked_delivery(self, session, instance: AgentInstance, wait: dict,
                                       row: AgentDelivery) -> bool:
        payload = row.payload or {}
        run, task = wait["run"], wait.get("task")
        decision_id = str(payload.get("decision_id") or "")
        if row.kind == "decision_answer" and decision_id in wait["decision_ids"]:
            if payload.get("response_instance_id") != instance.id:
                return False
            return ((row.task_id, row.attempt) == (task.id, task.current_attempt)
                    and payload.get("response_task_id") == task.id
                    and payload.get("response_attempt") == task.current_attempt
                    if task else row.task_id is None and row.attempt is None
                    and payload.get("response_task_id") is None and payload.get("response_attempt") is None)
        if row.kind == "task_review":
            child_id = str(payload.get("task_id") or "")
            child = await session.get(TeamTask, child_id) if child_id else None
            expected_attempt = wait["waiting_task_ids"].get(child_id)
            if (not child or child.current_attempt != payload.get("attempt")
                    or child.status not in {"submitted", "failed", "cancelled"}
                    or (expected_attempt is not None and expected_attempt != child.current_attempt)):
                return False
            if task:
                if (child_id not in wait["waiting_task_ids"]
                        or (row.task_id, row.attempt) != (task.id, task.current_attempt)
                        or child.assigning_run_id is None):
                    return False
                child_run = await session.get(AgentRun, child.assigning_run_id)
                return bool(child_run and child_run.instance_id == instance.id
                            and child_run.task_id == task.id and child_run.attempt == task.current_attempt)
            return (child_id in wait["waiting_task_ids"] and child.team_id == instance.team_id
                    and child.parent_agent_id == wait["plan"].get("lead_node_id")
                    and child.assigned_to == wait["plan"].get("orchestrator_node_id")
                    and row.task_id is None and row.attempt is None)
        if row.kind == "decision_request":
            source_task_id = str(payload.get("source_task_id") or "")
            source_task = await session.get(TeamTask, source_task_id) if source_task_id else None
            expected_attempt = wait["waiting_task_ids"].get(source_task_id)
            if (payload.get("response_instance_id") != instance.id or not source_task
                    or source_task.current_attempt != payload.get("source_attempt")
                    or source_task.status != "running"
                    or source_task.team_id != instance.team_id
                    or (expected_attempt is not None and expected_attempt != source_task.current_attempt)):
                return False
            requester = await session.get(AgentInstance, source_task.assigned_instance_id) if source_task.assigned_instance_id else None
            requester_run = await session.get(AgentRun, str(payload.get("source_run_id") or ""))
            if (requester is None or requester.team_id != instance.team_id
                    or requester.generation != instance.generation
                    or requester_run is None or requester_run.instance_id != requester.id
                    or requester_run.task_id != source_task.id
                    or requester_run.attempt != source_task.current_attempt
                    or requester_run.status not in {"running", "parked"}
                    or source_task.assigned_run_id != requester_run.id
                    or (requester_run.status == "running" and requester.active_run_id != requester_run.id)):
                return False
            if task:
                response_run = await session.get(AgentRun, str(payload.get("response_run_id") or ""))
                assigning_run = await session.get(AgentRun, source_task.assigning_run_id) if source_task.assigning_run_id else None
                return bool(source_task_id in wait["waiting_task_ids"]
                    and (row.task_id, row.attempt) == (task.id, task.current_attempt)
                    and payload.get("response_task_id") == task.id
                    and payload.get("response_attempt") == task.current_attempt
                    and response_run is not None and response_run.id == run.id
                    and response_run.instance_id == instance.id
                    and response_run.task_id == task.id and response_run.attempt == task.current_attempt
                    and assigning_run is not None and assigning_run.instance_id == instance.id
                    and assigning_run.task_id == task.id and assigning_run.attempt == task.current_attempt
                    and source_task.parent_agent_id == instance.template_node_id
                    and source_task.assigned_to == requester.template_node_id)
            response_run = await session.get(AgentRun, str(payload.get("response_run_id") or ""))
            assigning_run = await session.get(AgentRun, source_task.assigning_run_id) if source_task.assigning_run_id else None
            return bool(source_task_id in wait["waiting_task_ids"]
                and row.task_id is None and row.attempt is None
                and payload.get("response_task_id") is None and payload.get("response_attempt") is None
                and response_run is not None and response_run.id == run.id
                and response_run.instance_id == instance.id
                and response_run.task_id is None and response_run.attempt is None
                and assigning_run is not None and assigning_run.instance_id == instance.id
                and assigning_run.task_id is None and assigning_run.attempt is None
                and source_task.parent_agent_id == wait["plan"].get("lead_node_id")
                and source_task.assigned_to == wait["plan"].get("orchestrator_node_id")
                and requester.template_node_id == wait["plan"].get("orchestrator_node_id"))
        return False

    async def list_pending(self, limit: int = 100) -> list[dict]:
        from models.employees import EmployeeJob
        # Blocked assignments wait for acceptance. Stale assignments still
        # qualify so claim_run can retire them.
        has_mail = select(AgentDelivery.id).outerjoin(TeamTask, AgentDelivery.task_id == TeamTask.id).where(
            AgentDelivery.recipient_id == AgentInstance.id, AgentDelivery.state == "queued",
            AgentDelivery.task_id.is_(None) | TeamTask.id.is_(None) |
            (TeamTask.current_attempt != AgentDelivery.attempt) | (TeamTask.status != "blocked"),
        ).correlate(AgentInstance).exists()
        async with self.database.reserved_session() as session:
            rows = (await session.execute(select(AgentInstance)
                .outerjoin(EmployeeJob, AgentInstance.job_id == EmployeeJob.id)
                .outerjoin(AgentTeam, AgentInstance.team_id == AgentTeam.id)
                .where(EmployeeJob.state.notin_(["delivered", "completed", "cancelled"]) |
                    (EmployeeJob.id.is_(None) & (AgentTeam.id.is_(None) | (AgentTeam.status != "completed"))),
                    AgentInstance.active_run_id.is_not(None) | (AgentInstance.state.in_(["idle", "running"]) & has_mail))
                .order_by(AgentInstance.updated_at, AgentInstance.id)
                .limit(max(1, limit * 8)))).scalars().all()
            checked_at = now()
            pending = []
            for instance in rows:
                # Persist rotation before external hints. Failed hints and
                # active owners must not occupy the first page on every sweep.
                instance.updated_at = checked_at
                if not instance.active_run_id:
                    parked = await self._parked_wait(session, instance)
                    if parked:
                        deliveries = (await session.execute(select(AgentDelivery).where(
                            AgentDelivery.recipient_id == instance.id, AgentDelivery.state == "queued")
                            .order_by(AgentDelivery.sequence))).scalars().all()
                        eligible = False
                        for delivery in deliveries:
                            if await self._matches_parked_delivery(session, instance, parked, delivery):
                                eligible = True
                                break
                        if not eligible:
                            continue
                pending.append(instance)
                if len(pending) >= max(1, limit):
                    break
            await session.commit()
            return [instance.model_dump(mode="json") for instance in pending]

    async def list_stopping(self, limit: int = 100) -> list[str]:
        """Durable interrupted executors whose stop hint may have been lost."""
        async with self.database.reserved_session() as session:
            rows = (await session.execute(select(AgentInstance).join(
                AgentRun, AgentInstance.active_run_id == AgentRun.id).where(
                AgentRun.status == "draining").order_by(AgentInstance.updated_at, AgentInstance.id)
                .limit(max(1, limit)))).scalars().all()
            checked_at = now()
            for instance in rows:
                instance.updated_at = checked_at
            await session.commit()
            return [instance.id for instance in rows]

    async def _admit_deliveries(self, session, instance, run, *, allow_tasks: bool):
        rows = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == instance.id,
            AgentDelivery.state == "queued").order_by(AgentDelivery.sequence))).scalars().all()
        admitted, used, selected_task = [], 0, None
        parked = await self._parked_wait(session, instance)
        for row in rows:
            if parked and not await self._matches_parked_delivery(session, instance, parked, row):
                continue
            if row.kind == "task_review" and row.payload.get("review_reminder"):
                reviewed = await session.get(TeamTask, row.payload["task_id"])
                if reviewed is None or task_ref(reviewed) != row.payload["task_ref"]:
                    row.state = "cancelled"
                    continue
            row_task = None
            if row.task_id:
                task = await session.get(TeamTask, row.task_id)
                if not task or task.current_attempt != row.attempt or task.status in {"cancelled", "accepted", "failed", "submitted"}:
                    row.state = "cancelled"
                    continue
                if task.status == "blocked":
                    continue
                if row.kind in {"task_review", "decision_request", "decision_answer"}:
                    if not allow_tasks and (run.task_id, run.attempt) != (task.id, task.current_attempt):
                        continue
                    if selected_task and selected_task.id != task.id:
                        continue
                elif not allow_tasks or selected_task is not None:
                    break
                row_task = task
            if len(admitted) >= MAX_BATCH_MESSAGES or (admitted and used + len(row.content.encode()) > MAX_BATCH_BYTES):
                break
            if row.chain_id:
                chain = await session.get(AgentMessageChain, row.chain_id)
                if not chain:
                    chain = AgentMessageChain(id=row.chain_id, team_id=instance.team_id, allowance=self.chain_turns)
                    session.add(chain)
                    await session.flush()
                # One increment per run/chain, including a forwarded review.
                if run.chain_id != row.chain_id:
                    if chain.induced_turns >= chain.allowance:
                        row.state, row.hold_reason = "held", "peer_chain_limit"
                        instance.halt_reason = "peer_chain_limit"
                        await self._record_notice(session, instance, "peer_chain_limit", row.chain_id)
                        continue
                    if run.chain_id is not None:
                        break  # Never merge independent causal chains into one run.
                    chain.induced_turns += 1
                    run.chain_id = row.chain_id
            row.state, row.run_id = "admitted", run.id
            if row_task:
                selected_task = row_task
            admitted.append(row)
            used += len(row.content.encode())
        return admitted, selected_task

    async def _assign_orchestrator(self, session, job, chat, run, handoff, plan):
        """Admit the fixed handoff and the first assignment in a single transaction."""
        template_id = plan["orchestrator_node_id"]
        scope = {**chat.config["scope"], "agent_instance_id": chat.id, "agent_run_id": run.id,
                 "runtime_context": chat.config["runtime_context"]}
        await self._authorize(session, scope, lead=True)
        descriptor = next((member for member in plan["members"] if member["node_id"] == template_id), {})
        template = await self._template(session, scope, {
            "node_id": template_id, "node_type": descriptor["node_type"], "label": descriptor.get("label") or "Orchestrator",
            "parameters": job.source.get("parameters", {}).get(template_id, {}),
        })
        key = _id("intake_", job.id, chat.id)
        orchestrator = await self._new_instance(session, scope, template, _id("agent_", key))
        task = await self._new_task(session, scope, orchestrator, {"instructions": job.mission}, key, intake=True)
        handoff.state, handoff.run_id, handoff.consumed_at = "consumed", run.id, now()
        chat.fence, chat.updated_at = run.fence, now()
        run.status, run.completed_at = "completed", now()
        run.result = {"status": "queued", "task_id": task.id}
        run.input_snapshot = {"instance_wake": [orchestrator.id]}
        session.add(run)
        await session.commit()
        return {**run.input_snapshot, "run_id": run.id, "instance": chat.model_dump(mode="json"),
                "result": run.result, "status": run.status}

    async def claim_run(self, instance_id: str, *, request_id: str, workflow_id: str | None = None,
                        workflow_run_id: str | None = None) -> dict | None:
        from models.employees import EmployeeJob
        run_id = _id("agent_run_", instance_id, request_id)
        async with self.database.reserved_session() as session:
            instance = await self._instance(session, instance_id)
            existing = await session.get(AgentRun, run_id)
            if existing:
                return {**deepcopy(existing.input_snapshot), "run_id": existing.id, "status": existing.status,
                        "instance": instance.model_dump(mode="json"), "result": existing.result, "error": existing.error}
            job = await session.get(EmployeeJob, instance.job_id)
            if job and job.state in {"delivered", "completed", "cancelled"}:
                return None
            if job is None:
                team = await session.get(AgentTeam, instance.team_id)
                if team and team.status == "completed":
                    return None
            if instance.state in {"halted", "closed", "paused"} or instance.active_run_id:
                return None
            run = AgentRun(id=run_id, instance_id=instance_id, request_id=request_id,
                           fence=instance.fence + 1, temporal_workflow_id=workflow_id, temporal_run_id=workflow_run_id)
            plan = (job.source.get("team_plan") or {}) if job else {}
            if instance.role == "lead" and plan.get("version") == 3:
                handoff = await session.get(AgentDelivery, _id("delivery_", instance.id, "initial"))
                if handoff and handoff.state == "queued":
                    return await self._assign_orchestrator(session, job, instance, run, handoff, plan)
            deliveries, task = await self._admit_deliveries(session, instance, run, allow_tasks=True)
            if not deliveries:
                await session.commit()
                return None
            carried_wait = await self._parked_wait(session, instance)
            carried_waiting = dict(carried_wait.get("waiting_task_ids") or {}) if carried_wait else {}
            carried_decisions = set(carried_wait.get("decision_ids") or []) if carried_wait else set()
            answered_decisions = set()
            for delivery in deliveries:
                payload = delivery.payload or {}
                if delivery.kind == "task_review":
                    child_id = str(payload.get("task_id") or "")
                    expected = carried_waiting.get(child_id)
                    if expected is None or expected == payload.get("attempt"):
                        carried_waiting.pop(child_id, None)
                elif delivery.kind == "decision_answer":
                    identity = str(payload.get("decision_id") or "")
                    if identity:
                        answered_decisions.add(identity)
                        carried_decisions.discard(identity)
            carried_decisions.update(await self._pending_source_decision_ids(session, instance, task))
            carried_decisions.difference_update(answered_decisions)
            if instance.role == "lead" and job:
                # Owner decisions marked answered stay parked until the
                # corresponding decision_answer is actually admitted.
                records = (job.delivery or {}).get("owner_decisions") or {}
                carried_decisions.update(str(identity) for identity, record in records.items()
                    if self._owner_decision_matches(record, str(identity), instance, job, task,
                                                    {"pending", "answered"})
                    and str(identity) not in answered_decisions)
            instance.fence, instance.active_run_id, instance.state = run.fence, run.id, "running"
            instance.updated_at = now()
            if task:
                if task.status != "running":
                    task.status, task.started_at = "running", now()
                    task.revision += 1
                task.assigned_run_id = run.id
                task.runner_workflow_id = workflow_id
                task.runner_run_id = workflow_run_id
                run.task_id, run.attempt = task.id, task.current_attempt
                attempt = await session.get(TeamTaskAttempt, f"{task.id}:attempt:{task.current_attempt}")
                if attempt:
                    attempt.status, attempt.started_at, attempt.assigned_run_id = "running", task.started_at, run.id
                    attempt.runner_workflow_id = workflow_id
                    attempt.runner_run_id = workflow_run_id
            transcript = await session.get(AgentInstanceConversation, instance_id)
            transcript.owning_run_id = run.id
            result = {"run_id": run.id, "instance": instance.model_dump(mode="json"),
                "messages": [_wire(row) for row in deliveries], "deliveries": [row.id for row in deliveries],
                "task": _task_dict(task) if task else None, "transcript": deepcopy(transcript.messages),
                "transcript_revision": transcript.revision,
                "managed_waiting_task_ids": [
                    {"task_id": identity, **({"attempt": attempt} if attempt is not None else {})}
                    for identity, attempt in sorted(carried_waiting.items())
                ],
                "managed_pending_decision_ids": sorted(carried_decisions)}
            run.input_snapshot = deepcopy(result)
            session.add(run)
            await self._refresh_member(session, instance)
            await session.commit()
            return result

    async def review_candidates(self, instance_id: str, run_id: str, delivery_ids) -> list[dict] | None:
        """The submitted work a claimed run was woken to review, when that is
        all it was woken for (``auto_review.py``).

        One entry per delivery: a review notice, not a reminder, about a task
        that is still submitted on the attempt and revision the notice names,
        with the task's instructions (and any review feedback) and its result.
        None when the reviewer is the job's lead (Chat), when the run no
        longer owns its instance, or when any delivery is something else; the
        reviewer then takes an ordinary model turn.
        """
        async with self.database.get_session() as session:
            instance = await session.get(AgentInstance, instance_id)
            run = await session.get(AgentRun, run_id)
            if (instance is None or run is None or instance.role == "lead" or not delivery_ids
                    or instance.active_run_id != run_id or run.status != "running"):
                return None
            candidates = []
            for identity in delivery_ids:
                delivery = await session.get(AgentDelivery, identity)
                payload = (delivery.payload or {}) if delivery else {}
                if (delivery is None or delivery.kind != "task_review" or delivery.run_id != run_id
                        or payload.get("review_reminder") or payload.get("status") != "submitted"):
                    return None
                task = await session.get(TeamTask, str(payload.get("task_id") or ""))
                if (task is None or task.status != "submitted" or task.current_attempt != payload.get("attempt")
                        or task.revision != payload.get("revision")):
                    return None
                candidates.append({"task_id": task.id, "task_ref": task_ref(task), "title": task.title or "the task",
                                   "instructions": self._task_input(task), "result": deepcopy(task.result)})
            return candidates

    async def settle_auto_review(self, claim: dict, accepted: list[dict]) -> dict:
        """End a review run with no model turn (``auto_review.py``).

        Each entry of ``accepted`` (a ``review_candidates`` entry with its
        judgment's ``confidence``) is accepted through ``accept_task``, as its
        reviewer would, and marked as accepted automatically. The transcript
        gets the review notices and one line saying what was accepted, and the
        run completes with the reviewer's accepted work as its result, so the
        review gate can submit the reviewer's own task. Returns the
        completion's receipt, with what the accepts woke and the run's result.
        """
        from services.agent_instances.tools import _trusted_scope

        instance_id, run_id = claim["instance"]["id"], claim["run_id"]
        scope = _trusted_scope(claim["context"])
        wake: list[str] = []
        lines = []
        for item in accepted:
            receipt = await self.execute(
                "accept_task", scope=dict(scope), request_id=f"{instance_id}:{run_id}:auto-review:{item['task_id']}",
                arguments={"task_ref": item["task_ref"], "auto_confidence": item["confidence"]})
            wake.extend(receipt.get("instance_wake") or [])
            lines.append(f'Accepted "{item["title"]}" automatically: '
                         f'{round(item["confidence"] * 100)}% sure it completes the instructions.')
        transcript = [*(claim.get("transcript") or []), *(claim.get("messages") or []),
                      message_to_wire(Message(role="assistant", content="\n".join(lines)))]
        await self.checkpoint(instance_id, run_id, messages=transcript, expected_revision=int(claim.get("transcript_revision") or 0),
                              delivery_ids=claim.get("deliveries") or [], request_id="auto-review")
        result = {"response": await self._accepted_work(run_id)}
        completed = await self.complete_run(instance_id, run_id, result=result, request_id=f"complete:{run_id}")
        return {**completed, "result": result, "instance_wake": [*(completed.get("instance_wake") or []), *wake]}

    async def _accepted_work(self, run_id: str) -> str:
        """The accepted results of the work a run's task assigned, one block
        each: what the reviewer hands on as its own result."""
        async with self.database.get_session() as session:
            run = await session.get(AgentRun, run_id)
            task = await session.get(TeamTask, run.task_id) if run and run.task_id else None
            children = await self._children_of_attempt(session, task) if task else []
        blocks = []
        for child in children:
            if child.status != "accepted":
                continue
            response = child.result.get("response") if isinstance(child.result, dict) else child.result
            text = response if isinstance(response, str) else json.dumps(response, default=str)
            blocks.append(f"{child.title}:\n{text}")
        return "\n\n".join(blocks)

    async def _owned_run(self, session, instance_id: str, run_id: str):
        instance = await self._instance(session, instance_id)
        run = await session.get(AgentRun, run_id)
        if run is None or run.instance_id != instance_id or instance.active_run_id != run_id or run.fence != instance.fence or run.status != "running":
            raise InstanceError("stale_run", "This execution no longer owns the agent.")
        return instance, run

    async def drain_messages(self, instance_id: str, run_id: str, *, request_id: str) -> dict:
        key, fingerprint = _id("mutation_", run_id, "drain", request_id), _hash([instance_id, run_id])
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            instance, run = await self._owned_run(session, instance_id, run_id)
            if instance.state != "running":
                return {"messages": [], "deliveries": []}
            rows, _ = await self._admit_deliveries(session, instance, run, allow_tasks=False)
            result = {"messages": [_wire(row) for row in rows], "deliveries": [row.id for row in rows]}
            return await self._commit(session, key, fingerprint, result)

    async def checkpoint(self, instance_id: str, run_id: str, *, messages: list[dict], expected_revision: int,
                         delivery_ids=(), request_id: str) -> dict:
        key = _id("mutation_", run_id, "checkpoint", request_id)
        fingerprint = _hash([instance_id, run_id, messages, expected_revision, sorted(delivery_ids)])
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            await self._owned_run(session, instance_id, run_id)
            conversation = await session.get(AgentInstanceConversation, instance_id)
            if conversation.revision != expected_revision or conversation.owning_run_id != run_id:
                raise InstanceError("transcript_conflict")
            for identity in delivery_ids:
                row = await session.get(AgentDelivery, identity)
                if row is None or row.recipient_id != instance_id or row.run_id != run_id or row.state not in {"admitted", "consumed"}:
                    raise InstanceError("invalid_delivery_receipt")
                row.state, row.consumed_at = "consumed", now()
            conversation.messages, conversation.revision, conversation.updated_at = deepcopy(messages), expected_revision + 1, now()
            return await self._commit(session, key, fingerprint, {"revision": conversation.revision})

    async def _review_gate(self, session, instance, task, *, chain_id):
        """Return whether a parked task can submit, or why it cannot finish."""
        if task and await self._pending_source_decision_ids(session, instance, task):
            return False, None
        if task:
            requests = (await session.execute(select(AgentDelivery).where(
                AgentDelivery.team_id == instance.team_id,
                AgentDelivery.generation == instance.generation,
                AgentDelivery.kind == "decision_request"))).scalars().all()
            for request in requests:
                payload = request.payload or {}
                if (payload.get("source_instance_id") != instance.id
                        or payload.get("source_task_id") != task.id
                        or payload.get("source_attempt") != task.current_attempt
                        or payload.get("decision_state") != "answered"):
                    continue
                answer_id = payload.get("answer_delivery_id") or payload.get("upstream_answer_delivery_id")
                answer = await session.get(AgentDelivery, str(answer_id or "")) if answer_id else None
                if answer is not None and answer.state != "consumed":
                    return False, None
        children = await self._children_of_attempt(session, task)
        if not children:
            job = await session.get(EmployeeJob, instance.job_id)
            plan = job.source.get("team_plan", {}) if job else {}
            if (instance.template_node_id == plan.get("orchestrator_node_id")
                    and any(m.get("parent_node_id") == instance.template_node_id for m in plan["members"])):
                # A revision can reuse accepted evidence from an earlier attempt.
                accepted = await session.scalar(select(TeamTask.id).join(AgentRun, TeamTask.assigning_run_id == AgentRun.id)
                    .where(AgentRun.task_id == task.id, AgentRun.attempt < task.current_attempt,
                           TeamTask.status == "accepted").limit(1))
                if not accepted:
                    return False, "The Orchestrator finished without assigning work to a connected subagent."
            return True, None
        unresolved = [child for child in children if child.status != "accepted" and not child.reviewed_blocker]
        if not unresolved:
            return True, None
        if any(child.status in {"queued", "blocked", "running"} for child in unresolved):
            return False, None
        # An outcome can arrive after the current turn's last inbox drain.
        pending = await session.scalar(select(AgentDelivery.id).where(AgentDelivery.kind == "task_review",
            AgentDelivery.recipient_id == instance.id, AgentDelivery.task_id == task.id,
            AgentDelivery.attempt == task.current_attempt, AgentDelivery.state.in_(["queued", "held"])).limit(1))
        if pending:
            return False, None
        reminded = False
        for child in unresolved:
            if await self._remind_review(session, instance, child, parent_task=task, chain_id=chain_id):
                reminded = True
        if not reminded:
            # Preserve the submitted evidence and this upstream attempt for an
            # owner retry. The same manager can continue its other assignments.
            for child in unresolved:
                ref = task_ref(child)
                notice = await self._enqueue(session, instance,
                    identity=_id("delivery_", "owner_review", instance.active_run_id, ref),
                    sender_id=None, kind="task_review", task=task, content=self._review_instructions(child),
                    payload={"task_id": child.id, "task_ref": ref, "review_reminder": True})
                notice.state, notice.hold_reason = "held", "owner_review"
            job = await session.get(EmployeeJob, instance.job_id)
            if job and not self._protected_job(job):
                job.state, job.updated_at = "review_blocked", now()
        return False, None

    async def _chat_review_error(self, session, instance, result, *, chain_id) -> str | None:
        job = await session.get(EmployeeJob, instance.job_id)
        if not job or self._protected_job(job):
            return None
        plan = job.source.get("team_plan") or {}
        if plan.get("version") != 3:
            return None
        outer = await outer_assignment(session, job)
        if outer is None or outer.status in {"queued", "blocked", "running"}:
            return None  # The requested revision is still with the Orchestrator.
        if outer.status == "accepted" or outer.reviewed_blocker:
            if str((result or {}).get("response") or "").strip() not in {"", "NO_REPLY"}:
                return None
            error = "Chat reviewed the assignment and still needs to write the final answer."
        else:
            error = "Chat finished its turn with the Orchestrator's assignment still awaiting review."
        notice = await session.scalar(select(AgentDelivery.id).where(
            AgentDelivery.recipient_id == instance.id, AgentDelivery.kind == "task_review",
            AgentDelivery.state == "queued").limit(1))
        if notice or await self._remind_review(session, instance, outer, parent_task=None, chain_id=chain_id):
            return None
        return error

    async def park_run(self, instance_id: str, run_id: str, *, reason: str,
                       waiting_task_ids: list[str] | None = None,
                       decision_ids: list[str] | None = None, request_id: str) -> dict:
        """Settle one execution turn while keeping its assigned task open.

        The receipt captures the exact child tasks and decisions that may wake
        this turn. Chat may also park without its own TeamTask when it has
        committed the fixed outer Orchestrator assignment or an owner request.
        """
        waiting: dict[str, int | None] = {}
        if not isinstance(waiting_task_ids, (list, tuple)):
            raise InstanceError("invalid_wait_set")
        for value in waiting_task_ids or []:
            identity, attempt = (value.get("task_id"), value.get("attempt")) if isinstance(value, dict) else (value, None)
            identity = str(identity or "").strip()
            if not identity or len(identity) > 255 or attempt is not None and int(attempt) < 0:
                raise InstanceError("invalid_wait_set")
            attempt = int(attempt) if attempt is not None else None
            if identity in waiting and waiting[identity] != attempt:
                raise InstanceError("invalid_wait_set")
            waiting[identity] = attempt
        if len(waiting) > MAX_WAITING_TASKS:
            raise InstanceError("invalid_wait_set", "Too many waiting tasks were reported for one execution turn.")
        decisions = sorted({str(value).strip() for value in (decision_ids or []) if str(value).strip()})
        if len(decisions) > MAX_OWNER_DECISIONS or any(len(value) > 255 for value in decisions):
            raise InstanceError("invalid_wait_set", "Too many pending decisions were reported for one execution turn.")
        waiting_items = [{"task_id": identity, "attempt": waiting[identity]} for identity in sorted(waiting)]
        key = _id("mutation_", run_id, "park", request_id)
        fingerprint = _hash([reason, waiting_items, decisions])
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            instance = await self._instance(session, instance_id)
            run = await session.get(AgentRun, run_id)
            if run is None or run.instance_id != instance_id:
                raise InstanceError("stale_run")
            if run.status != "running" or instance.active_run_id != run_id or instance.fence != run.fence:
                if run.status == "parked":
                    return {"status": "parked", "instance_wake": [], "task_id": run.task_id,
                            "attempt": run.attempt, "waiting_task_ids": sorted(self._waiting_ids(run)),
                            "decision_ids": (run.result or {}).get("decision_ids") or []}
                raise InstanceError("stale_run")
            job = await session.get(EmployeeJob, instance.job_id)
            plan = (job.source.get("team_plan") or {}) if job else {}
            vnext = bool(plan.get("version") == 3 and plan.get("delegation_runtime_version") == 1)
            if vnext and reason not in {"child_work", "decision"}:
                raise InstanceError("invalid_park_reason")
            task = await session.get(TeamTask, run.task_id) if run.task_id else None
            if run.task_id:
                if (task is None or run.attempt is None or task.current_attempt != run.attempt
                        or task.assigned_run_id != run_id or task.assigned_instance_id != instance_id
                        or task.status != "running"):
                    raise InstanceError("stale_task_attempt")
            elif not vnext or instance.role != "lead" or instance.template_node_id != plan.get("lead_node_id"):
                raise InstanceError("park_requires_task")

            validated_waiting: dict[str, int] = {}
            if vnext and task:
                children = await self._children_of_attempt(session, task)
                open_children = {child.id: child for child in children
                                 if child.status not in {"accepted", "cancelled"}}
                for identity, expected_attempt in waiting.items():
                    if identity == task.id and reason == "decision":
                        if expected_attempt is not None and expected_attempt != task.current_attempt:
                            raise InstanceError("stale_task_attempt")
                        validated_waiting[identity] = task.current_attempt
                        continue
                    child = open_children.get(identity)
                    if child is None or (expected_attempt is not None and child.current_attempt != expected_attempt):
                        raise InstanceError("stale_waiting_task", "A waiting task is not open under this exact parent attempt.")
                    validated_waiting[identity] = child.current_attempt
                if reason == "child_work" and not validated_waiting:
                    raise InstanceError("park_requires_child", "Child-work parking requires an open child task for this attempt.")
            elif vnext:
                if not job or not job.team_id or job.team_id != instance.team_id:
                    raise InstanceError("invalid_scope")
                for identity, expected_attempt in waiting.items():
                    outer = await session.get(TeamTask, identity)
                    assignee = await session.get(AgentInstance, outer.assigned_instance_id) if outer and outer.assigned_instance_id else None
                    if (outer is None or outer.team_id != job.team_id or outer.workflow_id != job.workflow_id
                            or outer.parent_agent_id != plan.get("lead_node_id")
                            or outer.assigned_to != plan.get("orchestrator_node_id")
                            or outer.current_attempt != (expected_attempt if expected_attempt is not None else outer.current_attempt)
                            or outer.status in {"accepted", "cancelled"}
                            or assignee is None or assignee.job_id != job.id
                            or assignee.generation != instance.generation
                            or assignee.template_node_id != plan.get("orchestrator_node_id")):
                        raise InstanceError("stale_waiting_task", "A taskless Chat lead may wait only on its admitted outer Orchestrator task.")
                    validated_waiting[identity] = outer.current_attempt
                if reason == "child_work" and not validated_waiting:
                    raise InstanceError("park_requires_child", "Taskless Chat can park for child work only on its outer Orchestrator assignment.")

            if vnext and reason == "decision" and not decisions:
                raise InstanceError("park_requires_decision", "Decision parking requires a committed decision request.")
            if vnext and decisions:
                owner_decisions = (job.delivery or {}).get("owner_decisions") or {} if job else {}
                for decision_id in decisions:
                    owner = owner_decisions.get(decision_id) or {}
                    if self._owner_decision_matches(owner, decision_id, instance, job, task,
                                                    {"pending", "answered"}):
                        continue
                    requests = (await session.execute(select(AgentDelivery).where(
                        AgentDelivery.team_id == instance.team_id, AgentDelivery.generation == instance.generation,
                        AgentDelivery.kind == "decision_request"))).scalars().all()
                    committed = False
                    for row in requests:
                        payload = row.payload or {}
                        if (payload.get("decision_id") != decision_id
                                or payload.get("source_instance_id") != instance.id
                                or payload.get("source_task_id") != (task.id if task else None)
                                or payload.get("source_attempt") != (task.current_attempt if task else None)
                                or payload.get("decision_state") not in {"pending", "answered", "escalated"}):
                            continue
                        source_run = await session.get(AgentRun, str(payload.get("source_run_id") or ""))
                        if (source_run is not None and source_run.instance_id == instance.id
                                and source_run.task_id == (task.id if task else None)
                                and source_run.attempt == (task.current_attempt if task else None)
                                and source_run.status in {"running", "parked", "completed"}):
                            committed = True
                            break
                    if not committed:
                        raise InstanceError("stale_decision", "A parked decision must match a committed request from this exact run.")

            run.status = "parked"
            run.result = {"status": "parked", "reason": str(reason)[:100],
                          "task_id": task.id if task else None,
                          "attempt": task.current_attempt if task else None,
                          "waiting_task_ids": [{"task_id": identity, "attempt": attempt}
                                               for identity, attempt in sorted(validated_waiting.items())],
                          "decision_ids": decisions}
            run.completed_at = now()
            instance.active_run_id = None
            if instance.state == "running":
                instance.state = "idle"
            instance.updated_at = now()
            conversation = await session.get(AgentInstanceConversation, instance_id)
            if conversation and conversation.owning_run_id == run_id:
                conversation.owning_run_id = None
            await self._refresh_member(session, instance)
            result = {"status": "parked", "instance_wake": [], "task_id": task.id if task else None,
                      "attempt": task.current_attempt if task else None,
                      "waiting_task_ids": sorted(validated_waiting), "decision_ids": decisions}
            return await self._commit(session, key, fingerprint, result)

    async def complete_run(self, instance_id: str, run_id: str, *, result: dict | None = None,
                           error: str | dict | None = None, request_id: str) -> dict:
        from models.employees import EmployeeJob
        key = _id("mutation_", run_id, "complete", request_id)
        fingerprint = _hash([result, error])
        retire_scope = None
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            instance = await self._instance(session, instance_id)
            run = await session.get(AgentRun, run_id)
            if run is None or run.instance_id != instance_id:
                raise InstanceError("stale_run")
            if run.status not in {"running", "draining"}:
                return {"status": run.status, "instance_wake": []}
            stale = instance.active_run_id != run_id or instance.fence != run.fence or run.status == "draining"
            if instance.role == "lead" and not stale and not error:
                error = await self._chat_review_error(session, instance, result, chain_id=run.chain_id)
            error_text = str(error.get("message") or error.get("code") or error) if isinstance(error, dict) else error
            run.result = deepcopy(result) if not stale else None
            run.error = run.error or error_text
            run.status = "interrupted" if stale else ("failed" if error else "completed")
            run.completed_at = now()
            wake, stops, receipt = [], [], {}
            if run.task_id:
                task = await session.get(TeamTask, run.task_id)
                if task and task.current_attempt == run.attempt and task.assigned_run_id == run_id:
                    attempt = await session.get(TeamTaskAttempt, f"{task.id}:attempt:{run.attempt}")
                    interrupted = bool(task.assigning_run_id and stale and run.error == "interrupted")
                    can_submit = True
                    if task.assigning_run_id and not stale and not error and task.status == "running":
                        can_submit, gate_error = await self._review_gate(session, instance, task, chain_id=run.chain_id)
                        if gate_error:
                            error_text, run.error, run.status = gate_error, gate_error, "failed"
                    if (not stale or interrupted) and task.status == "running" and (can_submit or error_text):
                        if interrupted:
                            error_text = "interrupted"
                        task.status = "failed" if error_text else "submitted"
                        task.result = deepcopy(result) if not interrupted else None
                        task.error, task.completed_at = error_text[:2000] if error_text else None, now()
                        task.revision += 1
                        wake.extend(await self._task_notice(session, task, chain_id=run.chain_id))
                        if task.assigning_run_id:
                            if error_text:
                                await self._cancel_branch(session, task, stops)
                            if wake:
                                receipt["task_outcome"] = self._task_outcome(task, instance, wake[0])
                    if attempt:
                        attempt.status, attempt.result, attempt.error, attempt.completed_at = task.status, deepcopy(task.result), task.error, task.completed_at
            # A failed invocation must not immediately be restarted by its old input.
            pending = (await session.execute(select(AgentDelivery).where(AgentDelivery.run_id == run_id,
                AgentDelivery.state == "admitted"))).scalars().all()
            for delivery in pending:
                delivery.state, delivery.hold_reason = ("cancelled", "interrupted") if stale else ("held", "run_failed_before_checkpoint")
            if instance.active_run_id == run_id:
                instance.active_run_id = None
                if instance.state == "running":
                    instance.state = "idle"
                instance.updated_at = now()
                conversation = await session.get(AgentInstanceConversation, instance_id)
                conversation.owning_run_id = None
                await self._refresh_member(session, instance)
            if instance.role == "lead" and (error or instance.state == "halted"):
                from services.employees.jobs import job_has_approval
                job = await session.get(EmployeeJob, instance.job_id)
                tasks = (await session.execute(select(TeamTask.id).join(AgentInstance, TeamTask.assigned_instance_id == AgentInstance.id)
                    .where(AgentInstance.team_id == instance.team_id, AgentInstance.job_id == instance.job_id).limit(1))).first()
                if job and tasks and not self._protected_job(job) and not await job_has_approval(session, job):
                    instance.state = "halted"
                    instance.halt_reason = instance.halt_reason or "lead_turn_failed"
                    job.state, job.updated_at = "review_blocked", now()
                    job.delivery = {**job.delivery, "review_blocked": {"instance_id": instance.id,
                        "run_id": run.id, "reason": instance.halt_reason or error_text or "lead_failed"}}
            if instance.role == "lead" and not error and not stale:
                job = await session.get(EmployeeJob, instance.job_id)
                team = await session.get(AgentTeam, instance.team_id)
                if job is None and team and team.status == "completed":
                    retire_scope = deepcopy(instance.config.get("scope") or {})
            if stops:
                receipt["instance_stop"] = stops
            completed = await self._commit(session, key, fingerprint, {"status": run.status, "instance_wake": wake, **receipt})
        if retire_scope:
            retirement = await self.close_job(scope=retire_scope, request_id=f"completed:{run_id}")
            completed["instance_stop"] = retirement.get("instance_stop", [])
        return completed

    @staticmethod
    def _protected_job(job) -> bool:
        return job.state in {"completed", "delivered", "delivering", "cancelled", "waiting_for_approval", "delivery_needs_review"} or bool(
            job.result or any(job.delivery.get(key) for key in ("inflight", "completed", "resolution_pending", "claim_token")))

    async def retry_review(self, *, scope: dict, request_id: str) -> dict:
        """Owner-authorized recovery; caller authenticates the user before entry."""
        from models.employees import EmployeeJob
        scope = self._scope(scope)
        key, fingerprint = _id("mutation_", scope["job_id"], "retry_review", request_id), _hash(scope["workflow_id"])
        async with self.database.reserved_session() as session:
            await self._authorize(session, scope, lead=True)
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            job = await session.get(EmployeeJob, scope["job_id"])
            if not job or job.workflow_id != scope["workflow_id"] or job.team_id != scope["team_id"] or job.lead_node_id != scope["lead_node_id"]:
                raise InstanceError("job_not_found")
            if self._protected_job(job) or job.state != "review_blocked":
                raise InstanceError("review_not_retryable")
            from services.employees.jobs import job_has_approval
            if await job_has_approval(session, job):
                raise InstanceError("review_not_retryable")
            # Several managers may have stopped at independent reviews. Release
            # only their saved review notices, leaving other held inputs alone.
            held = (await session.execute(select(AgentDelivery).join(AgentInstance,
                AgentDelivery.recipient_id == AgentInstance.id).where(
                AgentInstance.job_id == job.id, AgentDelivery.team_id == job.team_id,
                AgentDelivery.generation == scope["generation"], AgentDelivery.state == "held",
                AgentDelivery.hold_reason == "owner_review"))).scalars().all()
            wake = set()
            for notice in held:
                notice.state, notice.hold_reason = "queued", None
                wake.add(notice.recipient_id)
            leads = (await session.execute(select(AgentInstance).where(AgentInstance.team_id == scope["team_id"],
                AgentInstance.job_id == scope["job_id"], AgentInstance.generation == scope["generation"],
                AgentInstance.role == "lead"))).scalars().all()
            if len(leads) != 1:
                raise InstanceError("agent_not_found")
            instance = leads[0]
            if not wake or instance.state == "halted":
                if instance.active_run_id or instance.state == "closed":
                    raise InstanceError("agent_draining")
                tasks = (await session.execute(select(TeamTask.id).join(AgentInstance, TeamTask.assigned_instance_id == AgentInstance.id)
                    .where(AgentInstance.team_id == scope["team_id"], AgentInstance.job_id == scope["job_id"]).limit(1))).first()
                if not tasks:
                    raise InstanceError("review_not_retryable")
                instance.state, instance.halt_reason = "idle", None
                instance.revision += 1
                content = "Resume review of this job's existing tasks. Inspect current task states and results; do not repeat accepted work."
                plan = job.source.get("team_plan") or {}
                if plan.get("version") == 3:
                    outer = await outer_assignment(session, job)
                    if outer:
                        content = self._review_instructions(outer)
                await self._enqueue(session, instance, identity=_id("delivery_", key), sender_id=None, kind="review_retry",
                    content=content)
                wake.add(instance.id)
            job.state, job.updated_at = "running", now()
            job.delivery = {key: value for key, value in job.delivery.items() if key != "review_blocked"}
            return await self._commit(session, key, fingerprint, {"success": True, "job_id": job.id, "instance_wake": sorted(wake)})

    async def close_job(self, *, scope: dict, request_id: str) -> dict:
        """Retire a delivered job's instances after the delivery transaction."""
        from models.employees import EmployeeJob
        scope = self._scope(scope)
        key, fingerprint = _id("mutation_", scope["job_id"], "close_job", request_id), _hash(scope["workflow_id"])
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            job = await session.get(EmployeeJob, scope["job_id"])
            if job:
                if job.workflow_id != scope["workflow_id"] or job.team_id != scope["team_id"] or job.lead_node_id != scope["lead_node_id"]:
                    raise InstanceError("job_not_found")
                if job.state not in {"delivered", "completed"}:
                    raise InstanceError("job_not_delivered")
                if (job.source.get("team_plan") or {}).get("version") == 3:
                    team = await session.get(AgentTeam, job.team_id)
                    if team:
                        team.status = "failed" if job.delivery.get("work_blocked") else "completed"
                        team.completed_at = now()
            else:
                team = await session.get(AgentTeam, scope["team_id"])
                if not team or team.workflow_id != scope["workflow_id"] or team.team_lead_node_id != scope["lead_node_id"] or team.status != "completed":
                    raise InstanceError("job_not_delivered")
            instances = (await session.execute(select(AgentInstance).where(AgentInstance.team_id == scope["team_id"],
                AgentInstance.job_id == scope["job_id"], AgentInstance.generation == scope["generation"]))).scalars().all()
            ids = [instance.id for instance in instances]
            if job is None and any(instance.role == "lead" and instance.active_run_id for instance in instances):
                raise InstanceError("agent_draining", "The lead must finish its final turn before retirement.")
            tasks = (await session.execute(select(TeamTask).where(TeamTask.assigned_instance_id.in_(ids)))).scalars().all() if ids else []
            if any(task.status not in {"accepted", "cancelled"} for task in tasks):
                raise InstanceError("outstanding_tasks")
            stops = []
            for instance in instances:
                if instance.state == "closed":
                    continue
                instance.state, instance.halt_reason = "closed", "job_delivered"
                instance.fence += 1
                instance.revision += 1
                if instance.active_run_id:
                    run = await session.get(AgentRun, instance.active_run_id)
                    if run and run.status == "running":
                        run.status, run.error = "draining", "job_delivered"
                    stops.append(instance.id)
                pending = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == instance.id,
                    AgentDelivery.state.in_(["queued", "admitted", "held"])))).scalars().all()
                for delivery in pending:
                    delivery.state, delivery.hold_reason = "recipient_closed", "job_delivered"
            return await self._commit(session, key, fingerprint, {"success": True, "closed": ids, "instance_stop": stops})

    async def close_completed_jobs(self, limit: int = 100) -> list[dict]:
        """Repair a crash between successful delivery and instance retirement."""
        from models.employees import EmployeeJob
        async with self.database.reserved_session() as session:
            scope_columns = (AgentInstance.workflow_id, AgentInstance.generation, AgentInstance.team_id,
                             AgentInstance.job_id, AgentTeam.team_lead_node_id.label("lead_node_id"))
            scopes = (await session.execute(select(*scope_columns).select_from(AgentInstance)
                .outerjoin(EmployeeJob, AgentInstance.job_id == EmployeeJob.id)
                .join(AgentTeam, AgentInstance.team_id == AgentTeam.id)
                .where(AgentInstance.state != "closed", EmployeeJob.state.in_(["delivered", "completed"]) |
                    (EmployeeJob.id.is_(None) & (AgentTeam.status == "completed")))
                .group_by(*scope_columns)
                .order_by(func.min(AgentInstance.updated_at), func.min(AgentInstance.id))
                .limit(max(1, limit)))).mappings().all()
            if scopes:
                keys = [(scope["team_id"], scope["job_id"], scope["generation"]) for scope in scopes]
                await session.execute(update(AgentInstance).where(AgentInstance.state != "closed",
                    tuple_(AgentInstance.team_id, AgentInstance.job_id, AgentInstance.generation).in_(keys)
                ).values(updated_at=now()))
            await session.commit()
        results = []
        for scope in scopes:
            job_id, generation = scope["job_id"], scope["generation"]
            try:
                results.append(await self.close_job(scope=dict(scope), request_id=f"delivered:{job_id}:{generation}"))
            except InstanceError as exc:
                # Inconsistent delivered jobs retain their records for inspection;
                # they are already fenced from new run admission above.
                results.append({"success": False, "job_id": job_id, "error": exc.code})
        return results

    async def halt_instance(self, instance_id: str, *, reason: str, request_id: str) -> dict:
        key, fingerprint = _id("mutation_", instance_id, "halt", request_id), _hash(reason)
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            instance = await self._instance(session, instance_id)
            instance.state, instance.halt_reason = "halted", reason
            instance.revision += 1
            await self._record_notice(session, instance, reason, request_id)
            # Do not revoke the current checkpoint writer: already admitted results drain.
            return await self._commit(session, key, fingerprint, {"agent": instance.model_dump(mode="json")})

    async def record_invalid_request(self, instance_id: str, run_id: str, *, request_id: str) -> dict:
        key, fingerprint = _id("mutation_", run_id, "invalid", request_id), _hash(instance_id)
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            instance, run = await self._owned_run(session, instance_id, run_id)
            run.invalid_requests += 1
            run.invalid_request_ids = [*(run.invalid_request_ids or []), request_id]
            if run.invalid_requests >= 3:
                instance.state, instance.halt_reason = "halted", "invalid_orchestration_requests"
                await self._record_notice(session, instance, instance.halt_reason, run.id)
            return await self._commit(session, key, fingerprint, {"count": run.invalid_requests, "halted": run.invalid_requests >= 3})

    async def set_continuation(self, instance_id: str, run_id: str, *, continuation: dict, request_id: str) -> dict:
        key, fingerprint = _id("mutation_", run_id, "continuation", request_id), _hash(continuation)
        async with self.database.reserved_session() as session:
            prior = await self._receipt(session, key, fingerprint)
            if prior is not None:
                return prior
            instance, _ = await self._owned_run(session, instance_id, run_id)
            instance.continuation = {**instance.continuation, **deepcopy(continuation)}
            return await self._commit(session, key, fingerprint, {"continuation": instance.continuation})

    async def reset_generation(self, workflow_id: str, generation: int) -> dict:
        async with self.database.reserved_session() as session:
            instances = (await session.execute(select(AgentInstance).where(AgentInstance.workflow_id == workflow_id,
                AgentInstance.generation == generation))).scalars().all()
            stops = []
            for instance in instances:
                instance.state, instance.halt_reason = "closed", "generation_reset"
                instance.fence += 1
                if instance.active_run_id:
                    run = await session.get(AgentRun, instance.active_run_id)
                    if run and run.status == "running":
                        run.status, run.error = "draining", "generation_reset"
                    stops.append(instance.id)
                deliveries = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == instance.id,
                    AgentDelivery.state.in_(["queued", "admitted", "held"])))).scalars().all()
                for row in deliveries:
                    row.state, row.hold_reason = "recipient_closed", "generation_reset"
                tasks = (await session.execute(select(TeamTask).where(TeamTask.assigned_instance_id == instance.id,
                    TeamTask.status.in_(["queued", "blocked", "running"])))).scalars().all()
                for task in tasks:
                    task.status, task.cancellation_requested = "cancelled", True
                    task.cancellation_reason, task.completed_at = "generation_reset", now()
                    task.revision += 1
                    attempt = await session.get(TeamTaskAttempt, f"{task.id}:attempt:{task.current_attempt}")
                    if attempt:
                        attempt.status, attempt.error, attempt.completed_at = "cancelled", "generation_reset", now()
            await session.commit()
            return {"closed": len(instances), "instance_stop": stops}
