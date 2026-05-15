"""Generic bridge: run any ``BaseSupervisor`` singleton from a CLI.

The Manager supervisor in ``machina/supervisor.py`` schedules
``ServiceSpec`` entries by spawning a subprocess and supervising it.
``BaseSupervisor``-managed runtimes (pgserver, Temporal binary) don't
ship a CLI of their own — they're Python singletons started via
``await runtime.start()``.

This shim bridges the two: import a runtime factory by dotted path,
call it, ``await singleton.start()``, then block on a shutdown signal.
``Manager`` sends SIGTERM during graceful shutdown; we catch it,
``await singleton.stop()``, then exit cleanly.

Usage (inside ``ServiceSpec.argv``):

    [
        "python", "-m", "cli.commands._supervised_runtime",
        "services.temporal._runtime:get_postgres_runtime",
    ]

Equivalent to (Python idiom):

    from services.temporal._runtime import get_postgres_runtime
    runtime = get_postgres_runtime()
    await runtime.start()
    await asyncio.Event().wait()  # block on signal
"""
from __future__ import annotations

import asyncio
import importlib
import signal
import sys
from typing import Awaitable, Callable


def _resolve(dotted: str) -> Callable[[], Awaitable[None]]:
    """Resolve ``module.path:attr`` to a callable factory."""
    if ":" not in dotted:
        raise SystemExit(f"factory must be 'module.path:attr', got {dotted!r}")
    mod_path, attr = dotted.split(":", 1)
    mod = importlib.import_module(mod_path)
    factory = getattr(mod, attr, None)
    if factory is None:
        raise SystemExit(f"{mod_path}.{attr} not found")
    return factory


async def _run(factory_dotted: str) -> int:
    factory = _resolve(factory_dotted)
    runtime = factory()
    stop = asyncio.Event()

    def _on_signal() -> None:
        if not stop.is_set():
            stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows: add_signal_handler raises for SIGTERM. The
            # Manager supervisor's tree-kill semantics (psutil-based)
            # work regardless — we just lose the graceful path on Win.
            pass

    print(f"[supervised_runtime] starting {factory_dotted}", flush=True)
    try:
        await runtime.start()
        print(f"[supervised_runtime] {factory_dotted} ready", flush=True)
        await stop.wait()
    finally:
        print(f"[supervised_runtime] stopping {factory_dotted}", flush=True)
        await runtime.stop()
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "usage: python -m cli.commands._supervised_runtime "
            "<module.path:factory>",
            file=sys.stderr,
        )
        return 2
    try:
        return asyncio.run(_run(sys.argv[1]))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
