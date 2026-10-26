"""Pi SDK coordinator layered over the existing durable agent business loop.

Only the harness changes. The inherited loop continues to own tool policies,
approvals, Context persistence, team permits, task waits, compaction and events.
SDK maintenance never occupies a business action or a child concurrency permit.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityCancellationType

from .agent_workflow import AgentWorkflow, LLM_STEP_HEARTBEAT_TIMEOUT, LLM_STEP_RETRY, llm_attempt_timeout
from .pi_protocol import DRIVER_QUEUE, INLINE_LIMIT, OperationLedger, canonical_digest, canonical_json, conflict, tool_operation_id
from .pi_control import PiDescendantExecutionControl, PiLocalExecutionControl


@workflow.defn(sandboxed=False, name="PiCodingAgentWorkflow")
class PiCodingAgentWorkflow(AgentWorkflow):
    _pi_runtime_enabled = True

    @workflow.init
    def __init__(self, context: dict | None = None) -> None:
        super().__init__(context)
        if context is not None:
            self._execution_control = (PiDescendantExecutionControl() if context.get("execution_control_version") == 1
                else PiLocalExecutionControl())
            self._execution_control.bind(context)
        self._pi = OperationLedger()
        self._pi_driver = None
        self._pi_driver_sequence = 0
        self._pi_request: dict = {}
        self._pi_context: dict = context or {}
        self._parent_node_id = (context or {}).get("parent_node_id")
        self._pi_calls: list[dict] = []
        self._pi_turn_start = 0
        self._pi_detached = False
        self._pi_checkpoint = (context or {}).get("_pi_checkpoint")
        self._pi_checkpointing = False
        self._pi_receipt_checkpointing = False
        self._pi_ready = False
        self._pi_validations: dict[str, dict] = {}
        self._pi_stop_pending = False

    @workflow.update
    async def wait_for_checkpoint(self, payload: dict | None = None) -> dict:
        status = await self._execution_control.wait_for_checkpoint(payload)
        if status["state"] == "paused" or status["producers_held"]:
            self._pi_stop_pending = True
            terminal = await self._stop_driver()
            if self._pi_checkpointing and ((terminal or {}).get("status") == "completed" or self._pi_receipt_checkpointing):
                # An older driver can park concurrently with round completion.
                # Drain its receipt-only reconstruction before fencing the epoch;
                # no paid operation is admitted by that reconstruction.
                await workflow.wait_condition(lambda: not self._pi_checkpointing)
            self._pi.epoch += 1
        return {**self._execution_control.status(), "pi_driver_checkpoint": self._pi_driver is None,
            "pi_driver_epoch": self._pi.epoch}

    @workflow.signal
    async def pause(self) -> None:
        self._control_paused = True
        await self._execution_control.set_control_state({"state": "paused", "revision": self._execution_control.revision + 1})

    @workflow.signal
    async def resume(self) -> None:
        self._control_paused = False
        await self._execution_control.set_control_state({"state": "running", "revision": self._execution_control.revision + 1,
            "producers_held": False})

    @wait_for_checkpoint.validator
    def validate_wait_for_checkpoint(self, payload: dict | None = None) -> None:
        self._execution_control.validate_control(payload)

    @workflow.update
    async def register_driver(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or not isinstance(payload.get("turn"), int) or isinstance(payload.get("turn"), bool):
            raise conflict("Pi driver registration requires an integer turn")
        return {"epoch": self._pi.register(int(payload.get("turn", -1)))}

    @workflow.update
    async def submit_operation(self, request: dict) -> dict:
        return self._pi.submit(request)

    @workflow.update
    async def assistant_ready(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise conflict("Pi assistant validation must be a mapping")
        if request.get("turn") != self._pi.turn or request.get("epoch") != self._pi.epoch or request.get("context_digest") != self._pi_request.get("context_digest") or request.get("binding_digest") != self._pi_request.get("binding_digest"):
            raise conflict("Pi assistant validation belongs to a different turn or driver")
        validations = request.get("calls")
        if not isinstance(validations, list) or len(validations) != len(self._pi_calls):
            raise conflict("Pi assistant validation omitted a recorded call")
        for index, value in enumerate(validations):
            if not isinstance(value, dict) or value.get("call_index") != index or value.get("call_id") != (self._pi_calls[index].get("id") or "") or not isinstance(value.get("receipt_only"), bool):
                raise conflict("Pi assistant validation changed a recorded call identity")
        normalized = {str(index): {"receipt_only": value["receipt_only"], "error": value.get("error")}
            for index, value in enumerate(validations)}
        if self._pi_ready and canonical_digest(normalized) != canonical_digest(self._pi_validations):
            raise conflict("Pi assistant validation changed during reconstruction")
        self._pi_validations = normalized
        self._pi_ready = True
        return {"ready": True}

    @workflow.query
    def operation_status(self, request: dict) -> dict:
        if not isinstance(request, dict) or not isinstance(request.get("epoch"), int) or isinstance(request.get("epoch"), bool):
            raise conflict("Pi status requires an integer driver epoch")
        epoch = int(request.get("epoch") or 0)
        stopped = (self._execution_control.enabled and
            (self._execution_control.state != "running" or self._execution_control.producers_held)) or self._control_paused
        if self._pi.parked or epoch != self._pi.epoch:
            return {"status": "park", "epoch": self._pi.epoch}
        if request.get("id") and (self._pi.operations.get(request["id"]) or {}).get("status") == "completed":
            return self._pi.status(request["id"], epoch)
        if stopped and not self._pi_checkpointing:
            return {"status": "park", "epoch": self._pi.epoch}
        if not request.get("id"):
            return {"status": "running", "epoch": self._pi.epoch}
        return self._pi.status(request["id"], epoch)

    @workflow.run
    async def run(self, context: dict | None = None) -> dict:
        if not context or context.get("pi_harness_version") != 1 or context.get("node_type") != "coding_agent":
            raise conflict("Pi workflow requires an admitted Coding Agent runtime marker")
        self._pi_context = context
        try:
            try:
                result = await super().run(context)
                if not result.get("success", True):
                    await self._clear_failed_skills(context)
                return result
            except Exception as exc:
                from temporalio.exceptions import is_cancelled_exception
                if is_cancelled_exception(exc):
                    raise
                cause = getattr(exc, "cause", None) or exc
                await self._clear_failed_skills(context)
                await self._emit_phase(context["node_id"], context.get("workflow_id"), max(self._pi.turn, 0),
                    max(self._pi.turn + 1, 1), phase="failed", status="error")
                return {"success": False, "error": "Pi could not safely complete the Coding Agent turn",
                    "error_type": getattr(cause, "type", None) or "PiProtocolConflict"}
        finally:
            # Cancellation and terminal failure cannot leave a driver process
            # occupying private capacity while native cleanup drains children.
            await self._stop_driver(cancel=True)
            self._pi_checkpointing = False
            self._pi_receipt_checkpointing = False

    async def _clear_failed_skills(self, context):
        await workflow.execute_activity("agent.skill.clear", {"workflow_id": context.get("workflow_id"),
            "execution_id": context.get("execution_id"), "agent_node_id": context.get("node_id")},
            activity_id="pi-clear-skills-failed", start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3))

    def _operation_payload(self, *, purpose=None, call_index=None, call_id=None) -> dict:
        fields = {key: self._pi_request[key] for key in
            ("scope", "turn", "context_digest", "config_digest", "binding_digest")}
        if purpose:
            fields["purpose"] = purpose
        else:
            fields.update(call_index=call_index, call_id=call_id or "")
        return fields

    async def _start_driver(self) -> None:
        if self._pi_stop_pending and not self._pi_receipt_checkpointing:
            await self._wait_until_resumed()
            self._pi_stop_pending = False
        if self._pi_driver is not None:
            if not self._pi_driver.done():
                return
            result = await self._pi_driver
            if result.get("status") == "completed":
                raise conflict("Pi driver completed before the coordinator accepted its checkpoint")
            self._pi_driver = None
        self._pi.parked = False
        self._pi_driver_sequence += 1
        self._pi_driver = workflow.start_activity("coding.pi_drive_turn", self._pi_request,
            activity_id=f"pi-driver-{self._pi.turn}-{self._pi_driver_sequence}", task_queue=DRIVER_QUEUE,
            start_to_close_timeout=timedelta(minutes=30), heartbeat_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30),
                maximum_attempts=0, non_retryable_error_types=["PiProtocolConflict", "PiRuntimeMissing", "PiSDKFailure", "PiSnapshotIncompatible"]),
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)

    async def _stop_driver(self, *, cancel=False) -> None:
        self._pi.parked = True
        handle = self._pi_driver
        if handle is None:
            return None
        if cancel and not handle.done():
            handle.cancel()
        try:
            return await handle
        except BaseException:
            if not cancel:
                raise
        finally:
            if self._pi_driver is handle:
                self._pi_driver = None

    async def _await_request(self, identity: str) -> None:
        while identity not in self._pi.operations or self._pi.operations[identity].get("fingerprint") is None:
            await self._wait_until_resumed()
            await self._start_driver()
            handle = self._pi_driver
            await workflow.wait_condition(lambda: (identity in self._pi.operations and
                self._pi.operations[identity].get("fingerprint") is not None) or handle.done())
            if handle.done() and (identity not in self._pi.operations or self._pi.operations[identity].get("fingerprint") is None):
                result = await handle
                if self._pi_driver is handle:
                    self._pi_driver = None
                if result.get("status") != "parked":
                    raise conflict("Pi driver omitted an admitted operation")

    async def _receipt(self, identity: str, result: Any) -> None:
        if len(canonical_json(result).encode("utf-8")) > INLINE_LIMIT:
            result = await workflow.execute_activity("coding.pi_put_blob", {
                "scope": self._pi_request["scope"], "kind": "receipt", "payload": result},
                activity_id=f"pi-blob-{identity}", start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=3))
        self._pi.complete(identity, result)

    async def _before_runtime_model_step(self, llm_payload, context, payload, iteration, tools=None):
        self._pi_context = context
        self._pi_turn_start = len(llm_payload["messages"])
        self._pi_detached = False
        self._pi_calls = []
        self._pi_ready = False
        self._pi_validations = {}
        scope = {"workflow_id": workflow.info().workflow_id,
            "generation_id": str(context.get("generation") or 0), "node_id": payload["node_id"]}
        config = {key: llm_payload.get(key) for key in
            ("provider", "model", "system_message", "temperature", "max_tokens", "thinking_config", "effort")}
        config.update(principal=str(context.get("user_id") or "owner"),
            tool_mode=payload.get("tool_mode", "connected"))
        if payload.get("runtime_behavior_version") == 1:
            config["runtime_behavior_version"] = 1
        self._pi_request = {"turn": iteration, "scope": scope,
            "context_digest": canonical_digest(llm_payload["messages"]),
            "config_digest": canonical_digest(config),
            "binding_digest": canonical_digest(tools if tools is not None else llm_payload["tools"]),
            "cwd": payload.get("workspace_dir") or ".", "messages": list(llm_payload["messages"]),
            "tools": llm_payload["tools"], "model": {"id": llm_payload["model"], "provider": llm_payload["provider"],
                "maxTokens": llm_payload.get("max_tokens", 4096), "contextWindow": payload.get("context_window") or 128000}}
        # A cache never selects the transcript. Branching and compaction change
        # this digest and therefore discard its derived reconstruction state.
        if self._pi_checkpoint and self._pi_checkpoint.get("context_digest") == self._pi_request["context_digest"] and self._pi_checkpoint.get("binding_digest") == self._pi_request["binding_digest"] and self._pi_checkpoint.get("config_digest") == self._pi_request["config_digest"]:
            self._pi_request["snapshot"] = self._pi_checkpoint["reference"]
        model_id = f"turn:{iteration}:model"
        self._pi.begin(iteration, {model_id: {"kind": "model", "payload": self._operation_payload(purpose="assistant")}})
        await self._await_request(model_id)

    async def _execute_model_step(self, llm_payload, context, payload, iteration):
        model_id = f"turn:{iteration}:model"
        step = await workflow.execute_activity("agent.execute_llm_step", args=[llm_payload],
            activity_id=f"llm-step-{iteration + 1}", start_to_close_timeout=llm_attempt_timeout(payload),
            heartbeat_timeout=LLM_STEP_HEARTBEAT_TIMEOUT, retry_policy=LLM_STEP_RETRY)
        self._pi_calls = list(step.get("calls") or [])
        for index, call in enumerate(self._pi_calls):
            identity = tool_operation_id(iteration, index, call)
            self._pi.expected[identity] = {"kind": "tool", "payload": self._operation_payload(
                call_index=index, call_id=call.get("id") or "")}
        await self._receipt(model_id, step)
        return step

    async def _before_runtime_tool_batch(self, calls, tools):
        while not self._pi_ready:
            await self._wait_until_resumed()
            await self._start_driver()
            handle = self._pi_driver
            await workflow.wait_condition(lambda: self._pi_ready or handle.done())
            if handle.done() and not self._pi_ready:
                result = await handle
                if self._pi_driver is handle:
                    self._pi_driver = None
                if result.get("status") != "parked":
                    raise conflict("Pi did not validate its recorded assistant calls")
        bindings = {entry["name"]: entry for entry in tools}
        # These native branches start an independent bounded child batch, or
        # await durable tasks. Park the parent before children need driver slots.
        self._pi_detached = any(str(call.get("name") or "").startswith("delegate_to_") or
            (bindings.get(call.get("name")) or {}).get("node_type") == "taskManager" for call in calls)
        if self._pi_detached:
            await self._stop_driver()

    async def _collect_tool_receipts(self, messages):
        wires = [message for message in messages[self._pi_turn_start:] if message.get("role") == "tool"]
        positions = {str(call.get("id") or ""): index for index, call in enumerate(self._pi_calls)}
        ordered = {}
        for wire in wires:
            identity_key = str(wire.get("tool_call_id") or "")
            index = positions.get(identity_key)
            if index is None or index in ordered or str(wire.get("name") or "") != str(self._pi_calls[index].get("name") or ""):
                raise conflict("Pi tool results duplicate or change a recorded call identity")
            ordered[index] = wire
            call = self._pi_calls[index]
            identity = tool_operation_id(self._pi.turn, index, call)
            if (self._pi.operations.get(identity) or {}).get("status") != "completed":
                await self._receipt(identity, wire)
        return [ordered[index] for index in sorted(ordered)]

    def _runtime_tool_call_allowed(self, call_index, call):
        return not (self._pi_validations.get(str(call_index)) or {}).get("receipt_only", True)

    async def _before_runtime_tool_call(self, call_index, call, messages):
        await self._collect_tool_receipts(messages)
        if (self._pi_validations.get(str(call_index)) or {}).get("receipt_only"):
            return "Pi rejected the recorded tool call; no tool was executed"
        if not self._pi_detached:
            identity = tool_operation_id(self._pi.turn, call_index, call)
            await self._await_request(identity)
            if self._pi.operations[identity].get("receipt_only"):
                return "Pi rejected the recorded tool call; no tool was executed"

    async def _complete_runtime_turn(self, messages):
        if not self._pi_request:
            return
        wires = await self._collect_tool_receipts(messages)
        if len(wires) != len(self._pi_calls):
            raise conflict("Pi complete turn is missing a tool receipt")
        expected = set(self._pi.expected)
        receipt_checkpoint = workflow.patched("pi-paused-receipt-checkpoint-v1")
        if receipt_checkpoint and any((self._pi.operations.get(identity) or {}).get("status") != "completed" for identity in expected):
            raise conflict("Pi checkpoint did not consume every recorded operation")
        self._pi_checkpointing = True
        self._pi_receipt_checkpointing = receipt_checkpoint
        # Completion is bookkeeping, not a new paid action. It must also run
        # while paused so admitted results are saved before a checkpoint parks.
        while True:
            if self._pi_driver is None or self._pi_driver.done():
                if self._pi_driver is not None:
                    result = await self._pi_driver
                    self._pi_driver = None
                    if result.get("status") == "completed":
                        break
                await self._start_driver()
            handle = self._pi_driver
            result = await handle
            if self._pi_driver is handle:
                self._pi_driver = None
            if result.get("status") == "completed":
                break
            # Stop may already have told this driver to park before completion
            # began. All effects now have receipts, so reconstruct the checkpoint
            # while admission is closed instead of holding an action until Resume.
            if not receipt_checkpoint:
                # Histories recorded before the fix waited for Resume here.
                self._pi_checkpointing = False
                await self._wait_until_resumed()
                self._pi_checkpointing = True
        if result.get("epoch") != self._pi.epoch or result.get("scope") != self._pi_request["scope"] or result.get("turn") != self._pi.turn or result.get("config_digest") != self._pi_request["config_digest"] or result.get("binding_digest") != self._pi_request["binding_digest"]:
            raise conflict("Pi checkpoint belongs to a stale driver or different configuration")
        consumed = result.get("consumed_operations")
        if not isinstance(consumed, list) or len(consumed) != len(expected) or any(not isinstance(item, str) for item in consumed) or set(consumed) != expected or any((self._pi.operations.get(identity) or {}).get("status") != "completed" for identity in expected):
            raise conflict("Pi checkpoint did not consume every recorded operation")
        projection_messages = [*self._pi_request["messages"],
            *[message for message in messages[self._pi_turn_start:] if message.get("role") == "assistant"], *wires]
        projection = canonical_digest(projection_messages)
        if result.get("projection_digest") != projection:
            raise conflict("Pi checkpoint canonical projection differs from Context")
        if canonical_digest(messages) == projection:
            self._pi_checkpoint = {"reference": result["snapshot"], "context_digest": projection,
                "binding_digest": self._pi_request["binding_digest"], "config_digest": self._pi_request["config_digest"]}
            self._pi_context["_pi_checkpoint"] = self._pi_checkpoint
        else:
            # Native agentBuilder may append an authoritative teammate roster
            # during this batch. Rebuild from Context rather than admitting a
            # snapshot whose projection omits that trusted canonical note.
            self._pi_checkpoint = None
            self._pi_context.pop("_pi_checkpoint", None)
        self._pi.operations.clear()
        self._pi.expected.clear()
        self._pi_request = {}
        self._pi.parked = True
        self._pi_checkpointing = False
        self._pi_receipt_checkpointing = False

    async def _before_runtime_rollover(self):
        if self._pi_driver is not None or self._pi_request or self._pi.operations or self._pi.expected or self._pi_checkpointing:
            raise conflict("Pi Continue-As-New requires a terminal driver and accepted complete round")
        # Task Manager runners return a queued receipt before their child is
        # finished. Keep the current run while any such handle remains live.
        return not self._execution_control.live_children()
