"""Structural Protocols for PTY transports.

Two interfaces:

  - ``PtyTransport`` — factory for spawning. One per process; obtained
    from :func:`services.cli_agent.transports.get_pty_transport`.
  - ``PtyHandle`` — one live PTY-attached subprocess. Owns the PTY
    pair (master + slave), the spawned process, and the lifecycle
    primitives (write, kill, alive-check) the session pool needs.

The handles are intentionally minimal — they expose only what the
``ClaudeSessionPool`` and ``AICliSession`` need to drive an interactive
``claude`` while reading events from the on-disk session JSONL. We
never read PTY stdout in the steady state (the TUI's ANSI output is
discarded), so ``read``/``recv`` are deliberately absent — adding them
would invite the same TUI-scraping rabbit hole NousResearch's Hermes
Agent is in.

The Protocols are intentionally not declared ``@runtime_checkable``:
``isinstance(x, PtyTransport)`` would pass any object with the right
method names but is misleading for Protocols carrying behavioural
contracts (lifecycle, threading guarantees). Static type-checkers
still enforce structural conformance at the factory call site.
"""

from __future__ import annotations

import signal
from pathlib import Path
from typing import Dict, List, Protocol


class PtyHandle(Protocol):
    """One live PTY-attached subprocess.

    Implementations:
      - :class:`services.cli_agent.transports.posix.PosixPtyHandle` —
        wraps ``ptyprocess.PtyProcess``.
      - :class:`services.cli_agent.transports.windows.WindowsPtyHandle` —
        wraps ``pywinpty.PTY``.

    Thread-safety: not promised. The session pool serialises access
    under a per-key ``asyncio.Lock`` so concurrent writes / kills don't
    race. Backends should still treat ``write`` as a single point-in-time
    operation (no internal buffering across calls).
    """

    @property
    def pid(self) -> int:
        """OS pid of the spawned subprocess. Used by ``Job Object``
        enrollment and the operator log."""

    def is_alive(self) -> bool:
        """``True`` while the subprocess is running, ``False`` after it
        exits or is killed. Cheap to call (no syscall on the hot path
        when possible)."""

    async def write(self, data: bytes) -> None:
        """Write raw bytes to the PTY master.

        Used both for the user's prompt and for slash commands
        (``/clear``, ``/compact``). Callers append the right line
        terminator — typically ``b"\\r"`` for interactive mode (claude's
        Ink TUI listens for Enter, not stdin-close; see
        `claude-code#15553 <https://github.com/anthropics/claude-code/issues/15553>`_).
        """

    async def kill(self, signal_: int = signal.SIGTERM) -> None:
        """Terminate the subprocess.

        First-stage ``SIGTERM``; backends may escalate to ``SIGKILL``
        after a grace window if the process doesn't exit on its own.
        Windows ignores ``signal_`` and uses
        ``TerminateProcess``/``CTRL_BREAK_EVENT`` semantics depending
        on what the pywinpty handle supports.
        """


class PtyTransport(Protocol):
    """Factory for spawning interactive subprocesses inside a PTY.

    Implementations:
      - :class:`services.cli_agent.transports.posix.PosixPtyTransport`
        (uses ``ptyprocess``).
      - :class:`services.cli_agent.transports.windows.WindowsPtyTransport`
        (uses ``pywinpty>=3.0.3``).

    There is one transport per process; ``services.cli_agent.transports
    .get_pty_transport()`` picks the right one for the current platform.
    """

    async def spawn(
        self,
        argv: List[str],
        *,
        cwd: Path,
        env: Dict[str, str],
    ) -> PtyHandle:
        """Spawn ``argv`` inside a fresh PTY pair under ``cwd`` with
        the given ``env``.

        Returns a live :class:`PtyHandle`. Raises ``FileNotFoundError``
        if ``argv[0]`` isn't executable, ``OSError`` for PTY allocation
        failures, and ``RuntimeError`` for backend-specific errors
        (e.g. ConPTY not available on a too-old Windows build).

        The handle is registered with the process tree supervisor
        (:mod:`machina.tree` on Windows, ``os.setsid`` on POSIX) so the
        child is reaped on host shutdown — see the existing
        ``BaseProcessSupervisor`` patterns.
        """
