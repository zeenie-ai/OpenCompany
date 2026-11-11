"""Python/Bun transport boundaries, using the released packaged SDK offline."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import InterfaceError, OperationalError
from temporalio.client import WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from services.pi_transport import ScopedProtocolBlobs
from services.temporal import pi_activities
from services.temporal.pi_protocol import INLINE_LIMIT, canonical_digest, canonical_json, conflict


@pytest.mark.parametrize("cause", [
    conflict("Conflicting native operation containing a private continuation"),
    ApplicationError("Incompatible snapshot", type="PiSnapshotIncompatible"),
    RuntimeError("Unexpected coordinator rejection"),
])
def test_wrapped_update_rejections_are_terminal_and_redacted(cause):
    error = pi_activities.driver_failure(WorkflowUpdateFailedError(cause))
    assert error.non_retryable
    assert error.type == (cause.type if isinstance(cause, ApplicationError) else "PiProtocolConflict")
    assert "private continuation" not in str(error)
    assert "Unexpected coordinator rejection" not in str(error)


@pytest.mark.parametrize("status,retryable", [
    (RPCStatusCode.UNAVAILABLE, True),
    (RPCStatusCode.DEADLINE_EXCEEDED, True),
    (RPCStatusCode.RESOURCE_EXHAUSTED, True),
    (RPCStatusCode.ABORTED, True),
    (RPCStatusCode.INTERNAL, True),
    (RPCStatusCode.PERMISSION_DENIED, False),
    (RPCStatusCode.INVALID_ARGUMENT, False),
])
def test_rpc_failures_retry_only_classified_transient_statuses(status, retryable):
    error = pi_activities.driver_failure(RPCError("private RPC details", status, b""))
    assert error.type == ("PiTransportFailure" if retryable else "PiDriverFailure")
    assert error.non_retryable is not retryable
    assert "private RPC details" not in str(error)


@pytest.mark.parametrize("cause", [
    ConnectionError("private endpoint"),
    TimeoutError("private endpoint"),
    WorkflowUpdateRPCTimeoutOrCancelledError(),
    OperationalError("private SQL", {}, RuntimeError("private database")),
    InterfaceError("private SQL", {}, RuntimeError("private database")),
])
def test_transport_and_storage_interruptions_allow_receipt_reconstruction(cause):
    error = pi_activities.driver_failure(cause)
    assert error.type == "PiTransportFailure"
    assert not error.non_retryable
    assert "private" not in str(error)


@pytest.mark.asyncio
async def test_activity_keeps_transport_retryable_and_makes_unclassified_failures_terminal():
    driver = pi_activities.PiDriverActivities(MagicMock())
    retry = ApplicationError("Reconnect", type="PiTransportFailure")
    driver._drive_turn = AsyncMock(side_effect=retry)
    with pytest.raises(ApplicationError) as caught:
        await driver.drive_turn({})
    assert caught.value is retry
    driver._drive_turn = AsyncMock(side_effect=ApplicationError("Unclassified", type="Unexpected"))
    with pytest.raises(ApplicationError) as caught:
        await driver.drive_turn({})
    assert caught.value.type == "Unexpected" and caught.value.non_retryable
    driver._drive_turn = AsyncMock(side_effect=WorkflowUpdateFailedError(conflict("operation conflict")))
    with pytest.raises(ApplicationError) as caught:
        await driver.drive_turn({})
    assert caught.value.type == "PiProtocolConflict" and caught.value.non_retryable


def _wire(role, content="", calls=None):
    calls = calls or []
    return {"role": role, "content": content, "blocks": [
        *([{"type": "tool_result" if role == "tool" else "text", "text": content,
            "tool_call": None, "tool_call_id": None, "name": None, "metadata": {}, "source": None}] if content else []),
        *[{"type": "tool_call", "text": "", "tool_call": call, "tool_call_id": None,
            "name": None, "metadata": {}, "source": None} for call in calls],
    ], "tool_calls": calls, "tool_call_id": None, "name": None, "provider_state": {}}


def _run(blob_dir):
    messages = [_wire("system", "Preserve native context."), _wire("user", "Read the recorded identifier.")]
    return {"type": "run_turn", "protocol": 1, "turn": 0, "epoch": 2,
        "scope": {"workflow_id": "packaged-python-round", "generation_id": "7", "node_id": "coding"},
        "blob_dir": str(Path(blob_dir).resolve()), "cwd": str(Path(blob_dir).resolve()),
        "model": {"id": "native", "provider": "anthropic"}, "messages": messages,
        "context_digest": canonical_digest(messages), "config_digest": canonical_digest({"model": "native"}),
        "binding_digest": canonical_digest([]), "tools": [{"name": "record_id", "description": "Read a recorded identifier",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "integer"}},
                "required": ["record_id"], "additionalProperties": False}}]}


def _pipe():
    return SimpleNamespace(stdin=SimpleNamespace(write=MagicMock(), drain=AsyncMock()))


@pytest.mark.asyncio
async def test_send_counts_lf_in_exact_inline_bound_and_buffers_the_next_byte(tmp_path):
    proc = _pipe()
    value = {"type": "keepalive", "padding": ""}
    overhead = len((canonical_json(value) + "\n").encode())
    value["padding"] = "x" * (INLINE_LIMIT - overhead)
    await pi_activities.PiDriverActivities._send(proc, value)
    assert len(proc.stdin.write.call_args.args[0]) == INLINE_LIMIT
    proc.stdin.write.reset_mock()
    value["padding"] += "x"
    with pytest.raises(ApplicationError) as caught:
        await pi_activities.PiDriverActivities._send(proc, value)
    assert caught.value.type == "PiProtocolConflict" and caught.value.non_retryable
    proc.stdin.write.assert_not_called()
    bridge = ScopedProtocolBlobs(tmp_path, {"workflow_id": "send"}, 2)
    await pi_activities.PiDriverActivities._send(proc, value, bridge)
    frame = proc.stdin.write.call_args.args[0]
    assert frame.endswith(b"\n") and len(frame) <= INLINE_LIMIT
    envelope = json.loads(frame)
    assert envelope["type"] == value["type"]
    assert bridge.decode(envelope["pi_payload"]) == value


@pytest.mark.asyncio
async def test_send_large_run_keeps_only_the_trusted_bootstrap_inline(tmp_path):
    run = _run(tmp_path)
    run["messages"][1]["provider_state"] = {"continuation": "x" * INLINE_LIMIT}
    run["context_digest"] = canonical_digest(run["messages"])
    bridge = ScopedProtocolBlobs(tmp_path, run["scope"], run["epoch"])
    proc = _pipe()
    await pi_activities.PiDriverActivities._send(proc, run, bridge)
    frame = proc.stdin.write.call_args.args[0]
    assert len(frame) <= INLINE_LIMIT
    envelope = json.loads(frame)
    assert set(envelope) == {"type", "protocol", "scope", "epoch", "blob_dir", "pi_payload"}
    assert bridge.decode(envelope["pi_payload"]) == run


async def _fake_host(monkeypatch, tmp_path, line):
    proc = _pipe()
    proc.stdout = SimpleNamespace(readline=AsyncMock(return_value=line))
    proc.stderr = SimpleNamespace(read=AsyncMock(return_value=b""))
    close = AsyncMock()
    monkeypatch.setattr(pi_activities.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    monkeypatch.setattr(pi_activities, "close_host", close)
    monkeypatch.setattr(pi_activities.PiDriverActivities, "_heartbeat", AsyncMock())
    run = _run(tmp_path)
    bridge = ScopedProtocolBlobs(tmp_path, run["scope"], run["epoch"])
    handle = SimpleNamespace(execute_update=AsyncMock(), query=AsyncMock())
    try:
        return await pi_activities.PiDriverActivities(MagicMock())._run_host(
            "bun", Path("host.js"), {}, run, bridge, handle, AsyncMock(), AsyncMock())
    finally:
        close.assert_awaited_once_with(proc)


@pytest.mark.asyncio
@pytest.mark.parametrize("frame", [
    b"not-json\n", b"[]\n", b'{"type":"unexpected"}\n', b'{"type":"parked"}',
    b" " * INLINE_LIMIT + b"\n",
    b'{"type":"operation","id":3,"kind":"model","payload":{}}\n',
    b'{"type":"operation","id":"op","kind":"invalid","payload":{}}\n',
    b'{"type":"operation","id":"op","kind":"model","payload":[]}\n',
    b'{"type":"turn_checkpoint","snapshot":{},"consumed_operations":["duplicate","duplicate"],"projection_digest":"x"}\n',
    b'{"type":"parked","pi_payload":{"type":"error"}}\n',
], ids=["invalid-json", "array", "unknown", "missing-lf", "oversized", "invalid-id",
    "invalid-kind", "invalid-payload", "duplicate-consumed", "changed-envelope"])
async def test_unknown_and_malformed_host_frames_are_terminal(monkeypatch, tmp_path, frame):
    with pytest.raises(ApplicationError) as caught:
        await _fake_host(monkeypatch, tmp_path, frame)
    assert caught.value.type == "PiProtocolConflict" and caught.value.non_retryable


@pytest.mark.asyncio
async def test_host_eof_is_a_retryable_transport_interruption(monkeypatch, tmp_path):
    with pytest.raises(ApplicationError) as caught:
        await _fake_host(monkeypatch, tmp_path, b"")
    assert caught.value.type == "PiTransportFailure" and not caught.value.non_retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("code,retryable,error_type,diagnostic", [
    ("transport_failure", True, "PiTransportFailure", True),
    ("transport_write_failed", True, "PiTransportFailure", True),
    ("sdk_failure", False, "PiSDKFailure", True),
    ("snapshot_incompatible", False, "PiSnapshotIncompatible", True),
    ("projection_mismatch", False, "PiProtocolConflict", True),
    ("invalid_blob", False, "PiProtocolConflict", False),
    ("private-code", False, "PiProtocolConflict", False),
    ({"private": "continuation"}, False, "PiProtocolConflict", False),
    (["private-code"], False, "PiProtocolConflict", False),
    (None, False, "PiProtocolConflict", False),
])
async def test_host_error_codes_are_classified_without_exposing_diagnostics(monkeypatch, tmp_path, code, retryable, error_type, diagnostic):
    line = json.dumps({"type": "error", "code": code, "message": "private provider continuation"}).encode() + b"\n"
    with pytest.raises(ApplicationError) as caught:
        await _fake_host(monkeypatch, tmp_path, line)
    assert caught.value.type == error_type
    assert caught.value.non_retryable is not retryable
    assert "private" not in str(caught.value)
    assert "private" not in repr(caught.value.details)
    message = "Pi SDK host could not complete the coding turn"
    assert caught.value.message == (f"{message} ({code})" if diagnostic else message)
    assert caught.value.details == (({"host_code": code},) if diagnostic else ())


@pytest.mark.asyncio
@pytest.mark.parametrize("restored_conversation", [False, True], ids=["fresh", "restored-with-timestamps"])
async def test_packaged_bun_round_preserves_large_native_wires_across_python_boundary(monkeypatch, restored_conversation):
    from core.js_runtime import bun_binary
    from services import pi_runtime

    bun = bun_binary()
    distribution = Path(pi_runtime.__file__).resolve().parent.parent / "pi" / "dist"
    if not bun or not (distribution / "package.json").is_file():
        pytest.skip("Build server/pi's bundle and provide Bun for the packaged boundary gate")
    # Outside the repository: imports cannot fall back to development node_modules.
    with tempfile.TemporaryDirectory(prefix="opencompany-pi-python-boundary-") as private_dir:
        private = Path(private_dir).resolve()
        assert private.is_relative_to(Path(tempfile.gettempdir()).resolve())
        for name in ("index.js", "package.json"):
            shutil.copyfile(distribution / name, private / name)
        host = private / "index.js"
        blob_dir = private / "transport"
        blob_dir.mkdir()
        run = _run(blob_dir)
        large = "continuation-é-" * (INLINE_LIMIT // 10)
        native_id = 9007199254740993
        run["messages"][1]["provider_state"] = {"continuation": large, "id": native_id, "😀": 2, "\uE000": 1,
            "opaque": {"ts": "provider-owned timestamp"}}
        if restored_conversation:
            from services.agent_context.conversation import _stamp_messages

            historical_call = {"id": "historical-call", "name": "record_id",
                "args": {"record_id": native_id, "ts": "argument-owned timestamp"},
                "raw_arguments": '{"record_id":9007199254740993,"ts":"argument-owned timestamp"}', "parse_error": None}
            historical_tool = _wire("tool", "Previously recorded result")
            historical_tool.update(tool_call_id=historical_call["id"], name=historical_call["name"])
            stored = _stamp_messages([
                run["messages"][1], _wire("assistant", "Previous request", [historical_call]), historical_tool,
            ], [], "2026-11-11T09:00:00+00:00")
            run["messages"] = [run["messages"][0], *stored, _wire("user", "Continue from the saved conversation.")]
        run["context_digest"] = canonical_digest(run["messages"])
        admitted_messages = copy.deepcopy(run["messages"])
        call = {"id": "native-call", "name": "record_id", "args": {"record_id": native_id},
            "raw_arguments": '{"record_id":9007199254740993}', "parse_error": None}
        assistant = _wire("assistant", "Read identifier", [call])
        assistant["blocks"].insert(0, {"type": "reasoning", "text": "Keep the native signature",
            "tool_call": None, "tool_call_id": None, "name": None,
            "metadata": {"signature": "native-reasoning-signature"}, "source": None})
        assistant["provider_state"] = {"continuation": large, "id": native_id,
            "signature": "native-provider-signature", "😀": 2, "\uE000": 1}
        tool = _wire("tool", str(native_id))
        tool.update(tool_call_id=call["id"], name=call["name"], provider_state={"id": native_id})
        receipt = {"assistant_message": assistant, "finish_reason": "tool_calls", "usage": {"total_tokens": 3}}
        expected_calls = [{"call_index": 0, "call_id": call["id"], "receipt_only": False}]
        update_order = []
        operations = {}
        snapshots = []
        buffers = []

        class ObservedTransport(ScopedProtocolBlobs):
            def encode(self, value):
                encoded = super().encode(value)
                buffers.append(("sent", value.get("type"), encoded is not value))
                return encoded

            def decode(self, value):
                decoded = super().decode(value)
                buffers.append(("received", decoded.get("type"), decoded is not value))
                if decoded.get("type") == "turn_checkpoint":
                    assert "assistant_message" not in decoded and "tool_results" not in decoded
                return decoded

        async def update(name, value, *, id):
            update_order.append(name)
            assert id.startswith("pi-")
            if name == "assistant_ready":
                assert value["calls"] == expected_calls
                return {"status": "accepted"}
            assert name == "submit_operation"
            payload = value["payload"]
            assert payload["scope"] == run["scope"] and payload["epoch"] == run["epoch"]
            assert payload["context_digest"] == run["context_digest"]
            assert "messages" not in payload and "args" not in payload
            logical = {key: item for key, item in payload.items() if key != "epoch"}
            assert value["fingerprint"] == canonical_digest({"id": value["id"], "kind": value["kind"], "payload": logical})
            if value["kind"] == "tool":
                assert payload["call_id"] == call["id"] and payload["call_index"] == 0
                assert payload["receipt_only"] is False
            operations[value["id"]] = value["kind"]
            return {"status": "accepted"}

        async def query(name, value):
            assert name == "operation_status" and value["epoch"] == run["epoch"]
            if "id" not in value:
                return {"status": "pending"}
            kind = operations[value["id"]]
            return {"status": "completed", "result": {"pi_blob": "model-receipt"} if kind == "model" else {"message": tool}}

        async def get_blob(scope, reference):
            assert scope == run["scope"] and reference == {"pi_blob": "model-receipt"}
            return copy.deepcopy(receipt)

        async def put_blob(scope, kind, snapshot):
            assert scope == run["scope"] and kind == "snapshot"
            snapshots.append(snapshot)
            return {"pi_blob": canonical_digest(snapshot)}

        monkeypatch.setattr(pi_activities.activity, "heartbeat", lambda: None)
        handle = SimpleNamespace(execute_update=AsyncMock(side_effect=update), query=AsyncMock(side_effect=query))
        transport = ObservedTransport(blob_dir, run["scope"], run["epoch"])
        env = {**os.environ, "NODE_PATH": "", "PI_OFFLINE": "1",
            "PI_PACKAGE_DIR": str(pi_runtime.pi_package_dir(host))}
        result = await asyncio.wait_for(pi_activities.PiDriverActivities(MagicMock())._run_host(
            bun, host, env, run, transport, handle, get_blob, put_blob), timeout=30)
        assert result["status"] == "completed" and result["epoch"] == run["epoch"]
        assert run["messages"] == admitted_messages
        assert run["context_digest"] == canonical_digest(admitted_messages)
        assert result["projection_digest"] == canonical_digest([*run["messages"], assistant, tool])
        assert result["consumed_operations"] == ["turn:0:model", "turn:0:tool:0:native-call"]
        assert update_order == ["submit_operation", "assistant_ready", "submit_operation"]
        assert ("sent", "run_turn", True) in buffers
        assert ("sent", "operation_result", True) in buffers
        assert ("received", "turn_checkpoint", True) in buffers
        assert len(snapshots) == 1
        snapshot = snapshots[0]
        assert len(canonical_json(snapshot).encode()) > INLINE_LIMIT
        messages = [entry["message"] for entry in snapshot["entries"] if entry["type"] == "message"]
        assert sum(message["role"] == "user" for message in messages) == (2 if restored_conversation else 1)
        saved = next(message for message in reversed(messages) if message["role"] == "assistant")
        assert saved["_oc"]["provider_state"] == assistant["provider_state"]
        saved_call = next(part for part in saved["content"] if part["type"] == "toolCall")
        assert saved_call["arguments"]["record_id"] == native_id
        assert saved_call["_ocBlock"]["call"]["raw_arguments"] == call["raw_arguments"]
        assert next(part for part in saved["content"] if part["type"] == "thinking")["thinkingSignature"] == "native-reasoning-signature"

        # Reuse the accepted snapshot without changing its admitted transcript
        # or digest; reconstruction must retain stored-view timestamps too.
        continued_messages = [*admitted_messages, assistant, tool]
        run.update(turn=1, epoch=3, messages=continued_messages,
            context_digest=result["projection_digest"], snapshot=snapshot)
        final_assistant = _wire("assistant", "Continued from the accepted snapshot.")
        receipt = {"assistant_message": final_assistant, "finish_reason": "stop", "usage": {"total_tokens": 1}}
        expected_calls = []
        update_order.clear()
        operations.clear()
        transport = ObservedTransport(blob_dir, run["scope"], run["epoch"])
        continued = await asyncio.wait_for(pi_activities.PiDriverActivities(MagicMock())._run_host(
            bun, host, env, run, transport, handle, get_blob, put_blob), timeout=30)
        assert continued["status"] == "completed"
        assert continued["consumed_operations"] == ["turn:1:model"]
        assert update_order == ["submit_operation", "assistant_ready"]
        assert run["messages"] == continued_messages
        assert continued["projection_digest"] == canonical_digest([*continued_messages, final_assistant])
        assert len(snapshots) == 2
