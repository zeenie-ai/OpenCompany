"""Versioned one-off control accounting without a synthetic deployment root."""

import asyncio
from datetime import timedelta

from temporalio import workflow

from ._retry_policies import DEFAULT_ACTIVITY_RETRY
from .execution_control import ExecutionControl


def raise_if_pi_cancelled() -> None:
    # A WAIT_CANCELLATION_COMPLETED activity can still return a normal result.
    # The workflow cancellation request survives that race, including on replay.
    if workflow.cancellation_reason() is not None and workflow.patched("pi-sticky-workflow-cancellation-v1"):
        raise asyncio.CancelledError()


async def execute_pi_activity(*args, **kwargs):
    raise_if_pi_cancelled()
    result = await workflow.execute_activity(*args, **kwargs)
    raise_if_pi_cancelled()
    return result


class PiDescendantExecutionControl(ExecutionControl):
    def __init__(self):
        super().__init__()
        self.children = {}
        self._child_handles = {}
        self._control_calls = 0

    async def wait_until_running(self) -> None:
        raise_if_pi_cancelled()
        await super().wait_until_running()
        raise_if_pi_cancelled()

    def bind(self, data):
        if not self._bound:
            self.children = dict(data.get("pi_control_children") or {})
        super().bind(data)

    def track_child(self, handle):
        self.children[handle.id] = {"workflow_id": handle.id,
            "first_execution_run_id": handle.first_execution_run_id}
        self._child_handles[handle.id] = handle

    def forget_child(self, identity):
        self.children.pop(identity, None)
        self._child_handles.pop(identity, None)

    def live_children(self):
        for identity, handle in list(self._child_handles.items()):
            if handle.done():
                self.forget_child(identity)
        return bool(self.children)

    async def _forward(self, operation, payload):
        if not self.children:
            return
        self._control_calls += 1
        control = {"state": self.state, "revision": self.revision, "producers_held": self.producers_held}
        if self._generation_value is not None:
            control["generation"] = self._generation_value
        result = await workflow.execute_activity("coding.pi_child_control", {
            "children": [self.children[key] for key in sorted(self.children)],
            "operation": operation, "control": control},
            activity_id=f"pi-child-control-{self._control_calls}",
            start_to_close_timeout=timedelta(minutes=30), heartbeat_timeout=timedelta(seconds=90),
            retry_policy=DEFAULT_ACTIVITY_RETRY)
        for identity in (result or {}).get("closed") or []:
            self.forget_child(identity)

    async def set_control_state(self, payload, *, explicit_release=True):
        result = await super().set_control_state(payload, explicit_release=explicit_release)
        await self._forward("set_control_state", payload)
        return result

    async def wait_for_checkpoint(self, payload=None):
        await super().wait_for_checkpoint(payload)
        await self._forward("wait_for_checkpoint", payload)
        # Child completion can start parent receipt bookkeeping while the
        # descendant checkpoint RPC is pending. Drain that bookkeeping too.
        return await super().wait_for_checkpoint(payload)

    def carry(self):
        return {**super().carry(), "pi_control_children": self.children}


class PiLocalExecutionControl(PiDescendantExecutionControl):
    def bind(self, data):
        super().bind({**data, "execution_control_version": 1,
            "execution_control_state": data.get("pi_local_control_state", data.get("execution_control_state", "running")),
            "execution_control_revision": data.get("pi_local_control_revision", data.get("execution_control_revision", -1))})

    def carry(self):
        return {"pi_local_control_version": 1, "execution_control_state": self.state,
            "execution_control_revision": self.revision, "execution_control_producers_held": self.producers_held,
            "pi_local_control_state": self.state, "pi_local_control_revision": self.revision,
            "pi_control_children": self.children}

    async def register_root(self):
        # One-off invocation/parent lifetime owns this workflow. There is no
        # deployment-wide registry to enroll in or fabricated controller ID.
        pass

    async def unregister_root(self):
        pass
