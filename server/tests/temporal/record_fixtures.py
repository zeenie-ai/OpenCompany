"""Record the Temporal histories the replay gate replays (RFC-0005 Phase 1).

Run from ``server/`` once per release, in a clean checkout of the release
commit, with the CLI the app pins (never an installed one):

    TEMPORAL_TEST_CLI=<pinned temporal binary> uv run python -m tests.temporal.record_fixtures

It starts a throwaway dev server on that CLI and drives every production
workflow type along its important paths. The workers register the
production workflow list (``_framework_workflows()`` plus
``temporal_plugins()``) and fake activities under the production activity
names. The histories go to ``tests/temporal/fixtures/<release>/``, where
``<release>`` is the root ``package.json`` version, and every later change
to workflow code must replay them (``test_replay_corpus.py``; RFC-0004
§8.6). Each history is replayed once more before it is written.

The process sets its own throwaway ``DATA_DIR`` before importing app code,
so nothing touches an install's data.
"""

from __future__ import annotations

import asyncio
import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHistory
from temporalio.service import RPCError, RPCStatusCode

SERVER_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVER_DIR.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
TASK_QUEUE = "fixture-tasks"
# Recorded in every history instead of the recording machine's pid@host.
IDENTITY = "fixture-recorder"
GRAPH_ID = "fixture-graph"
SESSION_ID = "fixture-session"
# The webhook trigger's CloudEvents type (nodes/trigger/webhook_trigger).
PUSH_EVENT_TYPE = "com.opencompany.webhook.received"
# The rollover server suggests continue-as-new from this many history
# events on. The suggestion is recorded in the history, so these
# fixtures replay against unmodified workflow code.
ROLLOVER_HISTORY_COUNT = 40
# The controller and the poller sleep at least 30 s between polls. Replay
# compares a timer's id, not its duration, so a shorter non-zero floor
# records the same commands.
POLL_INTERVAL_FLOOR_S = 1
_PAUSE_TOOL_STARTED = asyncio.Event()
_PAUSE_TOOL_RELEASE = asyncio.Event()


def release() -> str:
    return json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["version"]


# ---------------------------------------------------------------------------
# Fake activities: production names, canned results
# ---------------------------------------------------------------------------


# Node types whose ``node.<type>.v<version>`` activity the scenarios run.
PLUGIN_NODES = ("console", "calculatorTool", "chatReply", "taskManager", "mobile_use_agent")
# Task Manager's preflight answer: a trusted scheduling envelope for the
# ``helper`` teammate (nodes/tool/task_manager returns this shape).
DELEGATION_REQUEST = {
    "team_task_id": "task-1",
    "assignee_node_id": "helper",
    "delegate_name": "delegate_to_helper",
    "task": "Draft the weekly summary",
    "context": {},
}


def _node_activity(node_type: str):
    """``node.<type>.v<version>`` for one plugin, answering like a node."""
    from services.node_registry import get_node_class

    cls = get_node_class(node_type)

    @activity.defn(name=f"node.{cls.type}.v{cls.version}")
    async def run(context: dict) -> dict:
        if context.get("node_id") == "paused-tool":
            _PAUSE_TOOL_STARTED.set()
            await _PAUSE_TOOL_RELEASE.wait()
        if node_type == "taskManager":
            return {"success": True, "delegation_request": dict(DELEGATION_REQUEST)}
        return {"success": True, "result": {"node_id": context.get("node_id"), "answer": 42}}

    return run


@activity.defn(name="store_node_output_activity")
async def store_node_output(_payload: dict) -> None:
    return None


@activity.defn(name="workflow_runs.record_completion")
async def record_completion(_payload: dict) -> dict:
    return {"recorded": True}


@activity.defn(name="workflow_control.pause_on_failure")
async def pause_on_failure(_payload: dict) -> dict:
    return {"paused": False}


@activity.defn(name="chat_run.start")
async def chat_run_start(_payload: dict) -> dict:
    return {"claimed": True, "session_id": SESSION_ID}


@activity.defn(name="chat_run.finish")
async def chat_run_finish(_payload: dict) -> dict:
    return {"finished": True}


def _tool_binding(node_type: str, node_id: str, name: str, context: dict) -> dict:
    """A tool binding shaped like ``agent.prepare_payload`` builds it."""
    from services.node_registry import get_node_class
    from services.temporal.agent_activities import _tool_activity_policy

    cls = get_node_class(node_type)
    durable = context.get("execution_control_version") == 1 or context.get("pi_harness_version") == 1
    return {
        "name": name,
        "definition": {
            "name": name,
            "description": f"Fixture tool {name}",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}},
        },
        "node_type": node_type,
        "version": cls.version,
        "task_queue": cls.task_queue,
        "tool_node_id": node_id,
        "binding_key": node_id,
        "parameters": {},
        "tool_info": {"node_id": node_id, "node_type": node_type},
        **({"activity_policy": _tool_activity_policy(cls), "needs_canvas": bool(cls.needs_canvas)} if durable else {}),
        "llm_hidden": False,
    }


@activity.defn(name="agent.prepare_payload")
async def prepare_payload(context: dict) -> dict:
    """The agent's prepared payload. ``node_data["fixture"]`` names its tools,
    the call its first turn makes (``prompt == "call:<tool> <json args>"``)
    and any extra payload fields such as ``team_id``."""
    plan = (context.get("node_data") or {}).get("fixture") or {}
    tools = [{**_tool_binding(tool["node_type"], tool["node_id"], tool["name"], context), **tool.get("extra", {})}
             for tool in plan.get("tools", [])]
    return {
        **plan.get("payload", {}),
        **({"pi_harness_version": 1} if context.get("pi_harness_version") == 1 else {}),
        "node_id": context["node_id"],
        "node_type": context.get("node_type") or "aiAgent",
        "workflow_id": context.get("workflow_id") or GRAPH_ID,
        "session_id": SESSION_ID,
        "provider": "openai",
        "model": "fixture-model",
        "max_tokens": 100,
        "temperature": 0,
        "system_message": "Fixture agent",
        "user_prompt": plan.get("prompt", "answer"),
        "tools": tools,
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": plan.get("max_iterations", 4),
        "thinking_config": None,
        "compaction_threshold": None,
    }


@activity.defn(name="agent.execute_llm_step")
async def execute_llm_step(payload: dict) -> dict:
    """Call the tool the prompt names once, then answer."""
    from services.llm.protocol import Message, ToolCall, message_to_wire

    messages = payload["messages"]
    prompt = next((m["content"] for m in messages if m["role"] == "user"), "")
    called = any(m["role"] == "tool" for m in messages)
    usage = {"input_tokens": 10, "output_tokens": 5}
    if prompt.startswith("call:") and not called:
        name, _, args = prompt.removeprefix("call:").partition(" ")
        call = ToolCall(id="call-1", name=name, args=json.loads(args) if args else {"expression": "6*7"})
        assistant = Message(role="assistant", content="", tool_calls=[call])
        return {
            "kind": "tool_calls",
            "assistant_message": message_to_wire(assistant),
            "calls": [{"id": call.id, "name": call.name, "args": call.args}],
            "usage": usage,
        }
    return {
        "kind": "final",
        "assistant_message": message_to_wire(Message(role="assistant", content="42")),
        "content": "42",
        "thinking": None,
        "usage": usage,
    }


@activity.defn(name="agent.broadcast_progress")
async def broadcast_progress(_payload: dict) -> dict:
    return {"emitted": True}


@activity.defn(name="agent.store_output")
async def agent_store_output(_payload: dict) -> dict:
    return {"stored": True}


@activity.defn(name="agent.skill.clear")
async def clear_skills(_payload: dict) -> dict:
    return {"cleared": True}


def _bookkeeping(name: str, result: dict):
    """An activity whose result only acknowledges the call."""

    @activity.defn(name=name)
    async def run(_payload: dict) -> dict:
        return dict(result)

    return run


# Delegation and team bookkeeping: each persists a lifecycle row in
# production and returns an acknowledgement the workflow ignores, except
# the permit lease and the finished task's result.
DELEGATION_ACTIVITIES = {
    "agent.queue_delegation": {"queued": True},
    "agent.begin_delegation": {"begun": True},
    "agent.register_task_execution": {"registered": True},
    "agent.acquire_subagent_permit": {"lease_id": "lease-1"},
    "agent.release_subagent_permit": {"released": True},
    "agent.finish_delegation": {"success": True, "status": "completed", "result": "done"},
    "agent.cancel_delegation": {"cancelled": True},
    "agent.finalize_team": {"status": "active"},
}


# Trigger, controller, employee and approval bookkeeping: production writes
# rows or broadcasts; the workflows read only these fields of the results.
TRIGGER_ACTIVITIES = {
    "load_persisted_workflow_graph_activity": {"found": False},
    "controller.queue.spill": {"spilled": True},
    "employee.job.deliver": {"delivered": True, "state": "delivered"},
    "employee.job.failed": {"saved": True},
    "employee.job.resolve_delivery": {"delivered": True, "state": "delivered"},
    "approvals.record_outcome": {"status": "sent", "outcome": "sent"},
    "workspace_tasks.reset_runtime": {"reset_nodes": []},
    "coding.inline_delegation_status": {"recorded": True},
}
POLLED_MAIL = {"id": "mail-1", "subject": "Fixture mail", "from": "owner@example.com"}


@activity.defn(name="evaluate_trigger_filter_activity")
async def evaluate_trigger_filter(_payload: dict) -> bool:
    return True


@activity.defn(name="broadcast_trigger_status_activity")
async def broadcast_trigger_status(_payload: dict) -> None:
    return None


@activity.defn(name="poll.googleGmailReceive.v1")
async def poll_mail(payload: dict) -> dict:
    """The baseline sees one old mail; the next poll finds one new mail."""
    if payload["baseline_only"]:
        return {"events": [], "seen_ids": ["mail-0"]}
    seen = list(payload["seen_ids"])
    if POLLED_MAIL["id"] in seen:
        return {"events": [], "seen_ids": seen}
    return {"events": [dict(POLLED_MAIL)], "seen_ids": [*seen, POLLED_MAIL["id"]]}


@activity.defn(name="controller.queue.read")
async def queue_read(_payload: dict) -> dict:
    """One page holding one spilled event, and no more pages."""
    return {"events": [[f"{READ_SLUG}-hook", _envelope("spilled-1")]], "more": False}


@activity.defn(name="approvals.claim_send")
async def approvals_claim_send(payload: dict) -> dict:
    from services.node_registry import get_node_class

    cls = get_node_class("console")
    return {"claimed": True, "activity": f"node.{cls.type}.v{cls.version}",
            "context": {"node_id": "send", "node_type": "console", "node_data": {}, "approval_id": payload["approval_id"]}}


class PiDriverPeer:
    """The Pi SDK's side of one Coding Agent turn (``coding.pi_drive_turn``),
    reduced to its protocol: register, run the model operation, report the
    calls, run each tool operation, then return the completed checkpoint.
    The workflow runs the model step and the tool leaves it is asked for."""

    def __init__(self, client: Client) -> None:
        self.client = client

    @activity.defn(name="coding.pi_drive_turn")
    async def drive(self, payload: dict) -> dict:
        from services.temporal.pi_protocol import canonical_digest, tool_operation_id

        info = activity.info()
        handle = self.client.get_workflow_handle(info.workflow_id, run_id=info.workflow_run_id)
        epoch = (await handle.execute_update("register_driver", {"turn": payload["turn"]}))["epoch"]
        base = {key: payload[key] for key in ("scope", "turn", "context_digest", "config_digest", "binding_digest")}

        async def operation(identity: str, kind: str, fields: dict) -> dict:
            await handle.execute_update("submit_operation", {"id": identity, "kind": kind,
                                                             "payload": {**base, "epoch": epoch, **fields}})
            while True:
                status = await handle.query("operation_status", {"id": identity, "epoch": epoch})
                if status["status"] == "completed":
                    return status["result"]
                if status["status"] == "park":
                    return None
                activity.heartbeat()
                await asyncio.sleep(0.01)

        model_id = f"turn:{payload['turn']}:model"
        step = await operation(model_id, "model", {"purpose": "assistant"})
        if step is None:
            return {"status": "parked"}
        calls = step.get("calls") or []
        await handle.execute_update("assistant_ready", {
            "turn": payload["turn"], "epoch": epoch, "context_digest": payload["context_digest"],
            "binding_digest": payload["binding_digest"],
            "calls": [{"call_index": index, "call_id": call["id"], "receipt_only": False} for index, call in enumerate(calls)],
        })
        operations, results = [model_id], []
        for index, call in enumerate(calls):
            identity = tool_operation_id(payload["turn"], index, call)
            result = await operation(identity, "tool", {"call_index": index, "call_id": call["id"]})
            if result is None:
                return {"status": "parked"}
            results.append(result)
            operations.append(identity)
        projection = [*payload["messages"], step["assistant_message"], *results]
        return {"status": "completed", "epoch": epoch, "snapshot": {"pi_blob": "fixture"},
                "consumed_operations": operations, "projection_digest": canonical_digest(projection),
                "scope": payload["scope"], "turn": payload["turn"], "config_digest": payload["config_digest"],
                "binding_digest": payload["binding_digest"]}


def fake_activities() -> list:
    return [
        *(_node_activity(node_type) for node_type in PLUGIN_NODES),
        *(_bookkeeping(name, result) for name, result in DELEGATION_ACTIVITIES.items()),
        *(_bookkeeping(name, result) for name, result in TRIGGER_ACTIVITIES.items()),
        evaluate_trigger_filter,
        broadcast_trigger_status,
        poll_mail,
        queue_read,
        approvals_claim_send,
        store_node_output,
        record_completion,
        pause_on_failure,
        chat_run_start,
        chat_run_finish,
        prepare_payload,
        execute_llm_step,
        broadcast_progress,
        agent_store_output,
        clear_skills,
    ]


def activity_queues() -> set[str]:
    """Plugin pool queues the per-type node activities route to."""
    from services.node_registry import get_node_class

    return {get_node_class(node_type).task_queue for node_type in PLUGIN_NODES}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


@dataclass
class Recorder:
    client: Client
    worker: Callable[[str], Any]

    async def start(self, workflow: str, arg: Any, workflow_id: str, *, task_queue: str = TASK_QUEUE) -> Any:
        return await self.client.start_workflow(workflow, arg, id=workflow_id, task_queue=task_queue)

    async def run(self, workflow: str, arg: Any, workflow_id: str) -> Any:
        handle = await self.start(workflow, arg, workflow_id)
        await handle.result()
        return handle

    async def until(self, condition: Callable[[], Awaitable[bool]], timeout: float = 30) -> None:
        async def wait() -> None:
            while not await condition():
                await asyncio.sleep(0.05)

        await asyncio.wait_for(wait(), timeout=timeout)

    async def rolled_over(self, handle: Any) -> bool:
        return (await handle.describe()).run_id != handle.first_execution_run_id

    async def history(self, workflow_id: str) -> WorkflowHistory:
        return await self.client.get_workflow_handle(workflow_id).fetch_history()

    async def chain(self, workflow_id: str, first_run_id: str) -> list[WorkflowHistory]:
        """Every run of one id, following continue-as-new from the first."""
        histories: list[WorkflowHistory] = []
        run_id: str | None = first_run_id
        while run_id:
            history = await self.client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history()
            histories.append(history)
            last = history.events[-1]
            continued = last.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW
            run_id = last.workflow_execution_continued_as_new_event_attributes.new_execution_run_id if continued else None
        return histories

    async def wait_closed(self, workflow_id: str, timeout: float = 30) -> None:
        """Wait until a workflow another workflow started has finished."""

        async def wait() -> None:
            while True:
                try:
                    await self.client.get_workflow_handle(workflow_id).result()
                    return
                except RPCError as exc:
                    if exc.status != RPCStatusCode.NOT_FOUND:
                        raise
                    await asyncio.sleep(0.05)

        await asyncio.wait_for(wait(), timeout=timeout)


def _graph_input(nodes: list[dict], edges: list[dict], **extra: Any) -> dict:
    from services.temporal.executor import capture_temporal_routing_input

    return {
        "nodes": nodes,
        "edges": edges,
        "workflow_id": GRAPH_ID,
        "workflow_slug": GRAPH_ID,
        "session_id": SESSION_ID,
        **capture_temporal_routing_input(),
        **extra,
    }


def _edge(source: str, target: str, handle: str = "input-main", **data: Any) -> dict:
    return {"id": f"{source}->{target}", "source": source, "target": target, "targetHandle": handle, **({"data": data} if data else {})}


# ---------------------------------------------------------------------------
# Scenarios: one or more histories each
# ---------------------------------------------------------------------------


async def machina_manual_run(rec: Recorder) -> list[WorkflowHistory]:
    """A canvas Run: Start is skipped, one node activity runs."""
    nodes = [{"id": "start", "type": "start", "data": {}}, {"id": "console", "type": "console", "data": {}}]
    workflow_id = "fixture-machina-manual"
    await rec.run("MachinaWorkflow", _graph_input(nodes, [_edge("start", "console")], execution_id=workflow_id), workflow_id)
    return [await rec.history(workflow_id)]


async def machina_trigger_conditional(rec: Recorder) -> list[WorkflowHistory]:
    """A trigger-fired run: the trigger's output is stored, one conditional
    branch runs and one is skipped, and the run is recorded."""
    nodes = [
        {"id": "hook", "type": "webhookTrigger", "data": {}, "_pre_executed": True,
         "_trigger_output": {"method": "POST", "path": "fixture", "body": "{}", "json": {}}},
        {"id": "source", "type": "console", "data": {}},
        {"id": "hit", "type": "console", "data": {}},
        {"id": "miss", "type": "console", "data": {}},
    ]
    edges = [
        _edge("hook", "source"),
        _edge("source", "hit", condition={"field": "result.answer", "operator": "eq", "value": 42}),
        _edge("source", "miss", condition={"field": "result.answer", "operator": "eq", "value": 99}),
    ]
    workflow_id = "fixture-machina-trigger"
    await rec.run("MachinaWorkflow", _graph_input(nodes, edges, execution_id=workflow_id, graphVersion=2, generation=1,
                                                  root_execution_id=workflow_id, data_scope_id="fixture-scope"), workflow_id)
    return [await rec.history(workflow_id)]


async def machina_chat_agent(rec: Recorder) -> list[WorkflowHistory]:
    """The owner's chat message: the run is claimed and finished, and an
    agent child calls a tool once before it answers."""
    from services.temporal.workflow import CHAT_MESSAGE_SOURCE, CHAT_MESSAGE_TYPE

    envelope = {"id": "r_fixture", "source": CHAT_MESSAGE_SOURCE, "type": CHAT_MESSAGE_TYPE,
                "data": {"run_id": "r_fixture", "message": "What is 6*7?", "session_id": GRAPH_ID}}
    plan = {"prompt": "call:calculator", "tools": [{"node_type": "calculatorTool", "node_id": "calc", "name": "calculator"}]}
    nodes = [
        {"id": "chat", "type": "chatTrigger", "data": {}, "_pre_executed": True,
         "_trigger_output": {"message": "What is 6*7?", "_event_envelope": envelope}},
        {"id": "agent", "type": "aiAgent", "data": {"fixture": plan}},
        {"id": "calc", "type": "calculatorTool", "data": {}},
        {"id": "reply", "type": "chatReply", "data": {}},
    ]
    edges = [_edge("chat", "agent"), _edge("calc", "agent", "input-tools"), _edge("agent", "reply")]
    workflow_id = "fixture-machina-chat"
    await rec.run("MachinaWorkflow", _graph_input(nodes, edges, execution_id=workflow_id, graphVersion=2, generation=1,
                                                  root_execution_id=workflow_id, data_scope_id="fixture-scope"), workflow_id)
    return [await rec.history(workflow_id), await rec.history(f"{workflow_id}-agent-agent")]


def _agent_context(workflow_id: str, plan: dict, node_type: str = "aiAgent") -> dict:
    """A standalone agent's input, as a parent workflow passes it."""
    nodes = [{"id": "lead", "type": node_type, "data": {"fixture": plan}}, {"id": "helper", "type": "aiAgent", "data": {}}]
    return {
        "node_id": "lead",
        "node_type": node_type,
        "node_data": {"fixture": plan},
        "workflow_id": GRAPH_ID,
        "session_id": SESSION_ID,
        "execution_id": workflow_id,
        "nodes": nodes,
        "edges": [_edge("helper", "lead", "input-teammates")],
    }


async def agent_direct_delegation(rec: Recorder) -> list[WorkflowHistory]:
    """An agent delegates to a connected teammate (``delegate_to_*``), which
    runs as a child agent, then answers."""
    plan = {"prompt": 'call:delegate_to_helper {"task": "Summarise the day"}',
            "tools": [{"node_type": "aiAgent", "node_id": "helper", "name": "delegate_to_helper"}]}
    workflow_id = "fixture-agent-delegation"
    await rec.run("AgentWorkflow", _agent_context(workflow_id, plan), workflow_id)
    return [await rec.history(workflow_id), await rec.history(f"{workflow_id}-delegate-helper-1-1")]


async def agent_task_manager(rec: Recorder) -> list[WorkflowHistory]:
    """A team lead assigns work through Task Manager: the preflight, the
    abandoned DelegatedTaskWorkflow runner and the assignee agent it starts."""
    plan = {
        "prompt": 'call:tasks {"operation": "assign_task", "assignee_node_id": "helper", "task": "Draft the weekly summary"}',
        "payload": {"team_id": "fixture-team"},
        "tools": [
            {"node_type": "taskManager", "node_id": "tasks", "name": "tasks"},
            {"node_type": "aiAgent", "node_id": "helper", "name": "delegate_to_helper", "extra": {"llm_hidden": True}},
        ],
    }
    workflow_id = "fixture-agent-team-lead"
    await rec.run("AgentWorkflow", _agent_context(workflow_id, plan, node_type="ai_employee"), workflow_id)
    child_id = f"{workflow_id}-delegate-helper-1-1-{DELEGATION_REQUEST['team_task_id']}"
    await rec.wait_closed(f"{child_id}-runner")
    return [await rec.history(workflow_id), await rec.history(f"{child_id}-runner"), await rec.history(child_id)]


async def agent_rollover(rec: Recorder) -> list[WorkflowHistory]:
    """An agent continues as new after a tool turn, carrying its transcript,
    then answers in the next run. (Rollover server.)"""
    plan = {"prompt": "call:calculator", "tools": [{"node_type": "calculatorTool", "node_id": "calc", "name": "calculator"}]}
    workflow_id = "fixture-agent-rollover"
    handle = await rec.run("AgentWorkflow", _agent_context(workflow_id, plan), workflow_id)
    return await rec.chain(workflow_id, handle.first_execution_run_id)


# ---- controllers, listeners, cron ------------------------------------------

READ_SLUG = "fixture-queue-read"


def _envelope(event_id: str) -> dict:
    """A webhook event as ``dispatch.emit`` signals it (``model_dump``)."""
    return {
        "specversion": "1.0", "id": event_id, "source": "opencompany://nodes/webhook_trigger",
        "type": PUSH_EVENT_TYPE, "time": "2026-10-24T00:00:00Z", "subject": "fixture",
        "datacontenttype": "application/json", "dataschema": None,
        "data": {"method": "POST", "path": "fixture", "headers": {}, "query": {}, "body": "{}", "json": {}},
        "workflow_id": GRAPH_ID, "trigger_node_id": None, "correlation_id": None,
    }


def _trigger_graph(trigger: str, node_type: str) -> tuple[list[dict], list[dict]]:
    return [{"id": trigger, "type": node_type, "data": {}}, {"id": "console", "type": "console", "data": {}}], [_edge(trigger, "console")]


def _listener_args(slug: str, trigger: str, node_type: str, *, controlled: str | None, **extra: Any) -> dict:
    """``listener_args`` as ``DeploymentManager`` builds them for a trigger."""
    from services.temporal.executor import capture_temporal_routing_input

    nodes, edges = _trigger_graph(trigger, node_type)
    args = {
        "workflow_id": GRAPH_ID, "workflow_slug": slug, "trigger_node_id": trigger, "trigger_label": trigger,
        "node_type": node_type, "event_type": PUSH_EVENT_TYPE, "filter_params": {}, "nodes": nodes, "edges": edges,
        "parameter_snapshot": {}, "session_id": SESSION_ID, "user_id": "owner", **capture_temporal_routing_input(),
        "graphVersion": 2, "generation": 1, **extra,
    }
    if controlled:
        args.update(execution_id=controlled, root_execution_id=controlled, data_scope_id="fixture-scope",
                    execution_control_version=1, controller_workflow_id=controlled)
    return args


def _push_spec(slug: str, controller_id: str) -> dict:
    args = _listener_args(slug, "hook", "webhookTrigger", controlled=controller_id,
                          filter_params={"path": "fixture", "method": "POST"})
    return {"listener_id": f"{slug}-hook", "workflow_type": "TriggerListenerWorkflow", "listener_args": args,
            "event_type": PUSH_EVENT_TYPE, "event_types": [PUSH_EVENT_TYPE], "trigger_node_id": "hook",
            "workflow_id": GRAPH_ID, "trigger_kind": "webhook"}


def _poll_spec(slug: str, controller_id: str) -> dict:
    args = _listener_args(slug, "mail", "googleGmailReceive", controlled=controller_id,
                          filter_params={"poll_interval": POLL_INTERVAL_FLOOR_S}, version=1, seen_ids=[])
    return {"listener_id": f"{slug}-mail", "workflow_type": "PollingTriggerWorkflow", "listener_args": args,
            "event_type": "com.opencompany.gmail.received", "event_types": ["com.opencompany.gmail.received"],
            "trigger_node_id": "mail", "workflow_id": GRAPH_ID, "trigger_kind": "polling"}


def _controller_input(controller_id: str, **extra: Any) -> dict:
    """A versioned (execution-control v1) controller, as Start opens it."""
    return {"workflow_id": GRAPH_ID, "generation": 1, "execution_id": controller_id, "root_execution_id": controller_id,
            "data_scope_id": "fixture-scope", "state": "running", "revision": 1,
            "execution_control_version": 1, "controller_workflow_id": controller_id, **extra}


async def _settle_and_reset(rec: Recorder, controller: Any) -> None:
    """Wait for the queue to drain and every root to leave, then Reset."""

    async def idle() -> bool:
        status = await controller.query("status")
        return status["queued_events"] == 0 and not status.get("live_roots")

    await rec.until(idle)
    await controller.signal("reset")
    await controller.result()


async def controller_push(rec: Recorder) -> list[WorkflowHistory]:
    """A webhook trigger registers with its generation's controller, and one
    event spawns a graph run that enrolls as a root and finishes."""
    slug, controller_id = "fixture-push", "workflow-control-fixture-push-g1"
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(controller_id), controller_id)
    await controller.signal("register_trigger", _push_spec(slug, controller_id))
    await controller.signal("on_event", _envelope("event-1"))
    await rec.wait_closed(f"{slug}-hook-event-1")
    await _settle_and_reset(rec, controller)
    return [*await rec.chain(controller_id, controller.first_execution_run_id), await rec.history(f"{slug}-hook-event-1")]


async def controller_poll(rec: Recorder) -> list[WorkflowHistory]:
    """A polling trigger: the controller polls a baseline, finds one new
    mail on the next poll and runs the graph for it."""
    slug, controller_id = "fixture-poll", "workflow-control-fixture-poll-g1"
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(controller_id), controller_id)
    await controller.signal("register_trigger", _poll_spec(slug, controller_id))
    await rec.wait_closed(f"{slug}-mail-{POLLED_MAIL['id']}")
    await _settle_and_reset(rec, controller)
    return [*await rec.chain(controller_id, controller.first_execution_run_id), await rec.history(f"{slug}-mail-{POLLED_MAIL['id']}")]


async def controller_stop_resume(rec: Recorder) -> list[WorkflowHistory]:
    """Stop (production's admission and checkpoint Updates) queues two events;
    Resume releases them and each spawns its run."""
    from types import SimpleNamespace

    from services.deployment.execution_control import transition_generation

    slug, controller_id = "fixture-stop", "workflow-control-fixture-stop-g1"
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(controller_id), controller_id)
    await controller.signal("register_trigger", _push_spec(slug, controller_id))
    scope = SimpleNamespace(controller_workflow_id=controller_id, controller_run_id=controller.first_execution_run_id,
                            workflow_id=GRAPH_ID, generation=1, revision=2)
    await transition_generation(rec.client, scope, paused=True)
    for event_id in ("event-1", "event-2"):
        await controller.signal("on_event", _envelope(event_id))

    async def queued() -> bool:
        return (await controller.query("status"))["queued_events"] == 2

    await rec.until(queued)
    scope.revision = 3
    await transition_generation(rec.client, scope, paused=False)
    for event_id in ("event-1", "event-2"):
        await rec.wait_closed(f"{slug}-hook-{event_id}")
    await _settle_and_reset(rec, controller)
    return [*await rec.chain(controller_id, controller.first_execution_run_id),
            *[await rec.history(f"{slug}-hook-{event_id}") for event_id in ("event-1", "event-2")]]


async def controller_queue_read(rec: Recorder) -> list[WorkflowHistory]:
    """A controller that carried a spilled queue reads it back from the app
    database (``controller.queue.read``) and runs the event it holds."""
    controller_id = "workflow-control-fixture-queue-read-g1"
    spec = _push_spec(READ_SLUG, controller_id)
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(
        controller_id, triggers={spec["listener_id"]: spec}, overflow=True, durable_prefix=0), controller_id)
    await rec.wait_closed(f"{READ_SLUG}-hook-spilled-1")
    await _settle_and_reset(rec, controller)
    return [*await rec.chain(controller_id, controller.first_execution_run_id), await rec.history(f"{READ_SLUG}-hook-spilled-1")]


async def controller_spill_rollover(rec: Recorder) -> list[WorkflowHistory]:
    """A stopped controller holding 260 events rolls over: 256 are carried
    and the rest are spilled to the app database. (Rollover server.)"""
    slug, controller_id, queue = "fixture-spill", "workflow-control-fixture-spill-g1", "fixture-burst"
    spec = _push_spec(slug, controller_id)
    stopped = {"state": "paused", "participant_state": "paused", "participant_revision": 1}
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(
        controller_id, triggers={spec["listener_id"]: spec}, **stopped), controller_id, task_queue=queue)
    # No worker polls this queue yet, so every signal lands before the first
    # workflow task and one activation sees them all.
    for index in range(260):
        await controller.signal("on_event", _envelope(f"burst-{index}"))
    async with rec.worker(queue):
        await rec.until(lambda: rec.rolled_over(controller))
        await controller.signal("reset")
        await controller.result()
    return await rec.chain(controller_id, controller.first_execution_run_id)


async def legacy_trigger_listener(rec: Recorder) -> list[WorkflowHistory]:
    """A deployment without execution control (before RFC-0004): its own
    TriggerListenerWorkflow spawns a run per event and continues as new.
    (Rollover server.)"""
    slug = "fixture-listener"
    args = _listener_args(slug, "hook", "webhookTrigger", controlled=None, filter_params={"path": "fixture", "method": "POST"})
    listener = await rec.start("TriggerListenerWorkflow", args, f"{slug}-hook")
    for event_id in ("event-1", "event-2"):
        await listener.signal("on_event", _envelope(event_id))
        await rec.wait_closed(f"{slug}-hook-{event_id}")
    await rec.until(lambda: rec.rolled_over(listener))
    await listener.terminate("fixture recorded")
    return [*await rec.chain(f"{slug}-hook", listener.first_execution_run_id),
            *[await rec.history(f"{slug}-hook-{event_id}") for event_id in ("event-1", "event-2")]]


async def legacy_polling_trigger(rec: Recorder) -> list[WorkflowHistory]:
    """A deployment without execution control polls in its own
    PollingTriggerWorkflow, runs the graph for a new mail and continues as
    new. (Rollover server.)"""
    slug = "fixture-poller"
    args = _listener_args(slug, "mail", "googleGmailReceive", controlled=None,
                          filter_params={"poll_interval": POLL_INTERVAL_FLOOR_S}, version=1, seen_ids=[])
    poller = await rec.start("PollingTriggerWorkflow", args, f"{slug}-mail")
    await rec.wait_closed(f"{slug}-mail-{POLLED_MAIL['id']}")
    await rec.until(lambda: rec.rolled_over(poller))
    await poller.terminate("fixture recorded")
    return [*await rec.chain(f"{slug}-mail", poller.first_execution_run_id), await rec.history(f"{slug}-mail-{POLLED_MAIL['id']}")]


async def cron_schedule(rec: Recorder) -> list[WorkflowHistory]:
    """A cron trigger's Schedule fires CronTriggerWorkflow, which enrolls
    with its controller and starts the graph run."""
    from services.temporal.schedules import create_cron_schedule

    slug, controller_id = "fixture-cron", "workflow-control-fixture-cron-g1"
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(controller_id), controller_id)
    listener_data = _listener_args(slug, "cron", "cronScheduler", controlled=controller_id, cron_expression="0 9 * * 1",
                                   frequency="cron", timezone="UTC", schedule={"cron": "0 9 * * 1"})
    schedule_id = await create_cron_schedule(
        rec.client, workflow_id=GRAPH_ID, workflow_slug=slug, node_id="cron", trigger_label="cron",
        cron_expression="0 9 * * 1", timezone="UTC", listener_data=listener_data, task_queue=TASK_QUEUE,
    )
    schedule = rec.client.get_schedule_handle(schedule_id)
    await schedule.trigger()

    async def fired() -> bool:
        return bool((await schedule.describe()).info.recent_actions)

    await rec.until(fired)
    cron_id = (await schedule.describe()).info.recent_actions[-1].action.workflow_id
    result = await rec.client.get_workflow_handle(cron_id).result()
    await rec.wait_closed(result["spawned_child_id"])
    await schedule.delete()
    await _settle_and_reset(rec, controller)
    return [await rec.history(cron_id), await rec.history(result["spawned_child_id"]),
            *await rec.chain(controller_id, controller.first_execution_run_id)]


# ---- employee jobs, approved sends --------------------------------------------


async def employee_job(rec: Recorder) -> list[WorkflowHistory]:
    """An employee job enrolls with its controller, its lead agent answers,
    and the result is delivered; a later delivery is resolved on its own."""
    controller_id = "workflow-control-fixture-job-g1"
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(controller_id), controller_id)
    job_id = "employee-job:fixture"
    context = {
        **_agent_context(job_id, {}), "node_id": "lead", "node_type": "ai_employee", "employee_job_id": "fixture-job",
        "generation": 1, "graphVersion": 2, "root_execution_id": job_id, "data_scope_id": "fixture-scope",
        "execution_control_version": 1, "controller_workflow_id": controller_id,
    }
    await rec.run("EmployeeJobWorkflow", context, job_id)
    await rec.run("EmployeeJobDeliveryWorkflow", {"job_id": "fixture-job", "resolution_id": "fixture-resolution"},
                  "employee-delivery:fixture")
    await _settle_and_reset(rec, controller)
    return [await rec.history(job_id), await rec.history(f"{job_id}:lead"), await rec.history("employee-delivery:fixture"),
            *await rec.chain(controller_id, controller.first_execution_run_id)]


async def approved_tool_call(rec: Recorder) -> list[WorkflowHistory]:
    """An approved send waits out its Undo window, claims the send, runs the
    held node activity once and records the outcome."""
    from datetime import datetime, timedelta, timezone

    grace_until = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    workflow_id = "approval-send-fixture-r1"
    await rec.run("ApprovedToolCallWorkflow",
                  {"payload_version": 1, "approval_id": "fixture", "revision": 1, "grace_until": grace_until}, workflow_id)
    return [await rec.history(workflow_id)]


async def workspace_task(rec: Recorder) -> list[WorkflowHistory]:
    """A direct Workspace task: the workflow's task controller admits it by
    Update-with-Start and runs it as a NodeInvocationWorkflow; Reset then
    clears the Workspace runtime."""
    import hashlib

    from temporalio.client import WithStartWorkflowOperation
    from temporalio.common import WorkflowIDConflictPolicy

    from services.node_invocations import controller_id, invocation_id
    from services.node_registry import get_node_class
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow

    cls = get_node_class("mobile_use_agent")
    prompt = "Open the settings app"
    run_id = invocation_id("owner", GRAPH_ID, "phone", "00000000-0000-4000-8000-000000000001")
    context = {"node_id": "phone", "node_type": cls.type, "node_data": {"prompt": prompt}, "workflow_id": GRAPH_ID,
               "workflow_slug": GRAPH_ID, "execution_id": run_id, "session_id": run_id, "user_id": "owner",
               "nodes": [{"id": "phone", "type": cls.type, "data": {}}], "edges": [], "generation": 0,
               "graphVersion": 1, "inputs": {}}
    submission = {"principal": "owner", "workflow_id": GRAPH_ID, "node_id": "phone",
                  "fingerprint": hashlib.sha256(prompt.encode()).hexdigest(),
                  "activity": f"node.{cls.type}.v{cls.version}", "task_queue": cls.task_queue,
                  "timeout_s": int(cls.start_to_close_timeout.total_seconds()), "context": context, "admission_epoch": 0}
    controller = controller_id(GRAPH_ID)
    await rec.client.execute_update_with_start_workflow(
        WorkspaceTaskControllerWorkflow.submit, submission, id=f"submit-{run_id}",
        start_workflow_operation=WithStartWorkflowOperation(
            WorkspaceTaskControllerWorkflow.run, {"workflow_id": GRAPH_ID}, id=controller, task_queue=TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING),
    )
    await rec.wait_closed(run_id)
    handle = rec.client.get_workflow_handle(controller)
    await handle.execute_update(WorkspaceTaskControllerWorkflow.reset, "fixture-reset")
    # The task controller never ends on its own.
    await handle.terminate("fixture recorded")
    return [await rec.history(controller), await rec.history(run_id)]


def _pi_context(workflow_id: str, plan: dict) -> dict:
    """A Coding Agent on the Pi harness, as a graph run starts it."""
    from services.coding_agent_runtime import freeze_pi_local_control

    context = {"node_id": "coder", "node_type": "coding_agent", "pi_harness_version": 1, "node_data": {"fixture": plan},
               "workflow_id": GRAPH_ID, "session_id": SESSION_ID, "execution_id": workflow_id,
               "root_execution_id": workflow_id, "user_id": "owner",
               "nodes": [{"id": "coder", "type": "coding_agent", "data": {}}], "edges": []}
    freeze_pi_local_control(context)
    return context


async def pi_coding_agent(rec: Recorder) -> list[WorkflowHistory]:
    """A Coding Agent turn on the Pi harness: the driver runs the model
    operation and one tool operation through the workflow, then the next
    turn answers."""
    plan = {"prompt": "call:calculator", "tools": [{"node_type": "calculatorTool", "node_id": "calc", "name": "calculator"}]}
    workflow_id = "fixture-pi-coder"
    await rec.run("PiCodingAgentWorkflow", _pi_context(workflow_id, plan), workflow_id)
    return [await rec.history(workflow_id)]


async def pi_inline_delegation(rec: Recorder) -> list[WorkflowHistory]:
    """An agent outside Temporal hands a task to a Coding Agent: the
    detached PiInlineDelegationWorkflow verifies the admission and runs the
    task through a DelegatedTaskWorkflow runner and the Pi child."""
    from services.temporal.pi_inline_delegation import INLINE_DELEGATION_VERSION, validate_inline_request
    from services.temporal.pi_protocol import canonical_digest

    runner_id = "coding-inline-fixture"
    child = {**_pi_context(f"{runner_id}-agent", {}), "node_data": {"system_message": "Fix the build", "prompt": "Fix the build"},
             "parent_node_id": "lead", "delegation_depth": 1, "max_delegation_depth": 2, "max_concurrent_subagents": 1,
             "team_id": "fixture-team", "team_task_id": "delegated_pi_fixture", "trace_id": "call-1", "inputs": {},
             "outputs": {}, "invocation": {"task": "Fix the build", "context": ""}}
    lifecycle = {"team_id": "fixture-team", "team_task_id": "delegated_pi_fixture", "parent_agent_node_id": "lead",
                 "child_agent_node_id": "coder", "child_agent_name": "Coding Agent", "workflow_id": GRAPH_ID,
                 "task": "Fix the build", "context": None, "employee_job_id": None, "root_execution_id": child["root_execution_id"],
                 "delegation_depth": 1, "trace_id": "call-1", "assignment_event_id": "delegated_pi_fixture:assigned",
                 "queued_event_id": "delegated_pi_fixture:queued"}
    request = {"inline_delegation_version": INLINE_DELEGATION_VERSION, "runner_workflow_id": runner_id,
               "child_workflow_id": f"{runner_id}-agent", "limit": 1, "lifecycle": lifecycle, "child_context": child}
    request["request_digest"] = canonical_digest(request)
    validate_inline_request(request)
    await rec.run("PiInlineDelegationWorkflow", request, runner_id)
    return [await rec.history(runner_id), await rec.history(f"{runner_id}-runner"), await rec.history(f"{runner_id}-agent")]


async def _paused_agent(rec: Recorder, *, pi: bool) -> list[WorkflowHistory]:
    """Record a real acknowledged paused generation and its resumed agent."""
    _PAUSE_TOOL_STARTED.clear()
    _PAUSE_TOOL_RELEASE.clear()
    suffix = "pi" if pi else "native"
    identifier, controller_id = f"fixture-paused-{suffix}", f"fixture-paused-{suffix}-controller"
    controller = await rec.start("WorkflowControlWorkflow", _controller_input(controller_id), controller_id)
    plan = {"prompt": "call:calculator", "tools": [{"node_type": "calculatorTool", "node_id": "paused-tool", "name": "calculator"}]}
    context = _pi_context(identifier, plan) if pi else _agent_context(identifier, plan)
    context.update(execution_control_version=1, controller_workflow_id=controller_id, generation=1)
    handle = await rec.start("PiCodingAgentWorkflow" if pi else "AgentWorkflow", context, identifier)
    await asyncio.wait_for(_PAUSE_TOOL_STARTED.wait(), 30)
    paused = {"state": "paused", "revision": 2, "generation": 1}
    await controller.execute_update("set_control_state", paused)
    await handle.execute_update("set_control_state", paused)
    _PAUSE_TOOL_RELEASE.set()
    checkpoint = await asyncio.wait_for(handle.execute_update("wait_for_checkpoint", paused), 30)
    assert checkpoint["checkpoint"]
    paused_histories = [await handle.fetch_history(), await controller.fetch_history()]
    running = {"state": "running", "revision": 3, "generation": 1}
    await controller.execute_update("set_control_state", running)
    await handle.execute_update("set_control_state", running)
    assert (await asyncio.wait_for(handle.result(), 30))["success"]
    await _settle_and_reset(rec, controller)
    return [*paused_histories, await handle.fetch_history(), await controller.fetch_history()]


async def paused_native_generation(rec: Recorder) -> list[WorkflowHistory]:
    """Native agent tool completes, generation acknowledges Stop, then Resume finishes the same run."""
    return await _paused_agent(rec, pi=False)


async def paused_pi_generation(rec: Recorder) -> list[WorkflowHistory]:
    """Pi agent drains the driver at Stop, records its checkpoint, and resumes the same run."""
    return await _paused_agent(rec, pi=True)


Scenario = Callable[[Recorder], Awaitable[list[WorkflowHistory]]]

SCENARIOS: dict[str, Scenario] = {
    "paused_native_generation": paused_native_generation,
    "paused_pi_generation": paused_pi_generation,
    "machina_manual_run": machina_manual_run,
    "machina_trigger_conditional": machina_trigger_conditional,
    "machina_chat_agent": machina_chat_agent,
    "agent_direct_delegation": agent_direct_delegation,
    "agent_task_manager": agent_task_manager,
    "controller_push": controller_push,
    "controller_poll": controller_poll,
    "controller_stop_resume": controller_stop_resume,
    "controller_queue_read": controller_queue_read,
    "cron_schedule": cron_schedule,
    "employee_job": employee_job,
    "approved_tool_call": approved_tool_call,
    "workspace_task": workspace_task,
    "pi_coding_agent": pi_coding_agent,
    "pi_inline_delegation": pi_inline_delegation,
}
# Run on a server that suggests continue-as-new from ROLLOVER_HISTORY_COUNT
# events on, so each one records its rollover.
ROLLOVER_SCENARIOS: dict[str, Scenario] = {
    "agent_rollover": agent_rollover,
    "controller_spill_rollover": controller_spill_rollover,
    "legacy_trigger_listener": legacy_trigger_listener,
    "legacy_polling_trigger": legacy_polling_trigger,
}


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout.strip()


def _search_attributes() -> list:
    from temporalio.api.enums.v1 import IndexedValueType
    from temporalio.common import SearchAttributeKey

    from services.temporal.search_attributes import EVENT_SEARCH_ATTRIBUTES

    key = {
        IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD: SearchAttributeKey.for_keyword,
        IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST: SearchAttributeKey.for_keyword_list,
        IndexedValueType.INDEXED_VALUE_TYPE_DATETIME: SearchAttributeKey.for_datetime,
    }
    return [key[spec.indexed_type](spec.name) for spec in EVENT_SEARCH_ATTRIBUTES]


async def record(cli: str, out: Path, *, source_commit: str | None = None, source_root: Path | None = None) -> None:
    import nodes  # noqa: F401 -- the plugin registry names the node activities
    import temporalio
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    from services.temporal._install import cli_pin
    from services.temporal.execution_control_activities import ExecutionControlActivities
    from services.temporal.pi_activities import PiControlActivities, pi_put_blob
    from services.temporal.pi_protocol import DRIVER_QUEUE
    from services.temporal.plugin_registry import temporal_plugins
    from services.temporal.worker import _framework_workflows

    from services.temporal import polling_trigger_workflow, workflow_control_workflow

    polling_trigger_workflow._MIN_POLL_INTERVAL_S = POLL_INTERVAL_FLOOR_S
    workflow_control_workflow._MIN_POLL_INTERVAL_S = POLL_INTERVAL_FLOOR_S
    fakes = fake_activities()
    recorded: dict[str, list[WorkflowHistory]] = {}
    servers = (
        (SCENARIOS, []),
        (ROLLOVER_SCENARIOS, ["--dynamic-config-value", f"limit.historyCount.suggestContinueAsNew={ROLLOVER_HISTORY_COUNT}"]),
    )
    for scenarios, server_args in servers:
        with tempfile.TemporaryDirectory(prefix="opencompany-fixture-server-") as scratch:
            async with await WorkflowEnvironment.start_local(
                dev_server_existing_path=cli,
                dev_server_database_filename=str(Path(scratch) / "temporal.db"),
                dev_server_log_level="error",
                dev_server_extra_args=server_args,
                ip="127.0.0.1",
                port=None,
                ui=False,
                identity=IDENTITY,
                search_attributes=_search_attributes(),
            ) as env:
                client = env.client

                def worker(queue: str) -> Worker:
                    # The production workflow list, registered the way the
                    # embedded worker manager registers it.
                    return Worker(client, task_queue=queue, workflows=_framework_workflows(), plugins=temporal_plugins(),
                                  activities=[*fakes, *ExecutionControlActivities(client).activities(),
                                              *PiControlActivities(client).activities(), pi_put_blob],
                                  max_cached_workflows=0)

                async with contextlib.AsyncExitStack() as workers:
                    await workers.enter_async_context(worker(TASK_QUEUE))
                    for queue in sorted(activity_queues()):
                        await workers.enter_async_context(Worker(client, task_queue=queue, activities=fakes))
                    # The Pi driver runs out of process in production, on its own queue.
                    await workers.enter_async_context(Worker(client, task_queue=DRIVER_QUEUE,
                                                             activities=[PiDriverPeer(client).drive],
                                                             max_concurrent_activities=1))
                    rec = Recorder(client, worker)
                    for name, scenario in scenarios.items():
                        print(f"[fixtures] {name}")
                        recorded[name] = await asyncio.wait_for(scenario(rec), timeout=180)

    replayer = Replayer(workflows=_framework_workflows(), plugins=temporal_plugins())
    for name, histories in recorded.items():
        for history in histories:
            await replayer.replay_workflow(history)

    pin = cli_pin()
    actual_cli = subprocess.check_output([cli, "--version"], encoding="utf-8").strip()
    match = re.search(r"(?:temporal CLI|temporal version)\s+(\d+\.\d+\.\d+)\s+\(Server\s+(\d+\.\d+\.\d+)", actual_cli, re.I)
    if match is None:
        raise RuntimeError(f"Cannot identify recorder CLI: {actual_cli}")
    if out.exists():
        raise FileExistsError(f"Release histories are immutable: choose a new fixture output directory ({out})")
    out.mkdir(parents=True)
    for name, histories in recorded.items():
        document = {
            "scenario": name,
            "description": " ".join(({**SCENARIOS, **ROLLOVER_SCENARIOS}[name].__doc__ or "").split()),
            "histories": [{"workflow_id": history.workflow_id, "history": history.to_json_dict()} for history in histories],
        }
        (out / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
    manifest = {
        "release": release(),
        "origin_commit": source_commit or _git("rev-parse", "HEAD"),
        "origin_dirty": False if source_commit else bool(_git("status", "--porcelain")),
        "source_mode": "git-archive" if source_commit else "working-tree",
        "source_archive_autocrlf": False,
        "workflow_source_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((source_root or SERVER_DIR).joinpath("services", "temporal").glob("*.py"))
        },
        "temporalio": temporalio.__version__,
        "temporal_cli": match[1],
        "temporal_server": match[2],
        "release_cli_pin": pin["version"],
        "scenarios": sorted(recorded),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    golden_path = FIXTURES_DIR / "workflow_identities.json"
    if not golden_path.exists():
        write_golden(source_root or SERVER_DIR, manifest["origin_commit"])
    print(f"[fixtures] wrote {sum(len(h) for h in recorded.values())} histories to {out}")


def write_golden(source_root: Path, commit: str) -> None:
    """Freeze command and plugin identities from the selected source tree."""
    import nodes  # noqa: F401 -- production plugin discovery
    from services.node_registry import registered_node_classes
    from services.temporal.worker import _framework_workflows
    from services.temporal.plugin_registry import temporal_plugins
    from tests.temporal.workflow_identities import handler_snapshot, identity_snapshot

    registrations = {"workflows": _framework_workflows()}
    for plugin in temporal_plugins():
        registrations = plugin.configure_replayer(registrations)
    (FIXTURES_DIR / "workflow_identities.json").write_text(json.dumps({
        "origin_commit": commit,
        "workflow_types": sorted(item.__temporal_workflow_definition.name for item in registrations["workflows"]),
        "identities": identity_snapshot(source_root), "patch_entries": [],
        "handlers": handler_snapshot(source_root),
        "plugin_activity_types": {name: f"node.{cls.type}.v{cls.version}" for name, cls in registered_node_classes().items()},
    }, indent=2) + "\n", encoding="utf-8")


@contextlib.contextmanager
def committed_source(commit: str | None):
    """Load real N-1 source without checking out or editing the working tree.

    The recorder is the test driver, but every app module it imports comes
    from the immutable git archive. DATA_DIR stays separate from both trees.
    """
    if not commit:
        yield None, None
        return
    resolved = _git("rev-parse", "--verify", commit + "^{commit}")
    with tempfile.TemporaryDirectory(prefix="opencompany-n-minus-one-") as directory:
        root = Path(directory)
        archive = subprocess.check_output([
            "git", "-c", "core.autocrlf=false", "archive", resolved, "server/services", "server/nodes", "server/core",
            "server/models", "server/constants.py", "server/config", ".env.template", "package.json",
        ], cwd=REPO_ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(root, filter="data")
        sys.path.insert(0, str(root / "server"))
        previous = os.environ.get("OPENCOMPANY_APP_ROOT")
        os.environ["OPENCOMPANY_APP_ROOT"] = str(root)
        try:
            yield resolved, root / "server"
        finally:
            sys.path.remove(str(root / "server"))
            if previous is None:
                os.environ.pop("OPENCOMPANY_APP_ROOT", None)
            else:
                os.environ["OPENCOMPANY_APP_ROOT"] = previous


def main() -> int:
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        print("Set TEMPORAL_TEST_CLI to the pinned Temporal CLI binary (see TEMPORAL_ARCHITECTURE.md).", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", nargs="?", type=Path, default=FIXTURES_DIR / release())
    parser.add_argument("--source-commit", help="Record the exact app source at this commit, isolated from uncommitted changes")
    parser.add_argument("--golden-only", action="store_true", help="Write only the command identity inventory from the selected commit")
    options = parser.parse_args()
    os.environ["DEBUG"] = "false"
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    with tempfile.TemporaryDirectory(prefix="opencompany-fixtures-") as data_dir:
        os.environ["DATA_DIR"] = data_dir
        with committed_source(options.source_commit) as (commit, source_root):
            if options.golden_only:
                write_golden(source_root or SERVER_DIR, commit or _git("rev-parse", "HEAD"))
            else:
                asyncio.run(record(cli, options.out, source_commit=commit, source_root=source_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
