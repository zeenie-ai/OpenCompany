"""Deployment State - Immutable state snapshot for event-driven deployment."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional


@dataclass
class DeploymentState:
    """Immutable deployment state."""

    deployment_id: str
    workflow_id: str
    is_running: bool
    nodes: List[Dict]
    edges: List[Dict]
    session_id: str
    user_id: str = "owner"
    # Conversation scope. Both must be present for MachinaWorkflow to
    # stamp a Context generation on node activities; legacy/uncontrolled
    # deployments leave them at zero and run without a Context store.
    graph_version: int = 0
    generation: int = 0
    # Human-readable slug resolved from DB at deploy time. Used to
    # prefix Temporal workflow IDs (listener + per-firing run) so
    # operators browsing the Temporal Web UI can identify runs by
    # name instead of by UUID. Falls back to ``workflow_id`` when the
    # DB row is missing (one-off deploys, tests).
    workflow_slug: str = ""
    parameter_snapshot: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)
    deployed_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "workflow_id": self.workflow_id,
            "workflow_slug": self.workflow_slug,
            "is_running": self.is_running,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "graph_version": self.graph_version,
            "generation": self.generation,
            "settings": self.settings,
            "deployed_at": self.deployed_at,
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }


@dataclass
class TriggerInfo:
    """Info about a registered trigger."""

    node_id: str
    node_type: str
    job_id: Optional[str] = None  # For cron triggers
    fired: bool = False  # For start triggers

    def to_dict(self) -> Dict[str, Any]:
        d = {"type": self.node_type, "node_id": self.node_id}
        if self.job_id:
            d["job_id"] = self.job_id
        if self.fired:
            d["fired"] = True
        return d
