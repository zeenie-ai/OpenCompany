"""Long-lived deployment controller and trigger hub for one generation."""

import asyncio
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.common import SearchAttributeKey, SearchAttributePair
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

from services.temporal._retry_policies import DEFAULT_ACTIVITY_RETRY
from services.temporal.execution_control import ExecutionControl


# The controller is the ONE workflow expected to live for months, and it
# multiplexes every trigger's signals, poll timers/activities, and child
# spawns into a single event history. Temporal terminates any workflow
# around ~51,200 history events, so without continue-as-new a single
# polling trigger at the default 60s interval killed the whole
# deployment's control plane in days. The run rolls over (carrying
# triggers, control state, queued events, and per-trigger seen-id
# baselines) whenever the server suggests it or the history crosses the
# soft cap below.

# Roll over before the server's hard ceiling. is_continue_as_new_suggested
# is the primary signal; this cap is the deterministic backstop.
_HISTORY_SOFT_CAP = 10_000
# Bounds on state carried across continue-as-new — the CAN argument blob
# is capped at 2MiB by Temporal, so unbounded carries would trade a
# history overflow for an argument overflow.
_MAX_CARRIED_EVENTS = 256
_MAX_CARRIED_SEEN_IDS = 4_096
# Defensive floor for user-supplied poll intervals: a few-second interval
# burns ~100K+ history events/day. Mirrors the plugin-side clamp
# (PollingTriggerNode.poll_interval_clamp) which the legacy asyncio path
# applies but workflow payloads historically did not.
_MIN_POLL_INTERVAL_S = 30
_CAN_INPUT_MAX_BYTES = 1_900_000


class _AdmissionDeferred(Exception):
    """An accepted event remains queued while controller admission is closed."""


@workflow.defn(name="WorkflowControlWorkflow", sandboxed=False)
class WorkflowControlWorkflow:
    """Own control state and trigger scheduling without listener workflows.

    Trigger definitions and inbound events are recorded as signals in this
    workflow's own history. Only an actual triggered graph run becomes a child
    workflow, so Temporal's workflow list has no per-trigger listener rows.

    Longevity contract: the controller continue-as-news to keep its
    history bounded. All state a rollover must preserve lives in
    ``control_data`` — trigger specs (whose ``listener_args["seen_ids"]``
    the poll loops write back after every cycle), the pending push-event
    queue, the dedup baseline, and the control state/revision. Callers
    therefore address the controller by workflow id only, never a pinned
    run id.
    """

    @workflow.init
    def __init__(self, control_data: Dict[str, Any] = None) -> None:
        self._state = "running"
        self._revision = 0
        self._closed = False
        self._triggers: dict[str, Dict[str, Any]] = {}
        self._events: list[tuple[str, Dict[str, Any]]] = []
        # Insertion-ordered so the carry across continue-as-new can keep
        # the newest entries when trimming to _MAX_CARRIED_SEEN_IDS.
        self._seen_event_ids: dict[str, None] = {}
        # Pending items (including the one whose child start is in progress)
        # must remain deduplicated even after the bounded recent-ID cache ages
        # out. This index is rebuilt from the carried queue and durable pages;
        # it never adds an unbounded list of IDs to continuation inputs.
        self._pending_event_ids: set[str] = set()
        self._event_accumulator_v1: Optional[bool] = None
        self._messaging_v1: Optional[bool] = None
        self._poll_tasks: dict[str, asyncio.Task] = {}
        self._can_requested = False
        self._overflow = False
        self._durable_prefix = 0
        self._review_overflow_checked = False
        self._queue_reads = 0
        self._drain_tasks = False
        self._execution_control = ExecutionControl()
        self._live_roots: dict[str, Dict[str, Any]] = {}
        self._membership_epoch = 0
        self._carried_state_seeded = False
        if control_data is not None and int(control_data.get("execution_control_version") or 0) == 1:
            self._state = control_data.get("state", "running")
            self._seed_carried_state(control_data, start_runtime=False)

    @workflow.signal
    async def pause(self) -> None:
        if self._execution_control.enabled:
            return
        self._apply_control_state("paused")

    @workflow.signal
    async def resume(self) -> None:
        if self._execution_control.enabled:
            return
        self._apply_control_state("running")

    @workflow.update
    async def set_control_state(self, requested_state: str | Dict[str, Any]) -> Dict[str, Any]:
        """Idempotently apply and acknowledge a pause/resume transition.

        Updates give callers a durable acknowledgement that the controller
        processed the request. The legacy signals above remain registered for
        older callers and histories.
        """
        if isinstance(requested_state, dict):
            self.validate_control_state(requested_state)
            expected_epoch = self._expected_membership_epoch(requested_state)
            if requested_state.get("producers_held") is False and expected_epoch is not None and expected_epoch != self._membership_epoch:
                self._maybe_request_rollover_from_update()
                return self.status()
            previous_revision = self._execution_control.revision
            if "hold_admissions" in requested_state and "producers_held" not in requested_state:
                requested_state = {**requested_state, "producers_held": requested_state["hold_admissions"]}
            self._execution_control._apply(requested_state)
            if int(requested_state.get("revision", -1)) >= previous_revision:
                target = self._execution_control.state
                self._state = "paused" if self._execution_control.producers_held else target
                self._revision = self._execution_control.revision
                self._drain_tasks = False
            await self._execution_control.set_control_state(requested_state)
            self._maybe_request_rollover_from_update()
            return self.status()
        normalized = str(requested_state).strip().lower()
        aliases = {
            "pause": "paused",
            "paused": "paused",
            "resume": "running",
            "running": "running",
        }
        target_state = aliases.get(normalized)
        if target_state is None:
            raise ApplicationError(
                "Control state must be one of: pause, paused, resume, running",
                type="InvalidWorkflowControlState",
                non_retryable=True,
            )
        if self._messaging_enabled() and target_state == "running" and (
            self._closed or self._execution_control.state != "running" or self._execution_control.producers_held
        ):
            # safe_apply restores producer admission with this legacy string
            # Update. It cannot release a whole-generation Stop or the held
            # phase of Resume, whose revisioned Update owns that transition.
            self._apply_control_state("paused")
            self._drain_tasks = False
            self._maybe_request_rollover_from_update()
            return self.status()
        self._apply_control_state(target_state)
        self._drain_tasks = False
        self._maybe_request_rollover_from_update()
        return self.status()

    @set_control_state.validator
    def validate_control_state(self, requested_state: str | Dict[str, Any]) -> None:
        if isinstance(requested_state, dict):
            if not self._execution_control.enabled:
                raise ApplicationError("Controller does not support execution control v1", type="UnsupportedExecutionControlVersion", non_retryable=True)
            if "hold_admissions" in requested_state and "producers_held" not in requested_state:
                requested_state = {**requested_state, "producers_held": requested_state["hold_admissions"]}
            self._execution_control.validate_control(requested_state)
            self._expected_membership_epoch(requested_state)
        elif str(requested_state).strip().lower() not in {"pause", "paused", "resume", "running"}:
            raise ApplicationError(
                "Control state must be one of: pause, paused, resume, running",
                type="InvalidWorkflowControlState", non_retryable=True,
            )

    @staticmethod
    def _expected_membership_epoch(payload: Dict[str, Any]) -> Optional[int]:
        value = payload.get("expected_membership_epoch")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ApplicationError(
                "Expected membership epoch must be an integer",
                type="InvalidExecutionMembershipEpoch", non_retryable=True,
            ) from exc

    @workflow.update
    async def wait_for_checkpoint(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._execution_control.validate_control(payload)
        await self._execution_control.wait_for_checkpoint(payload)
        self._maybe_request_rollover_from_update()
        return self.status()

    @wait_for_checkpoint.validator
    def validate_checkpoint(self, payload: Optional[Dict[str, Any]] = None) -> None:
        self._execution_control.validate_control(payload)

    @workflow.query
    def execution_control_status(self) -> Dict[str, Any]:
        return self._execution_control.status()

    def _check_registration(self, registration: Dict[str, Any]) -> tuple[str, str]:
        if not self._execution_control.enabled:
            raise ApplicationError("Controller does not support execution control v1", type="UnsupportedExecutionControlVersion", non_retryable=True)
        if self._closed:
            raise ApplicationError("Execution controller is closing", type="ExecutionControllerClosed", non_retryable=True)
        if not isinstance(registration, dict):
            raise ApplicationError("Execution registration must be an object", type="InvalidExecutionRegistration", non_retryable=True)
        if str(registration.get("generation")) != self._execution_control.generation:
            raise ApplicationError("Execution generation does not match", type="StaleExecutionGeneration", non_retryable=True)
        workflow_id = str(registration.get("workflow_id") or "")
        first_run_id = str(registration.get("first_execution_run_id") or "")
        if not workflow_id or not first_run_id:
            raise ApplicationError("Execution chain identity is required", type="InvalidExecutionRegistration", non_retryable=True)
        return workflow_id, first_run_id

    @workflow.update
    async def register_execution(self, registration: Dict[str, Any]) -> Dict[str, Any]:
        workflow_id, first_run_id = self._check_registration(registration)
        existing = self._live_roots.get(workflow_id)
        if existing and existing["first_execution_run_id"] != first_run_id:
            raise ApplicationError("Execution root identity has changed", type="ExecutionChainConflict", non_retryable=True)
        if not existing:
            self._live_roots[workflow_id] = {
                "workflow_id": workflow_id,
                "first_execution_run_id": first_run_id,
            }
            self._membership_epoch += 1
        self._maybe_request_rollover_from_update()
        return self.status()

    @register_execution.validator
    def validate_registration(self, registration: Dict[str, Any]) -> None:
        workflow_id, first_run_id = self._check_registration(registration)
        existing = self._live_roots.get(workflow_id)
        if existing and existing["first_execution_run_id"] != first_run_id:
            raise ApplicationError("Execution root identity has changed", type="ExecutionChainConflict", non_retryable=True)

    @workflow.update
    async def unregister_execution(self, registration: Dict[str, Any]) -> Dict[str, Any]:
        workflow_id, first_run_id = self._check_registration(registration)
        existing = self._live_roots.get(workflow_id)
        if existing and existing["first_execution_run_id"] == first_run_id:
            del self._live_roots[workflow_id]
            self._membership_epoch += 1
        self._maybe_request_rollover_from_update()
        return self.status()

    @unregister_execution.validator
    def validate_unregistration(self, registration: Dict[str, Any]) -> None:
        self._check_registration(registration)

    @workflow.update
    async def pause_admissions(self, drain_tasks: bool = True) -> Dict[str, Any]:
        self._apply_control_state("paused")
        self._drain_tasks = drain_tasks
        if self._messaging_enabled() and (
            self._closed or self._execution_control.state != "running" or self._execution_control.producers_held
        ):
            self._drain_tasks = False
        self._maybe_request_rollover_from_update()
        return self.status()

    def _apply_control_state(self, target_state: str) -> None:
        """Apply one valid live-state transition without double revision."""
        if self._state not in {"running", "paused"}:
            return
        if self._state != target_state:
            self._state = target_state
            self._revision += 1

    @workflow.signal
    async def reset(self) -> None:
        self._state = "resetting"
        self._revision += 1
        self._closed = True
        for task in self._poll_tasks.values():
            task.cancel()

    @workflow.update
    async def replace_graph(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        """Swap future run snapshots while retaining queued events and baselines.

        Existing run children already hold their own snapshot. Pausing only
        admissions lets those children finish before this update is called.
        """
        self.validate_graph(graph)
        node_ids = {node["id"] for node in graph["nodes"]}
        for listener_id, spec in self._triggers.items():
            listener = spec.get("listener_args", {})
            if spec.get("trigger_node_id") not in node_ids:
                # Stop new admissions, but retain the old snapshot for events
                # already accepted by this trigger before its removal.
                spec["retired"] = True
                task = self._poll_tasks.get(listener_id)
                if task:
                    task.cancel()
                continue
            if spec.pop("retired", False) and spec.get("workflow_type") == "PollingTriggerWorkflow":
                self._poll_tasks[listener_id] = asyncio.create_task(self._poll_trigger(listener_id, spec))
            listener["nodes"] = graph["nodes"]
            listener["edges"] = graph["edges"]
            listener["parameter_snapshot"] = graph.get("parameters") or {}
            listener["graphVersion"] = graph.get("graphVersion", 2)
        self._revision += 1
        self._maybe_request_rollover_from_update()
        return {**self.status(), "applied": True}

    @replace_graph.validator
    def validate_graph(self, graph: Dict[str, Any]) -> None:
        if self._state != "paused":
            raise ApplicationError("Pause admissions before applying changes", non_retryable=True)
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
            raise ApplicationError("Graph snapshot requires nodes and edges lists", type="InvalidWorkflowGraphSnapshot", non_retryable=True)
        for node in graph["nodes"]:
            if not isinstance(node, dict) or "id" not in node:
                raise ApplicationError("Each graph node requires an id", type="InvalidWorkflowGraphSnapshot", non_retryable=True)
            try:
                hash(node["id"])
            except TypeError as exc:
                raise ApplicationError("Graph node ids must be hashable", type="InvalidWorkflowGraphSnapshot", non_retryable=True) from exc

    @workflow.signal
    async def register_trigger(self, spec: Dict[str, Any]) -> None:
        listener_id = str(spec["listener_id"])
        if listener_id in self._triggers:
            return
        self._triggers[listener_id] = spec
        if spec["workflow_type"] == "PollingTriggerWorkflow":
            self._poll_tasks[listener_id] = asyncio.create_task(self._poll_trigger(listener_id, spec))
        else:
            self._upsert_event_types_attribute()

    @workflow.signal
    async def on_event(self, event: Dict[str, Any]) -> None:
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")
        if not event_id or not event_type:
            return
        for listener_id, spec in self._triggers.items():
            if spec.get("retired"):
                continue
            if spec["workflow_type"] == "PollingTriggerWorkflow":
                continue
            if event_type not in set(spec.get("event_types") or [spec.get("event_type")]):
                continue
            dedup_key = f"{listener_id}:{event_id}"
            if not self._event_already_accepted(dedup_key):
                self._remember_event_id(dedup_key)
                self._events.append((listener_id, event))
                if self._event_accumulator_v1:
                    self._pending_event_ids.add(dedup_key)
        # A paused controller still receives matching signals; without this
        # check its history could overflow mid-pause with the run loop
        # parked. The flag wakes the run loop, which rolls over carrying
        # the queue (and the paused state) forward.
        self._maybe_request_rollover()

    def _next_event(self) -> Optional[int]:
        if self._state == "running" and self._events:
            return 0
        if self._state == "paused" and self._drain_tasks:
            return next((index for index, (listener_id, _) in enumerate(self._events)
                if (self._triggers.get(listener_id, {}).get("listener_args") or {}).get("node_type") == "taskTrigger"), None)
        return None

    @workflow.query
    def status(self) -> Dict[str, Any]:
        result = {
            "state": self._state, "revision": self._revision,
            "triggers": {key: value["trigger_node_id"] for key, value in self._triggers.items()},
            "queued_events": len(self._events),
        }
        if self._execution_control.enabled:
            result.update({
                "execution_control_version": 1,
                "participant_state": self._execution_control.state,
                "participant_revision": self._execution_control.revision,
                "producers_held": self._execution_control.producers_held,
                "live_roots": {key: dict(value) for key, value in self._live_roots.items()},
                "membership_epoch": self._membership_epoch,
                "active_actions": self._execution_control.status()["active_actions"],
                "pending_child_starts": self._execution_control.status()["pending_child_starts"],
                "checkpoint": self._execution_control.status()["checkpoint"],
            })
        return result

    @workflow.run
    async def run(self, control_data: Dict[str, Any] = None) -> Dict[str, Any]:
        control_data = control_data or {}
        if not self._carried_state_seeded:
            self._state = control_data.get("state", "running")
        self._seed_carried_state(control_data)
        queue_v2 = workflow.patched("controller-durable-queue-v2")
        accumulator_v1 = self._accumulator_enabled()
        while not self._closed:
            read_reviews = queue_v2 and self._state == "paused" and self._drain_tasks and self._next_event() is None and not self._review_overflow_checked
            if self._overflow and ((queue_v2 and self._state == "running" and self._durable_prefix == 0) or read_reviews or (not queue_v2 and not self._events)):
                self._queue_reads += 1
                page = await workflow.execute_activity("controller.queue.read", {
                    "controller_id": workflow.info().workflow_id,
                    "request_id": f"{workflow.info().run_id}:queue:{self._queue_reads}",
                    **({"review_listener_ids": [listener_id for listener_id, spec in self._triggers.items() if (spec.get("listener_args") or {}).get("node_type") == "taskTrigger"]} if read_reviews else {})},
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=DEFAULT_ACTIVITY_RETRY)
                incoming = [(str(item[0]), item[1]) for item in page["events"]]
                if queue_v2:
                    if accumulator_v1:
                        # A retry whose recent key aged out may already be in
                        # the live tail. Keep the older durable item's FIFO
                        # position rather than dispatching both copies.
                        incoming_ids = {self._event_key(*item) for item in incoming}
                        removed_prefix = sum(
                            self._event_key(*item) in incoming_ids
                            for item in self._events[:self._durable_prefix]
                        )
                        self._events = [
                            item for item in self._events
                            if self._event_key(*item) not in incoming_ids
                        ]
                        self._durable_prefix -= removed_prefix
                        self._pending_event_ids.update(incoming_ids)
                    self._events[0:0] = incoming
                    self._durable_prefix += len(incoming)
                    if read_reviews:
                        self._review_overflow_checked = not incoming
                else:
                    self._events.extend(incoming)
                self._overflow = page["more"]
            await workflow.wait_condition(
                lambda: (
                    self._closed
                    or self._can_requested
                    or self._next_event() is not None
                )
            )
            if self._closed:
                break
            if self._can_requested:
                await self._continue_as_new(control_data)
                # Unreachable in a real run; direct unit invocation returns.
                break
            selected_index = self._next_event() or 0
            was_durable = queue_v2 and selected_index < self._durable_prefix
            listener_id, event = self._events.pop(selected_index)
            if was_durable:
                self._durable_prefix -= 1
            spec = self._triggers.get(listener_id)
            if spec is None:
                if accumulator_v1:
                    self._finish_pending_event(listener_id, event)
                continue
            accepted = False
            try:
                await self._spawn_push_run(event, spec)
                accepted = True
            except _AdmissionDeferred:
                self._events.insert(selected_index, (listener_id, event))
                if was_durable:
                    self._durable_prefix += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — per-event isolation
                # One bad event (duplicate child id, exhausted broadcast
                # retries) must not fail the deployment's entire control
                # plane. Same isolation contract as the listener classes.
                if accumulator_v1 and isinstance(exc, WorkflowAlreadyStartedError):
                    # The deterministic child ID is the durable dispatch key.
                    # Temporal has already accepted this event's child start;
                    # retrying it forever would block all following events.
                    accepted = True
                else:
                    workflow.logger.error(
                        f"Controller push spawn failed for event.id={event.get('id')}: {exc}"
                    )
                if queue_v2 and not accepted:
                    self._events.insert(0, (listener_id, event))
                    self._durable_prefix += 1
                    await workflow.sleep(timedelta(seconds=5))
            if accumulator_v1 and accepted:
                self._finish_pending_event(listener_id, event)
            self._maybe_request_rollover()
        return {"state": self._state, "generation": control_data.get("generation")}

    # ---- continue-as-new machinery ------------------------------------

    def _seed_carried_state(self, control_data: Dict[str, Any], *, start_runtime: bool = True) -> None:
        """Restore snapshots once, then start runtime pollers from the run body.

        Versioned initialization uses only the command-free snapshot portion;
        legacy runs retain their original startup command order.
        """
        if not self._carried_state_seeded:
            self._revision = int(control_data.get("revision") or 0)
            self._execution_control.bind({
                **control_data,
                "execution_control_state": control_data.get("participant_state", control_data.get("state", "running")),
                "execution_control_revision": control_data.get("participant_revision", self._revision),
            })
            self._live_roots = {key: dict(value) for key, value in (control_data.get("live_roots") or {}).items()}
            self._membership_epoch = int(control_data.get("membership_epoch") or 0)
            for seen_id in control_data.get("seen_event_ids") or []:
                self._seen_event_ids[str(seen_id)] = None
            for pending in control_data.get("pending_events") or []:
                try:
                    listener_id, event = pending[0], pending[1]
                except (IndexError, TypeError, KeyError):
                    continue
                self._events.append((str(listener_id), event))
                # Initialization must remain command-free. Provisionally
                # index v1 carries, then discard the index if the run's patch
                # decision restores its pre-accumulator command path.
                if self._execution_control.enabled and self._event_accumulator_v1 is not False:
                    self._pending_event_ids.add(self._event_key(str(listener_id), event))
            for key, value in (control_data.get("triggers") or {}).items():
                self._triggers.setdefault(str(key), value)
            self._overflow = bool(control_data.get("overflow"))
            self._durable_prefix = int(control_data.get("durable_prefix", len(self._events)))
            self._drain_tasks = bool(control_data.get("drain_tasks"))
            self._carried_state_seeded = True
        if not start_runtime:
            return
        for listener_id, spec in self._triggers.items():
            if spec.get("workflow_type") == "PollingTriggerWorkflow" and listener_id not in self._poll_tasks:
                self._poll_tasks[listener_id] = asyncio.create_task(
                    self._poll_trigger(listener_id, spec)
                )
        if any(
            spec.get("workflow_type") != "PollingTriggerWorkflow"
            for spec in self._triggers.values()
        ):
            self._upsert_event_types_attribute()

    def _remember_event_id(self, dedup_key: str) -> None:
        self._seen_event_ids[dedup_key] = None
        if len(self._seen_event_ids) > _MAX_CARRIED_SEEN_IDS:
            oldest = next(iter(self._seen_event_ids))
            del self._seen_event_ids[oldest]

    @staticmethod
    def _event_key(listener_id: str, event: Dict[str, Any]) -> str:
        return f"{listener_id}:{event.get('id') or ''}"

    def _accumulator_enabled(self) -> bool:
        if self._event_accumulator_v1 is None:
            # The new branch must not change legacy generations or decisions
            # already recorded by an unpatched v1 controller run.
            self._event_accumulator_v1 = self._execution_control.enabled and workflow.patched(
                "controller-event-accumulator-v1"
            )
            if not self._event_accumulator_v1:
                self._pending_event_ids.clear()
        return self._event_accumulator_v1

    def _event_already_accepted(self, dedup_key: str) -> bool:
        return dedup_key in self._seen_event_ids or (
            self._accumulator_enabled() and dedup_key in self._pending_event_ids
        )

    def _finish_pending_event(self, listener_id: str, event: Dict[str, Any]) -> None:
        dedup_key = self._event_key(listener_id, event)
        self._pending_event_ids.discard(dedup_key)
        # Retain the most recently completed work as well as recent arrivals.
        self._seen_event_ids.pop(dedup_key, None)
        self._remember_event_id(dedup_key)

    def _history_pressure(self) -> bool:
        from services.temporal.trigger_listener_workflow import _history_pressure

        return _history_pressure(_HISTORY_SOFT_CAP)

    def _maybe_request_rollover(self) -> None:
        if not self._closed and not self._can_requested:
            if self._history_pressure():
                self._can_requested = True

    def _messaging_enabled(self) -> bool:
        if self._messaging_v1 is None:
            # Evaluate only from runtime handlers. @workflow.init must remain
            # command-free, and old generations and pre-marker v1 histories
            # retain their previous control and rollover decisions.
            self._messaging_v1 = self._execution_control.enabled and workflow.patched(
                "controller-messaging-v1"
            )
        return self._messaging_v1

    def _maybe_request_rollover_from_update(self) -> None:
        # Updates add history even when there are no Signals or pollers. Wake
        # the main run loop, which alone owns CAN and the handler completion
        # fence, rather than continuing as new from an Update handler.
        if self._messaging_enabled():
            self._maybe_request_rollover()

    async def _continue_as_new(self, control_data: Dict[str, Any]) -> None:
        """Roll the run over, carrying everything a controller must keep.

        Poll tasks are cancelled first — their provider ``seen_ids``
        baselines were written back into each trigger spec's
        ``listener_args`` after every cycle, so the carried specs restart
        the loops in the new run without re-emitting old items. Works
        while paused too: the paused state (and queued events) carry.
        """
        accumulator_v1 = self._accumulator_enabled()
        if self._execution_control.enabled:
            # Stop and rollover do not cancel an admitted provider poll. Let its
            # result update the seen-id baseline and durable pending queue.
            await workflow.wait_condition(lambda: self._execution_control.status()["checkpoint"])
            await workflow.wait_condition(workflow.all_handlers_finished)
        if accumulator_v1 and self._closed:
            return
        for task in self._poll_tasks.values():
            task.cancel()
        if self._poll_tasks:
            await asyncio.gather(*self._poll_tasks.values(), return_exceptions=True)
        queue_v2 = workflow.patched("controller-durable-queue-v2")
        if queue_v2:
            from services.temporal.controller_queue import event_batches
            # Carried events precede previously spilled pages. New arrivals
            # belong after those pages, even when signals arrived mid-drain.
            prefix_count = self._durable_prefix if self._overflow else len(self._events)
            prefix = self._events[:prefix_count]
            chunks = list(event_batches(prefix, max_events=_MAX_CARRIED_EVENTS))
            import json
            import hashlib
            async def spill(batch, prepend=False):
                base = {"controller_id": workflow.info().workflow_id, "prepend": prepend}
                async def invoke(payload):
                    await workflow.execute_activity("controller.queue.spill", payload, start_to_close_timeout=timedelta(seconds=60), retry_policy=DEFAULT_ACTIVITY_RETRY)
                if len(batch) == 1:
                    encoded = json.dumps(batch[0], separators=(",", ":"), ensure_ascii=True)
                    if len(encoded.encode()) > 512_000:
                        listener_id, event = batch[0]
                        key = hashlib.sha256(f'{base["controller_id"]}:{listener_id}:{event["id"]}'.encode()).hexdigest()
                        pieces = [encoded[index:index + 200_000] for index in range(0, len(encoded), 200_000)]
                        for index, piece in enumerate(pieces):
                            await invoke({**base, "chunk_id": key, "index": index, "chunk": piece})
                        await invoke({**base, "chunk_id": key, "count": len(pieces)})
                        return
                await invoke({**base, "events": batch})
            carry = chunks[0] if chunks and len(json.dumps(chunks[0]).encode()) <= 512_000 else []
            for batch in reversed(chunks[1:] if carry else chunks):
                await spill(batch, prepend=True)
                self._overflow = True
                if accumulator_v1 and self._closed:
                    return
            if accumulator_v1:
                # Every spill yields to Signals. Re-snapshot the newly appended
                # tail until it is empty, then recheck after waiting for message
                # handlers. There is no await between this final fence and CAN.
                cursor = prefix_count
                while True:
                    while cursor < len(self._events):
                        tail_end = len(self._events)
                        for batch in event_batches(self._events[cursor:tail_end]):
                            await spill(batch)
                            self._overflow = True
                            if self._closed:
                                return
                        cursor = tail_end
                    await workflow.wait_condition(workflow.all_handlers_finished)
                    if self._closed:
                        return
                    if cursor == len(self._events):
                        break
            else:
                for batch in event_batches(self._events[prefix_count:]):
                    await spill(batch)
                    self._overflow = True
        else:
            durable_queue = workflow.patched("controller-durable-overflow-v1")
            carry = self._events[-_MAX_CARRIED_EVENTS:]
            if durable_queue and len(self._events) > _MAX_CARRIED_EVENTS:
                # Spill the tail, retaining the oldest accepted events in FIFO
                # order in the bounded carry. No accepted event is discarded.
                await workflow.execute_activity("controller.queue.spill", {
                    "controller_id": workflow.info().workflow_id,
                    "events": [list(item) for item in self._events[_MAX_CARRIED_EVENTS:]]},
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=DEFAULT_ACTIVITY_RETRY)
                self._overflow = True
                carry = self._events[:_MAX_CARRIED_EVENTS]
        if self._execution_control.enabled and not accumulator_v1:
            await workflow.wait_condition(workflow.all_handlers_finished)
        carried: Dict[str, Any] = {
            **control_data,
            "state": self._state,
            "revision": self._revision,
            "triggers": self._triggers,
            "pending_events": [
                list(item) for item in carry
            ],
            "overflow": self._overflow,
            "durable_prefix": len(carry),
            "drain_tasks": self._drain_tasks,
            "seen_event_ids": list(self._seen_event_ids)[-_MAX_CARRIED_SEEN_IDS:],
        }
        if self._execution_control.enabled:
            carried.update({
                **self._execution_control.carry(),
                "participant_state": self._execution_control.state,
                "participant_revision": self._execution_control.revision,
                "live_roots": self._live_roots,
                "membership_epoch": self._membership_epoch,
            })
            encoded = workflow.payload_converter().to_payloads([carried])
            input_bytes = sum(item.ByteSize() for item in encoded)
            if input_bytes > _CAN_INPUT_MAX_BYTES:
                raise ApplicationError(
                    f"Controller continuation requires {input_bytes} bytes; limit is {_CAN_INPUT_MAX_BYTES}.",
                    type="ControllerContinuationTooLarge", non_retryable=True,
                )
        workflow.logger.info(
            f"Controller continue_as_new: triggers={len(self._triggers)} "
            f"pending={len(carried['pending_events'])} state={self._state}"
        )
        workflow.continue_as_new(args=[carried])

    def _upsert_event_types_attribute(self) -> None:
        """Advertise push event types via the ControlEventTypes attribute.

        Lets ``dispatch.emit`` skip controllers with no matching trigger
        instead of signalling every running controller with every
        platform event. Controllers without the attribute are treated as
        match-all by dispatch.
        """
        # Upsert is a workflow command and the failure path logs through
        # ``workflow.logger``; both need the workflow event loop. Direct
        # unit invocation has no loop, so no-op there rather than raising
        # out of the exception handler.
        if not workflow.in_workflow():
            return
        event_types: set[str] = set()
        for spec in self._triggers.values():
            if spec.get("workflow_type") == "PollingTriggerWorkflow":
                continue
            for event_type in spec.get("event_types") or [spec.get("event_type")]:
                if event_type:
                    event_types.add(str(event_type))
        if not event_types:
            return
        try:
            workflow.upsert_search_attributes(
                [
                    SearchAttributePair(
                        SearchAttributeKey.for_keyword_list("ControlEventTypes"),
                        sorted(event_types),
                    )
                ]
            )
        except Exception as exc:  # noqa: BLE001 — attribute is an optimisation
            workflow.logger.warning(f"ControlEventTypes upsert failed (non-fatal): {exc}")

    # ---- spawning -------------------------------------------------------

    async def _spawn_push_run(self, event: Dict[str, Any], spec: Dict[str, Any]) -> None:
        from services.temporal.trigger_listener_workflow import (
            TriggerListenerWorkflow,
            event_workflow_search_attributes,
        )

        listener = TriggerListenerWorkflow()
        listener_args = spec["listener_args"]
        if self._execution_control.enabled:
            listener_args = {**listener_args, **self._execution_control.carry()}
        await listener._spawn_child_run(
            event,
            listener_args,
            admission_check=self._wait_until_review if self._drain_tasks and listener_args.get("node_type") == "taskTrigger" else self._wait_until_running,
            search_attributes=(
                event_workflow_search_attributes(
                    listener_args.get("workflow_id")
                )
            ),
            **({"child_start_control": self._execution_control} if self._execution_control.enabled else {}),
        )

    async def _wait_until_review(self) -> None:
        if not self._drain_tasks:
            await self._wait_until_running()
        if self._closed:
            raise asyncio.CancelledError

    async def _wait_until_running(self) -> None:
        if self._execution_control.enabled and (self._state != "running" or self._can_requested):
            raise _AdmissionDeferred
        if self._state != "running":
            await workflow.wait_condition(lambda: self._closed or self._state == "running")
        if self._closed:
            raise asyncio.CancelledError

    async def _poll_trigger(self, listener_id: str, spec: Dict[str, Any]) -> None:
        from services.temporal.polling_trigger_workflow import (
            PollingTriggerWorkflow,
            _ACTIVITY_TIMEOUT_MULT,
            _DEFAULT_POLL_INTERVAL_S,
        )
        from services.temporal.trigger_listener_workflow import (
            event_workflow_search_attributes,
        )

        listener_data = spec["listener_args"]
        node_type = listener_data["node_type"]
        activity_name = f"poll.{node_type}.v{listener_data.get('version', 1)}"
        params = listener_data.get("filter_params", {}) or {}
        poll_interval = int(params.get("poll_interval") or _DEFAULT_POLL_INTERVAL_S)
        # A few-second user-supplied interval overflows history in hours.
        poll_interval = max(_MIN_POLL_INTERVAL_S, poll_interval)
        activity_timeout_s = max(30, poll_interval * _ACTIVITY_TIMEOUT_MULT)
        seen_ids: set[str] = set(listener_data.get("seen_ids") or [])
        baseline = not seen_ids
        runner = PollingTriggerWorkflow()

        while not self._closed and listener_id in self._triggers:
            if self._state != "running":
                await workflow.wait_condition(lambda: self._closed or self._state == "running")
            if self._closed:
                return
            if baseline:
                baseline = False
                baseline_only = True
            else:
                await workflow.sleep(timedelta(seconds=poll_interval))
                if self._state != "running":
                    continue
                baseline_only = False
            payload = {
                "node_id": listener_data["trigger_node_id"], "params": params,
                "seen_ids": list(seen_ids), "baseline_only": baseline_only,
            }
            if self._execution_control.enabled:
                try:
                    async with self._execution_control.action():
                        result = await workflow.execute_activity(
                            activity_name, payload, activity_id=listener_data["trigger_node_id"],
                            start_to_close_timeout=timedelta(seconds=activity_timeout_s),
                            retry_policy=DEFAULT_ACTIVITY_RETRY,
                        )
                        seen_ids = set(result.get("seen_ids") or [])
                        listener_data["seen_ids"] = list(seen_ids)
                        for event in result.get("events") or []:
                            event_id = str(event.get("id") or "")
                            dedup_key = f"{listener_id}:{event_id}"
                            if event_id and not self._event_already_accepted(dedup_key):
                                self._remember_event_id(dedup_key)
                                self._events.append((listener_id, event))
                                if self._event_accumulator_v1:
                                    self._pending_event_ids.add(dedup_key)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # per-poll isolation
                    workflow.logger.error(f"Controlled polling trigger failed: {exc}")
                self._maybe_request_rollover()
                continue
            try:
                result = await workflow.execute_activity(
                    activity_name, payload, activity_id=listener_data["trigger_node_id"],
                    start_to_close_timeout=timedelta(seconds=activity_timeout_s),
                    retry_policy=DEFAULT_ACTIVITY_RETRY,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                workflow.logger.error(f"Controlled polling trigger failed: {exc}")
                self._maybe_request_rollover()
                continue
            seen_ids = set(result.get("seen_ids") or [])
            # Write the provider baseline back into the carried spec so a
            # continue-as-new (which cancels this task) restarts the loop
            # exactly where it left off instead of re-emitting old items.
            listener_data["seen_ids"] = list(seen_ids)
            for event in result.get("events") or []:
                event_id = str(event.get("id") or "")
                dedup_key = f"{listener_id}:{event_id}"
                if not event_id or dedup_key in self._seen_event_ids:
                    continue
                self._remember_event_id(dedup_key)
                if workflow.patched("controller-durable-queue-v2"):
                    # Persist in the controller queue before waiting for pause
                    # admission. CAN cancellation can no longer lose a seen event.
                    self._events.append((listener_id, event))
                    continue
                if self._state != "running":
                    await workflow.wait_condition(lambda: self._closed or self._state == "running")
                if self._closed:
                    return
                try:
                    await runner._spawn_child_run(
                        event,
                        listener_data,
                        admission_check=self._wait_until_running,
                        search_attributes=event_workflow_search_attributes(
                            listener_data.get("workflow_id")
                        ),
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as spawn_exc:  # noqa: BLE001 — per-event isolation
                    # A failed spawn must not silently kill this poll task
                    # (the trigger would stop firing forever while the
                    # controller still reported Running).
                    workflow.logger.error(
                        f"Controlled polling spawn failed for event.id={event_id}: {spawn_exc}"
                    )
            # Poll cycles burn history even with zero events — request
            # rollover from here too, so a quiet mailbox cannot ride the
            # controller into the server's hard termination ceiling.
            self._maybe_request_rollover()


__all__ = [
    "WorkflowControlWorkflow",
]
