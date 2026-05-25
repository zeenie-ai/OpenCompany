"""Modern structured logging configuration with WebSocket broadcasting."""

import sys
import asyncio
import structlog
import logging
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, Optional
from queue import Queue
from threading import Thread
from core.config import Settings


# ---------------------------------------------------------------------------
# Source-tag resolution for the Terminal UI panel
# ---------------------------------------------------------------------------
#
# Every log record's ``record.name`` is a dotted-module path
# (``services.workflow.executor``, ``nodes.telegram._service``,
# ``routers.websocket``). The Terminal panel renders a single
# ``source`` column, so we collapse the dotted path to a concise tag
# (≤12 chars suits the supervisor's Honcho-style alignment, see
# ``cli/colors.py``).
#
# Three resolution stages, in order:
#
# 1. **Plugin auto-rule** — ``nodes.<plugin>.<anything>`` → ``<plugin>``.
#    Adding a new plugin folder gets the right tag for free; there is
#    no per-plugin entry in this module.
# 2. **Router auto-rule** — ``routers.<name>.<anything>`` → ``<name>``.
# 3. **Explicit registry** — :data:`_LOG_SOURCE_TAGS` plus runtime
#    additions via :func:`register_log_source_tag`. Only cross-cutting
#    services / core infra need entries here — typically because the
#    module name is too long (``workflow_validator`` → ``validator``)
#    or because several submodules share one logical area
#    (``services.user_auth`` + ``services.auth`` → ``auth``).
# 4. **Fallback** — second dotted segment (``services.ai`` → ``ai``).
#
# No node-specific entries belong here. Plugins that genuinely need a
# different label from their folder name should call
# :func:`register_log_source_tag` from their package ``__init__.py``,
# in the same self-registration style as the five plugin registries
# (ws_handler, filter_builder, trigger_precheck, service_refresh,
# output_schema).

_LOG_SOURCE_TAGS: Dict[str, str] = {
    # Cross-cutting service short tags. Longer prefixes must precede
    # shorter parents — Python ≥3.7 preserves dict-insertion order.
    "services.workflow_validator": "validator",
    "services.workflow_import": "wf_import",
    "services.parameter_resolver": "params",
    "services.node_executor": "executor",
    "services.status_broadcaster": "broadcaster",
    "services.ws_handler_registry": "ws_registry",
    "services.credential_registry": "credentials",
    "services.user_auth": "auth",
    "services.model_registry": "models",
    "services.node_output_schemas": "schemas",
    "services.markdown_formatter": "markdown",
    "services.example_loader": "examples",
    "services.skill_loader": "skills",
    "services.event_waiter": "waiter",
    # Core-infra short tags.
    "core.credentials_database": "credentials",
    "core.credential_backends": "credentials",
}


def register_log_source_tag(prefix: str, tag: str) -> None:
    """Register a short Terminal-UI tag for a logger-name prefix.

    Canonical extension point — matches the self-registration pattern
    used by the five plugin registries. Call from a plugin folder's
    ``__init__.py`` only when the auto-rule produces an unwanted tag
    (e.g. ``nodes.long_plugin_name`` → want ``lpn``). Most plugins do
    not need this.
    """
    _LOG_SOURCE_TAGS[prefix] = tag


def _resolve_source_tag(name: str) -> str:
    """Map ``record.name`` to a short Terminal-UI tag.

    Order:

    1. **Explicit registry** — :data:`_LOG_SOURCE_TAGS`, including any
       runtime additions made via :func:`register_log_source_tag`.
       Wins over the auto-rules so a plugin can override its
       folder-name default (the canonical opt-out path).
    2. **Plugin auto-rule** — ``nodes.<plugin>.<...>`` → ``<plugin>``.
    3. **Router auto-rule** — ``routers.<name>.<...>`` → ``<name>``.
    4. **Fallback** — second dotted segment (``services.ai`` → ``ai``).
    """
    # 1. Explicit registry — first matching prefix wins.
    for prefix, mapped in _LOG_SOURCE_TAGS.items():
        if name == prefix or name.startswith(prefix + "."):
            return mapped

    # 2. Plugin auto-rule.
    if name.startswith("nodes."):
        parts = name.split(".", 2)
        if len(parts) >= 2 and parts[1] and not parts[1].startswith("_"):
            return parts[1]

    # 3. Router auto-rule.
    if name.startswith("routers."):
        parts = name.split(".", 2)
        if len(parts) >= 2:
            return parts[1]

    # 4. Fallback — second dotted segment.
    parts = name.split(".", 2)
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return name


class WebSocketLogHandler(logging.Handler):
    """Logging handler that broadcasts logs to WebSocket clients.

    Uses a thread-safe queue to bridge sync logging with async WebSocket broadcasting.
    A background thread processes the queue and uses asyncio to broadcast.
    """

    _instance: Optional["WebSocketLogHandler"] = None

    def __init__(self, level: int = logging.INFO):
        super().__init__(level)
        self._queue: Queue = Queue(maxsize=1000)  # Bounded queue to prevent memory issues
        self._running = False
        self._thread: Optional[Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    def get_instance(cls) -> Optional["WebSocketLogHandler"]:
        """Get the singleton instance."""
        return cls._instance

    def emit(self, record: logging.LogRecord) -> None:
        """Queue log record for async broadcasting."""
        if not self._running:
            return

        try:
            # Get the raw message without structlog formatting
            message = record.getMessage()

            # Map source name via the module-level resolver (auto-rule
            # for nodes.<plugin> / routers.<name>, explicit registry for
            # cross-cutting services, second-segment fallback).
            source = _resolve_source_tag(record.name)

            # Extract structured key-value pairs from structlog
            details = None
            if hasattr(record, "_logger") or hasattr(record, "positional_args"):
                # Try to get extra kwargs from structlog
                extra_keys = set(record.__dict__.keys()) - {
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "exc_info",
                    "exc_text",
                    "thread",
                    "threadName",
                    "message",
                    "asctime",
                    "positional_args",
                    "_logger",
                }
                if extra_keys:
                    details = {k: record.__dict__[k] for k in extra_keys if not k.startswith("_")}

            # Create log entry
            log_data = {
                "timestamp": datetime.now().isoformat(),
                "level": record.levelname.lower(),
                "message": message,
                "source": source,
            }

            # Add details if present
            if details:
                log_data["details"] = details

            # Non-blocking put - drop if queue is full
            try:
                self._queue.put_nowait(log_data)
            except Exception:
                pass  # Drop log if queue is full

        except Exception:
            pass  # Never fail in log handler

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the background thread for processing logs."""
        if self._running:
            return

        self._loop = loop
        self._running = True
        self._thread = Thread(target=self._process_queue, daemon=True)
        self._thread.start()
        WebSocketLogHandler._instance = self

    def stop(self) -> None:
        """Stop the background thread."""
        self._running = False
        WebSocketLogHandler._instance = None
        if self._thread:
            self._thread.join(timeout=1.0)

    def _process_queue(self) -> None:
        """Background thread that processes log queue and broadcasts."""
        while self._running:
            try:
                # Block for up to 0.1 seconds waiting for logs
                try:
                    log_data = self._queue.get(timeout=0.1)
                except Exception:
                    continue

                # Schedule async broadcast on the event loop
                if self._loop and self._running:
                    asyncio.run_coroutine_threadsafe(self._broadcast(log_data), self._loop)

            except Exception:
                pass  # Never fail in background thread

    async def _broadcast(self, log_data: Dict[str, Any]) -> None:
        """Broadcast log to WebSocket clients."""
        try:
            from services.status_broadcaster import get_status_broadcaster

            broadcaster = get_status_broadcaster()
            await broadcaster.broadcast_terminal_log(log_data)
        except Exception:
            pass  # Don't fail if broadcaster not ready


def configure_logging(settings: Settings) -> None:
    """Configure structured logging based on settings.

    Console-mode output is deliberately timestamp-less — the supervisor
    (``cli/colors.py``) prepends ``[HH:MM:SS.fff]`` to every line
    it aggregates, so an inner ``TimeStamper`` would produce double-time
    output. JSON mode keeps the ISO timestamp because machine
    consumers (log shippers, query engines) parse it as a field.

    When ``log_file`` is set, a :class:`RotatingFileHandler` is used so
    long-running deployments don't fill the disk. Rotation thresholds
    (``log_file_max_bytes`` / ``log_file_backup_count``) come from
    :class:`Settings`.
    """
    log_level_value = getattr(logging, settings.log_level.upper())

    # Set up log file if specified
    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Rotating file handler — settings own the rotation thresholds.
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(log_level_value)

        # Configure console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level_value)

        # Configure root logger
        logging.basicConfig(level=log_level_value, handlers=[console_handler, file_handler], format="%(message)s")
    else:
        # Console only
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=log_level_value,
        )

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Configure structlog. ``merge_contextvars`` pulls fields bound via
    # ``structlog.contextvars.bind_contextvars`` (or the :func:`log_context`
    # helpers) into every log record automatically — survives
    # ``asyncio.gather`` child tasks via stdlib ``contextvars``.
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Pick the renderer. Console mode is timestamp-less by design
    # (see docstring).
    if settings.log_format == "json":
        processors.insert(0, structlog.processors.TimeStamper(fmt="iso"))
        processors.insert(0, structlog.stdlib.add_logger_name)
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(
            structlog.dev.ConsoleRenderer(
                colors=False,  # No ANSI colors for cleaner output
                pad_event=35,
                exception_formatter=structlog.dev.plain_traceback,
            )
        )

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Context-bound logging helpers
# ---------------------------------------------------------------------------
#
# Rather than threading ``workflow_id`` / ``node_id`` / ``execution_id``
# through every ``logger.info(..., workflow_id=...)`` callsite, callers
# bind once at the entry point and every log record in that async
# context automatically picks the fields up via
# ``structlog.contextvars.merge_contextvars`` (wired in
# :func:`configure_logging`). Stdlib ``contextvars`` propagates across
# ``await`` and ``asyncio.gather`` child tasks, so per-node executions
# spawned in parallel each inherit the right context.
#
# Two helpers — pick the one that fits the call site:
#
# - :func:`log_context` (async context manager) — for ``async with``
#   blocks around async work (e.g. inside ``BaseNode.execute``).
# - :func:`log_context_sync` (sync context manager) — for sync blocks
#   inside sync entry points; rare since most of the codebase is async.


@asynccontextmanager
async def log_context(**fields: Any) -> AsyncIterator[None]:
    """Bind ``fields`` to every log record emitted in this async context.

    Usage::

        async with log_context(workflow_id=wf_id, node_id=node_id):
            await do_work()  # all logs inside carry workflow_id + node_id

    Bindings clear on ``__aexit__`` even if the inner block raises.
    """
    structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*fields.keys())


@contextmanager
def log_context_sync(**fields: Any) -> Iterator[None]:
    """Synchronous variant of :func:`log_context` for sync callers."""
    structlog.contextvars.bind_contextvars(**fields)
    try:
        yield
    finally:
        structlog.contextvars.unbind_contextvars(*fields.keys())


def log_execution_time(logger: structlog.BoundLogger, operation: str, start_time: float, end_time: float, **kwargs) -> None:
    """Log execution time with additional context."""
    execution_time = end_time - start_time
    logger.info("Operation completed", operation=operation, execution_time_seconds=round(execution_time, 4), **kwargs)


def log_api_call(logger: structlog.BoundLogger, provider: str, model: str, operation: str, success: bool, **kwargs) -> None:
    """Log API calls with standardized format."""
    logger.info("API call completed", provider=provider, model=model, operation=operation, success=success, **kwargs)


def log_cache_operation(logger: structlog.BoundLogger, operation: str, key: str, hit: bool = None, **kwargs) -> None:
    """Log cache operations."""
    log_data = {"operation": operation, "cache_key": key, **kwargs}

    if hit is not None:
        log_data["cache_hit"] = hit

    logger.debug("Cache operation", **log_data)


# Global WebSocket log handler instance
_ws_log_handler: Optional[WebSocketLogHandler] = None


def setup_websocket_logging(loop: asyncio.AbstractEventLoop, level: int = logging.INFO) -> WebSocketLogHandler:
    """Setup and start the WebSocket log handler.

    Should be called during application startup after the event loop is running.

    Args:
        loop: The asyncio event loop to use for broadcasting
        level: Minimum log level to broadcast (default: INFO)

    Returns:
        The WebSocket log handler instance
    """
    global _ws_log_handler

    if _ws_log_handler is not None:
        return _ws_log_handler

    # Create handler
    _ws_log_handler = WebSocketLogHandler(level=level)

    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(_ws_log_handler)

    # Start the background processing thread
    _ws_log_handler.start(loop)

    return _ws_log_handler


def shutdown_websocket_logging() -> None:
    """Shutdown the WebSocket log handler.

    Should be called during application shutdown.
    """
    global _ws_log_handler

    if _ws_log_handler is None:
        return

    # Stop the handler
    _ws_log_handler.stop()

    # Remove from root logger
    root_logger = logging.getLogger()
    root_logger.removeHandler(_ws_log_handler)

    _ws_log_handler = None


def get_websocket_log_handler() -> Optional[WebSocketLogHandler]:
    """Get the current WebSocket log handler instance."""
    return _ws_log_handler
