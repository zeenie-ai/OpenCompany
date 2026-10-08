"""Acknowledged generation control using native Temporal execution trees.

The controller records only roots which can outlive a parent. Descriptions
discover attached children after each workflow has fenced child starts.
No visibility scan is used as evidence that a generation is quiescent.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from temporalio.client import WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from core.logging import get_logger

logger = get_logger(__name__)


async def count_generation_actions(client: Any, controller_id: str, status: dict) -> int:
    """A read-only UI count; transition verification uses acknowledgements."""
    queue = [controller_id, *(status.get("live_roots") or {})]
    seen: set[str] = set()
    count = 0
    while queue:
        workflow_id = queue.pop()
        if workflow_id in seen:
            continue
        seen.add(workflow_id)
        handle = client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        if desc.status != WorkflowExecutionStatus.RUNNING:
            continue
        participant = await handle.query("execution_control_status")
        count += int(participant.get("active_actions", 0))
        queue.extend(child.workflow_id for child in desc.raw_description.pending_children)
    return count


async def transition_generation(client: Any, control: Any, *, paused: bool, on_admission: Any = None) -> dict:
    controller = client.get_workflow_handle(control.controller_workflow_id)
    revision = int(control.revision)
    base = {"generation": int(control.generation), "revision": revision,
            "state": "paused" if paused else "running"}
    started = time.monotonic()

    async def describe(workflow_id: str, chain: str) -> Any:
        handle = client.get_workflow_handle(workflow_id)
        try:
            desc = await handle.describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        actual = desc.raw_description.workflow_execution_info.first_run_id
        if chain and actual != chain:
            # Workflow ID reuse must never let an old root control a new chain.
            return None
        if desc.status != WorkflowExecutionStatus.RUNNING:
            return None
        return desc

    controller_description = await describe(control.controller_workflow_id, control.controller_run_id or "")
    if controller_description is None:
        raise RuntimeError("controller_execution_missing")
    controller_chain = controller_description.raw_description.workflow_execution_info.first_run_id
    controller_payload = {**base, "first_execution_run_id": controller_chain, "producers_held": True}
    status = await controller.execute_update("set_control_state", controller_payload,
        id=f"control:{revision}:admission")
    if status.get("participant_revision", status.get("revision")) != revision:
        raise RuntimeError("control_revision_conflict")

    targets: dict[str, str] = {}
    order: list[str] = []
    removed: set[tuple[str, str]] = set()
    while True:
        before = await controller.query("status")
        epoch = before["membership_epoch"]
        roots = before.get("live_roots", {})
        queue = [(control.controller_workflow_id, controller_chain)]
        queue.extend((r["workflow_id"], r["first_execution_run_id"]) for r in roots.values())
        visited: set[str] = set()
        # First fence every reachable workflow. Never await its activity drain
        # here: an unfenced sibling could otherwise continue admitting work.
        while queue:
            workflow_id, chain = queue.pop()
            if workflow_id in visited:
                continue
            visited.add(workflow_id)
            desc = await describe(workflow_id, chain)
            if desc is None:
                targets.pop(workflow_id, None)
                if workflow_id in roots and (workflow_id, chain) not in removed:
                    await controller.execute_update("unregister_execution", {
                        "workflow_id": workflow_id, "first_execution_run_id": chain,
                        "generation": control.generation,
                    })
                    removed.add((workflow_id, chain))
                continue
            current_chain = desc.raw_description.workflow_execution_info.first_run_id
            handle = client.get_workflow_handle(workflow_id)
            if workflow_id != control.controller_workflow_id:
                payload = {**base, "first_execution_run_id": current_chain, "producers_held": True}
                try:
                    ack = await handle.execute_update("set_control_state", payload,
                        id=f"control:{revision}:admission")
                except RPCError as exc:
                    if exc.status == RPCStatusCode.NOT_FOUND:
                        continue
                    raise
                if ack.get("revision") != revision or ack.get("state") != base["state"]:
                    raise RuntimeError("temporal_control_ack_mismatch")
            targets[workflow_id] = current_chain
            if workflow_id not in order:
                order.append(workflow_id)
            # Setter acknowledges admitted child starts before this second
            # describe, so pending_children is a complete direct-child fence.
            desc = await describe(workflow_id, current_chain)
            if desc is not None:
                queue.extend((child.workflow_id, child.run_id)
                             for child in desc.raw_description.pending_children)

        logger.info("Generation admission acknowledged", workflow_id=control.workflow_id,
                    revision=revision, latency_seconds=time.monotonic() - started,
                    controlled_executions=len(targets))
        if on_admission is not None:
            await on_admission(await controller.query("status"))
        if paused:
            async def checkpoint(workflow_id: str, chain: str) -> None:
                if await describe(workflow_id, chain) is None:
                    return
                try:
                    result = await client.get_workflow_handle(workflow_id).execute_update(
                        "wait_for_checkpoint", {**base, "first_execution_run_id": chain},
                        id=f"control:{revision}:checkpoint")
                except RPCError as exc:
                    if exc.status == RPCStatusCode.NOT_FOUND:
                        return
                    raise
                if (not result.get("checkpoint") or result.get("state") != base["state"]
                    or result.get("revision", result.get("participant_revision")) != revision):
                    raise RuntimeError("temporal_checkpoint_ack_mismatch")
            await asyncio.gather(*(checkpoint(wid, chain) for wid, chain in targets.items()))
        after = await controller.query("status")
        if after["membership_epoch"] != epoch:
            continue
        if not paused:
            for workflow_id in reversed(order):
                if workflow_id == control.controller_workflow_id:
                    continue
                chain = targets.get(workflow_id)
                if chain and await describe(workflow_id, chain) is not None:
                    ack = await client.get_workflow_handle(workflow_id).execute_update(
                        "set_control_state", {**base, "first_execution_run_id": chain, "producers_held": False},
                        id=f"control:{revision}:release")
                    if ack.get("revision") != revision or ack.get("state") != "running" or ack.get("producers_held"):
                        raise RuntimeError("temporal_control_ack_mismatch")
            status = await controller.execute_update("set_control_state", {
                **controller_payload, "producers_held": False,
                "expected_membership_epoch": epoch,
            })
            if status.get("participant_revision") != revision or status.get("participant_state") != "running":
                raise RuntimeError("control_revision_conflict")
            if status.get("producers_held"):
                continue
        else:
            status = after
        if status["membership_epoch"] == epoch:
            break
    logger.info("Generation checkpoint acknowledged", workflow_id=control.workflow_id,
                revision=revision, latency_seconds=time.monotonic() - started,
                controlled_executions=len(targets))
    return {"controller_status": status, "controlled_executions": len(targets)}
