"""Monty Executor — sandboxed Python via Pydantic's Monty interpreter.

A deny-by-default alternative to ``pythonExecutor``. Where ``pythonExecutor``
runs user code through CPython ``exec()`` with a restricted-builtins namespace
(a known-weak sandbox whose ``timeout`` is never actually enforced), this node
runs code inside `pydantic-monty <https://github.com/pydantic/monty>`_ — a
minimal Python subset implemented in Rust with ENFORCED wall-clock + memory
limits and zero host access unless explicitly granted.

Capabilities are dynamic: the caller (an LLM, when wired as the
``sandboxed_python`` tool) selects from a fixed, vetted menu via the
``capabilities`` param. Each selection wires a real Monty grant — an
``external_functions`` entry (``http_get``) and/or a ``MountDir`` over the
per-workflow workspace (``workspace_read`` / ``workspace_write``). The default
(empty list) is pure deny-by-default.

Verified against pydantic-monty==0.0.18 (the pinned version):
  - ``Monty(code, inputs=[...])`` parses on construct; raises ``MontySyntaxError``.
  - ``m.run(*, inputs, limits, external_functions, print_callback, mount, os)``
    is SYNC and returns the program's last-expression value directly.
    ``run_async`` has no ``mount`` param, so we use ``run`` via ``asyncio.to_thread``.
  - ``ResourceLimits`` is a TypedDict: ``max_duration_secs`` (float),
    ``max_memory`` (BYTES), ``max_allocations``, ``max_recursion_depth``, ``gc_interval``.
  - Resource breach / unsupported feature / user runtime error -> ``MontyRuntimeError``
    (a ``MontyError`` subclass). Host-function exceptions also surface as
    ``MontyRuntimeError`` carrying the original message.
  - stdout/print -> ``CollectString()`` whose ``.output`` is the captured str.
  - ``MountDir(virtual_path, host_path, *, mode='read-only'|'read-write'|'overlay')``.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, NodeUserError, Operation

from .._base import CodeExecutorBase, CodeExecutorOutput


# Fixed, vetted capability menu the LLM may request per call. Each maps to a
# real Monty grant in ``_build_capabilities``. Keep this in sync with the
# tool_description below and the monty-skill SKILL.md.
CAPABILITY_MENU: Tuple[str, ...] = ("http_get", "workspace_read", "workspace_write")

# Subset note surfaced to the LLM on import errors etc.
_SUPPORTED = (
    "def/closures/lambda, if/for/while, try/except, list/dict/set comprehensions, "
    "f-strings, async def/await, and `import math|json|re`"
)
_UNSUPPORTED = "class, yield/generators, with, match, `import random|collections|os`"


# ---------------------------------------------------------------------------
# Capability builders (host functions run in the PARENT process; each is a
# deliberate, bounded hole in the sandbox).
# ---------------------------------------------------------------------------


def _is_public_url(url: str) -> Tuple[bool, str]:
    """SSRF guard: allow only http/https to a publicly-routable host.

    Resolves every A/AAAA record and rejects loopback / private / link-local /
    reserved / multicast / unspecified addresses. (DNS is re-resolved by httpx,
    so this is a best-effort guard, not TOCTOU-proof — acceptable for v1.)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"only http/https allowed (got {parsed.scheme or 'no scheme'!r})"
    host = parsed.hostname
    if not host:
        return False, "missing host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed: {exc}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"blocked non-public address {ip}"
    return True, ""


def _make_http_get(timeout: float = 15.0) -> Callable[[str], str]:
    """Return a vetted ``http_get(url) -> str`` for the sandbox to call.

    Synchronous (Monty calls external functions inline on the worker thread).
    Raises ValueError on a blocked URL / non-2xx — the message surfaces to the
    sandboxed code as a normal exception, and to the node as a NodeUserError.
    """
    import httpx

    def http_get(url: str) -> str:
        ok, reason = _is_public_url(url)
        if not ok:
            raise ValueError(f"http_get blocked: {reason}")
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        return resp.text

    return http_get


def _build_capabilities(
    monty, ctx: NodeContext, caps: set
) -> Tuple[Optional[Dict[str, Callable]], Optional[Any]]:
    """Translate the selected ``capabilities`` into Monty grants.

    Returns ``(external_functions | None, mount | None)``.
    """
    unknown = caps - set(CAPABILITY_MENU)
    if unknown:
        raise NodeUserError(
            f"Unknown capability {sorted(unknown)}. Available: {list(CAPABILITY_MENU)}"
        )

    ext_funcs: Dict[str, Callable] = {}
    mount = None

    if "http_get" in caps:
        ext_funcs["http_get"] = _make_http_get()

    if caps & {"workspace_read", "workspace_write"}:
        if not ctx.workspace_dir:
            raise NodeUserError(
                "A workspace capability was requested but no workspace_dir is "
                "available for this run."
            )
        # read-write is a superset of read-only; the sandbox only ever sees
        # the virtual '/workspace' path (MountDir handles path isolation).
        mode = "read-write" if "workspace_write" in caps else "read-only"
        mount = monty.MountDir("/workspace", ctx.workspace_dir, mode=mode)

    return (ext_funcs or None), mount


class MontyExecutorParams(BaseModel):
    code: str = Field(..., min_length=1, json_schema_extra={"editor": "code"})
    # Wall-clock limit, ENFORCED by Monty (unlike pythonExecutor's timeout).
    timeout: int = Field(default=30, ge=1, le=600)
    # Memory limit in MB, converted to bytes for ResourceLimits.max_memory.
    max_memory_mb: int = Field(default=256, ge=16, le=2048)
    # LLM picks per call from CAPABILITY_MENU; empty = pure deny-by-default.
    capabilities: list[str] = Field(
        default_factory=list,
        json_schema_extra={"enum_values": list(CAPABILITY_MENU)},
    )

    model_config = ConfigDict(extra="allow")


class MontyExecutorNode(CodeExecutorBase):
    type = "montyExecutor"
    display_name = "Monty Executor"
    subtitle = "Sandboxed Python"
    description = (
        "Run AI-generated Python in a hard sandbox (Pydantic Monty). "
        "Deny-by-default with enforced time + memory limits; opt-in capabilities."
    )
    tool_name = "sandboxed_python"
    tool_description = (
        "Run untrusted/AI-generated Python in a secure sandbox (Pydantic Monty) "
        "with ENFORCED time + memory limits. Deny-by-default: NO filesystem, "
        "network, or env access unless you list it in `capabilities`. "
        f"Supported: {_SUPPORTED}. NOT supported: {_UNSUPPORTED}. "
        "Read inputs from the `input_data` dict (upstream node outputs); return "
        "a result as the LAST expression. Optional `capabilities`: "
        "['http_get','workspace_read','workspace_write'] — request only what the "
        "task needs (workspace files are mounted at /workspace). For full Python "
        "with more libraries, use `python_code` instead."
    )

    Params = MontyExecutorParams
    Output = CodeExecutorOutput

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: MontyExecutorParams) -> Any:
        """Execute user code inside Monty with enforced limits + opt-in grants.

        ``input_data`` exposes ``connected_outputs`` (upstream node results);
        the program's last expression is returned as ``output`` and any
        ``print()`` output is captured into ``console_output``.
        """
        import asyncio

        if not params.code.strip():
            raise NodeUserError("No code provided")

        # Lazy import so the server still boots + registers every other node if
        # the wheel is missing for this platform.
        try:
            import pydantic_monty as monty
        except ImportError as exc:
            raise NodeUserError(
                "Monty sandbox is unavailable (pydantic-monty is not installed, "
                "or no wheel exists for this platform). Install `pydantic-monty`, "
                "or use the python_code tool for non-sandboxed execution."
            ) from exc

        input_data = ctx.raw.get("connected_outputs") or {}
        collector = monty.CollectString()

        caps = set(params.capabilities)
        ext_funcs, mount = _build_capabilities(monty, ctx, caps)

        limits = monty.ResourceLimits(
            max_duration_secs=params.timeout,
            max_memory=params.max_memory_mb * 1024 * 1024,
        )

        try:
            # Construction parses the code; run executes it. Both can raise the
            # "unsupported feature" NotImplementedError (e.g. `class` surfaces a
            # MontyRuntimeError at CONSTRUCT time), so they share one handler.
            # run() is sync with the full kwarg set (incl. mount, which
            # run_async lacks) and is offloaded so the CPU-bound Rust execution
            # doesn't block the event loop.
            m = monty.Monty(params.code, inputs=["input_data"])
            result = await asyncio.to_thread(
                m.run,
                inputs={"input_data": input_data},
                limits=limits,
                external_functions=ext_funcs,
                print_callback=collector,
                mount=mount,
            )
        except monty.MontyError as exc:
            # All Monty failures (syntax / typing / runtime / resource breach /
            # unsupported feature / host-function exception) subclass MontyError.
            # Classify by message so the LLM gets an actionable hint.
            msg = str(exc)
            low = msg.lower()
            if "does not yet support" in low or "notimplementederror" in low:
                raise NodeUserError(
                    f"{msg}\n\nMonty supports a Python SUBSET ({_SUPPORTED}); "
                    f"NOT {_UNSUPPORTED}. Use the python_code tool for those."
                ) from exc
            if "time limit" in low or "timeouterror" in low:
                raise NodeUserError(
                    f"Monty time limit exceeded (timeout={params.timeout}s): {msg}"
                ) from exc
            if "memory limit" in low or "memoryerror" in low:
                raise NodeUserError(
                    f"Monty memory limit exceeded (max_memory_mb={params.max_memory_mb}): {msg}"
                ) from exc
            if isinstance(exc, monty.MontySyntaxError):
                raise NodeUserError(f"SyntaxError: {msg}") from exc
            # Plain user-code runtime error (incl. exceptions raised by a
            # capability host function, e.g. a blocked http_get URL).
            raise NodeUserError(msg) from exc

        out = result.output if isinstance(result, monty.MontyComplete) else result
        return {"output": out, "console_output": collector.output}
