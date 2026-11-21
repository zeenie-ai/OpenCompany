"""Low latency instance wakeups backed by a durable database reconciliation.

A failed signal never changes the committed receipt. The reconciler retries
the same recipient identity; it does not search Temporal Visibility.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import uuid4

from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from services.agent_instances.protocol import session_workflow_id

logger = logging.getLogger(__name__)
RECONCILER_ID = "agent-instances:reconciler:v1"
RECONCILE_INTERVAL_SECONDS = 30
RECONCILE_TIMEOUT_SECONDS = 120


def _client():
    from core.container import container
    wrapper = container.temporal_client()
    client = getattr(wrapper, "client", None)
    if client is None:
        raise RuntimeError("Temporal is unavailable; agent delivery remains queued")
    return client


async def wake_instance(instance_id: str, *, client=None, context=None, database=None):
    from core.container import container
    from core.config import Settings
    from services.agent_instances.service import AgentInstanceService
    client = client or _client()
    instance = await AgentInstanceService(database or container.database()).get_instance(instance_id)
    if instance.get("state") in {"closed", "halted"}:
        return None
    config = instance.get("config") or {}
    admitted = config.get("runtime_context") or (config.get("template") or {}).get("runtime_context") or (config.get("scope") or {}).get("runtime_context") or {}
    control = {key: admitted[key] for key in (
        "workflow_id", "generation", "execution_id", "root_execution_id", "execution_control_version",
        "controller_workflow_id", "execution_control_state", "execution_control_revision",
        "execution_control_producers_held") if key in admitted}
    from services.temporal.trigger_listener_workflow import event_workflow_search_attributes
    search_attributes = event_workflow_search_attributes(instance.get("workflow_id"))
    return await client.start_workflow(
        "AgentInstanceSessionWorkflow", {**control, **(context or {}), "instance_id": instance_id},
        id=session_workflow_id(instance_id), task_queue=Settings().temporal_task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        start_signal="wake", start_signal_args=[{}],
        **({"search_attributes": search_attributes} if search_attributes is not None else {}),
    )


async def retire_legacy_reconciler(client) -> None:
    """Retire the previous release's timer loop after local recovery is healthy."""
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode

    try:
        current = await client.get_workflow_handle(RECONCILER_ID).describe()
        if current.status != WorkflowExecutionStatus.RUNNING or current.workflow_type != "AgentInstanceReconcilerWorkflow":
            return
        # Target the observed chain across Continue-As-New. An unrelated
        # execution that subsequently reuses this workflow ID stays untouched.
        first_run_id = current.raw_description.workflow_execution_info.first_run_id
        if not first_run_id:
            raise RuntimeError("Missing first run ID on the legacy agent reconciler")
        handle = client.get_workflow_handle(RECONCILER_ID, first_execution_run_id=first_run_id)
        await handle.cancel(reason="Agent recovery moved to the worker database scan")
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise


async def wake_instances(result: dict[str, Any], context=None, *, client=None, database=None) -> None:
    """Best-effort notification after the caller committed its operation."""
    stops = result.get("instance_stop") or []
    for stop in stops:
        identifier = stop if isinstance(stop, str) else stop.get("instance_id") or stop.get("id")
        if identifier:
            try:
                await (client or _client()).get_workflow_handle(session_workflow_id(identifier)).signal("stop_current", {})
            except Exception:
                logger.warning("Closed instance execution remains fenced pending cleanup", exc_info=True)
    candidates = result.get("instance_wake") or result.get("wake_instances") or []
    if isinstance(candidates, (str, dict)):
        candidates = [candidates]
    identifiers = set()
    for item in candidates:
        identifier = item if isinstance(item, str) else item.get("instance_id") or item.get("id")
        if identifier:
            identifiers.add(str(identifier))
    if result.get("status") == "stop_requested":
        item = result.get("agent") or result.get("instance") or {}
        if item.get("id"):
            identifiers.add(str(item["id"]))
        for identifier in sorted(identifiers):
            try:
                await (client or _client()).get_workflow_handle(session_workflow_id(identifier)).signal("stop_current", {})
            except Exception:
                logger.warning("Agent interruption is fenced; stop notification deferred", exc_info=True)
        return
    for identifier in sorted(identifiers):
        try:
            await wake_instance(identifier, client=client, database=database)
        except Exception:
            logger.warning("Agent wakeup deferred to durable reconciliation", exc_info=True)


async def _terminal_workflow(client, workflow_id: str, run_id: str | None = None) -> bool:
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode
    handle = client.get_workflow_handle(workflow_id, **({"run_id": run_id} if run_id else {}))
    try:
        description = await handle.describe()
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        return True
    return description.status != WorkflowExecutionStatus.RUNNING


async def _retire_terminal_run_permits(store, run: dict, client, *, cancel_child=False) -> bool:
    """Release only after the exact owner and its native child have ended."""
    if not run.get("temporal_workflow_id") or not run.get("temporal_run_id"):
        return False
    if not await _terminal_workflow(client, run["temporal_workflow_id"], run["temporal_run_id"]):
        return False
    config = ((run.get("input_snapshot") or {}).get("instance") or {}).get("config") or {}
    node_type = (config.get("template") or {}).get("node_type")
    if node_type not in {"claude_code_agent", "codex_agent", "rlm_agent", "gemini_cli_agent"}:
        child_id = f"agent-instance-run:{run['id']}"
        # A native child may have continued-as-new: checking its current
        # chain also protects leases carried from a closed physical run.
        if not await _terminal_workflow(client, child_id):
            if cancel_child:
                await client.get_workflow_handle(child_id).cancel()
            return False
    pending = [lease for lease in run.get("permit_leases") or [] if not lease.get("released")]
    for lease in pending:
        if not await _terminal_workflow(client, lease["workflow_id"], lease["workflow_run_id"]):
            return False
    for lease in pending:
        if not await store.database.retire_subagent_permit(lease["root_execution_id"], lease["lease_id"]):
            raise RuntimeError("Orphan permit retirement was not persisted")
        await store.release_run_permit(run["instance_id"], run["id"], lease_id=lease["lease_id"])
    return True


async def _recover_missing_owner(store, instance: dict, client) -> bool:
    """Only an exact terminal/missing Temporal run can release a DB owner.

    Visibility delay or transport failure is never evidence that a running
    executor disappeared. The recorded physical run avoids confusing a new
    busy period with the previous run under the reusable workflow ID.
    """
    run_id = instance.get("active_run_id")
    if not run_id:
        return False
    run = await store.get_run(instance["id"], run_id)
    if not run.get("temporal_workflow_id") or not run.get("temporal_run_id"):
        return False
    if not await _terminal_workflow(client, run["temporal_workflow_id"], run["temporal_run_id"]):
        return False
    # Persist draining state before cancelling a child, so lost cancellation
    # hints and worker crashes remain discoverable by list_stopping.
    await store.fence_orphan_run(instance["id"], run_id)
    if not await _retire_terminal_run_permits(store, run, client, cancel_child=True):
        return False
    result = await store.complete_run(instance["id"], run_id, error="interrupted",
        request_id=f"reconciled-stop:{run_id}")
    if instance.get("role") == "lead" and instance.get("job_id"):
        from services.employees.jobs import fail_no_assignments
        await fail_no_assignments(store.database, {
            "employee_job_id": instance["job_id"], "workflow_id": instance.get("workflow_id"),
            "team_id": instance.get("team_id"), "node_id": instance.get("template_node_id")})
    await wake_instances(result, client=client, database=store.database)
    return True


async def reconcile_pending(*, client=None, database=None, limit=100) -> int:
    from core.container import container
    from services.agent_instances.service import AgentInstanceService
    store = AgentInstanceService(database or container.database())
    client = client or _client()
    count = 0
    for result in await store.close_completed_jobs(limit=limit):
        await wake_instances(result, client=client, database=database)
    for run in await store.list_pending_permit_runs(limit=limit):
        try:
            instance = await store.get_instance(run["instance_id"])
            if instance.get("active_run_id") == run["id"]:
                await _recover_missing_owner(store, instance, client)
            else:
                # Completion may have committed immediately before owner
                # termination prevented its finally block from releasing.
                await _retire_terminal_run_permits(store, run, client)
        except Exception:
            logger.warning("Agent permit recovery will retry run %s", run.get("id"), exc_info=True)
    for identifier in await store.list_stopping(limit=limit):
        try:
            instance = await store.get_instance(identifier)
            if not await _recover_missing_owner(store, instance, client):
                await wake_instances({"instance_stop": [identifier]}, client=client, database=database)
        except Exception:
            logger.warning("Agent stop reconciliation will retry recipient %s", identifier, exc_info=True)
    for instance in await store.list_pending(limit=limit):
        try:
            if instance.get("active_run_id"):
                await _recover_missing_owner(store, instance, client)
                # A running session drains pending deliveries before going idle.
                # A recovery check must not append a wake event to its history.
                continue
            if await wake_instance(str(instance["id"]), client=client, database=database) is not None:
                count += 1
        except Exception:
            logger.warning("Agent reconciliation will retry recipient %s", instance.get("id"), exc_info=True)
    return count


async def scheduled_reconcile_pending(*, client=None, database=None, limit=100) -> int | None:
    """Claim one shared scan interval. No Temporal workflow owns this maintenance."""
    from core.container import container
    from services.agent_instances.service import AgentInstanceService
    database = database or container.database()
    store = AgentInstanceService(database)
    token = str(uuid4())
    # Keep a grace interval beyond the local deadline for cancellation to drain.
    if not await store.reserve_reconciliation(token, lease_seconds=RECONCILE_TIMEOUT_SECONDS + RECONCILE_INTERVAL_SECONDS):
        return None
    delay = RECONCILE_INTERVAL_SECONDS
    try:
        async with asyncio.timeout(RECONCILE_TIMEOUT_SECONDS):
            return await reconcile_pending(client=client, database=database, limit=limit)
    except asyncio.CancelledError:
        delay = 0
        raise
    finally:
        await store.finish_reconciliation(token, delay_seconds=delay)
