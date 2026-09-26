"""The Browser node (nodes/browser/browser): operations, policy and locked settings.

No Chrome and no CLI run here: a fake runtime hands the node a real
``ProfileController`` (so control and leases behave as in production) and a
fake CLI that records each script call.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest

from nodes.browser._cli import CliResult
from nodes.browser._netpolicy import NetPolicy
from nodes.browser._profiles import Profile
from nodes.browser._session import ControlState, ProfileController
from nodes.browser.browser import BrowserNode, BrowserToolInput
from services.plugin.context import NodeContext

PAGE = {"target_id": "T1", "url": "https://example.com/", "title": "Example"}


class FakeCli:
    def __init__(self, tmp_path) -> None:
        self.calls: List[tuple] = []
        self.results: List[CliResult] = []
        self.tmp = tmp_path

    async def run(self, op: str, args: Dict[str, Any], *, timeout: float) -> CliResult:
        self.calls.append((op, dict(args)))
        if self.results:
            return self.results.pop(0)
        return CliResult(ok=True, value={}, page=dict(PAGE))

    async def interrupt(self) -> None:
        return None

    def dirs(self):
        return {"tmp": self.tmp}

    def stop_daemon(self) -> None:
        return None


class FakeWebMcp:
    def __init__(self) -> None:
        self.invoked: List[tuple] = []

    def tools(self, target_id):
        return [{"name": "search", "read_only": True, "frame_id": "F", "main_frame": True}, {"name": "buy", "read_only": False, "frame_id": "F", "main_frame": True}]

    def count(self, target_id):
        return 2

    async def invoke(self, target_id, name, tool_input, *, frame_id=None, mode="read_only"):
        if mode == "read_only" and name == "buy":
            from services.plugin.base import NodeUserError

            raise NodeUserError("read-only")
        self.invoked.append((name, tool_input, mode))
        return {"status": "completed", "output": "ok", "untrusted": True, "tool": name}


class FakeRuntime:
    def __init__(self, tmp_path) -> None:
        self.controller = ProfileController("bp_1", "Work")
        self.controller.active_target_id = "T1"
        self.controller.tabs["T1"] = dict(PAGE)
        self.cli = FakeCli(tmp_path)
        self.prt = SimpleNamespace(controller=self.controller, cli=self.cli, webmcp=FakeWebMcp())
        self.sessions: Dict[str, Any] = {}
        self.stopped: List[str] = []

    def base_policy(self, *, allow_private_network=False, allowed_domains=()):
        return NetPolicy(allow_private_network=allow_private_network, blocked_local_ports=frozenset({5678}), allowed_domains=allowed_domains)

    def register_session(self, session):
        self.sessions[session.session_id] = session
        return session

    async def open(self, profile, *, wait):
        return self.prt

    async def stop_profile(self, profile_id, *, reason=""):
        self.stopped.append(profile_id)

    def running(self, profile_id):
        return self.prt


@pytest.fixture
def runtime(tmp_path):
    fake = FakeRuntime(tmp_path)
    profile = Profile(id="bp_1", owner_id="owner", name="Work", kind="shared", workflow_id=None, chrome_major=None)
    with (
        patch("nodes.browser._runtime.get_browser_runtime", return_value=fake),
        patch("nodes.browser.browser._profile_for", AsyncMock(return_value=profile)),
        patch("nodes.browser._events.dispatch_browser_updated", AsyncMock()),
        patch("services.employees.node_signals.node_state_changed", lambda *a, **k: None),
    ):
        yield fake


def _ctx(**raw) -> NodeContext:
    return NodeContext(node_id="n1", node_type="browser", workflow_id="wf1", execution_id="e1", user_id="owner", raw=dict(raw))


async def _run(params: Dict[str, Any], **raw) -> Dict[str, Any]:
    return await BrowserNode().execute("n1", params, _ctx(**raw))


async def test_navigate_runs_the_script_and_reports_the_page(runtime):
    result = await _run({"operation": "navigate", "url": "https://example.com"})
    assert result.get("success") is not False, result
    op, args = runtime.cli.calls[-1]
    assert op == "navigate" and args["url"] == "https://example.com" and args["_target_id"] == "T1"
    assert result["url"] == "https://example.com/" and result["title"] == "Example"
    assert runtime.controller.state == ControlState.IDLE  # the step released control


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://localhost:3000", "http://169.254.169.254/", "javascript:alert(1)"])
async def test_refused_urls_never_reach_the_browser(runtime, url):
    result = await _run({"operation": "navigate", "url": url})
    assert result.get("success") is False
    assert runtime.cli.calls == []


async def test_snapshot_refs_stay_server_side_and_resolve_for_click(runtime):
    runtime.cli.results.append(CliResult(ok=True, value={"text": "[e1] link \"Learn more\"", "refs": {"e1": 42}, "truncated": False}, page=dict(PAGE)))
    snap = await _run({"operation": "snapshot"})
    assert "[e1] link" in snap["snapshot"] and "WebMCP" in snap["snapshot"]
    assert "42" not in snap["snapshot"]

    await _run({"operation": "click", "ref": "e1"})
    op, args = runtime.cli.calls[-1]
    assert op == "click" and args["backend_node_id"] == 42 and "selector" not in args

    # An old agent-browser "@e1" selector means the same ref.
    await _run({"operation": "click", "selector": "@e1"})
    assert runtime.cli.calls[-1][1]["backend_node_id"] == 42

    missing = await _run({"operation": "click", "ref": "e9"})
    assert missing.get("success") is False and "snapshot" in missing["error"]


async def test_read_only_browser_refuses_changes(runtime):
    for op in ("click", "type", "press", "select", "webmcp_call"):
        result = await _run({"operation": op, "interaction": "read_only", "ref": "e1", "webmcp_tool": "buy"})
        assert result.get("success") is False and "read-only" in result["error"], op
    ok = await _run({"operation": "page_text", "interaction": "read_only"})
    assert ok.get("success") is not False


async def test_a_retried_change_is_not_repeated(runtime):
    with patch("nodes.browser.browser._attempt", return_value=2):
        result = await _run({"operation": "click", "x": 10, "y": 10})
    assert result.get("success") is False and "snapshot" in result["error"]
    assert runtime.cli.calls == []


async def test_a_tool_call_cannot_change_the_operator_settings(runtime):
    saved = {"operation": "navigate", "interaction": "read_only", "allow_private_network": False, "webmcp_mode": "disabled"}
    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value=saved))
    node = BrowserNode()
    with patch("services.plugin.deps.get_database", return_value=database):
        # On the Temporal path the model's arguments arrive merged over the saved ones.
        merged = {**saved, "operation": "click", "x": 5, "y": 5, "interaction": "full", "allow_private_network": True}
        result = await node.execute_as_tool({"operation": "click", "x": 5, "y": 5, "interaction": "full"}, merged, _ctx(tool_args={"operation": "click"}))
    assert "read-only" in (result.get("error") or "")
    assert runtime.cli.calls == []


def test_the_model_schema_has_no_operator_settings_or_host_code():
    schema = BrowserToolInput.model_json_schema()
    props = set(schema["properties"])
    for locked in ("profile_id", "interaction", "webmcp_mode", "allowed_domains", "allow_private_network", "code", "expression", "op_timeout_s"):
        assert locked not in props
    ops = set(schema["properties"]["operation"]["enum"])
    assert not ops & {"evaluate", "run_python", "close"}
    assert BrowserNode.server_controlled_fields >= {"profile_id", "interaction", "allow_private_network", "code"}


async def test_webmcp_mode_decides_what_the_agent_may_call(runtime):
    listed = await _run({"operation": "webmcp_list", "webmcp_mode": "read_only"})
    callable_by_name = {t["name"]: t["callable"] for t in listed["webmcp_tools"]}
    assert callable_by_name == {"search": True, "buy": False}
    refused = await _run({"operation": "webmcp_call", "webmcp_tool": "buy", "webmcp_mode": "read_only"})
    assert refused.get("success") is False
    done = await _run({"operation": "webmcp_call", "webmcp_tool": "buy", "webmcp_input": '{"qty": 1}', "webmcp_mode": "all"})
    assert done["webmcp_result"]["status"] == "completed"
    assert runtime.prt.webmcp.invoked[-1] == ("buy", {"qty": 1}, "all")


async def test_request_user_waits_for_the_hand_back(runtime):
    task = asyncio.create_task(_run({"operation": "request_user", "reason": "login", "message": "Please sign in"}))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if runtime.controller.state == ControlState.AWAITING_USER:
            break
    assert runtime.controller.state == ControlState.AWAITING_USER
    assert runtime.controller.pending.message == "Please sign in"
    granted, _ = await runtime.controller.take_over("viewer-1")
    assert granted and runtime.controller.state == ControlState.USER
    await runtime.controller.hand_back("viewer-1", note="signed in")
    result = await asyncio.wait_for(task, 5)
    assert result["handback"] == {"status": "handed_back", "note": "signed in"}
    assert runtime.controller.state == ControlState.IDLE


async def test_legacy_agent_browser_parameters_still_validate(runtime):
    result = await _run({"operation": "fill", "selector": "#q", "value": "hello", "session": "old", "headed": True, "timeout": 12})
    op, args = runtime.cli.calls[-1]
    assert op == "type" and args["text"] == "hello" and args["clear"] is True and args["selector"] == "#q"
    assert result.get("success") is not False
