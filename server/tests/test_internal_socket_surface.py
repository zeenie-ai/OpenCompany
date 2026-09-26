"""`/ws/internal` bypasses the cookie gate — it must admit only the worker,
and reach almost nothing.

It sets no user principal, yet it dispatched through the same registry as
the authenticated socket, so `save_workflow`, `delete_workflow` and all six
Memory handlers were reachable without credentials. It also accepted any
peer, and `execute_node` runs whatever node type the message names, so the
handshake now requires the worker token.

Every browser-facing socket also checks `Origin`: a browser lets any page
open a socket to `localhost`, so without the check any page open on the
machine (the agent's own browser included) could drive the app.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest


def test_worker_token_is_bound_to_the_secret():
    from services.authz import internal_socket_token

    secret = "a" * 48
    assert internal_socket_token(secret) == internal_socket_token(secret)
    assert internal_socket_token(secret) != internal_socket_token("b" * 48)
    assert secret not in internal_socket_token(secret)


def test_only_the_matching_token_is_an_internal_caller():
    from starlette.datastructures import Headers

    from services.authz import internal_socket_headers, is_internal_caller

    secret = "a" * 48
    assert is_internal_caller(Headers(headers=internal_socket_headers(secret)), secret)
    assert not is_internal_caller(Headers(headers={}), secret)
    assert not is_internal_caller(Headers(headers=internal_socket_headers("b" * 48)), secret)
    assert not is_internal_caller(Headers(headers={"X-OpenCompany-Internal-Token": "é"}), secret)


@pytest.mark.parametrize(
    ("origin", "host", "allowed", "expected"),
    [
        # Not a browser: the worker and scripts send no Origin.
        (None, "127.0.0.1:5678", [], True),
        # The SPA served by this backend, or through a proxy that keeps Host.
        ("http://localhost:5678", "localhost:5678", [], True),
        ("http://127.0.0.1:5679", "127.0.0.1:5679", [], True),
        ("https://app.example.com", "app.example.com:443", [], True),
        ("HTTP://LOCALHOST:5678", "localhost:5678", [], True),
        # Listed in CORS_ORIGINS.
        ("http://localhost:5173", "127.0.0.1:5679", ["http://localhost:5173/"], True),
        ("http://anything.example", "127.0.0.1:5678", ["*"], True),
        # A different page on the same machine.
        ("http://localhost:3000", "localhost:5678", ["http://localhost:5678"], False),
        ("http://evil.example", "localhost:5678", [], False),
        # Not an http(s) page at all.
        ("null", "localhost:5678", [], False),
        ("chrome-extension://abcdef", "localhost:5678", [], False),
        ("file://", "localhost:5678", [], False),
        # Foreign origin and no Host to compare against.
        ("http://evil.example", None, [], False),
    ],
)
def test_origin_rules(origin, host, allowed, expected):
    from services.authz import is_allowed_ws_origin

    assert is_allowed_ws_origin(origin, host, allowed) is expected


def _app_with_settings(monkeypatch, **settings):
    from fastapi import FastAPI

    import routers.websocket as ws

    # conftest stubs core.container with a MagicMock; give the endpoints real settings.
    fake_container = SimpleNamespace(settings=lambda: SimpleNamespace(**settings), user_auth_service=lambda: None)
    monkeypatch.setattr(ws, "container", fake_container)
    app = FastAPI()
    app.include_router(ws.router)
    return app


def test_handshake_without_the_token_is_refused(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from services.authz import internal_socket_headers

    secret = "s" * 48
    app = _app_with_settings(monkeypatch, secret_key=secret, cors_origins=[])
    with TestClient(app) as client:
        for headers in ({}, internal_socket_headers("t" * 48)):
            with pytest.raises(WebSocketDisconnect) as refused:
                with client.websocket_connect("/ws/internal", headers=headers):
                    pass
            assert refused.value.code == 4001

        with client.websocket_connect("/ws/internal", headers=internal_socket_headers(secret)) as sock:
            sock.send_json({"type": "ping", "request_id": "r1"})
            assert sock.receive_json()["request_id"] == "r1"


def test_internal_socket_refuses_a_page_even_with_the_token(monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from services.authz import internal_socket_headers

    secret = "s" * 48
    app = _app_with_settings(monkeypatch, secret_key=secret, cors_origins=[])
    headers = {**internal_socket_headers(secret), "origin": "http://evil.example"}
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws/internal", headers=headers):
                pass
        assert refused.value.code == 4003


def test_status_socket_refuses_a_foreign_page_with_login_off(monkeypatch):
    """Login off is the local default, so Origin is the only thing standing
    between a web page on the machine and the app's own socket."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = _app_with_settings(monkeypatch, secret_key="s" * 48, cors_origins=[], vite_auth_enabled="false")
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws/status", headers={"origin": "http://evil.example"}):
                pass
        assert refused.value.code == 4003


@pytest.mark.asyncio
async def test_status_handshake_resolves_the_principal():
    from services.authz import authenticate_ws

    class _Socket:
        def __init__(self, headers, cookies=None):
            self.headers = headers
            self.cookies = cookies or {}
            self.scope = {"path": "/ws/status"}
            self.closed = None

        async def close(self, code, reason=""):
            self.closed = code

    open_settings = SimpleNamespace(cors_origins=[], vite_auth_enabled="false", jwt_cookie_name="opencompany_token")
    same_origin = _Socket({"origin": "http://localhost:5678", "host": "localhost:5678"})
    assert await authenticate_ws(same_origin, settings=open_settings, user_auth_service=lambda: None) == "owner"
    assert same_origin.closed is None

    class _Auth:
        def verify_token(self, token):
            return {"sub": "user-7"} if token == "good" else None

    login_settings = SimpleNamespace(cors_origins=[], vite_auth_enabled="true", jwt_cookie_name="opencompany_token")
    logged_in = _Socket({"host": "localhost:5678"}, {"opencompany_token": "good"})
    assert await authenticate_ws(logged_in, settings=login_settings, user_auth_service=_Auth) == "user-7"

    no_cookie = _Socket({"host": "localhost:5678"})
    assert await authenticate_ws(no_cookie, settings=login_settings, user_auth_service=_Auth) is None
    assert no_cookie.closed == 4001

    bad_cookie = _Socket({"host": "localhost:5678"}, {"opencompany_token": "bad"})
    assert await authenticate_ws(bad_cookie, settings=login_settings, user_auth_service=_Auth) is None
    assert bad_cookie.closed == 4001


@pytest.mark.asyncio
async def test_the_activity_worker_sends_the_token(monkeypatch):
    from starlette.datastructures import Headers

    import services.temporal.activities as activities
    from services.authz import is_internal_caller

    secret = "s" * 48
    monkeypatch.setattr(activities, "Settings", lambda: SimpleNamespace(secret_key=secret))
    sent = {}

    class _Refused(Exception):
        pass

    class _Session:
        def ws_connect(self, url, **kwargs):
            sent.update(kwargs)
            raise _Refused

    worker = activities.NodeExecutionActivities.__new__(activities.NodeExecutionActivities)
    worker.session = _Session()
    worker.ws_url = "ws://example.invalid/ws/internal"
    worker.ws_headers = None
    with pytest.raises(_Refused):
        await worker._execute_via_websocket({"node_id": "n", "node_type": "console"})

    assert is_internal_caller(Headers(headers=sent["headers"]), secret)


@pytest.mark.asyncio
async def test_the_connection_pool_sends_the_token(monkeypatch):
    from starlette.datastructures import Headers

    import services.temporal.ws_client as ws_client
    from services.authz import is_internal_caller

    secret = "s" * 48
    monkeypatch.setattr(ws_client, "Settings", lambda: SimpleNamespace(secret_key=secret))
    sent = {}

    class _Refused(Exception):
        pass

    class _Session:
        closed = False

        def ws_connect(self, url, **kwargs):
            sent.update(kwargs)
            raise _Refused

    pool = ws_client.WSConnectionPool(url="ws://example.invalid/ws/internal")
    pool._session = _Session()
    with pytest.raises(_Refused):
        async with pool.connection():
            pass

    assert is_internal_caller(Headers(headers=sent["headers"]), secret)


def test_allowlist_is_exactly_what_the_worker_needs():
    from services.authz import INTERNAL_SOCKET_HANDLERS

    assert INTERNAL_SOCKET_HANDLERS == {
        "execute_node",
        "execute_ai_node",
        "ping",
    }, "widening this set grants unauthenticated access — justify it in review"


def test_every_other_registered_handler_is_refused():
    """Generated from the LIVE registry, so it cannot go stale.

    This is the check that would have caught the six Memory handlers.
    """
    import nodes  # noqa: F401 - populates the plugin handler registry
    from routers.websocket import MESSAGE_HANDLERS, _resolve_handler
    from services.authz import INTERNAL_SOCKET_HANDLERS, resolve_internal_handler
    from services.ws_handler_registry import get_ws_handlers

    every = set(MESSAGE_HANDLERS) | set(get_ws_handlers())
    assert len(every) > 50, "registry looks unpopulated; the guard would be vacuous"

    reachable = {
        name for name in every
        if resolve_internal_handler(name, _resolve_handler) is not None
    }
    assert reachable <= INTERNAL_SOCKET_HANDLERS

    # Spot-check the ones that actually matter.
    for dangerous in (
        "save_workflow",
        "delete_workflow",
        "get_workflow",
        "list_memory_items",
        "clear_memory_items",
        "remember_memory",
        "get_agent_context",
    ):
        if dangerous in every:
            assert resolve_internal_handler(dangerous, _resolve_handler) is None, (
                f"{dangerous} is reachable on the unauthenticated socket"
            )


def test_refusal_is_indistinguishable_from_unknown_type():
    """A distinct error would confirm to a prober that a handler exists."""
    from routers.websocket import _resolve_handler
    from services.authz import resolve_internal_handler

    assert resolve_internal_handler("save_workflow", _resolve_handler) is None
    assert resolve_internal_handler("no_such_handler_at_all", _resolve_handler) is None


def test_internal_loop_actually_uses_the_gate():
    """Guards against the router being refactored back to the raw resolver."""
    import routers.websocket as ws

    source = inspect.getsource(ws)
    internal = source[source.index("WebSocket Internal") - 4000 :]
    assert "resolve_internal_handler(msg_type, _resolve_handler)" in internal


def test_both_endpoints_run_their_admission_check_before_accepting():
    import routers.websocket as ws

    status = inspect.getsource(ws.websocket_status_endpoint)
    assert status.index("authenticate_ws(") < status.index("broadcaster.connect(")
    internal = inspect.getsource(ws.websocket_internal_endpoint)
    assert internal.index("admit_internal_ws(") < internal.index("websocket.accept()")
