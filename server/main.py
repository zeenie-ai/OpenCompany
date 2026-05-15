"""
Modern FastAPI backend for React Flow workflow automation platform.

Refactored with dependency injection, modular services, and clean architecture.
"""


def _startup_log(msg):
    """Pre-logger boot-progress marker.

    Emits via ``print()`` so the global CLI wrapper's
    ``[HH:MM:SS.ffffff]`` prefix supplies the timestamp — we don't add
    a second one here. Used during the import phase (before
    ``configure_logging`` runs) and inside the lifespan startup hook
    for milestones; after that, regular ``logger.info`` is preferred.
    """
    print(f"  {msg}", flush=True)


# Performance: Install uvloop if available (Linux/macOS only)
try:
    import uvloop
    uvloop.install()
except ImportError:
    pass  # Windows - uvloop not available, use default asyncio

import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

# Note: We don't register custom signal handlers.
# uvicorn already handles SIGINT (Ctrl+C) and SIGTERM (docker stop) gracefully.
# Adding custom handlers that raise KeyboardInterrupt causes cascading errors
# during async operations (WebSocket handlers, logging, etc.).

_startup_log("Importing FastAPI...")
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_startup_log("Importing DI container + all services...")
from core.container import container
_startup_log("Importing settings + logging...")
from core.config import Settings
from core.logging import configure_logging, get_logger, setup_websocket_logging, shutdown_websocket_logging
from core.tracing import init_tracing
_startup_log("Importing routers...")
from routers import workflow, database, nodejs_compat, websocket, webhook, auth, credentials, schemas
_startup_log("All imports complete")

# Initialize settings, logging, and tracing.
# init_tracing() registers the OpenTelemetry TracerProvider with a
# ConsoleSpanExporter so every span emitted via
# ``trace.get_tracer(__name__).start_as_current_span(...)`` shows up in
# stdout for cold-start benchmarking.
settings = Settings()
configure_logging(settings)
init_tracing()
logger = get_logger(__name__)

# Suppress noisy loggers
import logging
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logging.getLogger("watchfiles").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    # Startup
    _startup_log("Lifespan startup begin")

    # Wave 10.C: discover node plugins so their register_node() calls
    # populate the four registries before any router serves NodeSpec.
    # Side-effect import; the package __init__ walks its submodules.
    import nodes  # noqa: F401

    # Side-effect import: services/cli_agent/__init__.py self-registers
    # `cli_login` / `cli_auth_status` into `services.ws_handler_registry`.
    import services.cli_agent  # noqa: F401

    # Wire dependency injection
    container.wire(modules=[
        "routers.workflow",
        "routers.database",
        "routers.nodejs_compat",
        "routers.websocket",
        "routers.webhook",
        "routers.auth",
        "middleware.auth"
    ])

    # Start services
    await container.database().startup()
    await container.cache().startup()
    _startup_log("Database + cache started")

    # Initialize credentials database (creates tables if not exist)
    credentials_db = container.credentials_database()
    salt = await credentials_db.initialize()
    logger.info("Credentials database initialized")

    # Initialize encryption with server-scoped key (n8n pattern)
    # Key from .env persists across restarts, not tied to user sessions
    encryption = container.encryption_service()
    if not encryption.is_initialized():
        encryption.initialize(settings.api_key_encryption_key, salt)
        logger.info("Encryption service initialized")
    _startup_log("Credentials + encryption initialized")

    # Initialize event waiter with cache service for Redis Streams support
    from services import event_waiter
    event_waiter.set_cache_service(container.cache())

    # Start APScheduler for cron jobs
    from services.scheduler import start_scheduler, shutdown_scheduler
    start_scheduler()

    # Initialize execution engine recovery sweeper
    from services.execution import (
        ExecutionCache,
        RecoverySweeper,
        set_recovery_sweeper,
    )
    execution_cache = ExecutionCache(container.cache())
    recovery_sweeper = RecoverySweeper(execution_cache)
    set_recovery_sweeper(recovery_sweeper)

    # Scan for incomplete executions on startup
    if settings.redis_enabled:
        incomplete = await recovery_sweeper.scan_on_startup()
        if incomplete:
            logger.info("Found incomplete executions on startup",
                       count=len(incomplete),
                       execution_ids=incomplete)

        # Start background recovery sweeper
        await recovery_sweeper.start()
        logger.info("Execution recovery sweeper started")

    # Start WebSocket logging handler to broadcast logs to frontend
    loop = asyncio.get_running_loop()
    setup_websocket_logging(loop)
    logger.info("WebSocket logging handler started")

    # Start cleanup service for long-running daemon
    from core.cleanup import CleanupService
    from core.health import set_startup_time
    cleanup_service = None
    if settings.cleanup_enabled:
        cleanup_service = CleanupService(
            database=container.database(),
            cache=container.cache(),
            settings=settings
        )
        await cleanup_service.start()

    # Initialize compaction service and wire AI service
    compaction_svc = container.compaction_service()  # Trigger singleton initialization
    compaction_svc.set_ai_service(container.ai_service())
    logger.info("Compaction service initialized")
    _startup_log("Compaction service ready")

    # Initialize model registry service
    from services.model_registry import get_model_registry
    model_registry = get_model_registry()
    model_registry.startup()
    logger.info("Model registry initialized")

    # Background refresh if cache is stale
    if model_registry.is_stale():
        async def _refresh_registry():
            try:
                count = await model_registry.refresh()
                logger.info(f"Model registry refreshed: {count} models")
            except Exception as e:
                logger.warning(f"Model registry refresh failed (offline?): {e}")
        asyncio.create_task(_refresh_registry())

    # Initialize agent team service
    from services.agent_team import init_agent_team_service
    from services.status_broadcaster import get_status_broadcaster
    init_agent_team_service(container.database(), get_status_broadcaster())

    # Wire process service to broadcaster for Terminal tab streaming
    from services.process_service import get_process_service
    proc_svc = get_process_service()
    proc_svc.set_broadcaster(get_status_broadcaster())
    # Load max_processes from user settings if configured
    try:
        user_settings = await container.database().get_user_settings("default")
        if user_settings and "max_processes" in user_settings:
            proc_svc.max_processes = int(user_settings["max_processes"])
    except Exception:
        pass
    logger.info("Agent team service initialized")

    # Initialize proxy service (loads providers from DB, reads credentials)
    from services.proxy.service import init_proxy_service
    proxy_svc = init_proxy_service(
        auth_service=container.auth_service(),
        database=container.database(),
        settings=settings,
    )
    await proxy_svc.startup()
    _startup_log("Proxy service initialized")

    # Record startup time for health reporting
    set_startup_time()

    # Initialize Temporal in the background - do NOT block lifespan startup.
    # scripts/start.js launches the Python backend and temporal-server concurrently,
    # so on fresh startup temporal-server may take several seconds to become reachable.
    # Blocking the lifespan here would delay FastAPI HTTP serving and cascade into
    # frontend ERR_CONNECTION_REFUSED on /api/auth/status. Instead, yield fast and
    # let Temporal init happen in a background task. WorkflowService falls back to
    # parallel/sequential execution until Temporal is ready.
    app.state.temporal_worker_manager = None
    temporal_init_task: asyncio.Task | None = None

    if settings.temporal_enabled:
        from services.temporal import TemporalExecutor
        from services.temporal.worker import TemporalWorkerManager

        logger.info(
            "Scheduling Temporal initialization in background",
            server_address=settings.temporal_server_address,
            namespace=settings.temporal_namespace,
            task_queue=settings.temporal_task_queue,
        )
        _startup_log(f"[Temporal] Init scheduled for {settings.temporal_server_address}")

        async def _init_temporal_background() -> None:
            """Connect, wire executor, start worker. Retries every 3s until connected.

            This task is the single source of truth for Temporal lifecycle during
            startup. It replaces the previous blocking-connect + background-reconnect
            split, which blocked the lifespan for up to 30s and left the executor
            unwired after an initial failed connect.
            """
            temporal_client_wrapper = container.temporal_client()
            attempt = 0
            while True:
                attempt += 1
                client = await temporal_client_wrapper.connect(retries=1, delay=0)
                if client is None:
                    # Surface every failed attempt to stdout so users can see
                    # the retry loop is still alive when "Temporal is up" but
                    # the Python client can't connect (server-up != client-up).
                    _startup_log(
                        f"[Temporal] Connect attempt {attempt} failed for "
                        f"{settings.temporal_server_address} (ns={settings.temporal_namespace}); "
                        "retrying in 3s"
                    )
                else:
                    try:
                        temporal_executor = TemporalExecutor(
                            client=client,
                            task_queue=settings.temporal_task_queue,
                        )
                        container.workflow_service().set_temporal_executor(temporal_executor)

                        worker_manager = TemporalWorkerManager(
                            client=client,
                            task_queue=settings.temporal_task_queue,
                        )
                        await worker_manager.start()
                        app.state.temporal_worker_manager = worker_manager

                        _startup_log(
                            f"[Temporal] Worker started, execution engine ready (attempt {attempt})"
                        )
                        logger.info(
                            "Temporal integration initialized successfully",
                            attempts=attempt,
                        )
                        return
                    except Exception as exc:
                        _startup_log(
                            f"[Temporal] Executor/worker setup failed (attempt {attempt}): {exc}; will retry"
                        )
                        logger.error(
                            "Temporal executor/worker setup failed; will retry",
                            error=str(exc),
                        )
                        # Drop the client so the next iteration reconnects cleanly.
                        await temporal_client_wrapper.disconnect()
                await asyncio.sleep(3.0)

        temporal_init_task = asyncio.create_task(
            _init_temporal_background(), name="temporal-init"
        )
    else:
        _startup_log("[Temporal] Disabled")

    # Enter the CLI-agent MCP server's lifespan so its
    # StreamableHTTPSessionManager task group is initialised (Starlette
    # does NOT auto-propagate lifespans across `app.mount`). Stored on
    # `app.state` so shutdown can exit the context cleanly.
    cli_mcp_lifespan_ctx = None
    try:
        from services.cli_agent.mcp_server import get_mcp_app as _get_cli_mcp_app
        _cli_mcp_app = _get_cli_mcp_app()
        cli_mcp_lifespan_ctx = _cli_mcp_app.router.lifespan_context(_cli_mcp_app)
        await cli_mcp_lifespan_ctx.__aenter__()
        app.state.cli_mcp_lifespan_ctx = cli_mcp_lifespan_ctx
        _startup_log("[CLI MCP] StreamableHTTP session manager initialised")
    except Exception as exc:
        logger.warning("[CLI MCP] lifespan init failed: %s", exc)

    # One-time status-broadcaster refresh: populates the cache and runs
    # the load-bearing auto-reconnects (Telegram bot via stored token,
    # Android relay via stored pairing). Per-WS-client refresh used to
    # do this on every connect, which produced an M-by-N storm under
    # PartySocket's auto-reconnect on every page nav / network blip --
    # the refresh now runs ONCE at startup, and state changes after
    # that flow through the originating code path's event-driven
    # broadcasts (whatsapp `_handle_event`, telegram `_broadcast_status`,
    # OAuth callbacks, android relay broadcaster).
    #
    # Spawned as a background task so a slow upstream (Telegram getMe,
    # Twitter token validation) doesn't block lifespan startup.
    asyncio.create_task(get_status_broadcaster()._refresh_all_services())

    _startup_log("All services initialized")
    print("Application startup complete", flush=True)
    yield

    # Shutdown
    # Stop WebSocket logging handler
    shutdown_websocket_logging()

    # Cancel Temporal init task if it's still trying to connect.
    if temporal_init_task is not None and not temporal_init_task.done():
        temporal_init_task.cancel()
        try:
            await temporal_init_task
        except (asyncio.CancelledError, Exception):
            pass

    # Stop Temporal worker if it successfully started.
    worker_manager = getattr(app.state, "temporal_worker_manager", None)
    if worker_manager is not None:
        try:
            await worker_manager.stop()
            logger.info("Temporal worker stopped")
        except Exception as exc:
            logger.warning(f"Temporal worker stop raised: {exc}")

    # Disconnect Temporal client.
    if settings.temporal_enabled:
        try:
            temporal_client_wrapper = container.temporal_client()
            if temporal_client_wrapper is not None:
                await temporal_client_wrapper.disconnect()
        except Exception:
            pass

    # Shutdown proxy service
    from services.proxy.service import get_proxy_service
    _proxy_svc = get_proxy_service()
    if _proxy_svc:
        await _proxy_svc.shutdown()

    # Wave 12 C4 sub-piece B: drain every plugin that self-registered
    # a shutdown hook via services.plugin.shutdown_hooks. Today:
    # android relay client (prevents "Unclosed client session" warning),
    # browser service (prevents orphaned processes + EBUSY file locks).
    # New plugins with cleanup needs register a hook from their
    # __init__.py — main.py never edits.
    from services.plugin.shutdown_hooks import run_shutdown_hooks
    await run_shutdown_hooks()

    # Kill all managed processes (process manager node)
    from services.process_service import shutdown_process_service
    await shutdown_process_service()

    # Stop every plugin supervisor that registered itself via
    # services._supervisor.register_supervisor() (currently: WhatsApp;
    # other plugins migrate in PR 2).
    from services._supervisor import shutdown_all_supervisors
    await shutdown_all_supervisors()

    # Stop cleanup service
    if cleanup_service is not None:
        await cleanup_service.stop()

    # Stop recovery sweeper first
    if settings.redis_enabled:
        await recovery_sweeper.stop()
        logger.info("Execution recovery sweeper stopped")

    shutdown_scheduler()  # Stop APScheduler

    # Exit the CLI-agent MCP server's lifespan
    cli_mcp_lifespan_ctx = getattr(app.state, "cli_mcp_lifespan_ctx", None)
    if cli_mcp_lifespan_ctx is not None:
        try:
            await cli_mcp_lifespan_ctx.__aexit__(None, None, None)
        except Exception as exc:
            logger.debug("[CLI MCP] lifespan shutdown: %s", exc)

    await container.cache().shutdown()
    await container.database().shutdown()
    logger.info("Services shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="React Flow Python Services",
    version="3.0.0",
    description="Modern workflow automation backend with AI and Maps integration",
    lifespan=lifespan,
)

# Add exception handler middleware BEFORE CORS to catch all errors
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

class CatchAllExceptionsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error(f"Unhandled exception: {type(e).__name__}: {str(e)}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": f"{type(e).__name__}: {str(e)}",
                    "detail": "Internal server error"
                }
            )

app.add_middleware(CatchAllExceptionsMiddleware)

# Add Auth middleware (checks JWT cookie for protected routes)
from middleware.auth import AuthMiddleware
app.add_middleware(AuthMiddleware)

# Add CORS middleware (must be AFTER exception middleware)
logger.info("Configuring CORS middleware",
           origins_count=len(settings.cors_origins),
           origins=settings.cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Include framework-level routers (everything that's NOT plugin-specific
# stays here). Plugin-owned routers self-register via
# ``services.ws_handler_registry.register_router`` from their plugin
# folder's ``__init__.py`` and are mounted via the loop below — main.py
# never imports a migrated plugin module by name.
app.include_router(auth.router)  # Auth routes (login, register, logout, status)
app.include_router(nodejs_compat.router)  # Node.js compatibility (includes root endpoints)
app.include_router(workflow.router)
app.include_router(database.router)
app.include_router(websocket.router)
app.include_router(credentials.router)  # Credentials panel - lazy per-tile icon endpoint (n8n pattern)
app.include_router(schemas.router)  # Per-node output schema endpoint (GET /api/schemas/nodes/{type}.json)

# Routers awaiting migration into their plugin folders. As each plugin
# moves to the self-contained pattern (nodes/<plugin>/_router.py +
# register_router from __init__.py), the corresponding line below is
# removed. Tracked in the plugin-extraction plan.
app.include_router(webhook.router)
# Twitter, Google, Android, and Maps routers moved (Maps deleted in
# Wave 11.I milestone N -- all four endpoints were dead, the validate-key
# path now flows through CREDENTIAL_REGISTRY's GoogleMapsCredential._probe).

# Plugin-registered routers — populated by `nodes/<plugin>/__init__.py`
# at import time via `register_router(...)`. Plugins are imported during
# the node-discovery walk on app startup; iterating here picks up
# anything that registered.
from services.ws_handler_registry import get_routers as _get_plugin_routers
for _plugin_router in _get_plugin_routers():
    app.include_router(_plugin_router)


# ---------------------------------------------------------------------------
# CLI agent IDE MCP server (VSCode pattern)
# ---------------------------------------------------------------------------
#
# Spawned Claude Code / Codex CLI sessions auto-discover this server via
# a per-batch lockfile (~/.claude/ide/<pid>.lock) and call tools like
# mcp__machina__getSkill / getCredential / broadcastLog over MCP-over-HTTP.
# Bearer-token auth scoped per batch (see services/cli_agent/mcp_server.py).
try:
    from services.cli_agent.mcp_server import get_mcp_app as _get_cli_mcp_app
    _cli_mcp_app = _get_cli_mcp_app()
    app.mount("/mcp/ide", _cli_mcp_app)
    logger.info("[main] mounted CLI agent MCP server at /mcp/ide")
except Exception as exc:  # pragma: no cover — defensive; MCP must not block startup
    logger.warning("[main] failed to mount CLI agent MCP server: %s", exc)


# Stale lockfile sweep on startup — mirrors VSCode's behavior. PIDs in
# leftover lockfiles that are no longer alive get cleaned up.
@app.on_event("startup")
async def _sweep_cli_lockfiles_on_startup() -> None:
    try:
        from services.cli_agent.config import list_provider_names
        from services.cli_agent.factory import create_cli_provider
        from services.cli_agent.lockfile import sweep_stale_lockfiles
        # Ask the provider class for its lockfile dir rather than the
        # raw JSON config — the provider is the source of truth (e.g.
        # the claude provider derives its dir from
        # ``MACHINA_CLAUDE_DIR/ide`` so it stays in sync with the
        # ``CLAUDE_CONFIG_DIR`` env var we set on spawn). Reading from
        # the config dict would miss provider-class-computed paths.
        for name in list_provider_names():
            try:
                provider = create_cli_provider(name)
            except Exception:
                continue
            if provider.ide_lockfile_dir:
                sweep_stale_lockfiles(provider.ide_lockfile_dir)
    except Exception as exc:
        logger.debug("[main] CLI lockfile sweep failed: %s", exc)


@app.get("/health")
async def health_check():
    """Detailed health check with resource monitoring."""
    from services import event_waiter
    from services.execution import get_recovery_sweeper
    from core.health import get_health_status

    sweeper = get_recovery_sweeper()

    # Get comprehensive health status
    health = await get_health_status(
        database=container.database(),
        cache=container.cache(),
        settings=settings
    )

    # Check Temporal status
    temporal_status = {
        "enabled": settings.temporal_enabled,
        "connected": False,
    }
    if settings.temporal_enabled:
        try:
            temporal_client_wrapper = container.temporal_client()
            if temporal_client_wrapper is not None:
                temporal_status["connected"] = temporal_client_wrapper.is_connected
                temporal_status["server_address"] = settings.temporal_server_address
                temporal_status["task_queue"] = settings.temporal_task_queue
        except Exception:
            pass

    return {
        "status": health["status"],
        "service": "python",
        "version": "3.3.0",  # Bumped for daemon service support
        "environment": "development" if settings.debug else "production",
        "uptime_seconds": health["uptime_seconds"],
        "resources": {
            "memory_mb": health["memory_mb"],
            "disk_percent": health["disk_percent"],
            "cpu_percent": health["cpu_percent"],
        },
        "checks": health["checks"],
        "features": health["features"],
        "redis_enabled": settings.redis_enabled,
        "event_waiter_mode": event_waiter.get_backend_mode(),
        "execution_engine": {
            "enabled": settings.redis_enabled,
            "recovery_sweeper": sweeper is not None and sweeper._running,
        },
        "temporal": temporal_status,
        "timestamp": datetime.now().isoformat()
    }


if __name__ == "__main__":
    import uvicorn
    logger.info("Starting React Flow Python Services",
               host=settings.host, port=settings.port, debug=settings.debug)
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        reload_dirs=["."] if settings.debug else None,
        reload_excludes=["*.pyc", "__pycache__", "*.log", "*.db"] if settings.debug else None,
        workers=1 if settings.debug else settings.workers
    )