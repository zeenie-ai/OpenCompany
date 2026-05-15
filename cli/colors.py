"""Rich console + per-service color rotation (Honcho-style).

Latency-analysis hook: ``emit`` routes through ``Console.log`` instead of
``Console.print``, which prepends a built-in timestamp column. The console
is configured with ``log_time_format=[%H:%M:%S.%f]`` for ms precision and
``log_path=False`` to drop the file:line caller info that rich adds by
default. ``console.print`` callers (banner / step headers) remain timestamp-
less so the launch banner stays readable.
"""

from __future__ import annotations

from itertools import cycle
from rich.console import Console


# Single shared console — single-writer aggregator avoids interleaving
# concurrent stream output (VS Code's pty host pattern). ``log_*`` settings
# only affect ``console.log`` calls; ``console.print`` is unaffected.
console = Console(log_time_format="[%H:%M:%S.%f]", log_path=False)


# Honcho's rotation, lifted: skip very-dark (black) and very-bright (white)
# so prefixes remain readable on both light and dark terminals.
_PALETTE = [
    "cyan", "green", "yellow", "blue", "magenta",
    "bright_cyan", "bright_green", "bright_yellow", "bright_blue", "bright_magenta",
]
_color_cycle = cycle(_PALETTE)


def next_color() -> str:
    return next(_color_cycle)


def emit(name: str, color: str, line: str, *, stream: str = "stdout") -> None:
    """Print one line tagged with the service name + color, timestamped.

    Renders as ``[14:23:45.123] cyan         | <message>``. The timestamp
    column is added by ``console.log``; prefix width stays uniform so
    side-by-side latency comparison across services lines up visually.
    """
    width = 12
    prefix = f"[{color}]{name:<{width}}[/{color}]"
    style = "" if stream == "stdout" else "[dim]"
    suffix = "" if stream == "stdout" else "[/dim]"
    console.log(f"{prefix}{style} | {line}{suffix}", highlight=False)
