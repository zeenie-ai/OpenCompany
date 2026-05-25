"""On-disk JSONL watchers — protocol surface for interactive-mode claude.

Claude writes its session transcript to
``<CLAUDE_CONFIG_DIR>/projects/<project_key>/<session_uuid>.jsonl`` —
same shape ``-p`` used to write to stdout (Claude Code CHANGELOG
2.1.101 / 2.1.126 confirms the shared writer). In interactive mode we
keep the TUI alive in a PTY and read events off disk; this module
provides the two watchers ``AICliSession`` + ``ClaudeSessionPool`` need:

  - :class:`JsonlWatcher` tails one specific JSONL file and dispatches
    each new line as a parsed event. Drop-in replacement for the old
    ``_consume_stdout`` NDJSON parser.

  - :class:`JsonlDirWatcher` watches a directory for *new* JSONL files
    appearing — the mechanism the session pool uses to capture the
    new session UUID after sending ``/clear`` (which mints a fresh
    UUID, not an in-place clear; see issue `claude-code#32871
    <https://github.com/anthropics/claude-code/issues/32871>`_).

Both use a simple poll loop (default 100 ms / 250 ms) rather than
``watchdog``'s OS-native APIs (inotify/FSEvents/ReadDirectoryChangesW).
Polling is good enough for our latency target (UI updates were already
at the same cadence under the old stdout path), more predictable
under high-frequency append, and avoids the well-known watchdog races
on Windows under rapid file creation.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from core.logging import get_logger

logger = get_logger(__name__)


EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]
"""Async callback fired once per parsed JSONL line."""

FileHandler = Callable[[Path], Awaitable[None]]
"""Async callback fired once per newly-detected .jsonl file."""


class JsonlWatcher:
    """Tail a specific JSONL file and dispatch new lines as events.

    Replaces the old NDJSON-on-stdout consumer. Once :meth:`start` is
    called, every newline-terminated chunk appended to ``path`` is read,
    parsed with :func:`json.loads`, and handed to ``on_event``. Garbage
    (unparseable lines, partial writes during an append) is silently
    dropped — matches the old ``AnthropicClaudeProvider.parse_event``
    return-None-on-error semantics.

    The watcher is cancellation-safe: :meth:`stop` cancels the
    background task and closes the open file handle. Calling
    :meth:`stop` on an already-stopped watcher is a no-op.

    Initial position: by default we seek to the END of the file at
    :meth:`start` so we only see new appends. Pass ``start_from_end=False``
    to replay the entire file (useful for picking up the result event
    of a turn that completed while we were spawning).
    """

    __slots__ = (
        "_path",
        "_on_event",
        "_poll_interval",
        "_start_from_end",
        "_task",
        "_stopped",
    )

    def __init__(
        self,
        path: Path,
        on_event: EventHandler,
        *,
        poll_interval: float = 0.1,
        start_from_end: bool = False,
    ) -> None:
        self._path = Path(path)
        self._on_event = on_event
        self._poll_interval = max(0.01, float(poll_interval))
        self._start_from_end = bool(start_from_end)
        self._task: Optional[asyncio.Task[None]] = None
        self._stopped = asyncio.Event()

    @property
    def path(self) -> Path:
        return self._path

    async def start(self) -> None:
        """Spawn the tail-f task. Returns immediately."""
        if self._task is not None and not self._task.done():
            return  # idempotent
        self._stopped.clear()
        self._task = asyncio.create_task(
            self._run(),
            name=f"JsonlWatcher({self._path.name})",
        )

    async def stop(self) -> None:
        """Cancel the tail-f task and wait for it to settle."""
        self._stopped.set()
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover — defensive
                logger.debug(
                    "[JsonlWatcher] task exit error path=%s exc=%s",
                    self._path.name,
                    exc,
                )
        self._task = None

    async def _run(self) -> None:
        # Wait for the file to appear. Claude writes the JSONL on first
        # turn output; for first-spawn runs we may start watching
        # before it exists.
        while not self._path.exists():
            if self._stopped.is_set():
                return
            await asyncio.sleep(self._poll_interval)

        # Open in binary mode so partial UTF-8 sequences at the read
        # boundary don't blow up — we accumulate bytes and decode on
        # the newline split.
        try:
            handle = self._path.open("rb")
        except OSError as exc:
            logger.warning(
                "[JsonlWatcher] open failed path=%s exc=%s",
                self._path,
                exc,
            )
            return

        buf = b""
        try:
            if self._start_from_end:
                handle.seek(0, os.SEEK_END)

            while not self._stopped.is_set():
                chunk = handle.read()
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        await self._dispatch_line(raw)
                else:
                    # No new data — sleep and try again. Use a small
                    # interval so latency stays comparable to stdout
                    # streaming.
                    try:
                        await asyncio.wait_for(
                            self._stopped.wait(),
                            timeout=self._poll_interval,
                        )
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "[JsonlWatcher] read loop ended unexpectedly path=%s exc=%s",
                self._path.name,
                exc,
            )
        finally:
            # Flush any final no-newline bytes (claude generally writes
            # newline-terminated lines but be defensive).
            if buf.strip():
                await self._dispatch_line(buf)
            try:
                handle.close()
            except OSError:
                pass

    async def _dispatch_line(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            event: Dict[str, Any] = json.loads(text)
        except json.JSONDecodeError:
            return  # garbage / partial line — drop silently
        try:
            await self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — handler isolation
            logger.warning(
                "[JsonlWatcher] on_event handler raised path=%s exc=%s",
                self._path.name,
                exc,
            )


class JsonlDirWatcher:
    """Watch a directory for newly-appearing ``.jsonl`` files.

    Used by :class:`nodes.agent.claude_code_agent._pool.ClaudeSessionPool`
    to capture the new session UUID after sending ``/clear``: claude
    mints a fresh UUID and starts writing to
    ``<project_key>/<new_uuid>.jsonl``; the watcher fires the callback
    with that path, the pool reads the UUID off the filename, and
    points its memory bridge at the new file.

    Detection is "new file" only — we don't fire on appends to
    existing files (that's :class:`JsonlWatcher`'s job). Files present
    at :meth:`start` time are recorded as baseline and never fire the
    callback.
    """

    __slots__ = (
        "_dir",
        "_on_new_file",
        "_poll_interval",
        "_task",
        "_stopped",
        "_baseline",
    )

    def __init__(
        self,
        directory: Path,
        on_new_file: FileHandler,
        *,
        poll_interval: float = 0.25,
    ) -> None:
        self._dir = Path(directory)
        self._on_new_file = on_new_file
        self._poll_interval = max(0.05, float(poll_interval))
        self._task: Optional[asyncio.Task[None]] = None
        self._stopped = asyncio.Event()
        self._baseline: Set[str] = set()

    @property
    def directory(self) -> Path:
        return self._dir

    async def start(self) -> None:
        """Snapshot the current ``.jsonl`` files as the baseline, then
        start polling. Files in the baseline never fire the callback."""
        if self._task is not None and not self._task.done():
            return
        self._stopped.clear()
        # Refresh baseline at every start so a re-used watcher across
        # session-pool acquires sees a fresh snapshot.
        self._baseline = self._snapshot()
        self._task = asyncio.create_task(
            self._run(),
            name=f"JsonlDirWatcher({self._dir.name})",
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    def _snapshot(self) -> Set[str]:
        """Return the set of ``.jsonl`` filenames currently in the
        directory. Empty set if the directory doesn't exist yet."""
        try:
            return {entry.name for entry in self._dir.iterdir() if entry.is_file() and entry.suffix == ".jsonl"}
        except (FileNotFoundError, NotADirectoryError):
            return set()
        except OSError as exc:
            logger.debug(
                "[JsonlDirWatcher] snapshot failed dir=%s exc=%s",
                self._dir,
                exc,
            )
            return set()

    async def _run(self) -> None:
        # Wait for the directory to appear if it doesn't yet (first run
        # before claude has materialised the projects dir).
        while not self._dir.exists():
            if self._stopped.is_set():
                return
            await asyncio.sleep(self._poll_interval)
            # Note: if the directory appears mid-wait we re-snapshot at
            # next iteration; first new file detected only AFTER the
            # baseline is established, which matches the documented
            # "files at start time are baseline" semantic.

        while not self._stopped.is_set():
            current = self._snapshot()
            new = current - self._baseline
            if new:
                # Update baseline BEFORE firing the callback so a slow
                # handler doesn't cause the same file to fire twice on
                # the next tick. Order within the new set is undefined;
                # callers shouldn't depend on ordering across multiple
                # additions in one poll interval.
                self._baseline |= new
                for name in new:
                    path = self._dir / name
                    try:
                        await self._on_new_file(path)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # pragma: no cover
                        logger.warning(
                            "[JsonlDirWatcher] on_new_file handler raised " "dir=%s file=%s exc=%s",
                            self._dir.name,
                            name,
                            exc,
                        )

            try:
                await asyncio.wait_for(
                    self._stopped.wait(),
                    timeout=self._poll_interval,
                )
            except asyncio.TimeoutError:
                pass


def snapshot_jsonl_sizes(project_dir: Path) -> Dict[str, int]:
    """Return ``{name: size_bytes}`` for every ``.jsonl`` in ``project_dir``.

    Used by ``ClaudeSessionPool._wait_for_session_jsonl`` (and the
    equivalent in :class:`AICliSession`) to baseline the project dir
    before spawning ``claude``. Post-spawn the caller polls again and
    picks whichever file is new (absent from baseline) or grew
    (``current_size > baseline_size``) — both fresh spawns and
    ``--continue`` spawns produce a size-positive delta.

    Returns an empty dict if ``project_dir`` doesn't exist yet (first
    spawn under a new cwd). Silently skips entries the filesystem
    refuses to ``stat`` — they're typically transient and the post-spawn
    poll will catch them on the next tick once they settle.
    """
    sizes: Dict[str, int] = {}
    try:
        entries = list(project_dir.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return sizes
    except OSError:
        return sizes
    for entry in entries:
        if entry.suffix != ".jsonl":
            continue
        try:
            sizes[entry.name] = entry.stat().st_size
        except OSError:
            continue
    return sizes


def session_uuid_from_jsonl_path(path: Path) -> Optional[str]:
    """Extract the session UUID from a JSONL filename, or None if the
    name doesn't match the ``<uuid>.jsonl`` convention.

    Cheap helper so callers don't need to manually do ``path.stem`` and
    validate. Claude's filenames are RFC-4122 UUIDs; we don't validate
    the UUID format aggressively since claude controls the writer.
    """
    if path.suffix != ".jsonl":
        return None
    stem = path.stem
    return stem if stem else None
