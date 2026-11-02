import pytest
import asyncio
from types import SimpleNamespace
from temporalio.exceptions import ApplicationError

from services.temporal.pi_protocol import OperationLedger, canonical_digest, canonical_json


@pytest.fixture(autouse=True)
def receipt_checkpoint_patch(monkeypatch):
    # Direct coordinator unit calls do not run inside a Temporal history.
    monkeypatch.setattr("services.temporal.pi_workflow.workflow.patched", lambda _: True)


def _request(epoch=1, **changes):
    payload = {"scope": {"workflow_id": "w"}, "turn": 0, "purpose": "assistant", "epoch": epoch, **changes}
    return {"id": "turn:0:model", "kind": "model", "payload": payload}


def _ledger():
    ledger = OperationLedger()
    ledger.begin(0, {"turn:0:model": {"kind": "model", "payload": {
        "scope": {"workflow_id": "w"}, "turn": 0, "purpose": "assistant"}}})
    assert ledger.register(0) == 1
    return ledger


def test_cached_receipts_survive_driver_reconstruction_and_fence_old_attempts():
    ledger = _ledger()
    ledger.submit(_request())
    result = {"assistant_message": {"provider_state": {"signature": "signed"}}}
    ledger.complete("turn:0:model", result)
    assert ledger.register(0) == 2
    assert ledger.submit(_request(2))["status"] == "completed"
    assert ledger.status("turn:0:model", 2)["result"] == result
    assert ledger.status("turn:0:model", 1)["status"] == "park"
    with pytest.raises(ApplicationError, match="epoch"):
        ledger.submit(_request(1))
    with pytest.raises(ApplicationError, match="overwritten"):
        ledger.complete("turn:0:model", {"changed": True})


def test_semantic_conflicts_and_unadmitted_tool_positions_never_create_operations():
    ledger = _ledger()
    with pytest.raises(ApplicationError, match="scope"):
        ledger.submit(_request(scope={"workflow_id": "another"}))
    assert ledger.operations == {}
    ledger.submit(_request(extra="first"))
    with pytest.raises(ApplicationError, match="different content"):
        ledger.submit(_request(extra="second"))
    with pytest.raises(ApplicationError, match="position"):
        ledger.submit({**_request(), "id": "turn:0:tool:0:unrecorded", "kind": "tool"})


def test_precompleted_delegation_receipt_attaches_without_repeating_effect():
    ledger = _ledger()
    ledger.complete("turn:0:model", {"exact": True})
    assert ledger.submit(_request())["status"] == "completed"
    assert ledger.status("turn:0:model", 1)["result"] == {"exact": True}


def test_sdk_rejection_outcome_cannot_change_across_attempts():
    ledger = _ledger()
    ledger.submit(_request(receipt_only=True, error="rejected"))
    ledger.register(0)
    with pytest.raises(ApplicationError, match="different content"):
        ledger.submit(_request(2, receipt_only=False))


def test_unadmitted_receipts_are_terminal_and_do_not_mutate_ledger():
    ledger = _ledger()
    with pytest.raises(ApplicationError, match="admitted"):
        ledger.complete("turn:0:tool:unknown", {})
    assert ledger.operations == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("consumed,completed", [(["model", "model"], True), (["model"], False)])
async def test_checkpoint_requires_exact_unique_completed_receipts(consumed, completed):
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    value = PiCodingAgentWorkflow({})
    scope = {"workflow_id": "w"}
    value._pi_request = {"scope": scope, "messages": [], "config_digest": "cfg", "binding_digest": "bindings"}
    value._pi.begin(0, {"model": {"kind": "model", "payload": {}}})
    value._pi.register(0)
    if completed:
        value._pi.complete("model", {})
    future = asyncio.get_running_loop().create_future()
    future.set_result({"status": "completed", "epoch": 1, "scope": scope, "turn": 0,
        "config_digest": "cfg", "binding_digest": "bindings", "consumed_operations": consumed,
        "projection_digest": canonical_digest([]), "snapshot": {"pi_blob": "bad"}})
    value._pi_driver = future
    with pytest.raises(ApplicationError, match="every recorded operation"):
        await value._complete_runtime_turn([])
    assert value._pi_checkpoint is None


@pytest.mark.asyncio
async def test_canonical_roster_change_rebuilds_instead_of_accepting_partial_projection():
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    value = PiCodingAgentWorkflow({})
    scope = {"workflow_id": "w"}
    value._pi_request = {"scope": scope, "messages": [], "config_digest": "cfg", "binding_digest": "bindings"}
    value._pi.begin(0, {})
    value._pi.register(0)
    future = asyncio.get_running_loop().create_future()
    future.set_result({"status": "completed", "epoch": 1, "scope": scope, "turn": 0,
        "config_digest": "cfg", "binding_digest": "bindings", "consumed_operations": [],
        "projection_digest": canonical_digest([]), "snapshot": {"pi_blob": "unused"}})
    value._pi_driver = future
    await value._complete_runtime_turn([{"role": "system", "content": "Updated connected teammates"}])
    assert value._pi_checkpoint is None and "_pi_checkpoint" not in value._pi_context
    assert value._pi_request == {} and value._pi.operations == {}


@pytest.mark.asyncio
async def test_rollover_defers_until_detached_child_is_terminal():
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    value = PiCodingAgentWorkflow({})
    future = asyncio.get_running_loop().create_future()
    handle = SimpleNamespace(id="child", first_execution_run_id="chain", done=future.done)
    value._execution_control.track_child(handle)
    assert await value._before_runtime_rollover() is False
    future.set_result({})
    assert await value._before_runtime_rollover() is True
    assert value._execution_control.children == {}


def test_local_control_carries_versioned_state_without_a_controller_identity():
    from services.temporal.pi_control import PiLocalExecutionControl
    control = PiLocalExecutionControl()
    control.bind({"pi_local_control_version": 1, "pi_local_control_state": "paused", "pi_local_control_revision": 7})
    carried = control.carry()
    assert control.enabled and control.state == "paused" and control.revision == 7
    assert carried["pi_local_control_version"] == 1 and carried["pi_local_control_revision"] == 7
    assert "controller_workflow_id" not in carried and "execution_control_version" not in carried


@pytest.mark.asyncio
async def test_native_workflow_rejects_a_frozen_pi_coding_admission():
    from services.temporal.agent_workflow import AgentWorkflow
    value = AgentWorkflow({"node_type": "coding_agent", "pi_harness_version": 1})
    with pytest.raises(ApplicationError) as failure:
        await value.run({"node_type": "coding_agent", "pi_harness_version": 1})
    assert failure.value.type == "AgentHarnessRoutingConflict" and failure.value.non_retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [{"node_type": "coding_agent"}, {"node_type": "aiAgent", "pi_harness_version": 1}])
async def test_native_unmarked_and_other_agent_admissions_keep_their_runtime(monkeypatch, context):
    from services.temporal.agent_workflow import AgentWorkflow
    value = AgentWorkflow(context)
    async def completed(_):
        return {"success": True}
    monkeypatch.setattr(value, "_run_impl", completed)
    assert await value.run(context) == {"success": True}


@pytest.mark.asyncio
async def test_pi_subclass_accepts_the_frozen_pi_admission(monkeypatch):
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    context = {"node_type": "coding_agent", "pi_harness_version": 1}
    value = PiCodingAgentWorkflow(context)
    async def completed(_):
        return {"success": True}
    monkeypatch.setattr(value, "_run_impl", completed)
    value._execution_control = SimpleNamespace(bind=lambda _: None, enabled=False)
    assert await value.run(context) == {"success": True}


@pytest.mark.asyncio
async def test_stale_epoch_checkpoint_is_rejected_without_committing_cache():
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    value = PiCodingAgentWorkflow({})
    value._pi_request = {"scope": {"workflow_id": "w"}, "messages": [], "config_digest": "cfg", "binding_digest": "bindings"}
    value._pi.begin(0, {})
    value._pi.register(0)
    future = asyncio.get_running_loop().create_future()
    future.set_result({"status": "completed", "epoch": 0})
    value._pi_driver = future
    with pytest.raises(ApplicationError, match="stale driver"):
        await value._complete_runtime_turn([])
    assert value._pi_checkpoint is None


@pytest.mark.asyncio
async def test_changed_configuration_discards_derived_snapshot(monkeypatch):
    from services.temporal import pi_workflow
    value = pi_workflow.PiCodingAgentWorkflow({})
    messages = [{"role": "user", "content": "task"}]
    value._pi_checkpoint = {"context_digest": canonical_digest(messages), "binding_digest": canonical_digest([]),
        "config_digest": "previous configuration", "reference": {"pi_blob": "old"}}
    monkeypatch.setattr(pi_workflow.workflow, "info", lambda: SimpleNamespace(workflow_id="workflow"))
    async def admitted(_):
        pass
    monkeypatch.setattr(value, "_await_request", admitted)
    await value._before_runtime_model_step({"messages": messages, "tools": [], "model": "new", "provider": "openai"},
        {}, {"node_id": "coding"}, 0)
    assert "snapshot" not in value._pi_request


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["node", "policy", "principal"])
async def test_authoritative_binding_and_principal_changes_have_distinct_digests(monkeypatch, change):
    from services.temporal import pi_workflow
    monkeypatch.setattr(pi_workflow.workflow, "info", lambda: SimpleNamespace(workflow_id="workflow"))
    async def admitted(_):
        pass
    definition = {"name": "same", "parameters": {"type": "object"}}
    digests = []
    for index in range(2):
        value = pi_workflow.PiCodingAgentWorkflow({})
        monkeypatch.setattr(value, "_await_request", admitted)
        binding = {"definition": definition, "tool_node_id": "second" if change == "node" and index else "first",
            "activity_policy": {"task_queue": "second" if change == "policy" and index else "first"}}
        context = {"user_id": "second" if change == "principal" and index else "first"}
        await value._before_runtime_model_step({"messages": [], "tools": [definition], "model": "stub", "provider": "openai"},
            context, {"node_id": "coding"}, 0, [binding])
        digests.append((value._pi_request["binding_digest"], value._pi_request["config_digest"]))
        assert value._pi_request["tools"] == [definition]
    assert digests[0] != digests[1]


@pytest.mark.asyncio
async def test_stop_checkpoint_quiesces_driver_and_fences_epoch_after_leaf_drain():
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    value = PiCodingAgentWorkflow({})
    status = {"state": "paused", "producers_held": False, "checkpoint": True}
    async def drained(_):
        return status
    value._execution_control = SimpleNamespace(wait_for_checkpoint=drained, status=lambda: status)
    value._pi.begin(0, {})
    value._pi.register(0)
    handle = asyncio.get_running_loop().create_future()
    handle.set_result({"status": "parked"})
    value._pi_driver = handle
    receipt = await value.wait_for_checkpoint({"state": "paused"})
    assert receipt["pi_driver_checkpoint"] is True
    assert receipt["pi_driver_epoch"] == 2
    assert value._pi_driver is None and value._pi_stop_pending
    assert value._pi.status("anything", 1)["status"] == "park"


@pytest.mark.parametrize("operation", [None, [], {"id": []}, {"id": "turn:0:model", "payload": []},
    {"id": "turn:0:model", "kind": "model", "payload": {"epoch": "1"}}])
def test_malformed_operation_is_terminal_protocol_error(operation):
    ledger = _ledger()
    with pytest.raises(ApplicationError) as error:
        ledger.submit(operation)
    assert error.value.non_retryable


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_a_non_finite_number_is_a_terminal_protocol_error_not_a_workflow_bug(value):
    from services.temporal._exits import is_workflow_bug

    with pytest.raises(ApplicationError) as error:
        canonical_json({"result": [value]})
    assert error.value.type == "PiProtocolConflict" and error.value.non_retryable
    assert not is_workflow_bug(error.value)


@pytest.mark.parametrize("value,expected", [(1.0, "1"), (-0.0, "0"), (1e-7, "1e-7"),
    (1e-6, "0.000001"), (1e20, "100000000000000000000"), (1e21, "1e+21"), (0.7, "0.7")])
def test_hash_numbers_match_javascript(value, expected):
    assert canonical_json(value) == expected
    assert canonical_digest({"b": value, "a": "😀"}) == canonical_digest({"a": "😀", "b": value})
