"""Native Browser tasks on separate owner queues, with SDK replay and cleanup.

Uses an explicitly supplied native Temporal binary. Browser/model Activities
are controlled fixtures: no external website, provider or credential access.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, suppress
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


async def run_gate(cli: str):
    from temporalio import activity
    from temporalio.client import WithStartWorkflowOperation
    from temporalio.common import WorkflowIDConflictPolicy
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker, Replayer
    from services.llm.protocol import Message, ToolCall, message_to_wire
    from services.temporal import agent_workflow as agent_module
    from services.temporal.agent_workflow import AgentWorkflow
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow

    # Roll over after the first completed tool turn, proving generation-zero
    # tasks retain their payload, task token, tools and transcript.
    agent_module._AGENT_HISTORY_SOFT_CAP = 1
    token = uuid4().hex
    main_queue = f"browser-main-{token}"
    queues = {owner: f"browser-{owner}-{token}" for owner in ("a", "b")}
    prepared_calls, llm_calls, records, completions = [], [], [], []
    cancel_entered, cancel_release, cleanup_entered, cleanup_release = (asyncio.Event() for _ in range(4))

    def context(identity, owner, *, cancelling=False):
        binding = {"owner_id": owner, "profile_id": owner, "runtime_epoch": "epoch", "task_queue": queues[owner]}
        return {"node_id": "agent", "node_type": "browser_agent", "workflow_id": "saved",
            "execution_id": identity, "session_id": identity, "user_id": "owner", "generation": 0,
            "native_workspace_version": 1, "temporal_worker_pool_enabled": False,
            "workspace_task_prompt": "read issues", "nodes": [], "edges": [],
            "browser_bindings": {"browser": binding}, "_browser_task_id": identity, "cancelling": cancelling}

    @activity.defn(name="agent.prepare_payload")
    async def prepare(ctx: dict):
        prepared_calls.append(ctx["execution_id"])
        binding = ctx["browser_bindings"]["browser"]
        return {"node_id": "agent", "node_type": "browser_agent", "workflow_id": "saved", "session_id": ctx["session_id"],
            "provider": "openai", "model": "fixture", "system_message": "Browser role stays intact", "user_prompt": ctx["workspace_task_prompt"],
            "max_iterations": 3, "memory_node_id": "", "memory_content": "", "compaction_threshold": None,
            "browser_runtime_version": 1, "browser_bindings": ctx["browser_bindings"],
            "tools": [{"name": "browser", "node_type": "browser", "tool_node_id": "browser", "version": 1,
                "parameters": {}, "definition": {"name": "browser", "description": "browser fixture", "parameters": {"type": "object"}},
                "activity_policy": {"start_to_close_seconds": 60, "heartbeat_seconds": 10,
                    "task_queue": binding["task_queue"], "owner_routing": True,
                    "retry_policy": {"initial_interval_seconds": 1, "backoff_coefficient": 2, "maximum_interval_seconds": 5,
                        "maximum_attempts": 3, "non_retryable_error_types": []}}}]}

    @activity.defn(name="agent.execute_llm_step")
    async def llm(payload: dict):
        llm_calls.append(payload)
        if payload["iteration"] == 0:
            call = ToolCall(id="call-1", name="browser", args={"operation": "snapshot"})
            return {"kind": "tool_calls", "calls": [{"id": "call-1", "name": "browser", "args": {"operation": "snapshot"}}],
                    "assistant_message": message_to_wire(Message(role="assistant", content="", tool_calls=[call])), "usage": {"input_tokens": 3}}
        assert any(message.get("role") == "tool" and "observed" in str(message) for message in payload["messages"])
        return {"kind": "final", "content": "done", "usage": {"input_tokens": 2}}

    @activity.defn(name="agent.broadcast_progress")
    async def progress(payload: dict):
        return {}
    @activity.defn(name="agent.skill.clear")
    async def clear(payload: dict):
        return {}
    @activity.defn(name="agent.store_output")
    async def output(payload: dict):
        return {}
    @activity.defn(name="workspace_tasks.admit_record")
    async def admit(payload: dict):
        records.append((payload["context"]["execution_id"], "admitted"))
    @activity.defn(name="workspace_tasks.update_record")
    async def update(payload: dict):
        records.append((payload["invocation_id"], payload["status"]))
    @activity.defn(name="workflow_runs.record_completion")
    async def completion(payload: dict):
        completions.append(payload)

    class Owner:
        def __init__(self, identity):
            self.identity = identity
            self.claims, self.releases, self.operations = [], [], []
        @activity.defn(name="browser_tasks.claim")
        async def claim(self, payload: dict):
            assert payload["binding"]["owner_id"] == self.identity
            self.claims.append(payload["task_id"])
            return {"claimed": True, "binding": payload["binding"]}
        @activity.defn(name="browser_tasks.release")
        async def release(self, payload: dict):
            assert payload["binding"]["owner_id"] == self.identity
            if payload["task_id"].endswith("cancel"):
                cleanup_entered.set()
                while not cleanup_release.is_set():
                    activity.heartbeat("cleanup fixture")
                    await asyncio.sleep(.02)
            self.releases.append(payload["task_id"])
            return {"released": True}
        @activity.defn(name="node.browser.v1")
        async def browser(self, payload: dict):
            assert payload["_browser_owner"]["owner_id"] == self.identity
            self.operations.append(payload["_browser_task_id"])
            if payload["_browser_task_id"].endswith("cancel"):
                cancel_entered.set()
                try:
                    while not cancel_release.is_set():
                        activity.heartbeat("browser fixture")
                        await asyncio.sleep(.02)
                except asyncio.CancelledError:
                    raise
            return {"success": True, "result": {"observed": True,
                "screenshot": {"path": "screenshots/fixture.png", "workflow_id": "saved",
                               "filename": "fixture.png", "mime_type": "image/png"}}}

    owners = {name: Owner(name) for name in queues}
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli) as environment:
        client = environment.client
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(Worker(client, task_queue=main_queue,
                workflows=[WorkspaceTaskControllerWorkflow, NodeInvocationWorkflow, AgentWorkflow],
                activities=[prepare, llm, progress, clear, output, admit, update, completion]))
            async def start_owner(name):
                owner = owners[name]
                await stack.enter_async_context(Worker(client, task_queue=queues[name],
                    activities=[owner.claim, owner.release, owner.browser],
                    max_heartbeat_throttle_interval=timedelta(milliseconds=50),
                    default_heartbeat_throttle_interval=timedelta(milliseconds=50)))
            await start_owner("b")
            controller_id = f"browser-controller-{token}"
            async def submit(identity, owner, cancelling=False):
                ctx = context(identity, owner, cancelling=cancelling)
                return await client.execute_update_with_start_workflow(WorkspaceTaskControllerWorkflow.submit,
                    {"principal": "owner", "workflow_id": "saved", "node_id": "agent", "fingerprint": identity,
                     "dispatch_version": 1, "dispatch_kind": "native_agent", "history_version": 1,
                     "admission_epoch": 0, "context": ctx}, id=uuid4().hex,
                    start_workflow_operation=WithStartWorkflowOperation(WorkspaceTaskControllerWorkflow.run,
                        {"workflow_id": "saved"}, id=controller_id, task_queue=main_queue,
                        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING))
            first, second = f"browser-{token}-a", f"browser-{token}-b"
            await submit(first, "a")
            await submit(second, "b")
            # Owner B cannot consume A's call, even with pool routing disabled.
            assert (await asyncio.wait_for(client.get_workflow_handle(second).result(), 20))["success"]
            assert first in prepared_calls and not owners["a"].claims
            assert owners["b"].operations == [second]
            await start_owner("a")
            first_result = await asyncio.wait_for(client.get_workflow_handle(first).result(), 20)
            assert first_result["success"]
            assert first_result["result"]["artifacts"][0]["url"] == "/api/workspace/saved/files/screenshots/fixture.png"
            assert prepared_calls.count(first) == prepared_calls.count(second) == 1
            assert owners["a"].claims == [first, first]  # idempotent reacquire after CAN
            assert owners["a"].releases == [first]
            assert len(completions) == 2  # parent boundary only, regardless of CAN

            cancelled = f"browser-{token}-cancel"
            await submit(cancelled, "a", cancelling=True)
            await asyncio.wait_for(cancel_entered.wait(), 10)
            handle = client.get_workflow_handle(cancelled)
            await handle.cancel()
            await asyncio.wait_for(cleanup_entered.wait(), 10)
            assert (cancelled, "cancelled") not in records
            cleanup_release.set()
            with suppress(Exception):
                await asyncio.wait_for(handle.result(), 10)
            assert owners["a"].releases[-1] == cancelled
            assert (cancelled, "cancelled") in records

            replayer = Replayer(workflows=[WorkspaceTaskControllerWorkflow, NodeInvocationWorkflow, AgentWorkflow])
            for identity in (first, second, cancelled, controller_id):
                history = await client.get_workflow_handle(identity).fetch_history()
                assert (await replayer.replay_workflow(history)).replay_failure is None
            for identity in (first, second, cancelled):
                history = await client.get_workflow_handle(f"{identity}:agent").fetch_history()
                assert (await replayer.replay_workflow(history)).replay_failure is None
                assert "CANARY" not in history.to_json()
            await client.get_workflow_handle(controller_id).terminate("isolated fixture finished")


def test_native_browser_owner_routing_continuation_and_cleanup_replay():
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to a native Temporal binary")
    completed = subprocess.run([sys.executable, "-X", "utf8", "-m", "tests.temporal.test_browser_workspace_replay", cli],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, capture_output=True,
        encoding="utf-8", timeout=90, env={**os.environ, "DEBUG": "false"})
    assert completed.returncode == 0, (completed.stdout + completed.stderr)[-9000:]


if __name__ == "__main__":
    asyncio.run(run_gate(sys.argv[1]))
