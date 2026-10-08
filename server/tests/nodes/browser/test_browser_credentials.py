"""Canary tests for configured login; no browser or real credential retrieval."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nodes.browser._credentials import fill_credentials
from nodes.browser._session import ProfileController

ORIGIN = "https://example.com"
CANARY = " secret-Σ-canary\n "


def fixture(*, suspension=True, changed_origin=False, capture_error=False):
    controller = ProfileController("profile", "Work")
    controller.active_target_id = "T"
    controller.refs["T"] = {"e1": 1, "e2": 2, "e3": 3}
    calls = []
    async def send(method, args=None, **kwargs):
        calls.append((method, args))
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"url": "https://evil.test" if changed_origin else ORIGIN + "/login"}}}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": str(args["backendNodeId"])}}
        if method == "Runtime.evaluate":
            if args.get("expression") == "document.documentElement":
                return {"result": {"objectId": "document"}}
            return {"result": {"value": {"origin": ORIGIN, "path": "/account", "password": False}}}
        return {"result": {"value": True}}
    page = SimpleNamespace(send=send, detach=AsyncMock())
    runtime = SimpleNamespace(profile=SimpleNamespace(id="profile"), controller=controller, chrome=SimpleNamespace(),
                              cli=SimpleNamespace(suspend_for_credentials=AsyncMock(return_value=suspension)),
                              page_session=AsyncMock(return_value=page),
                              hub=SimpleNamespace(sensitive_barrier=AsyncMock(side_effect=RuntimeError("capture uncertain") if capture_error else None)))
    metadata = {"origin": ORIGIN, "success_path": "/account"}
    auth = SimpleNamespace(get_browser_credential_binding=AsyncMock(return_value=metadata),
                           resolve_browser_credentials=AsyncMock(return_value={**metadata, "username": " user ", "password": CANARY}))
    ctx = SimpleNamespace(user_id="owner", execution_id="task", workflow_id="wf", raw={})
    call = SimpleNamespace(credential_binding_id="opaque", username_ref="e1", password_ref="e2", submit_ref="e3")
    return runtime, auth, ctx, call, calls


async def run_fixture(runtime, auth, ctx, call):
    with patch("core.container.container.auth_service", return_value=auth), patch("services.browser_owners.set_sensitive", AsyncMock()):
        return await fill_credentials(ctx, runtime, call, timeout=5)


async def test_secret_values_only_enter_private_cdp_with_exact_whitespace():
    runtime, auth, ctx, call, calls = fixture()
    result = await run_fixture(runtime, auth, ctx, call)
    assert result == {"success": True}
    assert CANARY not in str(result)
    fills = [args for method, args in calls if method == "Runtime.callFunctionOn" and "setter.call" in args["functionDeclaration"]]
    assert [args["arguments"][0]["value"] for args in fills] == [" user ", CANARY]
    assert runtime.cli.suspend_for_credentials.await_count == 1
    assert runtime.controller.sensitive_login is False
    assert runtime.controller.needs_observation is True


async def test_wrong_origin_does_not_resolve_credentials():
    runtime, auth, ctx, call, _ = fixture(changed_origin=True)
    result = await run_fixture(runtime, auth, ctx, call)
    assert result["success"] is False
    auth.resolve_browser_credentials.assert_not_awaited()
    assert runtime.controller.sensitive_login is False


async def test_uncertain_daemon_suspension_keeps_gate_and_does_not_resolve():
    runtime, auth, ctx, call, _ = fixture(suspension=False)
    result = await run_fixture(runtime, auth, ctx, call)
    assert result["success"] is False
    auth.resolve_browser_credentials.assert_not_awaited()
    assert runtime.controller.sensitive_login is True


async def test_capture_barrier_failure_does_not_resolve_credentials():
    runtime, auth, ctx, call, _ = fixture(capture_error=True)
    result = await run_fixture(runtime, auth, ctx, call)
    assert result["success"] is False
    auth.resolve_browser_credentials.assert_not_awaited()


async def test_changed_origin_after_authorization_never_fills():
    runtime, auth, ctx, call, calls = fixture()
    original = runtime.page_session.return_value.send
    async def send(method, args=None, **kwargs):
        if method == "Page.getFrameTree" and auth.resolve_browser_credentials.await_count:
            return {"frameTree": {"frame": {"url": "https://evil.test/"}}}
        return await original(method, args, **kwargs)
    runtime.page_session.return_value.send = send
    result = await run_fixture(runtime, auth, ctx, call)
    assert result["success"] is False and CANARY not in str(result)
    assert not any(method == "Runtime.callFunctionOn" and "setter.call" in args["functionDeclaration"] for method, args in calls)


async def test_spa_success_cue_cannot_unhide_a_still_filled_username():
    runtime, auth, ctx, call, _ = fixture()
    original = runtime.page_session.return_value.send
    async def send(method, args=None, **kwargs):
        if method == "Runtime.callFunctionOn" and "return !this.isConnected" in args.get("functionDeclaration", ""):
            return {"result": {"value": False}}
        return await original(method, args, **kwargs)
    runtime.page_session.return_value.send = send
    with patch("core.container.container.auth_service", return_value=auth), patch("services.browser_owners.set_sensitive", AsyncMock()):
        result = await fill_credentials(ctx, runtime, call, timeout=1)
    assert result["success"] is False and CANARY not in str(result)
    assert runtime.controller.sensitive_login


async def test_success_page_password_reflection_keeps_observation_gate():
    runtime, auth, ctx, call, _ = fixture()
    original = runtime.page_session.return_value.send
    async def send(method, args=None, **kwargs):
        if method == "Runtime.callFunctionOn" and "text.includes(password)" in args.get("functionDeclaration", ""):
            return {"result": {"value": False}}
        return await original(method, args, **kwargs)
    runtime.page_session.return_value.send = send
    with patch("core.container.container.auth_service", return_value=auth), patch("services.browser_owners.set_sensitive", AsyncMock()):
        result = await fill_credentials(ctx, runtime, call, timeout=1)
    assert result["success"] is False and CANARY not in str(result)
    assert runtime.controller.sensitive_login


async def test_late_cleanup_cannot_release_replacement_task():
    controller = ProfileController("profile", "Work")
    controller.claim_task("replacement")
    assert await controller.release_task("old") is False
    assert controller.task_id == "replacement"


async def test_webmcp_gate_drains_and_rejects_page_metadata_until_restored():
    from nodes.browser._webmcp import WebMcpTracker
    tracker = WebMcpTracker()
    session = SimpleNamespace(on=lambda *_: None, send=AsyncMock())
    await tracker.attach("T", session)
    tracker._added("T", {"tools": [{"name": "safe", "frameId": "F"}]})
    assert tracker.count("T") == 1
    await tracker.sensitive_barrier(True)
    tracker._added("T", {"tools": [{"name": CANARY, "frameId": "F"}]})
    tracker._responded({"invocationId": "late", "output": CANARY})
    assert tracker.tools("T") == [] and tracker._early == {}
    session.send.assert_awaited_with("WebMCP.disable", timeout=5)
    await tracker.sensitive_barrier(False)
    session.send.assert_awaited_with("WebMCP.enable", timeout=5)
    assert CANARY not in str(tracker._tools)
