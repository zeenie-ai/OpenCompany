"""Strip ANSI escape sequences from captured terminal output.

Subprocess output (vite / npm / git / etc.) embeds ANSI CSI colour and
cursor-control codes. OpenCompany renders terminal output as plain text — the
Terminal tab (``process_service`` broadcasts + log files) and node Output panels
(the shell node's ``stdout``) — so the raw codes surface as garbage like
``[36mvite v7.3.3[39m``. Strip them at the capture boundary so stored logs,
broadcasts, and node outputs are all clean.

Delegates to ``click.unstyle`` (``click==8.3.2`` in ``requirements.txt``), a
faithful strip: it removes ANSI CSI colour / cursor / erase codes while
preserving everything else — trailing newlines, tabs, spacing — so command
output isn't silently mangled. (``rich.text.Text.from_ansi`` was rejected: it
line-normalizes and drops trailing newlines.)
"""

from __future__ import annotations

import click


def strip_ansi(text: str) -> str:
    """Return ``text`` with all ANSI escape sequences removed.

    Empty / falsy input is returned unchanged. Pure-text input is byte-for-byte
    unaffected, so this is safe to call unconditionally on any captured stream
    line.
    """
    if not text:
        return text
    return click.unstyle(text)


__all__ = ["strip_ansi"]
