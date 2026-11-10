"""Ephemeral SDK maintenance driver; all business effects stay in leaf Activities."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError, RPCStatusCode

from services.temporal.pi_protocol import INLINE_LIMIT, canonical_digest, canonical_json, conflict
from services.pi_transport import ScopedProtocolBlobs


def _object(value: Any, description: str) -> dict:
    if not isinstance(value, dict):
        raise conflict(f"Pi {description} must be a JSON object")
    return value


def driver_failure(exc: Exception) -> ApplicationError:
    """Only classified transport/storage interruptions reconstruct receipts."""
    if isinstance(exc, WorkflowUpdateFailedError):
        cause = exc.cause
        if isinstance(cause, ApplicationError):
            return ApplicationError("Pi coordinator rejected an operation", type=cause.type or "PiProtocolConflict", non_retryable=True)
        return conflict("Pi coordinator could not validate an operation")
    if isinstance(exc, ApplicationError):
        if exc.type == "PiTransportFailure" or exc.non_retryable:
            return exc
        return ApplicationError("Pi driver failed terminal validation", type=exc.type or "PiDriverFailure", non_retryable=True)
    if isinstance(exc, RPCError):
        transient = exc.status in {RPCStatusCode.UNAVAILABLE, RPCStatusCode.DEADLINE_EXCEEDED,
            RPCStatusCode.RESOURCE_EXHAUSTED, RPCStatusCode.ABORTED, RPCStatusCode.INTERNAL}
        return ApplicationError("Pi coordinator transport was interrupted", type="PiTransportFailure" if transient else "PiDriverFailure", non_retryable=not transient)
    if isinstance(exc, (ConnectionError, TimeoutError, WorkflowUpdateRPCTimeoutOrCancelledError)):
        return ApplicationError("Pi driver transport was interrupted", type="PiTransportFailure")
    from sqlalchemy.exc import InterfaceError, OperationalError
    if isinstance(exc, (InterfaceError, OperationalError)):
        return ApplicationError("Pi checkpoint storage was interrupted", type="PiTransportFailure")
    return ApplicationError("Pi driver could not safely complete its turn", type="PiDriverFailure", non_retryable=True)


@activity.defn(name="coding.pi_put_blob")
async def pi_put_blob(payload: dict) -> dict:
    from services.pi_blobs import put_blob
    return await put_blob(payload["scope"], payload["kind"], payload["payload"])


def collect_pi_activities() -> list:
    return [pi_put_blob]


class PiControlActivities:
    """Control attached descendants without occupying a business admission."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def activities(self) -> list:
        return [self.child_control]

    @activity.defn(name="coding.pi_child_control")
    async def child_control(self, payload: dict) -> dict:
        payload = _object(payload, "child control request")
        operation = payload.get("operation")
        children = payload.get("children")
        control = _object(payload.get("control"), "child control state")
        if (operation not in {"set_control_state", "wait_for_checkpoint"}
                or not isinstance(children, list) or len(children) > 1000
                or control.get("state") not in {"paused", "running"}
                or type(control.get("revision")) is not int):
            raise conflict("Invalid Pi descendant control request")
        seen = set()
        for child in children:
            child = _object(child, "child control identity")
            if (set(child) != {"workflow_id", "first_execution_run_id"}
                    or any(not isinstance(child[key], str) or not child[key] or len(child[key]) > 512 for key in child)
                    or child["workflow_id"] in seen):
                raise conflict("Invalid or duplicate Pi descendant identity")
            seen.add(child["workflow_id"])
        tasks = [asyncio.create_task(self._control_child(child, operation, control)) for child in children]
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            closed = await asyncio.gather(*tasks)
            return {"closed": [identity for identity in closed if identity is not None]}
        except ApplicationError:
            raise
        except Exception as exc:
            # RPC/storage exceptions can embed request or SQL parameter data.
            # Keep classified failures and diagnostics free of those bodies.
            raise driver_failure(exc) from None
        finally:
            heartbeat.cancel()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(heartbeat, *tasks, return_exceptions=True)

    async def _control_child(self, child: dict, operation: str, control: dict) -> str | None:
        handle = self.client.get_workflow_handle(child["workflow_id"],
            first_execution_run_id=child["first_execution_run_id"])
        state = {**control, "first_execution_run_id": child["first_execution_run_id"]}
        update_id = "pi-child-control:" + canonical_digest({"child": child, "operation": operation, "control": state})
        try:
            await handle.execute_update(operation, state, id=update_id)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return child["workflow_id"]
            if exc.status == RPCStatusCode.FAILED_PRECONDITION:
                # Never classify permission or validation errors as closed.
                description = await handle.describe()
                if description.status != WorkflowExecutionStatus.RUNNING:
                    return child["workflow_id"]
            raise
        return None

    @staticmethod
    async def _heartbeat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(30)


def pi_host_path() -> Path:
    from services.pi_runtime import resolve_pi_host
    return resolve_pi_host()


async def close_host(proc: asyncio.subprocess.Process) -> None:
    """Request SDK disposal, then bound graceful waiting and kill the tree."""
    from services._supervisor.util import kill_tree
    if proc.returncode is not None:
        return
    try:
        if proc.stdin:
            proc.stdin.write(b'{"type":"abort"}\n')
            await asyncio.wait_for(proc.stdin.drain(), timeout=1)
            proc.stdin.close()
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (TimeoutError, ConnectionError, BrokenPipeError):
        kill_tree(proc.pid)
        await proc.wait()


def pi_host_environment(environment: dict[str, str]) -> dict[str, str]:
    """Pass runtime essentials, never inherited application credentials or loaders."""
    allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
        "HOME", "USERPROFILE", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    return {**{key: value for key, value in environment.items() if key.upper() in allowed},
        "NODE_PATH": "", "PI_OFFLINE": "1"}


class PiDriverActivities:
    def __init__(self, client: Client) -> None:
        self.client = client

    def activities(self) -> list:
        return [self.drive_turn]

    @activity.defn(name="coding.pi_drive_turn")
    async def drive_turn(self, payload: dict[str, Any]) -> dict:
        try:
            return await self._drive_turn(payload)
        except ApplicationError as exc:
            if exc.type == "PiTransportFailure" or exc.non_retryable:
                raise
            raise ApplicationError("Pi driver failed terminal validation", type=exc.type or "PiDriverFailure", non_retryable=True) from exc
        except Exception as exc:
            raise driver_failure(exc) from None

    async def _drive_turn(self, payload: dict[str, Any]) -> dict:
        from core.js_runtime import require_bun
        from services.pi_blobs import get_blob, put_blob
        from services.pi_runtime import pi_package_dir

        handle = self.client.get_workflow_handle(activity.info().workflow_id,
            run_id=activity.info().workflow_run_id)
        # Activity attempt is included in Update identity, not logical effects.
        admission = _object(await handle.execute_update("register_driver", {"turn": payload["turn"],
            "attempt": activity.info().attempt, "activity_id": activity.info().activity_id},
            id=f"pi-register:{activity.info().activity_id}:{activity.info().attempt}"), "driver admission")
        if type(admission.get("epoch")) is not int or admission["epoch"] <= 0:
            raise conflict("Pi driver admission omitted its epoch")
        run = dict(payload)
        run.update(type="run_turn", protocol=1, epoch=admission["epoch"])
        scope = run["scope"]
        snapshot = run.get("snapshot")
        if snapshot and "pi_blob" in snapshot:
            run["snapshot"] = await get_blob(scope, snapshot)
        env = pi_host_environment(dict(os.environ))
        host = await asyncio.to_thread(pi_host_path)
        env["PI_PACKAGE_DIR"] = str(pi_package_dir(host))
        try:
            bun = require_bun("Coding Agent's Pi harness")
        except RuntimeError as exc:
            raise ApplicationError("The shipped Bun runtime is missing", type="PiRuntimeMissing", non_retryable=True) from exc
        # Each Activity attempt has its own trusted directory and epoch. It
        # survives until process cleanup; all durable content stays in the DB.
        with tempfile.TemporaryDirectory(prefix="opencompany-pi-") as blob_dir:
            run["blob_dir"] = str(Path(blob_dir).resolve())
            transport = ScopedProtocolBlobs(run["blob_dir"], scope, admission["epoch"])
            return await self._run_host(bun, host, env, run, transport, handle, get_blob, put_blob)

    async def _run_host(self, bun, host, env, run, transport, handle, get_blob, put_blob):
        scope, epoch = run["scope"], run["epoch"]
        proc = await asyncio.create_subprocess_exec(bun, str(host),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, limit=INLINE_LIMIT + 1)
        stderr_task = asyncio.create_task(self._drain_stderr(proc))
        heartbeat_task = asyncio.create_task(self._heartbeat(proc))
        try:
            await self._send(proc, run, transport)
            while True:
                frame_task = asyncio.create_task(proc.stdout.readline())
                try:
                    while not frame_task.done():
                        done, _ = await asyncio.wait({frame_task}, timeout=1)
                        if done:
                            break
                        status = _object(await handle.query("operation_status", {"epoch": epoch}), "operation status")
                        if status.get("status") == "park":
                            await self._send(proc, {"type": "park"})
                            return {"status": "parked"}
                    try:
                        line = await frame_task
                    except ValueError as exc:
                        raise conflict("Pi SDK host exceeded the JSON frame limit") from exc
                finally:
                    if not frame_task.done():
                        frame_task.cancel()
                        await asyncio.gather(frame_task, return_exceptions=True)
                if not line:
                    raise ApplicationError("Pi SDK host exited before completing its turn", type="PiTransportFailure")
                if len(line) > INLINE_LIMIT or not line.endswith(b"\n"):
                    raise conflict("Pi SDK host exceeded the JSON frame limit")
                try:
                    event = _object(json.loads(line), "host frame")
                except (ValueError, UnicodeError) as exc:
                    raise conflict("Pi SDK host emitted invalid JSON") from exc
                if "pi_payload" in event:
                    envelope_type = event.get("type")
                    event = _object(transport.decode(event["pi_payload"]), "buffered host frame")
                    if event.get("type") != envelope_type or "pi_payload" in event:
                        raise conflict("Pi host buffer changed its envelope type")
                kind = event.get("type")
                if kind == "operation":
                    op_id = event.get("id")
                    if not isinstance(op_id, str) or not op_id or len(op_id) > 4096 or event.get("kind") not in {"model", "tool"}:
                        raise conflict("Pi host operation omitted a valid identity or kind")
                    _object(event.get("payload"), "operation payload")
                    # The content fingerprint keeps Temporal Update dedup from
                    # hiding a conflicting reuse of an operation's logical ID.
                    update_id = f"pi-op:{epoch}:{canonical_digest({'id': op_id, 'request': event})}"
                    await handle.execute_update("submit_operation", event, id=update_id)
                    while True:
                        status = _object(await handle.query("operation_status", {"id": op_id, "epoch": epoch}), "operation status")
                        if status["status"] == "park":
                            await self._send(proc, {"type": "park"})
                            return {"status": "parked"}
                        if status["status"] == "completed":
                            if "result" not in status:
                                raise conflict("Pi completed operation omitted its receipt")
                            result = status["result"]
                            if isinstance(result, dict) and "pi_blob" in result:
                                result = await get_blob(scope, result)
                            await self._send(proc, {"type": "operation_result", "id": op_id, "result": result}, transport)
                            break
                        if status.get("status") != "pending":
                            raise conflict("Pi operation returned an unknown status")
                        await asyncio.sleep(0.1)
                elif kind == "turn_checkpoint":
                    snapshot = _object(event.get("snapshot"), "checkpoint snapshot")
                    consumed = event.get("consumed_operations")
                    if (not isinstance(consumed, list) or any(not isinstance(value, str) for value in consumed)
                            or len(set(consumed)) != len(consumed) or not isinstance(event.get("projection_digest"), str)):
                        raise conflict("Pi checkpoint omitted its exact consumed operation set")
                    if (snapshot.get("scope") != scope or snapshot.get("turn") != run["turn"] or
                        snapshot.get("epoch") != epoch or snapshot.get("config_digest") != run["config_digest"] or
                        snapshot.get("binding_digest") != run["binding_digest"] or snapshot.get("context_digest") != event.get("projection_digest")):
                        raise conflict("Pi checkpoint metadata differs from its admitted turn")
                    reference = await put_blob(scope, "snapshot", event["snapshot"])
                    return {"status": "completed", "epoch": epoch, "snapshot": reference,
                        "consumed_operations": event["consumed_operations"],
                        "projection_digest": event["projection_digest"], "scope": scope, "turn": run["turn"],
                        "config_digest": run["config_digest"], "binding_digest": run["binding_digest"]}
                elif kind == "assistant_ready":
                    await handle.execute_update("assistant_ready", event,
                        id=f"pi-ready:{epoch}:{canonical_digest(event)}")
                elif kind == "parked":
                    return {"status": "parked"}
                elif kind == "error":
                    code = event.get("code")
                    transient = code in {"transport_failure", "transport_write_failed"}
                    error_type = {"sdk_failure": "PiSDKFailure", "snapshot_incompatible": "PiSnapshotIncompatible"}.get(
                        code, "PiTransportFailure" if transient else "PiProtocolConflict")
                    raise ApplicationError("Pi SDK host could not complete the coding turn", type=error_type, non_retryable=not transient)
                else:
                    raise conflict("Pi SDK host emitted an unknown frame")
        finally:
            heartbeat_task.cancel()
            # Cleanup must finish on Temporal cancellation before the private
            # capacity slot is returned, including descendant processes.
            await asyncio.shield(close_host(proc))
            stderr_task.cancel()
            await asyncio.gather(heartbeat_task, stderr_task, return_exceptions=True)

    @staticmethod
    async def _send(proc, value: dict, transport: ScopedProtocolBlobs | None = None) -> None:
        wire = transport.encode(value) if transport is not None else value
        if wire is not value:
            wire = {"type": value["type"], "pi_payload": wire}
            if value["type"] == "run_turn":
                wire.update({key: value[key] for key in ("protocol", "scope", "epoch", "blob_dir")})
        encoded = (canonical_json(wire) + "\n").encode("utf-8")
        if len(encoded) > INLINE_LIMIT:
            raise conflict("Pi input exceeds the JSON frame limit")
        proc.stdin.write(encoded)
        await proc.stdin.drain()

    @staticmethod
    async def _heartbeat(proc) -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(30)
            await PiDriverActivities._send(proc, {"type": "keepalive"})

    @staticmethod
    async def _drain_stderr(proc) -> None:
        # Diagnostic text can contain user data or provider continuations;
        # drain continuously without exposing it in workflow errors/logs.
        while await proc.stderr.read(65536):
            pass
