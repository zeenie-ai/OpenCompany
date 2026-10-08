"""Mobile boundary contracts, using no emulator, credentials, or network."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from fastapi import HTTPException
from services.plugin import NodeContext, NodeUserError
from nodes.mobile import _node, _router, _video


def context():
    return NodeContext(
        node_id="mobile",
        node_type="mobile_use_agent",
        workflow_id="wf",
        execution_id="run",
        nodes=[{"id": "model", "type": "test_model", "data": {}}],
        edges=[{"source": "model", "target": "mobile", "targetHandle": "input-model"}],
    )


async def test_connector_uses_saved_model_and_only_selected_key(monkeypatch):
    import services.plugin.deps as deps
    import services.node_registry as registry
    import constants

    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={"model": "chosen-model", "api_key": "untrusted-inline"}))
    auth = SimpleNamespace(resolve_api_key=AsyncMock(return_value="resolved-secret"), get_api_key=AsyncMock(), get_stored_models=AsyncMock(return_value=[]))
    monkeypatch.setattr(deps, "get_database", lambda: database)
    monkeypatch.setattr(deps, "get_ai_service", lambda: SimpleNamespace(auth=auth))
    monkeypatch.setattr(registry, "get_node_class", lambda _: SimpleNamespace(component_kind="model"))
    monkeypatch.setattr(constants, "detect_ai_provider", lambda *_: "gemini")
    config = await _node.resolve_model(context())
    assert config == {"provider": "google", "model": "chosen-model", "model_env": {"GOOGLE_API_KEY": "resolved-secret", "GOOGLE_GENAI_USE_VERTEXAI": "false"}}
    auth.resolve_api_key.assert_awaited_once_with("gemini", "default", principal=None)
    auth.get_api_key.assert_not_awaited()


async def test_connector_requires_exactly_one_model():
    ctx = context()
    ctx.edges = [{"target": "mobile", "targetHandle": "input-model"}] * 2
    with pytest.raises(NodeUserError, match="exactly one"):
        await _node.resolve_model(ctx)


async def test_node_passes_execution_identity_and_broker(monkeypatch):
    from nodes.mobile import _runtime

    runtime = SimpleNamespace(
        run=AsyncMock(return_value={"outcome": "completed"}), ensure_broker=AsyncMock(return_value="http://127.0.0.1/private")
    )
    monkeypatch.setattr(_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(_node, "require_mobile_owner", AsyncMock())
    monkeypatch.setattr(_node, "resolve_model", AsyncMock(return_value={"provider": "openai"}))
    await _node.MobileUseAgent().execute_op(context(), _node.MobileParams(prompt="Open Settings"))
    args = runtime.run.await_args.kwargs
    assert (args["principal"], args["workflow_id"], args["node_id"]) == ("owner", "wf", "mobile")
    assert args["run_id"] == _node.task_identity(context())
    assert args["broker_url"] == "http://127.0.0.1/private"


@pytest.mark.parametrize(
    "operation,parameters",
    [
        ("tap", {"x": float("nan"), "y": 1}),
        ("swipe", {"x": 1, "y": 2}),
        ("key", {"key": "shell"}),
        ("text", {"text": "ok", "serial": "other-device"}),
    ],
)
def test_manual_input_rejects_malformed_commands(operation, parameters):
    body = _router.DeviceInput(
        viewer_id=uuid4(),
        operation_id=uuid4(),
        epoch=1,
        operation=operation,
        parameters=parameters,
        geometry={"width": 10, "height": 10, "rotation": 0},
    )
    with pytest.raises(HTTPException) as exc:
        _router.validate_input(body)
    assert exc.value.status_code == 422


async def test_input_passes_bound_lease_not_claimed_owner(monkeypatch):
    from nodes.mobile import _runtime

    runtime = SimpleNamespace(input=AsyncMock(return_value=True))
    monkeypatch.setattr(_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(_router, "viewer_identity", lambda principal, *_: "bound-" + principal)
    body = _router.DeviceInput(viewer_id=uuid4(), operation_id=uuid4(), epoch=7, operation="key", parameters={"key": "home"})
    await _router.device_input(body, SimpleNamespace(cookies={}), "user123")
    lease = runtime.input.await_args.args[0]
    assert lease.owner == "viewer:bound-user123" and lease.epoch == 7


async def test_cancel_targets_only_matching_submission(monkeypatch):
    from nodes.mobile import _runtime
    import services.node_invocations as invocations

    runtime = SimpleNamespace(cancel=AsyncMock())
    monkeypatch.setattr(_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(invocations, "status", AsyncMock(return_value={"status": "cancelled"}))
    submission = uuid4()
    await _router.cancel(submission, "wf", "node", "user")
    runtime.cancel.assert_awaited_once_with("wf", "node", run_id=invocations.invocation_id("user", "wf", "node", str(submission)))


def test_video_server_is_read_only_and_exact_version(monkeypatch):
    monkeypatch.setattr(_video, "sdk_tool", lambda _: "adb")
    args = _video.server_arguments("emulator-5560", "00000001")
    assert "control=false" in args and "audio=false" in args
    assert "4.1" in args and "video_codec=h264" in args and "max_fps=30" in args
    assert "send_frame_meta=true" in args and "send_device_meta=true" in args


async def test_shared_device_denies_nonowner_and_remote_installation(monkeypatch):
    from core.container import container

    settings = SimpleNamespace(vite_auth_enabled="true", deployment_mode="local")
    monkeypatch.setattr(container, "settings", lambda: settings)
    monkeypatch.setattr(
        container,
        "user_auth_service",
        lambda: SimpleNamespace(get_user_by_id=AsyncMock(return_value=SimpleNamespace(is_active=True, is_owner=False))),
    )
    with pytest.raises(NodeUserError, match="owner only"):
        await _node.require_mobile_owner("12")
    settings.deployment_mode = "cloud"
    with pytest.raises(NodeUserError, match="local installation"):
        await _node.require_mobile_owner("12")


async def test_http_authorization_rejects_foreign_origin_and_resolves_saved_node(monkeypatch):
    import sys
    from types import ModuleType
    from core.container import container
    import services.authz.workflow_node as resolver

    cookies = ModuleType("core.auth_cookies")
    cookies.get_session_token = lambda *_: "cookie-token"
    monkeypatch.setitem(sys.modules, "core.auth_cookies", cookies)
    settings = SimpleNamespace(vite_auth_enabled="true", cors_origins=[])
    auth = SimpleNamespace(get_current_user=AsyncMock(return_value=SimpleNamespace(id=14)))
    monkeypatch.setattr(container, "settings", lambda: settings)
    monkeypatch.setattr(container, "user_auth_service", lambda: auth)
    owner_check = AsyncMock()
    saved_node = AsyncMock()
    monkeypatch.setattr(_router, "require_mobile_owner", owner_check)
    monkeypatch.setattr(resolver, "resolve_workflow_node", saved_node)
    request = SimpleNamespace(headers={"origin": "https://foreign.example", "host": "localhost:8000"}, cookies={})
    with pytest.raises(HTTPException) as exc:
        await _router.authorize(request, "saved-workflow", "saved-node")
    assert exc.value.status_code == 403
    auth.get_current_user.assert_not_awaited()
    request.headers["origin"] = "http://localhost:8000"
    assert await _router.authorize(request, "saved-workflow", "saved-node") == "14"
    owner_check.assert_awaited_once_with("14")
    saved_node.assert_awaited_once_with("14", "saved-workflow", "saved-node", workspace_kind="mobile")


def test_agent_discovery_requires_execution_capability(monkeypatch):
    from services.workspace_capabilities import is_registered_agent
    import services.node_registry as registry

    monkeypatch.setattr(registry, "get_node_class", lambda _: SimpleNamespace(component_kind="agent"))
    assert not is_registered_agent("renderer_only_agent")
    monkeypatch.setattr(registry, "get_node_class", lambda _: _node.MobileUseAgent)
    assert is_registered_agent("mobile_use_agent")
    assert _node.MobileUseAgent.needs_canvas and _node.MobileUseAgent.requires_context
    assert {"input-context", "input-model"} <= {handle["name"] for handle in _node.MobileUseAgent.handles}


async def test_video_strips_only_forward_sentinel_and_cleans_tunnel(monkeypatch):
    import asyncio
    import hashlib
    from nodes.mobile import _runtime

    runtime = SimpleNamespace(serial="emulator-5560", video_viewer=None, viewer=None)
    monkeypatch.setattr(_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(_video, "sdk_tool", lambda _: "adb")

    class Server:
        def __truediv__(self, _):
            return self

        def is_file(self):
            return True

        def read_bytes(self):
            return b"pinned"

        def __str__(self):
            return "scrcpy-server"

    monkeypatch.setattr(_video, "mobile_root", Server)
    monkeypatch.setattr(_video, "SCRCPY_SHA256", hashlib.sha256(b"pinned").hexdigest())
    command = AsyncMock(side_effect=["", "12345", ""])
    monkeypatch.setattr(_video, "command", command)
    proc = SimpleNamespace(returncode=None, kill=lambda: None, wait=AsyncMock())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    # Raw payload deliberately includes a metadata-looking zero prefix; it
    # must not be trimmed or repacked by the backend.
    raw = b"\x00device-name-and-scrcpy-packets"
    reader = SimpleNamespace(readexactly=AsyncMock(return_value=b"\x00"), read=AsyncMock(side_effect=[raw, b""]))
    writer = SimpleNamespace(close=lambda: None, wait_closed=AsyncMock())
    monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))

    async def receive():
        await asyncio.Event().wait()

    websocket = SimpleNamespace(accept=AsyncMock(), send_json=AsyncMock(), send_bytes=AsyncMock(), receive=receive, close=AsyncMock())
    await _video.stream_video(websocket, "viewer1")
    reader.readexactly.assert_awaited_once_with(1)
    websocket.send_bytes.assert_awaited_once_with(raw)
    websocket.send_json.assert_awaited_once_with({"type": "video", "version": "4.1", "codec": "h264"})
    assert command.await_args_list[-1].args[0][-2:] == ["--remove", "tcp:12345"]
    assert runtime.video_viewer is None
    proc.wait.assert_awaited_once()


def test_task_identity_separates_nodes_and_tool_calls():
    ctx = context()
    first = _node.task_identity(ctx)
    ctx.node_id = "another-mobile"
    second = _node.task_identity(ctx)
    ctx.raw["tool_call_id"] = "second-call"
    assert len({first, second, _node.task_identity(ctx)}) == 3
    ctx.execution_id = "node-invoke-already-scoped"
    assert _node.task_identity(ctx) == ctx.execution_id
