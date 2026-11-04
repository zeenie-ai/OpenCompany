"""The JS executor sidecar answers only the backend that started it.

The sidecar (``server/nodejs/src/index.ts``) runs whatever code it is sent,
so it refuses ``/execute`` and ``/packages`` without the token it was
started with (``NODEJS_EXECUTOR_TOKEN``). These tests lock the backend's
half: the runtime spawns the sidecar with its token and sends that token to
no listener it did not start, the client sends it on every request, and a
sidecar that refuses it reads as an unavailable executor. The last two read
the sidecar's source for what both halves must agree on.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
from unittest.mock import patch

from aiohttp import test_utils, web

import nodes.code._nodejs as nodejs
from nodes.code._client import TOKEN_HEADER, NodeJSClient
from nodes.code._runtime import ENV_TOKEN, NodeJSExecutorRuntime, _sidecar_dir


def _recording_app(seen: list, status: int = 200) -> web.Application:
    async def handler(request: web.Request) -> web.Response:
        seen.append((request.path, request.headers.get(TOKEN_HEADER)))
        return web.json_response({"success": status == 200, "packages": {}}, status=status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    return app


@contextlib.asynccontextmanager
async def _listening_where_the_runtime_probes(app: web.Application):
    """Serve ``app`` on one free port at every address ``localhost`` names.

    The runtime probes ``localhost``, which is ``::1`` first on Windows, and
    a refused connect there outlasts the probe, so a server on ``127.0.0.1``
    alone would look absent."""
    runner = web.AppRunner(app)
    await runner.setup()
    port = test_utils.unused_port()
    try:
        for host in dict.fromkeys(i[4][0] for i in socket.getaddrinfo("localhost", port, type=socket.SOCK_STREAM)):
            with contextlib.suppress(OSError):  # no IPv6 loopback, say
                await web.TCPSite(runner, host, port).start()
        yield port
    finally:
        await runner.cleanup()


async def test_the_client_sends_the_token_on_every_request():
    seen: list = []
    async with test_utils.TestServer(_recording_app(seen)) as server:
        client = NodeJSClient(str(server.make_url("/")), token="the-token")
        await client.health_check()
        await client.execute("output = 1")
        await client.install_packages(["lodash"])
        await client.list_packages()

    assert seen == [
        ("/health", "the-token"),
        ("/execute", "the-token"),
        ("/packages/install", "the-token"),
        ("/packages", "the-token"),
    ]


async def test_the_shared_client_sends_the_runtime_token(monkeypatch):
    seen: list = []
    monkeypatch.setattr(nodejs, "_client", None)
    async with test_utils.TestServer(_recording_app(seen)) as server:
        await nodejs.get_nodejs_client(str(server.make_url("/"))).execute("output = 1")

    assert seen == [("/execute", nodejs.get_nodejs_executor_runtime().token)]


def test_each_backend_process_spawns_the_sidecar_with_its_own_token(monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    first, second = NodeJSExecutorRuntime(), NodeJSExecutorRuntime()

    assert first.token and first.token != second.token
    assert first.env()[ENV_TOKEN] == first.token
    # Only the sidecar gets it, not every other child of the backend.
    assert ENV_TOKEN not in os.environ


def test_an_operator_token_is_used_as_set(monkeypatch):
    # An external executor, or several processes sharing one sidecar.
    monkeypatch.setenv(ENV_TOKEN, "shared-by-every-process")
    runtime = NodeJSExecutorRuntime()

    assert runtime.token == "shared-by-every-process"
    assert runtime.env()[ENV_TOKEN] == "shared-by-every-process"


async def test_a_port_held_by_a_process_this_backend_did_not_start_gets_nothing(harness, monkeypatch):
    # A sidecar an earlier backend left running, or any program on the port:
    # neither the token made for this process nor the node's code reach it.
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    monkeypatch.delenv("NODEJS_EXECUTOR_URL", raising=False)
    seen: list = []
    async with _listening_where_the_runtime_probes(_recording_app(seen)) as port:
        monkeypatch.setenv("NODEJS_EXECUTOR_PORT", str(port))
        runtime = NodeJSExecutorRuntime()
        monkeypatch.setattr(nodejs, "get_nodejs_executor_runtime", lambda: runtime)
        monkeypatch.setattr(nodejs, "_client", None)
        result = await harness.execute("javascriptExecutor", {"code": "output = 1"})

    harness.assert_envelope(result, success=False)
    assert "JavaScript executor is unavailable" in result["error"]
    assert "held by a process this backend did not start" in result["error"]
    assert seen == []
    assert not runtime.is_running()


async def test_with_a_shared_token_the_executor_on_the_port_is_used(monkeypatch):
    # Several backend processes, or a standalone worker, sharing one sidecar.
    monkeypatch.setenv(ENV_TOKEN, "shared-by-every-process")
    monkeypatch.delenv("NODEJS_EXECUTOR_URL", raising=False)
    seen: list = []
    async with _listening_where_the_runtime_probes(_recording_app(seen)) as port:
        monkeypatch.setenv("NODEJS_EXECUTOR_PORT", str(port))
        runtime = NodeJSExecutorRuntime()
        monkeypatch.setattr(nodejs, "get_nodejs_executor_runtime", lambda: runtime)
        monkeypatch.setattr(nodejs, "_client", None)
        client = await nodejs.acquire_client()
        await client.execute("output = 1")

    assert seen == [("/execute", "shared-by-every-process")]
    assert not runtime.is_running()


async def test_a_refused_token_reads_as_an_unavailable_executor(harness):
    async with test_utils.TestServer(_recording_app([], status=401)) as server:
        client = NodeJSClient(str(server.make_url("/")), token="not-its-token")
        with patch("nodes.code._nodejs.acquire_client", return_value=client):
            result = await harness.execute("javascriptExecutor", {"code": "output = 1"})

    harness.assert_envelope(result, success=False)
    assert "JavaScript executor is unavailable" in result["error"]
    assert "NODEJS_EXECUTOR_TOKEN" in result["error"]


def _sidecar_source() -> str:
    return (_sidecar_dir() / "src" / "index.ts").read_text(encoding="utf-8")


def test_the_sidecar_checks_the_token_before_any_route_but_health_or_any_body():
    source = _sidecar_source()

    assert f"process.env.{ENV_TOKEN}" in source
    assert f"const TOKEN_HEADER = '{TOKEN_HEADER}';" in source
    # In registration order: the open /health, the token gate, then the body
    # parser and every other route. A route above the gate would be open.
    registered = re.split(r"^(?=app\.\w+\()", source, flags=re.M)[1:]
    assert registered[0].startswith("app.get('/health'")
    assert registered[1].startswith("app.use(") and "hasToken(req)" in registered[1]
    assert registered[2].startswith("app.use(express.json(")


def test_the_sidecar_compares_the_token_bytes_the_client_sends():
    # aiohttp sends header values as UTF-8 and the sidecar's HTTP parser hands
    # them to JS as latin1, so only 'latin1' turns the header back into the
    # bytes sent. Without it a non-ASCII NODEJS_EXECUTOR_TOKEN never matches,
    # even on the sidecar this backend just started.
    has_token = re.search(r"^function hasToken\(.*?^\}", _sidecar_source(), re.S | re.M)

    assert has_token and "Buffer.from(req.get(TOKEN_HEADER) ?? '', 'latin1')" in has_token.group(0)
