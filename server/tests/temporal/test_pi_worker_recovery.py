"""Hard worker death exercises the actual Temporal driver and packaged Pi SDK.

The coordinator's model/tool Activities remain deterministic fixtures. A
separate Python worker is terminated after a tool receipt has reached that
worker but before its Bun host receives it. No graceful cleanup runs there.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

import psutil
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.agent_workflow import AgentWorkflow
from services.temporal.pi_activities import PiControlActivities, PiDriverActivities, pi_put_blob
from services.temporal.pi_protocol import DRIVER_QUEUE, canonical_digest, canonical_json
from services.temporal.pi_workflow import PiCodingAgentWorkflow
from tests.temporal import test_pi_workflow_integration as fixture


def install_shared_blobs(directory: Path) -> None:
    """An immutable disk fixture lets separate worker processes share caches."""
    from services import pi_blobs
    directory.mkdir(parents=True, exist_ok=True)

    async def put(scope, kind, value):
        identity = canonical_digest({"scope": scope, "kind": kind, "value": value})
        target = directory / (identity + ".json")
        if not target.exists():
            temporary = directory / (identity + "-" + uuid4().hex + ".tmp")
            temporary.write_text(canonical_json({"scope": scope, "kind": kind, "value": value}), encoding="utf-8")
            temporary.replace(target)
        return {"pi_blob": identity, "scope_digest": canonical_digest(scope),
            "kind": kind, "digest": canonical_digest(value)}

    async def get(scope, reference):
        stored = json.loads((directory / (reference["pi_blob"] + ".json")).read_text(encoding="utf-8"))
        assert stored["scope"] == scope
        assert canonical_digest(stored["value"]) == reference["digest"]
        assert canonical_digest(scope) == reference["scope_digest"]
        return stored["value"]

    pi_blobs.put_blob, pi_blobs.get_blob = put, get


async def wait_until(predicate, timeout=20):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


def process_alive(identity: dict) -> bool:
    try:
        process = psutil.Process(identity["pid"])
        return (process.create_time() == identity["created"] and
            process.is_running() and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.NoSuchProcess:
        return False


class PauseReceiptClient:
    def __init__(self, client, directory, pause):
        self.client, self.directory, self.pause = client, directory, pause

    def get_workflow_handle(self, *args, **kwargs):
        return PauseReceiptHandle(self.client.get_workflow_handle(*args, **kwargs), self.directory, self.pause)


class PauseReceiptHandle:
    def __init__(self, handle, directory, pause):
        self.handle, self.directory, self.pause = handle, directory, pause

    async def execute_update(self, *args, **kwargs):
        return await self.handle.execute_update(*args, **kwargs)

    async def query(self, name, payload):
        result = await self.handle.query(name, payload)
        if self.pause and result.get("status") == "completed" and ":tool:" in payload.get("id", ""):
            # Blocking here means the recorded leaf succeeded, while the SDK
            # has not consumed this receipt or accepted a round checkpoint.
            marker = {"id": payload["id"], "epoch": payload["epoch"],
                "result_digest": canonical_digest(result["result"]), "attempt": activity.info().attempt}
            (self.directory / "receipt-ready.json").write_text(json.dumps(marker), encoding="utf-8")
            await asyncio.Event().wait()
        return result


async def driver_worker() -> None:
    directory = Path(os.environ["PI_RECOVERY_DIRECTORY"])
    install_shared_blobs(directory / "blobs")
    client = await Client.connect(os.environ["PI_RECOVERY_TARGET"], namespace=os.environ["PI_RECOVERY_NAMESPACE"])
    # Only the actual SDK host is launched after this hook is installed.
    original_spawn = asyncio.create_subprocess_exec
    async def record_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        identity = {"pid": process.pid, "created": psutil.Process(process.pid).create_time(),
            "attempt": activity.info().attempt, "node_id": activity.info().workflow_id}
        with (directory / "hosts.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(identity) + "\n")
        return process
    asyncio.create_subprocess_exec = record_spawn
    wrapped = PauseReceiptClient(client, directory, os.environ.get("PI_RECOVERY_PAUSE") == "1")
    async with Worker(client, task_queue=DRIVER_QUEUE, activities=[PiDriverActivities(wrapped).drive_turn],
            max_concurrent_activities=1):
        (directory / os.environ["PI_RECOVERY_READY"]).write_text("ready", encoding="utf-8")
        await asyncio.Event().wait()


async def recovery_gate(directory: Path) -> None:
    install_shared_blobs(directory / "blobs")
    fixture.COUNTS.clear()
    workers = []
    logs = []
    try:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            with environment.auto_time_skipping_disabled():
                async with Worker(environment.client, task_queue=fixture.QUEUE,
                        workflows=[PiCodingAgentWorkflow, AgentWorkflow],
                        activities=[fixture._prepare, fixture._llm, fixture._tool, fixture._task,
                            fixture._broadcast, fixture._store, fixture._clear, pi_put_blob,
                            *PiControlActivities(environment.client).activities()], max_cached_workflows=0):
                    # TerminateProcess cannot run TemporaryDirectory cleanup.
                    # Keep all attempt-private files under the test's owner.
                    private_temp = directory / "private-temp"
                    private_temp.mkdir()
                    def start_worker(number, *, pause):
                        env = {**os.environ, "DEBUG": "false", "PI_RECOVERY_DIRECTORY": str(directory),
                            "PI_RECOVERY_TARGET": environment.client.service_client.config.target_host,
                            "PI_RECOVERY_NAMESPACE": environment.client.namespace,
                            "PI_RECOVERY_PAUSE": "1" if pause else "0", "PI_RECOVERY_READY": f"ready-{number}",
                            "TEMP": str(private_temp), "TMP": str(private_temp), "TMPDIR": str(private_temp)}
                        log = (directory / f"driver-{number}.log").open("wb")
                        logs.append(log)
                        process = subprocess.Popen([sys.executable, "-m", "tests.temporal.test_pi_worker_recovery", "--driver-worker"],
                            cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT, env=env)
                        workers.append(process)
                        return process
                    first = start_worker(1, pause=True)
                    await wait_until(lambda: (directory / "ready-1").exists())
                    handle = await environment.client.start_workflow("PiCodingAgentWorkflow", {
                        "node_id": "agent", "node_type": "coding_agent", "pi_harness_version": 1,
                        "mode": "worker-recovery", "pi_local_control_version": 1, "pi_local_control_revision": 0},
                        id="pi-worker-kill-" + uuid4().hex, task_queue=fixture.QUEUE)
                    await wait_until(lambda: (directory / "receipt-ready.json").exists(), timeout=30)
                    marker = json.loads((directory / "receipt-ready.json").read_text(encoding="utf-8"))
                    [orphan] = [json.loads(line) for line in (directory / "hosts.jsonl").read_text().splitlines()]
                    assert marker["attempt"] == 1 and process_alive(orphan)
                    assert fixture.COUNTS["llm:agent"] == fixture.COUNTS["A"] == 1 and fixture.COUNTS["B"] == 0
                    # SIGKILL / TerminateProcess bypasses Activity finally and
                    # Bun disposal. The orphan must notice its pipe's EOF.
                    first.kill()
                    await asyncio.to_thread(first.wait, 5)
                    await wait_until(lambda: not process_alive(orphan), timeout=15)
                    print("Hard-killed Python worker; orphan Bun exited on EOF", flush=True)
                    second = start_worker(2, pause=False)
                    await wait_until(lambda: (directory / "ready-2").exists())
                    assert second.poll() is None
                    # A dead Activity retains the test server's Activity time
                    # lock, so use the real ninety-second heartbeat timeout.
                    # No timeout or SDK behavior is shortened for this gate.
                    result = await asyncio.wait_for(handle.result(), 115)
                    assert result["success"], result
                    assert result["result"]["usage"]["total_tokens"] == 6
                    assert fixture.COUNTS["llm:agent"] == 2
                    assert fixture.COUNTS["A"] == fixture.COUNTS["B"] == 1
                    hosts = [json.loads(line) for line in (directory / "hosts.jsonl").read_text().splitlines()]
                    assert any(host["attempt"] == 2 for host in hosts), hosts
                    await wait_until(lambda: all(not process_alive(host) for host in hosts), timeout=15)
                    history = await handle.fetch_history()
                replay = await Replayer(workflows=[PiCodingAgentWorkflow, AgentWorkflow]).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            await asyncio.to_thread(worker.wait, 5)
        for stream in logs:
            stream.close()
        if (directory / "hosts.jsonl").exists():
            for line in (directory / "hosts.jsonl").read_text().splitlines():
                host = json.loads(line)
                if process_alive(host):
                    psutil.Process(host["pid"]).kill()


def test_real_driver_worker_death_reconstructs_completed_receipts():
    workspace = Path(__file__).parents[3]
    with tempfile.TemporaryDirectory(prefix=".pi-worker-recovery-", dir=workspace) as temporary:
        directory = Path(temporary)
        env = {**os.environ, "DEBUG": "false", "DATA_DIR": str(directory / "data")}
        result = subprocess.run([sys.executable, "-m", "tests.temporal.test_pi_worker_recovery", str(directory)],
            cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            # About 115 s on a Windows runner. Above the sum of the gate's own
            # waits, so a slow run reports which one ran out instead of being
            # killed without its output.
            stderr=subprocess.STDOUT, env=env, timeout=300, check=False)
        logs = "\n".join(path.read_text(errors="replace")[-5000:] for path in directory.glob("driver-*.log"))
        assert result.returncode == 0, result.stdout.decode(errors="replace")[-15000:] + logs


if __name__ == "__main__":
    if sys.argv[1] == "--driver-worker":
        asyncio.run(driver_worker())
    else:
        asyncio.run(recovery_gate(Path(sys.argv[1])))
