"""Agent Team Service - Claude SDK Agent Teams pattern.

Coordinates multi-agent teams with shared task lists and messaging.
Teams are scoped to specific workflow executions.
"""

import uuid
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from core.database import Database
from core.logging import get_logger

logger = get_logger(__name__)


class AgentTeamService:
    """Service for managing agent teams.

    Teams are workflow-specific - each team belongs to a workflow execution.
    """

    def __init__(self, database: Database, broadcaster=None):
        self.database = database
        self.broadcaster = broadcaster
        # Track active teams per workflow
        self._active_teams: Dict[str, str] = {}  # workflow_id -> team_id

    # -------------------------------------------------------------------------
    # Team Lifecycle
    # -------------------------------------------------------------------------

    async def create_team(
        self, team_lead_node_id: str, teammate_node_ids: List[Dict[str, Any]], workflow_id: str, config: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None, root_execution_id: Optional[str] = None,
        team_lead_type: str = "orchestrator_agent", team_lead_label: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a team with lead and teammates.

        Args:
            team_lead_node_id: Node ID of the team lead agent
            teammate_node_ids: List of {node_id, node_type, label} for teammates
            workflow_id: Workflow containing the team
            config: Team configuration (mode: parallel/sequential/hybrid)

        Returns:
            Team dict or None on failure
        """
        if execution_id:
            digest = hashlib.sha256(f"{execution_id}:{team_lead_node_id}".encode()).hexdigest()[:16]
            team_id = f"team_{digest}"
            existing = await self.database.get_team(team_id)
            if existing:
                return {"team_id": team_id, **existing}
        else:
            team_id = f"team_{uuid.uuid4().hex[:12]}"

        # Create team
        team = await self.database.create_team(
            team_id=team_id, workflow_id=workflow_id, team_lead_node_id=team_lead_node_id,
            config=config or {"mode": "parallel"}, execution_id=execution_id,
            root_execution_id=root_execution_id or execution_id,
        )
        if not team:
            return None

        # Add team lead as member
        await self.database.add_team_member(
            team_id=team_id, agent_node_id=team_lead_node_id, agent_type=team_lead_type,
            agent_label=team_lead_label, role="team_lead"
        )

        # Add teammates
        for teammate in teammate_node_ids:
            await self.database.add_team_member(
                team_id=team_id,
                agent_node_id=teammate["node_id"],
                agent_type=teammate.get("node_type", "agent"),
                agent_label=teammate.get("label"),
                role="teammate",
                capabilities=teammate.get("capabilities"),
            )

        # Broadcast team creation
        if self.broadcaster:
            await self.broadcaster.broadcast_team_event(
                team_id, "team_created", {"team_id": team_id, "workflow_id": workflow_id, "member_count": len(teammate_node_ids) + 1}
            )

        # Track active team for this workflow
        self._active_teams[workflow_id] = team_id

        logger.info(f"[Teams] Created team {team_id} with {len(teammate_node_ids)} teammates")
        return {"team_id": team_id, **team}

    async def get_or_create_execution_team(
        self, *, team_lead_node_id: str, teammates: List[Dict[str, Any]], workflow_id: str,
        execution_id: str, root_execution_id: Optional[str] = None,
        team_lead_type: str = "orchestrator_agent", team_lead_label: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Idempotently create the durable team for a lead execution."""
        existing = await self.database.find_team(workflow_id, team_lead_node_id, execution_id)
        if existing:
            return {"team_id": existing["id"], **existing}
        return await self.create_team(
            team_lead_node_id, teammates, workflow_id, config,
            execution_id, root_execution_id, team_lead_type, team_lead_label,
        )

    def get_active_team_for_workflow(self, workflow_id: str) -> Optional[str]:
        """Get the active team ID for a workflow."""
        return self._active_teams.get(workflow_id)

    async def dissolve_team(self, team_id: str, workflow_id: Optional[str] = None) -> bool:
        """Dissolve a team."""
        success = await self.database.update_team_status(team_id, "dissolved")
        if success:
            # Remove from active teams tracking
            if workflow_id and workflow_id in self._active_teams:
                del self._active_teams[workflow_id]
            if self.broadcaster:
                await self.broadcaster.broadcast_team_event(team_id, "team_dissolved", {"team_id": team_id})
        return success

    async def get_team_status(
        self, team_id: Optional[str] = None, *, workflow_id: Optional[str] = None,
        team_lead_node_id: Optional[str] = None, execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get comprehensive team status."""
        if not team_id and workflow_id and team_lead_node_id:
            team = await self.database.find_team(workflow_id, team_lead_node_id, execution_id)
            team_id = team["id"] if team else None
        if not team_id:
            return {"error": "Team not found"}
        status = await self.database.get_team_stats(team_id)
        if workflow_id and team_lead_node_id and not status.get("error"):
            executions = await self.database.list_team_executions(
                workflow_id, team_lead_node_id
            )
            status["archived_executions"] = [
                {
                    **item,
                    "label": f"{item.get('status', 'unknown').title()} · "
                    f"{(item.get('execution_id') or item['team_id'])[:12]}",
                }
                for item in executions
                if item.get("execution_id")
                and item.get("execution_id") != status.get("execution_id")
            ]
        return status

    async def list_durable_task_history(
        self, *, workflow_id: str, team_lead_node_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return tasks across this lead's executions without crossing authority scope."""
        history: List[Dict[str, Any]] = []
        for execution in await self.database.list_team_executions(
            workflow_id, team_lead_node_id
        ):
            tasks = await self.database.get_team_tasks(execution["team_id"], status)
            history.extend(
                {**task, "team_execution_id": execution.get("execution_id")}
                for task in tasks
            )
        return history

    async def resolve_lead_scope(
        self, *, workflow_id: str, team_lead_node_id: str,
        execution_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve authority from the owning lead; callers cannot choose a team."""
        team = await self.database.find_team(workflow_id, team_lead_node_id, execution_id)
        if not team:
            raise ValueError("No team exists for this lead execution")
        return team

    async def _authorized_assignee(self, team_id: str, agent_node_id: str) -> Dict[str, Any]:
        stats = await self.database.get_team_stats(team_id)
        member = next((m for m in stats.get("members", []) if m["agent_node_id"] == agent_node_id), None)
        if not member or member.get("role") != "teammate":
            raise ValueError("Assignee is not a teammate of this lead")
        if member.get("status") == "offline":
            raise ValueError("Assignee is no longer available")
        return member

    async def assign_durable_task(
        self, *, workflow_id: str, team_lead_node_id: str, execution_id: Optional[str],
        assignee_node_id: str, title: str, mission: str,
        context: Optional[Dict[str, Any]] = None,
        acceptance_criteria: Optional[Dict[str, Any]] = None,
        depends_on: Optional[List[str]] = None, task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        team = await self.resolve_lead_scope(workflow_id=workflow_id, team_lead_node_id=team_lead_node_id, execution_id=execution_id)
        team_id = team["id"]
        await self._authorized_assignee(team_id, assignee_node_id)
        dependencies_resolved = True
        for dependency in depends_on or []:
            dependency_task = await self.database.get_durable_team_task(team_id, dependency)
            if not dependency_task:
                raise ValueError(f"Dependency is outside this team: {dependency}")
            dependencies_resolved = dependencies_resolved and dependency_task.get("status") == "accepted"
        task = await self.database.create_durable_team_task(
            id=task_id or f"task_{uuid.uuid4().hex[:12]}", team_id=team_id,
            workflow_id=team["workflow_id"], execution_id=team.get("execution_id"),
            root_execution_id=team.get("root_execution_id"), parent_agent_id=team_lead_node_id,
            title=title, mission=mission, description=mission, context=context,
            acceptance_criteria=acceptance_criteria, created_by=team_lead_node_id,
            assigned_to=assignee_node_id, depends_on=depends_on, trace_id=trace_id,
            status="queued" if dependencies_resolved else "blocked",
        )
        if not task:
            raise RuntimeError("Failed to persist task")
        await self.database.update_team_status(team_id, "active")
        if self.broadcaster:
            await self.broadcaster.broadcast_team_event(team_id, "team.task.queued", task)
        return task

    async def list_durable_tasks(self, *, workflow_id: str, team_lead_node_id: str, execution_id: Optional[str], status: Optional[str] = None) -> List[Dict[str, Any]]:
        team = await self.resolve_lead_scope(workflow_id=workflow_id, team_lead_node_id=team_lead_node_id, execution_id=execution_id)
        return await self.database.get_team_tasks(team["id"], status)

    async def get_durable_task(self, *, workflow_id: str, team_lead_node_id: str, execution_id: Optional[str], task_id: str) -> Dict[str, Any]:
        team = await self.resolve_lead_scope(workflow_id=workflow_id, team_lead_node_id=team_lead_node_id, execution_id=execution_id)
        task = await self.database.get_durable_team_task(team["id"], task_id)
        if not task:
            raise ValueError("Task not found in this lead execution")
        return task

    async def mutate_durable_task(
        self, *, workflow_id: str, team_lead_node_id: str, execution_id: Optional[str],
        task_id: str, revision: int, operation: str, **payload: Any,
    ) -> Dict[str, Any]:
        team = await self.resolve_lead_scope(workflow_id=workflow_id, team_lead_node_id=team_lead_node_id, execution_id=execution_id)
        team_id = team["id"]
        task = await self.database.get_durable_team_task(team_id, task_id)
        if not task:
            raise ValueError("Task not found in this lead execution")
        now = datetime.now(timezone.utc)
        transitions: Dict[str, tuple[List[str], Dict[str, Any], bool]] = {
            "modify": (["blocked", "queued"], {k: payload[k] for k in ("title", "mission", "context", "acceptance_criteria") if payload.get(k) is not None}, False),
            "cancel": (["blocked", "queued", "running"], {"status": "cancelled", "cancellation_requested": True, "cancellation_reason": payload.get("reason"), "completed_at": now}, False),
            "accept": (["submitted"], {"status": "accepted", "completed_at": now, "progress": 100}, False),
            "retry": (["failed", "submitted", "cancelled"], {"status": "queued", "current_attempt": task["current_attempt"] + 1, "retry_count": task["retry_count"] + 1, "result": None, "error": None, "completed_at": None, "cancellation_requested": False, "cancellation_reason": None}, True),
        }
        if operation == "reassign":
            assignee = payload.get("assignee_node_id")
            await self._authorized_assignee(team_id, assignee)
            allowed, values, create_attempt = transitions["retry"]
            values = {**values, "assigned_to": assignee}
        elif operation in transitions:
            allowed, values, create_attempt = transitions[operation]
        else:
            raise ValueError(f"Unsupported task operation: {operation}")
        if operation == "modify" and not values:
            raise ValueError("No mutable task fields supplied")
        changed = await self.database.transition_team_task(team_id, task_id, revision, allowed, values, create_attempt=create_attempt)
        if not changed:
            raise ValueError("Task changed or is not in a valid state; refresh and retry")
        if self.broadcaster:
            await self.broadcaster.broadcast_team_event(team_id, f"team.task.{changed['status']}", changed)
        return changed

    async def finish_durable_team(self, *, workflow_id: str, team_lead_node_id: str, execution_id: Optional[str]) -> Dict[str, Any]:
        team = await self.resolve_lead_scope(workflow_id=workflow_id, team_lead_node_id=team_lead_node_id, execution_id=execution_id)
        tasks = await self.database.get_team_tasks(team["id"])
        unresolved = [t for t in tasks if t["status"] not in {"accepted", "cancelled"}]
        if unresolved:
            raise ValueError("Team has unresolved tasks")
        await self.database.update_team_status(team["id"], "completed")
        return await self.database.get_team_stats(team["id"])

    # -------------------------------------------------------------------------
    # Task Management
    # -------------------------------------------------------------------------

    async def add_task(
        self,
        team_id: str,
        title: str,
        created_by: str,
        description: Optional[str] = None,
        priority: int = 3,
        depends_on: Optional[List[str]] = None,
        task_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add a task to the shared task list."""
        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"

        task = await self.database.add_team_task(
            task_id=task_id,
            team_id=team_id,
            title=title,
            created_by=created_by,
            description=description,
            priority=priority,
            depends_on=depends_on,
        )

        # A completed execution team may receive another delegation later in
        # the same lead run (legacy fire-and-forget loop). Re-open it before
        # broadcasting the newly queued task.
        if task:
            await self.database.update_team_status(team_id, "active")

        if task and self.broadcaster:
            await self.broadcaster.broadcast_team_event(team_id, "task_added", task)

        return task

    async def claim_task(self, team_id: str, task_id: str, agent_node_id: str) -> Optional[Dict[str, Any]]:
        """Claim a task for an agent."""
        task = await self.database.claim_task(task_id, agent_node_id)

        # Only the winner of the conditional database claim becomes working.
        # Updating first left every losing parallel contender stuck in the
        # working state even though exactly one owned the task.
        if task:
            await self.database.update_member_status(team_id, agent_node_id, "working")

        if task and self.broadcaster:
            await self.broadcaster.broadcast_team_event(team_id, "task_claimed", {**task, "claimed_by": agent_node_id})

        return task

    async def complete_task(self, team_id: str, task_id: str, result: Optional[Dict[str, Any]] = None) -> bool:
        """Submit a worker result for lead review."""
        # Get task to find assigned agent
        tasks = await self.database.get_team_tasks(team_id)
        task = next((t for t in tasks if t["id"] == task_id), None)

        success = await self.database.complete_task(task_id, result)

        if success:
            # Update member status back to idle
            if task and task.get("assigned_to"):
                await self.database.update_member_status(team_id, task["assigned_to"], "idle")

            if self.broadcaster:
                await self.broadcaster.broadcast_team_event(team_id, "team.task.submitted", {"task_id": task_id, "result": result})

        return success

    async def fail_task(self, team_id: str, task_id: str, error: str) -> bool:
        """Mark a task as failed."""
        tasks = await self.database.get_team_tasks(team_id)
        task = next((t for t in tasks if t["id"] == task_id), None)

        success = await self.database.fail_task(task_id, error)

        if success:
            if task and task.get("assigned_to"):
                await self.database.update_member_status(team_id, task["assigned_to"], "idle")

            if self.broadcaster:
                await self.broadcaster.broadcast_team_event(team_id, "task_failed", {"task_id": task_id, "error": error})

        return success

    async def get_claimable_tasks(self, team_id: str) -> List[Dict[str, Any]]:
        """Get tasks ready to be claimed."""
        return await self.database.get_claimable_tasks(team_id)

    async def is_team_done(self, team_id: str) -> bool:
        """Check whether all tasks have been explicitly resolved."""
        tasks = await self.database.get_team_tasks(team_id)
        if not tasks:
            return True
        return all(t["status"] in ("accepted", "failed", "cancelled", "skipped") for t in tasks)

    async def acquire_subagent_permit(self, root_execution_id: str, permit_id: str, limit: int = 3) -> bool:
        """Acquire one cross-process descendant slot for a delegation."""
        return await self.database.acquire_subagent_permit(root_execution_id, permit_id, limit)

    async def release_subagent_permit(self, root_execution_id: str, permit_id: str) -> bool:
        """Release a descendant slot; repeated releases are successful no-ops."""
        return await self.database.release_subagent_permit(root_execution_id, permit_id)

    # -------------------------------------------------------------------------
    # Messaging
    # -------------------------------------------------------------------------

    async def send_message(
        self, team_id: str, from_agent: str, content: str, to_agent: Optional[str] = None, message_type: str = "direct",
        event_id: Optional[str] = None, extra_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Send a message to a specific agent or broadcast."""
        msg = await self.database.add_agent_message(
            team_id=team_id,
            from_agent=from_agent,
            content=content,
            message_type=message_type if to_agent else "broadcast",
            to_agent=to_agent,
            event_id=event_id,
            extra_data=extra_data,
        )

        if msg and self.broadcaster:
            await self.broadcaster.broadcast_team_event(team_id, "team_message", msg)

        return msg

    async def broadcast(self, team_id: str, from_agent: str, content: str) -> Optional[Dict[str, Any]]:
        """Broadcast message to all team members."""
        return await self.send_message(team_id, from_agent, content, to_agent=None)

    async def get_messages(self, team_id: str, agent_node_id: Optional[str] = None, unread_only: bool = False) -> List[Dict[str, Any]]:
        """Get messages for a team or specific agent."""
        return await self.database.get_agent_messages(team_id, agent_node_id, unread_only)

    async def mark_read(self, team_id: str, agent_node_id: str) -> int:
        """Mark all messages as read for an agent."""
        return await self.database.mark_messages_read(team_id, agent_node_id)


# Singleton instance
_service: Optional[AgentTeamService] = None


def get_agent_team_service() -> AgentTeamService:
    """Get the singleton AgentTeamService instance."""
    global _service
    if _service is None:
        raise RuntimeError("AgentTeamService not initialized. Call init_agent_team_service first.")
    return _service


def init_agent_team_service(database: Database, broadcaster=None) -> AgentTeamService:
    """Initialize the singleton AgentTeamService."""
    global _service
    _service = AgentTeamService(database, broadcaster)
    return _service
