"""JS executor runtime — supervises the Express sidecar on bun.

The JS/TS code-executor nodes dispatch to a persistent HTTP server
(source tree at ``server/nodejs/``, bundled by ``bun build`` into a
self-contained ``dist/index.js`` by ``company build``) that runs on the
bun runtime resolved through :mod:`core.js_runtime`. Historically only
``company serve`` spawned it as a CLI ServiceSpec, so JS/TS nodes were
dead in ``dev`` / ``start`` modes. It is now backend-owned like every
other optional daemon: spawned on demand from the plugin's own
:func:`ensure_started` (first JS/TS node execution), torn down by the
lifespan's ``shutdown_all_supervisors()``.

Same :class:`BaseProcessSupervisor` idiom as ``nodes/whatsapp/_runtime``
and ``services/temporal/_runtime`` — including the TCP probe-before-spawn
from the Temporal runtime, so an externally managed executor (or one
started by another backend process, e.g. a standalone Temporal worker)
is detected and left alone; it is used only with a shared token (below).

The sidecar runs whatever code it is sent, so every route but ``/health``
answers only requests carrying the token it was started with
(``NODEJS_EXECUTOR_TOKEN``). Each backend process makes its own random
token unless the operator sets one, which is how an external executor, or
several processes sharing one sidecar, authenticate. A token this process
made goes only to the sidecar it started: when something else holds the
port, :meth:`NodeJSExecutorRuntime.ensure_started` refuses instead of
sending it a request, and never kills it. A sidecar started with another
token answers 401, which the client reports (``_client.py``).

Config is plugin-owned: the ``NODEJS_EXECUTOR_*`` env vars are read
here directly (pushed into ``os.environ`` from ``.env`` by the CLI) —
core ``Settings`` carries no executor fields.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

from core.js_runtime import ENV_BUN_BIN, INSTALL_HINT, bun_binary
from services._supervisor import BaseProcessSupervisor

# Sub-second per-attempt probe — a stalled sidecar fails health fast.
_PROBE_TIMEOUT_SECONDS = 1.0

#: The variable the sidecar reads its token from (``server/nodejs/src/index.ts``).
ENV_TOKEN = "NODEJS_EXECUTOR_TOKEN"


async def _probe_tcp_port(port: int, host: str = "localhost") -> bool:
    """True iff a TCP connection to ``host:port`` succeeds within
    :data:`_PROBE_TIMEOUT_SECONDS`. Mirrors the Temporal runtime's
    loopback readiness check. ``localhost`` (not ``127.0.0.1``) so the
    probe resolves the same way the HTTP client and the sidecar's own
    bind do — the sidecar listens on whatever ``localhost`` resolves to
    for its runtime, which is the IPv6 loopback ``[::1]`` on Windows."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, OSError):
            pass
        return True
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return False


def executor_port() -> int:
    from core.env_defaults import env_int

    return env_int("NODEJS_EXECUTOR_PORT")


def _sidecar_dir() -> Path:
    # <server>/nodejs — resolved through core.approot so the layout rule
    # lives in one place.
    from core.approot import server_root

    return server_root() / "nodejs"


class NodeJSExecutorRuntime(BaseProcessSupervisor):
    name = "nodejs-executor"

    pipe_streams = True
    terminate_grace_seconds = 5.0
    graceful_shutdown = sys.platform == "win32"

    def __init__(self) -> None:
        super().__init__()
        # The operator's token when set, else one made for this process. The
        # client sends it on every request (``_nodejs.get_nodejs_client``).
        self.token = os.environ.get(ENV_TOKEN) or secrets.token_urlsafe(32)

    # ---- BaseProcessSupervisor overrides ---------------------------------

    async def _pre_spawn(self) -> None:
        if bun_binary() is None:
            raise RuntimeError(
                f"bun not found on PATH (or {ENV_BUN_BIN}) — the JS/TS executor sidecar "
                f"runs on bun; {INSTALL_HINT}."
            )
        if not (_sidecar_dir() / "dist" / "index.js").is_file():
            raise RuntimeError(
                "JS executor bundle missing (server/nodejs/dist/index.js). "
                "Run `company build` to produce it."
            )

    def binary_path(self) -> Path:
        return Path(bun_binary() or "bun")

    def argv(self) -> list[str]:
        return [str(self.binary_path()), str(_sidecar_dir() / "dist" / "index.js")]

    def cwd(self) -> Path:
        return _sidecar_dir()

    def env(self) -> dict[str, str]:
        return {**os.environ, "NODEJS_EXECUTOR_PORT": str(executor_port()), ENV_TOKEN: self.token}

    async def health_check(self) -> bool:
        if not self.is_running():
            return False
        return await _probe_tcp_port(executor_port())

    async def ensure_started(self) -> None:
        """Start the sidecar unless something already serves the port,
        then wait until it accepts connections.

        A process this backend did not start (a sidecar an earlier backend
        left running, one started by hand or by another backend process,
        or any other program) is left alone and used only when the operator
        set a shared ``NODEJS_EXECUTOR_TOKEN``. Otherwise this raises before
        any request goes there, because the token made for this process
        must reach only the sidecar it starts. Idempotent like ``start()``.
        """
        if await _probe_tcp_port(executor_port()):
            if self.is_running() or os.environ.get(ENV_TOKEN):
                return
            raise RuntimeError(
                f"port {executor_port()} (NODEJS_EXECUTOR_PORT) is held by a process "
                "this backend did not start, so no code is sent there. Stop that "
                "process (`company stop` stops a sidecar its checkout left running) "
                "or set another NODEJS_EXECUTOR_PORT. Backend processes that share one "
                f"executor need the same {ENV_TOKEN}."
            )
        await self.start()
        for _ in range(50):  # ~15 s ceiling; the bundled sidecar boots in <2 s
            if await _probe_tcp_port(executor_port()):
                return
            await asyncio.sleep(0.3)
        raise RuntimeError(
            f"JS executor did not become ready on port {executor_port()}"
        )

    def _extra_status(self) -> dict:
        base = super()._extra_status()
        return {**base, "port": executor_port()}


def get_nodejs_executor_runtime() -> NodeJSExecutorRuntime:
    """Return the JS executor runtime singleton."""
    return NodeJSExecutorRuntime.get_instance()
