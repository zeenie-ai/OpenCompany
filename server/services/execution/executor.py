"""Workflow executor with Conductor decide pattern and parallel execution.

Implements:
- Conductor-style workflow_decide() for orchestration
- Prefect-style task caching for idempotency
- Fork/Join parallel execution with asyncio.wait (FIRST_COMPLETED pattern)
- Dynamic workflow branching at runtime
- Proper handling of long-running trigger nodes in parallel batches
"""

import asyncio
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, Awaitable, Set

from core.logging import get_logger
from constants import WORKFLOW_TRIGGER_TYPES
from .models import (
    ExecutionContext,
    TaskStatus,
    WorkflowStatus,
    NodeExecution,
    hash_inputs,
    get_retry_policy,
)
from .cache import ExecutionCache
from .conditions import evaluate_condition
from .dlq import create_dlq_handler

logger = get_logger(__name__)


def is_trigger_node(node_type: str) -> bool:
    """Check if a node type is a trigger node (workflow starting point).

    Trigger nodes have no input handles and serve as entry points for workflows.
    They are identified by WORKFLOW_TRIGGER_TYPES in constants.py.

    Args:
        node_type: The node type string

    Returns:
        True if the node is a trigger type
    """
    return node_type in WORKFLOW_TRIGGER_TYPES


class WorkflowExecutor:
    """Executes workflows using Conductor decide pattern with parallel execution.

    Features:
    - Isolated ExecutionContext per workflow run
    - Parallel execution of independent nodes (Fork/Join)
    - Result caching for idempotency (Prefect pattern)
    - Distributed locking to prevent race conditions
    - Event history for debugging and recovery
    """

    def __init__(
        self,
        cache: ExecutionCache,
        node_executor: Callable[[str, str, Dict, Dict], Awaitable[Dict]],
        status_callback: Callable[[str, str, Dict], Awaitable[None]] = None,
        dlq_enabled: bool = False,
    ):
        """Initialize executor.

        Args:
            cache: ExecutionCache for Redis persistence
            node_executor: Async function to execute a single node
                          Signature: async def execute(node_id, node_type, params, context) -> result
            status_callback: Optional async callback for status updates
                            Signature: async def callback(node_id, status, data)
            dlq_enabled: Whether to add failed nodes to Dead Letter Queue
        """
        self.cache = cache
        self.node_executor = node_executor
        self.status_callback = status_callback

        # Create DLQ handler (modular - uses Null Object pattern when disabled)
        self.dlq = create_dlq_handler(cache, enabled=dlq_enabled)

        # Active executions (in-memory for fast lookup)
        self._active_contexts: Dict[str, ExecutionContext] = {}

    # =========================================================================
    # EXECUTION ENTRY POINTS
    # =========================================================================

    async def execute_workflow(
        self, workflow_id: str, nodes: List[Dict], edges: List[Dict], session_id: str = "default", enable_caching: bool = True
    ) -> Dict[str, Any]:
        """Execute a workflow with parallel node execution.

        Args:
            workflow_id: Workflow identifier
            nodes: List of workflow nodes
            edges: List of edges connecting nodes
            session_id: Session identifier
            enable_caching: Whether to use result caching

        Returns:
            Execution result dict
        """
        start_time = time.time()

        # Create isolated execution context
        ctx = ExecutionContext.create(
            workflow_id=workflow_id,
            session_id=session_id,
            nodes=nodes,
            edges=edges,
        )

        # Compute execution layers (for parallel batches)
        ctx.execution_order = self._compute_execution_layers(nodes, edges)

        logger.info(
            "Starting workflow execution",
            execution_id=ctx.execution_id,
            workflow_id=workflow_id,
            node_count=len(nodes),
            layers=len(ctx.execution_order),
        )

        # Track in memory
        self._active_contexts[ctx.execution_id] = ctx

        # Persist initial state
        ctx.status = WorkflowStatus.RUNNING
        ctx.started_at = time.time()
        await self.cache.save_execution_state(ctx)

        # Add workflow_started event
        await self.cache.add_event(
            ctx.execution_id,
            "workflow_started",
            {
                "workflow_id": workflow_id,
                "node_count": len(nodes),
            },
        )

        try:
            # Run the decide loop
            await self._workflow_decide(ctx, enable_caching)

            # Determine final status
            if ctx.all_nodes_complete():
                ctx.status = WorkflowStatus.COMPLETED
            elif ctx.errors:
                ctx.status = WorkflowStatus.FAILED

            ctx.completed_at = time.time()
            await self.cache.save_execution_state(ctx)

            # Add workflow_completed event
            await self.cache.add_event(
                ctx.execution_id,
                "workflow_completed",
                {
                    "status": ctx.status.value,
                    "completed_nodes": len(ctx.get_completed_nodes()),
                    "execution_time": ctx.completed_at - ctx.started_at,
                },
            )

            return {
                "success": ctx.status == WorkflowStatus.COMPLETED,
                "execution_id": ctx.execution_id,
                "status": ctx.status.value,
                "nodes_executed": ctx.get_completed_nodes(),
                "outputs": ctx.outputs,
                "errors": ctx.errors,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except asyncio.CancelledError:
            ctx.status = WorkflowStatus.CANCELLED
            ctx.completed_at = time.time()
            await self.cache.save_execution_state(ctx)
            await self.cache.add_event(ctx.execution_id, "workflow_cancelled", {})
            return {
                "success": False,
                "execution_id": ctx.execution_id,
                "status": "cancelled",
                "error": "Cancelled by user",
                "execution_time": time.time() - start_time,
            }

        except Exception as e:
            logger.error("Workflow execution failed", execution_id=ctx.execution_id, error=str(e))
            ctx.status = WorkflowStatus.FAILED
            ctx.errors.append({"error": str(e), "timestamp": time.time()})
            await self.cache.save_execution_state(ctx)
            await self.cache.add_event(
                ctx.execution_id,
                "workflow_failed",
                {
                    "error": str(e),
                },
            )
            return {
                "success": False,
                "execution_id": ctx.execution_id,
                "status": "failed",
                "error": str(e),
                "execution_time": time.time() - start_time,
            }

        finally:
            # Cleanup
            self._active_contexts.pop(ctx.execution_id, None)

    async def cancel_execution(self, execution_id: str) -> bool:
        """Cancel a running execution.

        Args:
            execution_id: Execution to cancel

        Returns:
            True if cancelled successfully
        """
        ctx = self._active_contexts.get(execution_id)
        if ctx:
            ctx.status = WorkflowStatus.CANCELLED
            for node_exec in ctx.node_executions.values():
                if node_exec.status in (TaskStatus.PENDING, TaskStatus.SCHEDULED, TaskStatus.RUNNING, TaskStatus.WAITING):
                    node_exec.status = TaskStatus.CANCELLED
            await self.cache.save_execution_state(ctx)
            logger.info("Execution cancelled", execution_id=execution_id)
            return True
        return False

    # =========================================================================
    # CONDUCTOR DECIDE PATTERN
    # =========================================================================

    async def _workflow_decide(self, ctx: ExecutionContext, enable_caching: bool = True) -> None:
        """Core orchestration loop - Conductor's decide pattern.

        Evaluates current state, finds ready nodes, executes them in parallel,
        then recurses until all nodes complete or error occurs.

        Args:
            ctx: ExecutionContext to process
            enable_caching: Whether to use result caching
        """
        # Distributed lock prevents concurrent decides for same execution
        try:
            async with self.cache.distributed_lock(f"execution:{ctx.execution_id}:decide", timeout=60):
                await self._decide_iteration(ctx, enable_caching)
        except TimeoutError:
            logger.warning("Could not acquire decide lock", execution_id=ctx.execution_id)
            # Retry after short delay
            await asyncio.sleep(0.5)
            await self._workflow_decide(ctx, enable_caching)

    async def _decide_iteration(self, ctx: ExecutionContext, enable_caching: bool) -> None:
        """Continuous scheduling loop - Temporal/Conductor pattern.

        When any node completes, immediately check for newly-ready dependents
        and start them without waiting for entire layer to complete.

        Example: Cron3 (5s) completes -> immediately start WS3,
        even while Cron1 (20s) is still running.
        """
        # Check if cancelled
        if ctx.status == WorkflowStatus.CANCELLED:
            return

        # Find initial ready nodes
        ready_nodes = self._find_ready_nodes(ctx)

        if not ready_nodes:
            if ctx.all_nodes_complete():
                logger.info("All nodes complete", execution_id=ctx.execution_id)
            else:
                pending = ctx.get_pending_nodes()
                if pending:
                    logger.warning("Stuck: pending nodes with unsatisfied deps", execution_id=ctx.execution_id, pending=pending)
            return

        logger.info(
            "Starting continuous execution",
            execution_id=ctx.execution_id,
            initial_batch=len(ready_nodes),
            nodes=[n.node_id for n in ready_nodes],
        )

        # Execute with continuous scheduling - new pattern
        await self._execute_with_continuous_scheduling(ctx, ready_nodes, enable_caching)

        # Save final state
        await self.cache.save_execution_state(ctx)

    # =========================================================================
    # CONTINUOUS SCHEDULING (Temporal/Conductor Pattern)
    # =========================================================================

    async def _execute_with_continuous_scheduling(
        self, ctx: ExecutionContext, initial_nodes: List[NodeExecution], enable_caching: bool
    ) -> None:
        """Execute workflow with continuous scheduling.

        Modern pattern: When any node completes, immediately check for and start
        newly-ready dependent nodes. This enables true parallel pipelines where
        each path progresses independently.

        Uses asyncio.wait(FIRST_COMPLETED) to process completions immediately.

        Args:
            ctx: ExecutionContext
            initial_nodes: Initial batch of ready nodes
            enable_caching: Whether to use result caching
        """
        # Track all running tasks: task -> NodeExecution
        task_to_node: Dict[asyncio.Task, NodeExecution] = {}
        pending_tasks: Set[asyncio.Task] = set()
        workflow_failed = False

        def create_node_task(node: NodeExecution) -> asyncio.Task:
            """Create and track a task for node execution."""
            node.status = TaskStatus.SCHEDULED
            task = asyncio.create_task(self._execute_node_with_retry(ctx, node, enable_caching), name=f"node_{node.node_id}")
            task_to_node[task] = node
            pending_tasks.add(task)
            return task

        # Start initial nodes
        for node in initial_nodes:
            create_node_task(node)
            await self._notify_status(node.node_id, "scheduled", {})
            logger.info("Scheduled node", node_id=node.node_id)

        # Process completions and schedule new nodes continuously
        while pending_tasks and not workflow_failed:
            if ctx.status == WorkflowStatus.CANCELLED:
                # Cancel all pending tasks
                for task in pending_tasks:
                    task.cancel()
                break

            # Wait for ANY task to complete
            done, pending_tasks = await asyncio.wait(pending_tasks, return_when=asyncio.FIRST_COMPLETED)

            # Process each completed task
            for task in done:
                node = task_to_node[task]
                newly_ready = []

                try:
                    result = task.result()

                    if isinstance(result, Exception):
                        node.status = TaskStatus.FAILED
                        node.error = str(result)
                        node.completed_at = time.time()
                        ctx.errors.append(
                            {
                                "node_id": node.node_id,
                                "error": str(result),
                                "timestamp": time.time(),
                            }
                        )
                        await self._notify_status(node.node_id, "error", {"error": str(result)})
                        logger.error("Node failed", node_id=node.node_id, error=str(result))
                        workflow_failed = True

                    elif result.get("retries_exhausted"):
                        node.status = TaskStatus.FAILED
                        node.error = result.get("error", "Unknown error")
                        node.completed_at = time.time()
                        ctx.errors.append(
                            {
                                "node_id": node.node_id,
                                "error": node.error,
                                "retries_exhausted": True,
                                "timestamp": time.time(),
                            }
                        )
                        workflow_failed = True

                    elif not result.get("success"):
                        node.status = TaskStatus.FAILED
                        node.error = result.get("error", "Unknown error")
                        node.completed_at = time.time()
                        ctx.errors.append(
                            {
                                "node_id": node.node_id,
                                "error": node.error,
                                "timestamp": time.time(),
                            }
                        )
                        await self._notify_status(node.node_id, "error", {"error": node.error})
                        logger.error("Node failed", node_id=node.node_id, error=node.error)
                        workflow_failed = True

                    else:
                        # Success - checkpoint and find newly ready nodes
                        ctx.add_checkpoint(node.node_id)
                        logger.info("Node completed", node_id=node.node_id)

                        # Find nodes that are now ready (their dependencies just completed)
                        newly_ready = self._find_ready_nodes(ctx)

                except asyncio.CancelledError:
                    node.status = TaskStatus.CANCELLED
                    node.completed_at = time.time()
                    logger.info("Node cancelled", node_id=node.node_id)

                except Exception as e:
                    node.status = TaskStatus.FAILED
                    node.error = str(e)
                    node.completed_at = time.time()
                    ctx.errors.append(
                        {
                            "node_id": node.node_id,
                            "error": str(e),
                            "timestamp": time.time(),
                        }
                    )
                    await self._notify_status(node.node_id, "error", {"error": str(e)})
                    logger.error("Node exception", node_id=node.node_id, error=str(e))
                    workflow_failed = True

                # Schedule newly ready nodes immediately
                if newly_ready and not workflow_failed:
                    for ready_node in newly_ready:
                        create_node_task(ready_node)
                        await self._notify_status(ready_node.node_id, "scheduled", {})
                        logger.info("Scheduled dependent node", node_id=ready_node.node_id, triggered_by=node.node_id)

            # Periodic state save
            await self.cache.save_execution_state(ctx)

        # Handle workflow failure - cancel remaining tasks
        if workflow_failed and pending_tasks:
            logger.info("Workflow failed, cancelling remaining tasks", pending_count=len(pending_tasks))

            for task in pending_tasks:
                task.cancel()

            # Wait for cancelled tasks
            if pending_tasks:
                cancelled_done, _ = await asyncio.wait(pending_tasks, return_when=asyncio.ALL_COMPLETED)

                for task in cancelled_done:
                    node = task_to_node.get(task)
                    if node and node.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                        node.status = TaskStatus.CANCELLED
                        node.completed_at = time.time()

            ctx.status = WorkflowStatus.FAILED

    # =========================================================================
    # PARALLEL EXECUTION (Legacy - Fork/Join with FIRST_COMPLETED pattern)
    # =========================================================================

    async def _execute_parallel_nodes(self, ctx: ExecutionContext, nodes: List[NodeExecution], enable_caching: bool) -> None:
        """Execute multiple nodes in parallel using asyncio.wait with FIRST_COMPLETED.

        Uses the standard asyncio pattern for mixed task types:
        - Regular nodes complete quickly
        - Trigger nodes wait indefinitely for external events
        - If a regular node fails, cancel remaining trigger nodes immediately

        This follows Python asyncio best practices:
        https://docs.python.org/3/library/asyncio-task.html#asyncio.wait

        Args:
            ctx: ExecutionContext
            nodes: List of NodeExecution to run in parallel
            enable_caching: Whether to use result caching
        """
        # Mark all as scheduled
        for node in nodes:
            node.status = TaskStatus.SCHEDULED
            await self._notify_status(node.node_id, "scheduled", {})

        # Create named tasks for parallel execution
        # Using dict to track node -> task mapping for proper result handling
        node_to_task: Dict[str, asyncio.Task] = {}
        task_to_node: Dict[asyncio.Task, NodeExecution] = {}

        for node in nodes:
            task = asyncio.create_task(self._execute_node_with_retry(ctx, node, enable_caching), name=f"node_{node.node_id}")
            node_to_task[node.node_id] = task
            task_to_node[task] = node

        pending: Set[asyncio.Task] = set(node_to_task.values())
        workflow_failed = False

        # Process tasks as they complete using FIRST_COMPLETED pattern
        while pending:
            # Wait for any task to complete
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            # Process completed tasks
            for task in done:
                node = task_to_node[task]

                try:
                    result = task.result()

                    if isinstance(result, Exception):
                        # Task raised exception
                        node.status = TaskStatus.FAILED
                        node.error = str(result)
                        node.completed_at = time.time()
                        ctx.errors.append(
                            {
                                "node_id": node.node_id,
                                "error": str(result),
                                "timestamp": time.time(),
                            }
                        )
                        await self._notify_status(node.node_id, "error", {"error": str(result)})
                        logger.error("Parallel node failed", node_id=node.node_id, error=str(result))
                        workflow_failed = True

                    elif result.get("retries_exhausted"):
                        # Node failed after all retries - already in DLQ
                        node.status = TaskStatus.FAILED
                        node.error = result.get("error", "Unknown error")
                        node.completed_at = time.time()
                        ctx.errors.append(
                            {
                                "node_id": node.node_id,
                                "error": node.error,
                                "retries_exhausted": True,
                                "timestamp": time.time(),
                            }
                        )
                        workflow_failed = True

                    elif not result.get("success"):
                        # Node returned failure without exhausting retries
                        node.status = TaskStatus.FAILED
                        node.error = result.get("error", "Unknown error")
                        node.completed_at = time.time()
                        ctx.errors.append(
                            {
                                "node_id": node.node_id,
                                "error": node.error,
                                "timestamp": time.time(),
                            }
                        )
                        await self._notify_status(node.node_id, "error", {"error": node.error})
                        logger.error("Parallel node failed", node_id=node.node_id, error=node.error)
                        workflow_failed = True

                except asyncio.CancelledError:
                    # Task was cancelled (by us or externally)
                    node.status = TaskStatus.CANCELLED
                    node.completed_at = time.time()
                    logger.info("Parallel node cancelled", node_id=node.node_id)

                except Exception as e:
                    # Unexpected exception from task.result()
                    node.status = TaskStatus.FAILED
                    node.error = str(e)
                    node.completed_at = time.time()
                    ctx.errors.append(
                        {
                            "node_id": node.node_id,
                            "error": str(e),
                            "timestamp": time.time(),
                        }
                    )
                    await self._notify_status(node.node_id, "error", {"error": str(e)})
                    logger.error("Parallel node exception", node_id=node.node_id, error=str(e))
                    workflow_failed = True

            # If workflow failed, cancel remaining pending tasks
            # This prevents trigger nodes from blocking forever when a regular node fails
            if workflow_failed and pending:
                logger.info("Workflow failed, cancelling remaining tasks", pending_count=len(pending))

                for task in pending:
                    task.cancel()

                # Wait for cancelled tasks to finish
                if pending:
                    cancelled_done, _ = await asyncio.wait(pending, return_when=asyncio.ALL_COMPLETED)

                    # Mark cancelled nodes
                    for task in cancelled_done:
                        node = task_to_node[task]
                        if node.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                            node.status = TaskStatus.CANCELLED
                            node.completed_at = time.time()
                            logger.info("Cancelled pending node", node_id=node.node_id)

                pending = set()  # All done now

        # Mark workflow as failed if any node failed
        if workflow_failed:
            ctx.status = WorkflowStatus.FAILED

    async def _execute_single_node(self, ctx: ExecutionContext, node: NodeExecution, enable_caching: bool) -> None:
        """Execute a single node with retry logic.

        Args:
            ctx: ExecutionContext
            node: NodeExecution to run
            enable_caching: Whether to use result caching
        """
        node.status = TaskStatus.SCHEDULED
        await self._notify_status(node.node_id, "scheduled", {})

        try:
            result = await self._execute_node_with_retry(ctx, node, enable_caching)

            if result.get("retries_exhausted"):
                # Node failed after all retries - already in DLQ
                node.status = TaskStatus.FAILED
                node.error = result.get("error", "Unknown error")
                node.completed_at = time.time()
                ctx.errors.append(
                    {
                        "node_id": node.node_id,
                        "error": node.error,
                        "retries_exhausted": True,
                        "timestamp": time.time(),
                    }
                )
                ctx.status = WorkflowStatus.FAILED

        except Exception as e:
            node.status = TaskStatus.FAILED
            node.error = str(e)
            node.completed_at = time.time()
            ctx.errors.append(
                {
                    "node_id": node.node_id,
                    "error": str(e),
                    "timestamp": time.time(),
                }
            )
            await self._notify_status(node.node_id, "error", {"error": str(e)})
            ctx.status = WorkflowStatus.FAILED

    # =========================================================================
    # RETRY LOGIC
    # =========================================================================

    async def _execute_node_with_retry(self, ctx: ExecutionContext, node: NodeExecution, enable_caching: bool) -> Dict[str, Any]:
        """Execute node with retry logic and DLQ on final failure.

        Uses exponential backoff retry policy based on node type.
        On exhausted retries, adds entry to Dead Letter Queue.

        Args:
            ctx: ExecutionContext
            node: NodeExecution to run
            enable_caching: Whether to use result caching

        Returns:
            Execution result
        """
        # Get retry policy for this node type
        node_data = self._get_node_data(ctx, node.node_id)
        custom_policy = node_data.get("parameters", {}).get("retryPolicy")
        retry_policy = get_retry_policy(node.node_type, custom_policy)

        last_error = None
        inputs = self._gather_node_inputs(ctx, node.node_id)

        for attempt in range(retry_policy.max_attempts):
            try:
                node.retry_count = attempt
                result = await self._execute_node_with_caching(ctx, node, enable_caching)

                # Success - return result
                if result.get("success"):
                    return result

                # Execution returned failure (not exception)
                error = result.get("error", "Unknown error")
                last_error = error

                # Check if we should retry
                if retry_policy.should_retry(error, attempt + 1):
                    delay = retry_policy.calculate_delay(attempt)
                    logger.info(
                        "Retrying node after failure",
                        node_id=node.node_id,
                        attempt=attempt + 1,
                        max_attempts=retry_policy.max_attempts,
                        delay=delay,
                        error=error[:100],
                    )

                    await self._notify_status(
                        node.node_id,
                        "retrying",
                        {
                            "attempt": attempt + 1,
                            "max_attempts": retry_policy.max_attempts,
                            "delay": delay,
                            "error": error,
                        },
                    )

                    await asyncio.sleep(delay)

                    # Reset node status for retry
                    node.status = TaskStatus.PENDING
                    node.error = None
                    continue
                else:
                    # Not retryable, break out
                    break

            except asyncio.CancelledError:
                raise  # Propagate cancellation
            except Exception as e:
                last_error = str(e)
                logger.warning("Node execution exception", node_id=node.node_id, attempt=attempt + 1, error=last_error)

                # Check if we should retry
                if retry_policy.should_retry(last_error, attempt + 1):
                    delay = retry_policy.calculate_delay(attempt)
                    logger.info("Retrying node after exception", node_id=node.node_id, attempt=attempt + 1, delay=delay)

                    await asyncio.sleep(delay)
                    node.status = TaskStatus.PENDING
                    node.error = None
                    continue
                else:
                    break

        # All retries exhausted - add to DLQ (handler is no-op if disabled)
        await self.dlq.add_failed_node(ctx, node, inputs, last_error or "Unknown error")

        # Return failure result
        return {
            "success": False,
            "error": last_error or "Unknown error",
            "retries_exhausted": True,
            "retry_count": node.retry_count,
        }

    # =========================================================================
    # CACHED NODE EXECUTION (Prefect pattern)
    # =========================================================================

    async def _execute_node_with_caching(self, ctx: ExecutionContext, node: NodeExecution, enable_caching: bool) -> Dict[str, Any]:
        """Execute node with result caching (Prefect pattern).

        Args:
            ctx: ExecutionContext
            node: NodeExecution to run
            enable_caching: Whether to check cache

        Returns:
            Execution result
        """
        # Get node parameters and inputs
        node_data = self._get_node_data(ctx, node.node_id)
        inputs = self._gather_node_inputs(ctx, node.node_id)

        # Check cache first (Prefect pattern)
        if enable_caching:
            cached_result = await self.cache.get_cached_result(ctx.execution_id, node.node_id, inputs)
            if cached_result:
                logger.info("Cache hit", node_id=node.node_id)
                node.status = TaskStatus.CACHED
                node.output = cached_result
                node.input_hash = hash_inputs(inputs)
                node.completed_at = time.time()
                ctx.outputs[node.node_id] = cached_result
                await self._notify_status(node.node_id, "success", {"cached": True, **cached_result})
                await self.cache.add_event(
                    ctx.execution_id,
                    "node_cached",
                    {
                        "node_id": node.node_id,
                    },
                )
                return cached_result

        # Execute node
        node.status = TaskStatus.RUNNING
        node.started_at = time.time()
        node.input_hash = hash_inputs(inputs)
        await self._notify_status(node.node_id, "executing", {})
        await self.cache.add_event(
            ctx.execution_id,
            "node_started",
            {
                "node_id": node.node_id,
                "node_type": node.node_type,
            },
        )

        # Update heartbeat (for crash detection)
        await self.cache.update_heartbeat(ctx.execution_id, node.node_id)

        # Build execution context for node handler
        # workflow_id is included for per-workflow status scoping (n8n pattern)
        logger.info(f"[Executor] Building context for {node.node_id}, ctx.outputs keys: {list(ctx.outputs.keys())}")
        exec_context = {
            "nodes": ctx.nodes,
            "edges": ctx.edges,
            "session_id": ctx.session_id,
            "execution_id": ctx.execution_id,
            "workflow_id": ctx.workflow_id,  # For per-workflow status broadcasts
            "start_time": node.started_at,
            "outputs": ctx.outputs,  # Previous node outputs
        }
        logger.info(f"[Executor] exec_context['outputs'] keys: {list(exec_context['outputs'].keys())}")

        # Call the actual node executor
        result = await self.node_executor(node.node_id, node.node_type, node_data.get("parameters", {}), exec_context)

        # Process result
        if result.get("success"):
            node.status = TaskStatus.COMPLETED
            node.output = result.get("result", {})
            node.completed_at = time.time()
            ctx.outputs[node.node_id] = node.output

            # Cache result (Prefect pattern)
            if enable_caching:
                await self.cache.set_cached_result(ctx.execution_id, node.node_id, inputs, node.output)

            await self._notify_status(node.node_id, "success", node.output)
            await self.cache.add_event(
                ctx.execution_id,
                "node_completed",
                {
                    "node_id": node.node_id,
                    "execution_time": node.completed_at - node.started_at,
                },
            )
        else:
            node.status = TaskStatus.FAILED
            node.error = result.get("error", "Unknown error")
            node.completed_at = time.time()
            ctx.errors.append(
                {
                    "node_id": node.node_id,
                    "error": node.error,
                    "timestamp": time.time(),
                }
            )

            await self._notify_status(node.node_id, "error", {"error": node.error})
            await self.cache.add_event(
                ctx.execution_id,
                "node_failed",
                {
                    "node_id": node.node_id,
                    "error": node.error,
                },
            )

            # Mark workflow as failed
            ctx.status = WorkflowStatus.FAILED

        return result

    # =========================================================================
    # DAG ANALYSIS
    # =========================================================================

    def _compute_execution_layers(self, nodes: List[Dict], edges: List[Dict]) -> List[List[str]]:
        """Compute execution layers for parallel execution.

        Nodes in the same layer have no dependencies on each other
        and can execute in parallel. Layer 0 contains trigger nodes
        (workflow starting points with no input handles).

        Following n8n pattern: Trigger nodes are the starting point of every
        workflow. They listen for specific events/conditions and initiate
        the execution of the entire workflow.

        Config nodes and toolkit sub-nodes are excluded from layers since
        they don't execute as independent workflow nodes.

        Args:
            nodes: List of workflow nodes
            edges: List of edges

        Returns:
            List of layers, where each layer is a list of node IDs
        """
        from constants import CONFIG_NODE_TYPES, TOOLKIT_NODE_TYPES, AI_AGENT_TYPES

        # Build node type lookup for trigger detection
        node_types: Dict[str, str] = {node["id"]: node.get("type", "unknown") for node in nodes}

        # Find toolkit sub-nodes (nodes that connect TO a toolkit)
        toolkit_node_ids = {n.get("id") for n in nodes if n.get("type") in TOOLKIT_NODE_TYPES}

        # Find AI Agent nodes (all agent types have config handles)
        ai_agent_node_ids = {n.get("id") for n in nodes if n.get("type") in AI_AGENT_TYPES}

        subnode_ids: set = set()
        for edge in edges:
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

        # Filter out config nodes and sub-nodes from execution
        excluded_ids = set()
        for node in nodes:
            node_id = node.get("id")
            node_type = node.get("type", "unknown")
            if node_type in CONFIG_NODE_TYPES or node_id in subnode_ids:
                excluded_ids.add(node_id)

        # Build adjacency and in-degree maps (excluding filtered nodes)
        in_degree: Dict[str, int] = defaultdict(int)
        adjacency: Dict[str, List[str]] = defaultdict(list)
        node_ids = {node["id"] for node in nodes if node["id"] not in excluded_ids}

        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source in node_ids and target in node_ids:
                adjacency[source].append(target)
                in_degree[target] += 1

        # Initialize in-degree for all nodes
        for node_id in node_ids:
            if node_id not in in_degree:
                in_degree[node_id] = 0

        # Kahn's algorithm for topological sort with layers
        layers = []
        remaining = set(node_ids)
        is_first_layer = True

        while remaining:
            # Find all nodes with in-degree 0 (no dependencies)
            layer = [node_id for node_id in remaining if in_degree[node_id] == 0]

            if not layer:
                # Cycle detected or stuck
                logger.warning("Cycle detected or no start nodes", remaining=list(remaining))
                # Add remaining as single layer to avoid infinite loop
                layers.append(list(remaining))
                break

            # For layer 0, validate that starting nodes are trigger nodes
            if is_first_layer:
                trigger_nodes = []
                non_trigger_nodes = []

                for node_id in layer:
                    node_type = node_types.get(node_id, "unknown")
                    if is_trigger_node(node_type):
                        trigger_nodes.append(node_id)
                    else:
                        non_trigger_nodes.append(node_id)
                        logger.warning(
                            "Non-trigger node found at graph entry point",
                            node_id=node_id,
                            node_type=node_type,
                            expected_types=list(WORKFLOW_TRIGGER_TYPES),
                        )

                # Log trigger node identification
                if trigger_nodes:
                    logger.info(
                        "Identified trigger nodes as workflow starting points",
                        trigger_count=len(trigger_nodes),
                        trigger_nodes=[f"{nid[:8]}({node_types.get(nid)})" for nid in trigger_nodes],
                    )

                is_first_layer = False

            layers.append(layer)

            # Remove layer nodes and update in-degrees
            for node_id in layer:
                remaining.remove(node_id)
                for successor in adjacency[node_id]:
                    in_degree[successor] -= 1

        logger.debug("Computed execution layers", layer_count=len(layers), layers=[[n[:8] for n in layer] for layer in layers])

        return layers

    def _find_ready_nodes(self, ctx: ExecutionContext) -> List[NodeExecution]:
        """Find nodes ready to execute (dependencies satisfied + conditions met).

        A node is ready if:
        - Status is PENDING
        - Not disabled (n8n-style disable feature)
        - All upstream nodes are COMPLETED, CACHED, or SKIPPED
        - Edge conditions (if any) evaluate to True based on upstream outputs

        Supports runtime conditional branching (Prefect-style dynamic workflows).

        Args:
            ctx: ExecutionContext

        Returns:
            List of NodeExecution ready to run
        """
        from constants import CONFIG_NODE_TYPES

        # Build set of completed nodes
        completed = set(ctx.get_completed_nodes())

        # Build map of node_id -> node_type for config node detection
        node_types: Dict[str, str] = {}
        for node in ctx.nodes:
            node_types[node.get("id", "")] = node.get("type", "unknown")

        # Build dependency map and track conditional edges
        # Skip edges from config nodes (they don't execute, provide config only)
        dependencies: Dict[str, Set[str]] = defaultdict(set)
        conditional_edges: Dict[str, List[Dict]] = defaultdict(list)  # target -> edges with conditions

        for edge in ctx.edges:
            target = edge.get("target")
            source = edge.get("source")
            if target and source:
                # Skip edges from config nodes - they provide configuration, not execution dependencies
                source_type = node_types.get(source, "unknown")
                if source_type in CONFIG_NODE_TYPES:
                    continue

                dependencies[target].add(source)
                # Track edges with conditions for evaluation
                if edge.get("data", {}).get("condition"):
                    conditional_edges[target].append(edge)

        # Find ready nodes
        ready = []
        for node_id, node_exec in ctx.node_executions.items():
            if node_exec.status != TaskStatus.PENDING:
                continue

            # Check if all dependencies are satisfied
            deps = dependencies.get(node_id, set())
            if not deps <= completed:  # Not all deps completed
                continue

            # Check if node is disabled (n8n-style disable)
            node_data = self._get_node_data(ctx, node_id)
            if node_data.get("data", {}).get("disabled"):
                node_exec.status = TaskStatus.SKIPPED
                node_exec.completed_at = time.time()
                logger.debug("Skipping disabled node", node_id=node_id)
                # Notify status callback about skipped node
                asyncio.create_task(self._notify_status(node_id, "skipped", {"disabled": True}))
                continue

            # Check conditional edges for this node
            if node_id in conditional_edges:
                # Has conditional incoming edges - evaluate them
                conditions_met = self._evaluate_incoming_conditions(ctx, node_id, conditional_edges[node_id])
                if not conditions_met:
                    # Mark as SKIPPED if conditions not met and all deps done
                    node_exec.status = TaskStatus.SKIPPED
                    logger.info("Node skipped due to unmet conditions", node_id=node_id)
                    continue

            ready.append(node_exec)

        return ready

    def _evaluate_incoming_conditions(self, ctx: ExecutionContext, target_node_id: str, edges: List[Dict]) -> bool:
        """Evaluate conditions on incoming edges to determine if node should run.

        Args:
            ctx: ExecutionContext
            target_node_id: The node we're checking
            edges: Incoming edges with conditions

        Returns:
            True if at least one conditional edge evaluates to True
        """
        for edge in edges:
            source_id = edge.get("source")
            condition = edge.get("data", {}).get("condition")

            if not condition:
                continue

            # Get output from source node
            source_output = ctx.outputs.get(source_id, {})

            # Evaluate condition
            if evaluate_condition(condition, source_output):
                logger.debug("Conditional edge matched", source=source_id, target=target_node_id, condition=condition)
                return True

        # No conditions matched
        logger.debug("No conditional edges matched", target=target_node_id, edge_count=len(edges))
        return False

    def _get_node_data(self, ctx: ExecutionContext, node_id: str) -> Dict[str, Any]:
        """Get node data from context.

        Args:
            ctx: ExecutionContext
            node_id: Node ID

        Returns:
            Node data dict
        """
        for node in ctx.nodes:
            if node.get("id") == node_id:
                return node
        return {}

    def _gather_node_inputs(self, ctx: ExecutionContext, node_id: str) -> Dict[str, Any]:
        """Gather inputs for a node from upstream outputs.

        Args:
            ctx: ExecutionContext
            node_id: Target node ID

        Returns:
            Dict of upstream outputs keyed by source node type
        """
        inputs = {}
        for edge in ctx.edges:
            if edge.get("target") == node_id:
                source_id = edge.get("source")
                if source_id in ctx.outputs:
                    # Find source node type
                    source_node = self._get_node_data(ctx, source_id)
                    source_type = source_node.get("type", source_id)
                    inputs[source_type] = ctx.outputs[source_id]
        return inputs

    # =========================================================================
    # STATUS NOTIFICATIONS
    # =========================================================================

    async def _notify_status(self, node_id: str, status: str, data: Dict[str, Any]) -> None:
        """Send status notification via callback.

        Args:
            node_id: Node ID
            status: Status string
            data: Additional data
        """
        if self.status_callback:
            try:
                await self.status_callback(node_id, status, data)
            except Exception as e:
                logger.warning("Status callback failed", node_id=node_id, error=str(e))

    # =========================================================================
    # RECOVERY
    # =========================================================================

    async def recover_execution(self, execution_id: str, nodes: List[Dict], edges: List[Dict]) -> Optional[Dict[str, Any]]:
        """Recover and resume an interrupted execution.

        Args:
            execution_id: Execution ID to recover
            nodes: Workflow nodes
            edges: Workflow edges

        Returns:
            Execution result if resumed, None if not found
        """
        ctx = await self.cache.load_execution_state(execution_id, nodes, edges)
        if not ctx:
            logger.warning("Execution not found for recovery", execution_id=execution_id)
            return None

        if ctx.status != WorkflowStatus.RUNNING:
            logger.info("Execution already complete", execution_id=execution_id, status=ctx.status.value)
            return {
                "success": ctx.status == WorkflowStatus.COMPLETED,
                "execution_id": execution_id,
                "status": ctx.status.value,
                "recovered": False,
            }

        logger.info("Recovering execution", execution_id=execution_id, checkpoints=ctx.checkpoints)

        # Reset any RUNNING nodes to PENDING (they were interrupted)
        for node_exec in ctx.node_executions.values():
            if node_exec.status == TaskStatus.RUNNING:
                node_exec.status = TaskStatus.PENDING
                node_exec.started_at = None

        # Track in memory
        self._active_contexts[ctx.execution_id] = ctx

        # Resume decide loop
        try:
            await self._workflow_decide(ctx, enable_caching=True)

            if ctx.all_nodes_complete():
                ctx.status = WorkflowStatus.COMPLETED
            elif ctx.errors:
                ctx.status = WorkflowStatus.FAILED

            ctx.completed_at = time.time()
            await self.cache.save_execution_state(ctx)

            return {
                "success": ctx.status == WorkflowStatus.COMPLETED,
                "execution_id": ctx.execution_id,
                "status": ctx.status.value,
                "recovered": True,
                "outputs": ctx.outputs,
            }

        finally:
            self._active_contexts.pop(ctx.execution_id, None)

    async def get_active_executions(self) -> List[str]:
        """Get list of active execution IDs.

        Returns:
            List of execution IDs currently running
        """
        return list(self._active_contexts.keys())

    # =========================================================================
    # DLQ REPLAY
    # =========================================================================

    async def replay_dlq_entry(self, entry_id: str, nodes: List[Dict], edges: List[Dict]) -> Dict[str, Any]:
        """Replay a failed node from the Dead Letter Queue.

        Creates a new execution context and attempts to re-execute the failed node.

        Args:
            entry_id: DLQ entry ID to replay
            nodes: Workflow nodes
            edges: Workflow edges

        Returns:
            Execution result dict
        """
        # Get DLQ entry
        entry = await self.cache.get_dlq_entry(entry_id)
        if not entry:
            return {
                "success": False,
                "error": f"DLQ entry not found: {entry_id}",
            }

        logger.info(
            "Replaying DLQ entry",
            entry_id=entry_id,
            node_id=entry.node_id,
            node_type=entry.node_type,
            original_execution=entry.execution_id,
        )

        # Create new execution context for replay
        ctx = ExecutionContext.create(
            workflow_id=entry.workflow_id,
            session_id="dlq_replay",
            nodes=nodes,
            edges=edges,
        )

        # Get the node execution
        node_exec = ctx.node_executions.get(entry.node_id)
        if not node_exec:
            return {
                "success": False,
                "error": f"Node not found in workflow: {entry.node_id}",
            }

        # Set up context with stored inputs
        ctx.outputs = entry.inputs  # Restore input state

        ctx.status = WorkflowStatus.RUNNING
        ctx.started_at = time.time()
        self._active_contexts[ctx.execution_id] = ctx

        try:
            # Execute the single node with retry
            await self._execute_single_node(ctx, node_exec, enable_caching=False)

            if node_exec.status == TaskStatus.COMPLETED:
                # Success - remove from DLQ
                await self.cache.remove_from_dlq(entry_id)
                logger.info("DLQ replay succeeded", entry_id=entry_id, node_id=entry.node_id)

                return {
                    "success": True,
                    "execution_id": ctx.execution_id,
                    "node_id": entry.node_id,
                    "result": node_exec.output,
                    "removed_from_dlq": True,
                }
            else:
                # Still failing - update DLQ entry
                await self.cache.update_dlq_entry(entry_id, entry.retry_count + 1, node_exec.error or "Unknown error")

                return {
                    "success": False,
                    "execution_id": ctx.execution_id,
                    "node_id": entry.node_id,
                    "error": node_exec.error,
                    "retry_count": entry.retry_count + 1,
                }

        finally:
            self._active_contexts.pop(ctx.execution_id, None)
