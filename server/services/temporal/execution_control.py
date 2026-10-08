"""Cooperative admission and checkpoint state held in Temporal history.

Only versioned generations use this protocol. The workflow that owns this
helper exposes the Update/Query methods; the helper never installs handlers or
intercepts SDK commands. An action includes consumption of its recorded result,
while a child start includes only its start acknowledgement, never child result.
"""

from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.exceptions import ApplicationError

from services.temporal._retry_policies import DEFAULT_ACTIVITY_RETRY


class ExecutionControl:
    def __init__(self) -> None:
        self.version = 0
        self.state = "running"
        self.revision = -1
        self.producers_held = False
        self.generation: str | None = None
        self._generation_value: Any = None
        self.controller_workflow_id: str | None = None
        self.root_run_id: str | None = None
        self._actions = 0
        self._child_starts = 0
        self._bound = False
        self._control_initialized = False
        self._released_revision = -1

    @property
    def enabled(self) -> bool:
        return self.version == 1

    def bind(self, data: dict[str, Any]) -> None:
        if self._bound:
            return
        self._bound = True
        self.version = int(data.get("execution_control_version") or 0)
        if not self.enabled:
            return
        self.controller_workflow_id = data.get("controller_workflow_id")
        generation = data.get("generation")
        self._generation_value = generation
        self.generation = str(generation) if generation is not None else None
        self.root_run_id = data.get("execution_control_root_run_id")
        self._released_revision = int(data.get("execution_control_released_revision", -1))
        self._apply({
            "state": data.get("execution_control_state", "running"),
            "revision": data.get("execution_control_revision", -1),
            "producers_held": data.get("execution_control_producers_held", False),
        })

    def validate_control(self, payload: dict[str, Any] | None) -> None:
        """Reject malformed control messages without changing Workflow state.

        Update validators use this before acceptance. Handlers also call it
        because validators do not execute during replay or direct helper calls.
        An omitted checkpoint payload means only wait for the existing intent.
        """
        if not self.enabled or payload is None:
            return
        if not isinstance(payload, dict):
            raise ApplicationError("Control request must be a mapping", type="InvalidWorkflowControlRequest", non_retryable=True)
        generation = payload.get("generation")
        if generation is not None and self.generation != str(generation):
            raise ApplicationError("Execution generation does not match", type="StaleExecutionGeneration", non_retryable=True)
        first_run_id = payload.get("first_execution_run_id")
        if first_run_id is not None and str(first_run_id) != workflow.info().first_execution_run_id:
            raise ApplicationError("Execution chain identity has changed", type="ExecutionChainConflict", non_retryable=True)
        if payload.get("state") not in ("paused", "running"):
            raise ApplicationError("Control state must be paused or running", type="InvalidWorkflowControlState", non_retryable=True)
        try:
            int(payload.get("revision", -1))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ApplicationError("Control revision must be an integer", type="InvalidWorkflowControlRevision", non_retryable=True) from exc
        if "producers_held" in payload and not isinstance(payload["producers_held"], bool):
            raise ApplicationError("Producer admission hold must be a boolean", type="InvalidWorkflowControlRequest", non_retryable=True)

    def _apply(self, payload: dict[str, Any], *, explicit_release: bool = False) -> None:
        if not self.enabled:
            return
        if payload is None:
            raise ApplicationError("Control request must be a mapping", type="InvalidWorkflowControlRequest", non_retryable=True)
        self.validate_control(payload)
        state = payload["state"]
        revision = int(payload.get("revision", -1))
        # The initial input may use -1. Once a revision is observed, delayed
        # registration responses and duplicate messages cannot undo it.
        if not self._control_initialized or revision > self.revision:
            self.state, self.revision = state, revision
            self.producers_held = bool(payload.get("producers_held", False))
            self._control_initialized = True
        elif revision == self.revision and state == self.state and "producers_held" in payload:
            # Only an acknowledged release finishes this revision's phase.
            # A new root may inherit unheld input while the controller is held;
            # enrollment must still hold it before admitting business work.
            held = bool(payload["producers_held"])
            if not held or self._released_revision < revision:
                self.producers_held = held
        if explicit_release and revision == self.revision and state == self.state == "running" and payload.get("producers_held") is False:
            self._released_revision = max(self._released_revision, revision)

    async def set_control_state(self, payload: dict[str, Any], *, explicit_release: bool = True) -> dict[str, Any]:
        self._apply(payload, explicit_release=explicit_release)
        if self.enabled and (self.state == "paused" or self.producers_held) and self._child_starts:
            await workflow.wait_condition(lambda: self._child_starts == 0 or (self.state == "running" and not self.producers_held))
        return self.status()

    async def wait_for_checkpoint(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload is not None:
            self._apply(payload)
        if self.enabled and (self._actions or self._child_starts):
            await workflow.wait_condition(lambda: (self._actions == 0 and self._child_starts == 0) or (self.state == "running" and not self.producers_held))
        return self.status()

    async def wait_until_running(self) -> None:
        if self.enabled and (self.state != "running" or self.producers_held):
            await workflow.wait_condition(lambda: self.state == "running" and not self.producers_held)

    async def begin_action(self) -> None:
        await self.wait_until_running()
        if self.enabled:
            self._actions += 1

    def begin_bookkeeping(self) -> None:
        """Consume completed child results while admission remains closed."""
        if self.enabled:
            self._actions += 1

    def end_action(self) -> None:
        if self.enabled:
            self._actions -= 1
            if self._actions < 0:
                raise RuntimeError("Unbalanced execution action accounting")

    async def begin_child_start(self) -> None:
        await self.wait_until_running()
        if self.enabled:
            self._child_starts += 1

    def end_child_start(self) -> None:
        if self.enabled:
            self._child_starts -= 1
            if self._child_starts < 0:
                raise RuntimeError("Unbalanced child-start accounting")

    @asynccontextmanager
    async def action(self):
        await self.begin_action()
        try:
            yield
        finally:
            self.end_action()

    @asynccontextmanager
    async def child_start(self):
        await self.begin_child_start()
        try:
            yield
        finally:
            self.end_child_start()

    def status(self) -> dict[str, Any]:
        return {
            "execution_control_version": self.version,
            "generation": self._generation_value,
            "state": self.state,
            "revision": self.revision,
            "producers_held": self.producers_held,
            "active_actions": self._actions,
            "pending_child_starts": self._child_starts,
            "checkpoint": self._actions == 0 and self._child_starts == 0,
        }

    def carry(self) -> dict[str, Any]:
        if not self.enabled:
            return {}
        return {
            "execution_control_version": self.version,
            "controller_workflow_id": self.controller_workflow_id,
            "generation": self._generation_value,
            "execution_control_state": self.state,
            "execution_control_revision": self.revision,
            "execution_control_producers_held": self.producers_held,
            "execution_control_root_run_id": self.root_run_id,
            "execution_control_released_revision": self._released_revision,
        }

    def _registration(self) -> dict[str, Any]:
        if not self.controller_workflow_id or self.generation is None:
            raise ApplicationError("Controlled execution requires controller identity and generation", type="MissingExecutionController", non_retryable=True)
        info = workflow.info()
        self.root_run_id = self.root_run_id or info.first_execution_run_id
        return {
            "controller_workflow_id": self.controller_workflow_id,
            "generation": self._generation_value,
            "workflow_id": info.workflow_id,
            "first_execution_run_id": self.root_run_id,
        }

    async def register_root(self) -> None:
        if not self.enabled or self.root_run_id is not None:
            return
        payload = self._registration()
        result = await workflow.execute_activity(
            "execution_control.register", payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DEFAULT_ACTIVITY_RETRY,
        )
        await self.set_control_state({
            "state": result["participant_state"],
            "revision": result["participant_revision"],
            "generation": self.generation,
            "producers_held": result.get("producers_held", False),
        }, explicit_release=False)

    async def unregister_root(self) -> None:
        if not self.enabled:
            return
        await workflow.execute_activity(
            "execution_control.unregister", self._registration(),
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=DEFAULT_ACTIVITY_RETRY,
        )
