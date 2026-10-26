"""No secret rides a run's copy of its nodes' settings into Temporal, and
every node still runs with its saved secrets (RFC-0006 1b.31): the sanitizer
drops them, and the node's saved row puts them back where it runs."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import nodes  # noqa: F401 -- populate plugin registry
from services.workflow_sanitizer import restore_dropped, sanitize_runtime_payload

SAVED = {
    "url": "https://api.example/orders",
    "method": "GET",
    "api_key": "saved-key",
    "headers": {"Authorization": "Bearer saved", "Proxy-Authorization": "Basic p", "Cookie": "s=1", "Accept": "application/json"},
    "items": [{"token": "t"}],
}


def test_the_header_secrets_are_dropped():
    assert sanitize_runtime_payload(SAVED)["headers"] == {"Accept": "application/json"}


def test_restore_dropped_puts_back_only_what_the_sanitizer_drops():
    target = {**sanitize_runtime_payload(SAVED), "url": "https://api.example/refunds"}
    before = deepcopy((target, SAVED))

    restored = restore_dropped(target, {**SAVED, "timeout": 30})

    # The run's own values stay, lists are left as they are, and a key the
    # sanitizer keeps is not copied in.
    assert restored == {**SAVED, "url": "https://api.example/refunds", "items": [{}]}
    assert (target, SAVED) == before
    assert restore_dropped(target, SAVED, keep=("headers",))["headers"] == {"Accept": "application/json"}


@pytest.mark.asyncio
async def test_a_run_from_its_snapshot_sends_the_saved_authorization(harness):
    harness.database.get_node_parameters = AsyncMock(return_value=SAVED)
    captured = sanitize_runtime_payload(SAVED)
    snapshot = {"http": captured}

    params = await harness.executor._prepare_parameters(
        "http", "httpRequest", dict(captured), "s", parameter_snapshot=snapshot, inject_keys=False
    )
    assert params["headers"]["Authorization"] == "Bearer saved"

    # A headers map the model chose replaces the saved one.
    params = await harness.executor._prepare_parameters(
        "http",
        "httpRequest",
        {**captured, "headers": {"Accept": "text/plain"}},
        "s",
        tool_args={"headers": {"Accept": "text/plain"}},
        parameter_snapshot=snapshot,
        inject_keys=False,
    )
    assert params["headers"] == {"Accept": "text/plain"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("node_type", "restored"), [("httpRequest", True), ("taskManager", False)])
async def test_an_in_process_action_tool_gets_its_saved_secrets(monkeypatch, node_type, restored):
    # A ToolNode (Task Manager) reads only the run's copy.
    from services.approvals import tool_calls
    from services.handlers import tools

    seen = {}

    async def check(context, node_cls, params, tool_args):
        seen["params"] = params
        return tool_calls.Checked(result={"status": "checked"})

    monkeypatch.setattr(tool_calls, "check_in_process", check)
    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value=SAVED))
    captured = sanitize_runtime_payload(SAVED)
    config = {
        "node_type": node_type,
        "node_id": "n1",
        "parameters": captured,
        "parameter_snapshot": {"n1": captured},
        "database": database,
    }

    assert await tools._dispatch_tool("tool", {}, config) == {"status": "checked"}
    assert ("Authorization" in seen["params"]["headers"]) is restored
    assert database.get_node_parameters.await_count == int(restored)
