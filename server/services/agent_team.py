"""Agent Team Service - Claude SDK Agent Teams pattern.

Coordinates multi-agent teams with shared task lists and messaging.
Teams are scoped to specific workflow executions.
"""

import uuid
import hashlib
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
        return await self.database.get_team_stats(team_id)

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
        """Mark a task as completed."""
        # Get task to find assigned agent
        tasks = await self.database.get_team_tasks(team_id)
        task = next((t for t in tasks if t["id"] == task_id), None)

        success = await self.database.complete_task(task_id, result)

        if success:
            # Update member status back to idle
            if task and task.get("assigned_to"):
                await self.database.update_member_status(team_id, task["assigned_to"], "idle")

            if self.broadcaster:
                await self.broadcaster.broadcast_team_event(team_id, "task_completed", {"task_id": task_id, "result": result})

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
        """Check if all tasks are completed/failed."""
        tasks = await self.database.get_team_tasks(team_id)
        if not tasks:
            return True
        return all(t["status"] in ("completed", "failed", "skipped") for t in tasks)

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
