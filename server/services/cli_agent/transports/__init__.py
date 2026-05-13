"""PTY transports for driving interactive CLI agents (claude) cross-platform.

Public surface:

  - ``PtyTransport`` / ``PtyHandle`` Protocols (in :mod:`.base`).
  - ``get_pty_transport()`` factory picks the right backend for the
    current platform — ``ptyprocess`` on POSIX, ``pywinpty>=3.0.3`` on
    Windows. Both run in-process behind the same Protocol so the rest
    of ``services/cli_agent/`` is platform-agnostic.

If ``pywinpty`` / ``ptyprocess`` stability ever becomes an issue, the
deferred upgrade path is an out-of-process Python pty-host subprocess
(VSCode's ``ptyHostMain.ts`` pattern, in Python) — the Protocol
boundary makes that swap cheap. See
``docs-internal/claude_code_interactive_mode.md`` for the topology
rationale.
"""

from __future__ import annotations

import sys

from .base import PtyHandle, PtyTransport

__all__ = [
    "PtyHandle",
    "PtyTransport",
    "get_pty_transport",
]


def get_pty_transport() -> PtyTransport:
    """Return the appropriate ``PtyTransport`` for the current platform.

    Lazy-imports the backend module so the heavy native dep (ptyprocess
    on POSIX, pywinpty on Windows) is only loaded when something
    actually needs to drive a PTY — keeps ``services.cli_agent``
    importable in unit-test environments where the native dep may be
    missing.
    """
    if sys.platform == "win32":
        from .windows import WindowsPtyTransport
        return WindowsPtyTransport()
    from .posix import PosixPtyTransport
    return PosixPtyTransport()
