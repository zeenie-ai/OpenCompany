"""Generic CLI invocation helper.

``run_cli_command`` resolves the binary on PATH, injects the
plugin's API key via the convention flag (``--api-key`` by default),
runs the subprocess with a timeout, captures stdout/stderr, and parses
stdout as JSON when possible. Used by any plugin that wraps a CLI
tool (Stripe, GitHub, Vercel, Cloudflare, gcloud, ...).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Dict, List, Optional, Type

from services._supervisor.util import kill_tree
from services.process_environment import without_onepassword_environment


async def run_cli_command(
    *,
    binary: str,
    argv: List[str],
    credential: Optional[Type] = None,
    api_key_arg: str = "--api-key",
    timeout: float = 30.0,
    env: Optional[Dict[str, str]] = None,
    stdin: Optional[int] = None,
    cwd: Optional[str] = None,
    input: Optional[bytes] = None,
) -> Dict[str, Any]:
    """Run ``<binary> <argv> [api_key_arg <key>]`` once, return parsed JSON.

    ``env``: optional process environment override. When None, the child
    inherits ordinary parent variables. 1Password authentication variables
    are removed after any caller overrides, before spawning the child.

    ``cwd``: optional working directory for the child. Needed by CLIs
    whose commands are directory-scoped (``vercel deploy`` deploys the
    cwd). ``None`` inherits the parent's cwd.

    ``stdin``: optional override for the child's stdin handle. ``None``
    (default) inherits the parent's stdin — what most one-shot CLI
    invocations want. Pass :data:`asyncio.subprocess.PIPE` (left
    un-written) when the CLI reads stdin during the run and the parent
    has no usable stdin — without it, the CLI sees immediate EOF and
    can exit prematurely. Concrete case: ``claude auth login`` (native
    binary, 2.1.162+) reads stdin while waiting for the browser
    callback; under the FastAPI daemon (no TTY, closed stdin) the CLI
    EOFs, exits, and kills its localhost callback server — the
    browser's ``http://localhost:<port>/callback?code=…`` redirect
    then hits a dead socket and the success page never renders, even
    though credentials were written before exit. ``stdin=PIPE`` makes
    the read block forever, keeping the callback server alive until
    the CLI naturally exits after rendering its response.

    ``input``: optional bytes written to the child's stdin and then closed.
    Requires ``stdin=asyncio.subprocess.PIPE``. Used by CLIs that take their
    payload on stdin rather than argv (himalaya reads an RFC 2822 message
    that way, which also keeps the message body out of the process table).

    Returns a uniform envelope:
        {"success": bool, "result": parsed-JSON-or-None,
         "stdout": str, "stderr": str, "error": str or None}
    """
    api_key: Optional[str] = None
    if credential is not None:
        try:
            secrets = await credential.resolve()
        except PermissionError:
            return {"success": False, "error": f"{binary}: credential required"}
        api_key = secrets.get("api_key")
        if not api_key:
            return {"success": False, "error": f"{binary}: credential required"}

    # shutil.which stays the primary path: on Windows it honours PATHEXT and
    # turns a bare "npm" into the absolute "npm.cmd" that CreateProcessW needs.
    # But a caller that already resolved its binary (himalaya's ensure_binary,
    # which caches the which() result) hands us an absolute path, and
    # re-resolving it buys nothing while making the call fail whenever which()
    # is stricter than the eventual spawn. Trust an absolute path as-is and let
    # the spawn be the judge.
    resolved = shutil.which(binary)
    if resolved is None and os.path.isabs(binary):
        resolved = binary
    if resolved is None:
        return {"success": False, "error": f"{binary!r} not on PATH"}

    full_argv = [resolved, *argv]
    if api_key:
        full_argv.extend([api_key_arg, api_key])

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_argv,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=without_onepassword_environment(env),
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=input), timeout=timeout)
    except asyncio.TimeoutError:
        # wait_for cancels the communicate() task but does NOT touch the
        # child, so without this the process keeps running, orphaned, still
        # holding whatever handles it opened. Callers that hand it a temp
        # config then unlink that file in a finally block -- which on Windows
        # raises PermissionError against the live process and masks the
        # timeout entirely.
        kill_tree(proc.pid)
        try:
            await proc.wait()
        except Exception:  # pragma: no cover - reaping is best-effort
            pass
        return {"success": False, "error": f"{binary} timed out ({timeout}s)"}
    except OSError as exc:
        # MUST stay below the TimeoutError clause: since 3.11 asyncio.TimeoutError
        # is the builtin TimeoutError, which subclasses OSError -- catching OSError
        # first silently swallows every timeout and skips the kill above.
        # An absolute path is no longer pre-validated by which(), so a missing or
        # non-executable binary lands here; keep the envelope uniform rather than
        # raising through every caller.
        return {"success": False, "error": f"{binary}: {exc or type(exc).__name__}"}

    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode != 0:
        return {
            "success": False,
            "stdout": out,
            "stderr": err,
            "error": err or f"exit {proc.returncode}",
        }
    try:
        parsed = json.loads(out) if out else None
    except json.JSONDecodeError:
        parsed = None
    return {"success": True, "result": parsed, "stdout": out, "stderr": err, "error": None}
