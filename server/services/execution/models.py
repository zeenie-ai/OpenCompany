"""Execution engine state models.

Based on Netflix Conductor task lifecycle and Prefect 3.0 patterns.
All models are JSON-serializable for Redis persistence and cross-runtime portability.
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


class TaskStatus(str, Enum):
    """Task execution states (Conductor-style lifecycle).

    State transitions:
        PENDING -> SCHEDULED -> RUNNING -> COMPLETED
                                       -> FAILED
                                       -> CANCELLED
        CACHED (Prefect pattern - result from cache, no execution)
    """

    PENDING = "pending"  # Created, not yet scheduled
    SCHEDULED = "scheduled"  # In task queue, waiting for worker
    RUNNING = "running"  # Worker executing
    COMPLETED = "completed"  # Success, result cached
    FAILED = "failed"  # Error, may retry
    CACHED = "cached"  # Result from cache (Prefect pattern)
    CANCELLED = "cancelled"  # User cancelled
    WAITING = "waiting"  # Waiting for external event (triggers)
    SKIPPED = "skipped"  # Skipped due to condition


class WorkflowStatus(str, Enum):
    """Workflow execution states."""

    PENDING = "pending"  # Created, not started
    RUNNING = "running"  # At least one node executing
    PAUSED = "paused"  # User paused
    COMPLETED = "completed"  # All nodes completed successfully
    FAILED = "failed"  # Execution failed
    CANCELLED = "cancelled"  # User cancelled


@dataclass
class RetryPolicy:
    """Retry configuration for node execution.

    Implements exponential backoff with configurable limits.
    Delay formula: min(initial_delay * (backoff_multiplier ^ attempt), max_delay)
    """

    max_attempts: int = 3
    initial_delay: float = 1.0  # seconds
    max_delay: float = 60.0  # seconds
    backoff_multiplier: float = 2.0
    retry_on_timeout: bool = True
    retry_on_connection_error: bool = True
    retry_on_server_error: bool = True  # 5xx errors

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay before next retry attempt.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds before next attempt
        """
        delay = self.initial_delay * (self.backoff_multiplier**attempt)
        return min(delay, self.max_delay)

    def should_retry(self, error: str, attempt: int) -> bool:
        """Determine if execution should be retried.

        Args:
            error: Error message from failed execution
            attempt: Current attempt number (0-indexed)

        Returns:
            True if should retry, False otherwise
        """
        if attempt >= self.max_attempts:
            return False

        error_lower = error.lower()

        if self.retry_on_timeout and "timeout" in error_lower:
            return True
        if self.retry_on_connection_error and ("connection" in error_lower or "connect" in error_lower):
            return True
        if self.retry_on_server_error and ("500" in error or "502" in error or "503" in error or "504" in error):
            return True

        return False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "max_attempts": self.max_attempts,
            "initial_delay": self.initial_delay,
            "max_delay": self.max_delay,
            "backoff_multiplier": self.backoff_multiplier,
            "retry_on_timeout": self.retry_on_timeout,
            "retry_on_connection_error": self.retry_on_connection_error,
            "retry_on_server_error": self.retry_on_server_error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetryPolicy":
        """Create from dict."""
        return cls(
            max_attempts=data.get("max_attempts", 3),
            initial_delay=data.get("initial_delay", 1.0),
            max_delay=data.get("max_delay", 60.0),
            backoff_multiplier=data.get("backoff_multiplier", 2.0),
            retry_on_timeout=data.get("retry_on_timeout", True),
            retry_on_connection_error=data.get("retry_on_connection_error", True),
            retry_on_server_error=data.get("retry_on_server_error", True),
        )


# Default retry policies for different node types
DEFAULT_RETRY_POLICIES: Dict[str, RetryPolicy] = {
    "httpRequest": RetryPolicy(max_attempts=3, initial_delay=2.0),
    "webhookTrigger": RetryPolicy(max_attempts=1),  # Don't retry triggers
    "whatsappReceive": RetryPolicy(max_attempts=1),  # Don't retry triggers
    "aiAgent": RetryPolicy(max_attempts=2, initial_delay=5.0, max_delay=30.0),
    "openaiChatModel": RetryPolicy(max_attempts=2, initial_delay=5.0),
    "anthropicChatModel": RetryPolicy(max_attempts=2, initial_delay=5.0),
    "googleChatModel": RetryPolicy(max_attempts=2, initial_delay=5.0),
}


def get_retry_policy(node_type: str, custom_policy: Dict = None) -> RetryPolicy:
    """Get retry policy for a node type.

    Args:
        node_type: The node type string
        custom_policy: Optional custom policy dict from node parameters

    Returns:
        RetryPolicy instance
    """
    if custom_policy:
        return RetryPolicy.from_dict(custom_policy)
    return DEFAULT_RETRY_POLICIES.get(node_type, RetryPolicy())


@dataclass
class DLQEntry:
    """Dead Letter Queue entry for failed node executions.

    Stores failed execution details for manual review and replay.
    """

    id: str
    execution_id: str
    workflow_id: str
    node_id: str
    node_type: str
    error: str
    inputs: Dict[str, Any]
    retry_count: int
    created_at: float = field(default_factory=time.time)
    last_error_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "error": self.error,
            "inputs": self.inputs,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "last_error_at": self.last_error_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DLQEntry":
        """Create from dict."""
        return cls(
            id=data["id"],
            execution_id=data["execution_id"],
            workflow_id=data["workflow_id"],
            node_id=data["node_id"],
            node_type=data["node_type"],
            error=data["error"],
            inputs=data.get("inputs", {}),
            retry_count=data.get("retry_count", 0),
            created_at=data.get("created_at", time.time()),
            last_error_at=data.get("last_error_at", time.time()),
        )

    @classmethod
    def create(cls, ctx: "ExecutionContext", node_exec: "NodeExecution", inputs: Dict[str, Any]) -> "DLQEntry":
        """Factory method to create DLQ entry from failed execution."""
        return cls(
            id=str(uuid.uuid4()),
            execution_id=ctx.execution_id,
            workflow_id=ctx.workflow_id,
            node_id=node_exec.node_id,
            node_type=node_exec.node_type,
            error=node_exec.error or "Unknown error",
            inputs=inputs,
            retry_count=node_exec.retry_count,
        )


@dataclass
class NodeExecution:
    """Tracks execution state for a single node.

    Prefect-style: includes input hash for cache lookup.
    """

    node_id: str
    node_type: str
    status: TaskStatus = TaskStatus.PENDING
    input_hash: Optional[str] = None  # For cache lookup
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    retry_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status.value,
            "input_hash": self.input_hash,
            "output": self.output,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeExecution":
        """Create from dict (Redis deserialization)."""
        return cls(
            node_id=data["node_id"],
            node_type=data["node_type"],
            status=TaskStatus(data["status"]),
            input_hash=data.get("input_hash"),
            output=data.get("output"),
            error=data.get("error"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            retry_count=data.get("retry_count", 0),
        )


@dataclass
class ExecutionContext:
    """Isolated execution context for a workflow run.

    Replaces global _deployment_running flag.
    Each workflow execution gets its own context with isolated state.

    Conductor pattern: workflow_id identifies the workflow definition,
    execution_id identifies this specific run.
    """

    execution_id: str
    workflow_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    session_id: str = "default"

    # Node states and outputs
    node_executions: Dict[str, NodeExecution] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    # DAG structure (cached for parallel batch detection)
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)

    # Execution tracking
    execution_order: List[str] = field(default_factory=list)
    current_layer: int = 0
    checkpoints: List[str] = field(default_factory=list)

    # Timing
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # Error tracking
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def create(
        cls, workflow_id: str, session_id: str = "default", nodes: List[Dict] = None, edges: List[Dict] = None
    ) -> "ExecutionContext":
        """Factory method to create new execution context.

        Supports pre-executed nodes (marked with _pre_executed=True) for
        event-driven execution where trigger nodes are already complete.

        Config nodes (memory, tools, model configs) are excluded from execution
        as they provide configuration to other nodes via special handles.

        Toolkit sub-nodes (nodes connected TO a toolkit like androidTool) are also
        excluded - they execute only when called via the toolkit's tool interface.
        """
        from constants import CONFIG_NODE_TYPES, TOOLKIT_NODE_TYPES, AI_AGENT_TYPES

        execution_id = str(uuid.uuid4())
        ctx = cls(
            execution_id=execution_id,
            workflow_id=workflow_id,
            session_id=session_id,
            nodes=nodes or [],
            edges=edges or [],
        )

        # Find toolkit sub-nodes (nodes that connect TO a toolkit node)
        # These should only execute when called via the toolkit, not as workflow nodes
        toolkit_node_ids = {n.get("id") for n in (nodes or []) if n.get("type") in TOOLKIT_NODE_TYPES}

        # Find AI Agent nodes (all agent types have config handles)
        ai_agent_node_ids = {n.get("id") for n in (nodes or []) if n.get("type") in AI_AGENT_TYPES}

        subnode_ids: set = set()
        for edge in edges or []:
            source = edge.get("source")
            target = edge.get("target")
            target_handle = edge.get("targetHandle")

            # Any node that connects TO a toolkit is a sub-node
            if target in toolkit_node_ids and source:
                subnode_ids.add(source)

            # Nodes connected to AI Agent config handles are sub-nodes
            # These handles: input-memory, input-tools, input-skill, input-teammates
            if target in ai_agent_node_ids and source and target_handle:
                if target_handle in ("input-memory", "input-tools", "input-skill", "input-teammates"):
                    subnode_ids.add(source)

        # Initialize node executions for all nodes (excluding config nodes and sub-nodes)
        for node in nodes or []:
            node_id = node.get("id")
            node_type = node.get("type", "unknown")

            # Skip config nodes - they don't execute independently
            # They provide configuration to other nodes via special handles
            if node_type in CONFIG_NODE_TYPES:
                continue

            # Skip toolkit sub-nodes - they execute only via toolkit tool calls
            if node_id in subnode_ids:
                continue

            # Check if node is pre-executed (e.g., trigger that already fired)
            if node.get("_pre_executed"):
                # Mark as COMPLETED with trigger output
                trigger_output = node.get("_trigger_output", {})
                logger.info(f"[ExecutionContext] Pre-executed node found: {node_id} (type={node_type})")
                logger.info(f"[ExecutionContext] Trigger output keys: {list(trigger_output.keys()) if trigger_output else 'empty'}")
                node_exec = NodeExecution(
                    node_id=node_id,
                    node_type=node_type,
                    status=TaskStatus.COMPLETED,
                    output=trigger_output,
                    completed_at=time.time(),
                )
                ctx.outputs[node_id] = trigger_output
                logger.info(f"[ExecutionContext] Set ctx.outputs[{node_id}] = trigger_output")
                ctx.checkpoints.append(node_id)
            else:
                node_exec = NodeExecution(
                    node_id=node_id,
                    node_type=node_type,
                )

            ctx.node_executions[node_id] = node_exec

        logger.info(f"[ExecutionContext] Created context with outputs: {list(ctx.outputs.keys())}")
        return ctx

    def get_node_status(self, node_id: str) -> Optional[TaskStatus]:
        """Get status for a specific node."""
        node_exec = self.node_executions.get(node_id)
        return node_exec.status if node_exec else None

    def set_node_status(self, node_id: str, status: TaskStatus, output: Dict = None, error: str = None) -> None:
        """Update node execution status."""
        if node_id not in self.node_executions:
            return

        node_exec = self.node_executions[node_id]
        node_exec.status = status
        self.updated_at = time.time()

        if status == TaskStatus.RUNNING:
            node_exec.started_at = time.time()
        elif status in (TaskStatus.COMPLETED, TaskStatus.CACHED):
            node_exec.completed_at = time.time()
            if output:
                node_exec.output = output
                self.outputs[node_id] = output
        elif status == TaskStatus.SKIPPED:
            # Skipped due to conditional branching - mark as completed but no output
            node_exec.completed_at = time.time()
        elif status == TaskStatus.FAILED:
            node_exec.completed_at = time.time()
            if error:
                node_exec.error = error
                self.errors.append({"node_id": node_id, "error": error, "timestamp": time.time()})

    def add_checkpoint(self, node_id: str) -> None:
        """Add checkpoint after node completion (for recovery)."""
        self.checkpoints.append(node_id)
        self.updated_at = time.time()

    def get_completed_nodes(self) -> List[str]:
        """Get list of completed node IDs."""
        return [
            node_id
            for node_id, node_exec in self.node_executions.items()
            if node_exec.status in (TaskStatus.COMPLETED, TaskStatus.CACHED, TaskStatus.SKIPPED)
        ]

    def get_pending_nodes(self) -> List[str]:
        """Get list of pending node IDs."""
        return [node_id for node_id, node_exec in self.node_executions.items() if node_exec.status == TaskStatus.PENDING]

    def all_nodes_complete(self) -> bool:
        """Check if all nodes are complete."""
        for node_exec in self.node_executions.values():
            if node_exec.status not in (TaskStatus.COMPLETED, TaskStatus.CACHED, TaskStatus.SKIPPED, TaskStatus.CANCELLED):
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict for Redis storage."""
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "session_id": self.session_id,
            "node_executions": {k: v.to_dict() for k, v in self.node_executions.items()},
            "outputs": self.outputs,
            "execution_order": self.execution_order,
            "current_layer": self.current_layer,
            "checkpoints": self.checkpoints,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "errors": self.errors,
            # Don't store full nodes/edges - too large
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], nodes: List[Dict] = None, edges: List[Dict] = None) -> "ExecutionContext":
        """Create from dict (Redis deserialization)."""
        ctx = cls(
            execution_id=data["execution_id"],
            workflow_id=data["workflow_id"],
            status=WorkflowStatus(data["status"]),
            session_id=data.get("session_id", "default"),
            nodes=nodes or [],
            edges=edges or [],
            execution_order=data.get("execution_order", []),
            current_layer=data.get("current_layer", 0),
            checkpoints=data.get("checkpoints", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            errors=data.get("errors", []),
        )

        # Restore node executions
        for node_id, node_data in data.get("node_executions", {}).items():
            ctx.node_executions[node_id] = NodeExecution.from_dict(node_data)

        # Restore outputs
        ctx.outputs = data.get("outputs", {})

        return ctx

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_str: str, nodes: List[Dict] = None, edges: List[Dict] = None) -> "ExecutionContext":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data, nodes, edges)


def hash_inputs(inputs: Dict[str, Any]) -> str:
    """Generate deterministic hash of inputs for cache key (Prefect pattern).

    Args:
        inputs: Dictionary of input parameters

    Returns:
        SHA256 hash of canonicalized inputs
    """
    # Canonical JSON (sorted keys, no extra whitespace)
    canonical = json.dumps(inputs, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def generate_cache_key(execution_id: str, node_id: str, inputs: Dict[str, Any]) -> str:
    """Generate cache key for node result (Prefect pattern).

    Format: result:{execution_id}:{node_id}:{input_hash}
    """
    input_hash = hash_inputs(inputs)
    return f"result:{execution_id}:{node_id}:{input_hash}"
