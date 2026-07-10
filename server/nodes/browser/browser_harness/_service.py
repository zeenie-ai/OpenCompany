"""Thin wrapper around the browser-harness CLI (browser-use/browser-harness).

Stateless client — finds the binary via :mod:`._install`, pipes Python
code to the CLI's stdin (the upstream-documented driving model), and
returns the captured output. The harness's background daemon holds the
single CDP WebSocket to Chrome and persists across CLI invocations;
this service never manages the daemon directly beyond best-effort
shutdown.

Differences from the sibling ``agent-browser`` service worth knowing:

- The CLI **exits** after executing the piped code (the daemon is a
  separate detached process), so a plain ``subprocess.run`` with
  timeout works — no first-line-then-kill-tree dance.
- Interaction model is screenshot + coordinate clicks + ``js()``, not
  accessibility-tree ``@eN`` refs.
- Chrome must be reachable over CDP: either the user's Chrome grants
  remote debugging (chrome://inspect), a dedicated Chrome runs with
  ``--remote-debugging-port=9222``, or ``BU_CDP_URL``/``BU_CDP_WS``
  point at one. Failures surface as :class:`NodeUserError` with that
  guidance.

Runtime/state isolation: ``BH_RUNTIME_DIR`` (daemon sock/port/pid) and
``BH_TMP_DIR`` (screenshots, daemon log) are pinned under
``<DATA_DIR>/daemons/browser-harness/`` so the harness never writes to
the user-global ``~/.config/browser-harness`` runtime location.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any, Dict, Optional

from core.logging import get_logger
from core.paths import daemons_dir
from services.plugin.base import NodeUserError

from ._install import browser_harness_binary_path

logger = get_logger(__name__)

_MAX_OUTPUT = 100_000

# stderr fragments that indicate a user-correctable environment problem
# (vs a genuine bug). Matched case-insensitively.
_CHROME_HINTS = (
    "devtoolsactiveport",
    "unreachable",
    "is the dedicated automation chrome running",
    "chrome://inspect",
    "no browser connection",
    "connection refused",
)

_CHROME_GUIDANCE = (
    "browser-harness cannot reach Chrome over CDP. Either start a "
    "dedicated Chrome with --remote-debugging-port=9222, enable "
    "chrome://inspect/#remote-debugging in your running Chrome, or set "
    "BU_CDP_URL. Run the 'doctor' operation for a full diagnosis."
)


def _runtime_dir():
    d = daemons_dir() / "browser-harness"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _spawn_env() -> Dict[str, str]:
    """Env for harness spawns. The daemon inherits this on auto-start,
    so the runtime/tmp isolation must ride every invocation that could
    be the one that starts it. BU_CDP_URL / BU_CDP_WS pass through from
    the process env untouched (operator-controlled overrides)."""
    root = _runtime_dir()
    tmp = root / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    return {
        **os.environ,
        "BH_RUNTIME_DIR": str(root),
        "BH_TMP_DIR": str(tmp),
    }


class BrowserHarnessService:
    """Subprocess wrapper for the browser-harness CLI."""

    def __init__(self, binary: str) -> None:
        self._binary = binary

    async def run_code(self, code: str, timeout: int = 60) -> Dict[str, Any]:
        """Pipe Python ``code`` to the harness and return its output.

        Convention (taught by the skill): scripts print a JSON object as
        their final line for structured output. When the last stdout
        line parses as JSON it is returned under ``result``; the full
        stdout always rides along under ``output``.
        """
        if not code.strip():
            raise NodeUserError("code is required — Python to run against the browser-harness helpers")

        raw = await asyncio.to_thread(self._run_sync, [self._binary], code, timeout)
        return self._shape_output(raw)

    async def doctor(self) -> Dict[str, Any]:
        """Run ``browser-harness doctor``. Exit 1 = checks failed, which
        is a *report*, not an error — always return the output."""
        raw = await asyncio.to_thread(self._run_sync, [self._binary, "doctor"], None, 30, check=False)
        return {"output": raw}

    @staticmethod
    def _shape_output(raw: str) -> Dict[str, Any]:
        if len(raw) > _MAX_OUTPUT:
            raw = raw[:_MAX_OUTPUT] + "\n...(truncated)"
        lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
        if lines:
            try:
                return {"result": json.loads(lines[-1]), "output": raw.strip()}
            except json.JSONDecodeError:
                pass
        return {"output": raw.strip()}

    def _run_sync(
        self,
        argv: list,
        stdin_text: Optional[str],
        timeout: int,
        check: bool = True,
    ) -> str:
        """Run the CLI to completion (it exits after executing the piped
        code; the daemon detaches separately). shell=False with list
        argv per the BatBadBut-safe invocation rule."""
        try:
            proc = subprocess.run(
                argv,
                input=stdin_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
                env=_spawn_env(),
            )
        except subprocess.TimeoutExpired as e:
            raise NodeUserError(
                f"browser-harness timed out after {timeout}s — the page may be slow; "
                "raise the timeout parameter or simplify the script"
            ) from e
        except FileNotFoundError as e:
            raise NodeUserError("browser-harness binary vanished — it reinstalls automatically on next use") from e

        if check and proc.returncode != 0:
            err = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            low = err.lower()
            if any(h in low for h in _CHROME_HINTS):
                raise NodeUserError(f"{_CHROME_GUIDANCE}\n\nDetail: {err[-500:]}")
            # Python errors from the user's snippet are user-correctable too.
            if "traceback" in low or "error" in low:
                raise NodeUserError(f"browser-harness script failed:\n{err[-1500:]}")
            raise RuntimeError(f"browser-harness exited {proc.returncode}: {err[-1500:]}")

        out = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if not out and stderr:
            # doctor + some diagnostics write to stderr
            return stderr
        return out


# -- Module-level lazy singleton (BrowserService pattern) ----------------

_instance: Optional[BrowserHarnessService] = None


def get_browser_harness_service() -> Optional[BrowserHarnessService]:
    """Return the singleton, or None if the CLI cannot be installed."""
    global _instance
    if _instance is None:
        binary = browser_harness_binary_path()
        if binary:
            _instance = BrowserHarnessService(binary)
    return _instance


async def shutdown_browser_harness_service() -> None:
    """Best-effort daemon stop on FastAPI lifespan shutdown.

    The harness has no ``stop`` CLI verb; its daemon writes
    ``<BH_RUNTIME_DIR>/bu.pid`` (per-instance stem because we isolate
    BH_RUNTIME_DIR). Kill the tree if present; missing/stale pid files
    are silently ignored.
    """
    pid_file = _runtime_dir() / "bu.pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return
    try:
        from services._supervisor.util import kill_tree

        kill_tree(pid)
        logger.info("[browser-harness] daemon pid=%d stopped", pid)
    except Exception as e:  # noqa: BLE001 — shutdown must never raise
        logger.debug("[browser-harness] daemon stop skipped: %s", e)
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "BrowserHarnessService",
    "get_browser_harness_service",
    "shutdown_browser_harness_service",
]
