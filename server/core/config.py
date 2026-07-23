"""Environment-driven configuration with Pydantic v2."""

from typing import List, Literal, Optional
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


# The dev placeholder secrets shipped in ``.env.template``. SSOT for these
# literals — ``company build`` scaffolds fresh values over the ``dev-``
# placeholders and ``company deploy`` mints per-deploy keys. Drift-locked
# against the template by ``tests/core_config/test_dev_secret_guard.py``.
DEV_SECRET_LITERALS: frozenset = frozenset(
    {
        "dev-secret-key-12345678901234567890123456789012",
        "dev-jwt-secret-key-12345678901234567890",
        "dev-encryption-key-12345678901234567890123456",
    }
)


def dev_secret_offenders(settings) -> list[str]:
    """Env-var names still carrying dev placeholder secrets, non-dev posture only.

    Dev posture = auth disabled (``VITE_AUTH_ENABLED == "false"``, matching
    ``middleware/auth.py``) AND ``deployment_mode == "local"`` — returns []
    there. Duck-typed: accepts any object with the three secret attrs plus
    ``vite_auth_enabled`` and ``deployment_mode``.
    """
    auth_disabled = (settings.vite_auth_enabled or "").lower() == "false"
    if auth_disabled and settings.deployment_mode == "local":
        return []
    offenders: list[str] = []
    for attr, env_name in (
        ("secret_key", "SECRET_KEY"),
        ("jwt_secret_key", "JWT_SECRET_KEY"),
        ("api_key_encryption_key", "API_KEY_ENCRYPTION_KEY"),
    ):
        if getattr(settings, attr, None) in DEV_SECRET_LITERALS:
            offenders.append(env_name)
    return offenders


class Settings(BaseSettings):
    """Application settings driven entirely by environment variables."""

    # Service Ports (used by start.js, vite, and docker-compose, not hardcoded here)
    vite_client_port: Optional[int] = Field(default=None, env="VITE_CLIENT_PORT")
    python_backend_port: Optional[int] = Field(default=None, env="PYTHON_BACKEND_PORT")
    whatsapp_rpc_port: Optional[int] = Field(default=None, env="WHATSAPP_RPC_PORT")
    redis_port: Optional[int] = Field(default=None, env="REDIS_PORT")

    # Server Configuration
    host: str = Field(env="HOST")
    port: int = Field(env="PORT", ge=1024, le=65535)
    debug: bool = Field(default=False, env="DEBUG")
    workers: int = Field(default=1, env="WORKERS", ge=1, le=8)

    # Authentication
    auth_mode: Literal["single", "multi"] = Field(default="single", env="AUTH_MODE")
    jwt_secret_key: str = Field(env="JWT_SECRET_KEY", min_length=32)
    jwt_expire_minutes: int = Field(default=10080, env="JWT_EXPIRE_MINUTES", ge=60)  # 7 days
    jwt_cookie_name: str = Field(default="opencompany_token", env="JWT_COOKIE_NAME")
    jwt_cookie_secure: bool = Field(default=False, env="JWT_COOKIE_SECURE")  # True in production
    jwt_cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax", env="JWT_COOKIE_SAMESITE")

    # Security
    secret_key: str = Field(env="SECRET_KEY", min_length=32)
    cors_origins: List[str] = Field(env="CORS_ORIGINS")

    # Database Configuration. ``database_url`` is a computed property
    # (see below) — the SQLite file name lives in ``WORKFLOW_DB_FILENAME``
    # and resolves under ``DATA_DIR`` like every other state path,
    # rather than being hardcoded inside a SQLAlchemy URL string.
    workflow_db_filename: str = Field(env="WORKFLOW_DB_FILENAME")
    database_echo: bool = Field(default=False, env="DATABASE_ECHO")
    database_pool_size: int = Field(default=20, env="DATABASE_POOL_SIZE", ge=5, le=100)
    database_max_overflow: int = Field(default=30, env="DATABASE_MAX_OVERFLOW", ge=10, le=100)

    # Cache Configuration
    redis_url: Optional[str] = Field(default=None, env="REDIS_URL")
    redis_enabled: bool = Field(default=False, env="REDIS_ENABLED")
    cache_ttl: int = Field(default=3600, env="CACHE_TTL", ge=60)

    # Execution Engine
    dlq_enabled: bool = Field(default=False, env="DLQ_ENABLED")

    # Temporal Configuration. All sourced from ``.env.template``
    # (canonical defaults live there); no Python-side fallbacks.
    temporal_enabled: bool = Field(env="TEMPORAL_ENABLED")
    temporal_server_address: str = Field(env="TEMPORAL_SERVER_ADDRESS")
    temporal_namespace: str = Field(env="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field(env="TEMPORAL_TASK_QUEUE")
    # F4.A: per-type activity dispatch. When True, MachinaWorkflow.run() schedules
    # `node.{type}.v{version}` per plugin (with task_queue=cls.task_queue) instead
    # of the single `execute_node_activity` legacy name. Workers register per-type
    # activities alongside the legacy one. Default on as shipped in
    # .env.template; TEMPORAL_PER_TYPE_DISPATCH=false is the rollback.
    temporal_per_type_dispatch: bool = Field(env="TEMPORAL_PER_TYPE_DISPATCH")
    # F4.B: agent-as-child-workflow. When True, MachinaWorkflow.run() schedules
    # AgentWorkflow (child workflow) for the 15 migrating agent types
    # (aiAgent / chatAgent / 11 specialized agents / 2 team leads) instead of
    # an activity. Tool calls inside the agent become per-type Temporal
    # activities. `rlm_agent`, `claude_code_agent` continue to run as F4.A
    # per-type activities (NOT migrated -- their external session state would
    # break across activity boundaries). Default on as shipped in
    # .env.template; TEMPORAL_AGENT_WORKFLOW_ENABLED=false is the rollback.
    # Implies temporal_per_type_dispatch=True.
    temporal_agent_workflow_enabled: bool = Field(
        env="TEMPORAL_AGENT_WORKFLOW_ENABLED",
    )
    # Wave 12 A3: SIGTERM grace window for Temporal workers. Activities
    # mid-flight finish (or hand back to the server for retry) instead of
    # being killed mid-call. Default 30s matches the polling-trigger
    # heartbeat interval — a worker drain completes within one polling
    # cycle. Tune via env when SIGTERM-to-restart latency matters.
    temporal_graceful_shutdown_seconds: int = Field(
        env="TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS",
        ge=1,
    )
    # Wave 12 canary-flag default flipped to True (2026-05-15).
    # The Temporal-native event dispatch path
    # (services/events/dispatch.py:emit) is production-default;
    # consumer fan-out + WS broadcast both fire.
    #
    # Rollback procedure: set EVENT_FRAMEWORK_ENABLED=false in the
    # .env (or process env) and restart. dispatch.emit() reverts to a
    # pass-through no-op; legacy event_waiter.dispatch path keeps
    # working unchanged for non-canary triggers. See
    # docs-internal/event_framework.md § Rollback procedure.
    event_framework_enabled: bool = Field(
        default=True,
        env="EVENT_FRAMEWORK_ENABLED",
    )
    # Wave 16: per-queue activity routing. When True, main.py starts a
    # TemporalWorkerPool (one activity-only Worker per plugin-declared
    # task queue) alongside the TemporalWorkerManager, and
    # MachinaWorkflow._resolve_activity returns cls.task_queue so
    # per-type activities land on their specialised pool worker.
    #
    # Wave 16.4: default flipped to True — per-queue routing is the
    # production default (LLM / browser / code-exec / REST workloads
    # get isolated slot pools instead of sharing one 100-slot queue).
    # Rollback: TEMPORAL_WORKER_POOL_ENABLED=false + restart — the pool
    # stops and every activity routes back to the single manager worker
    # on "machina-tasks". Opt out via the env var, never by reverting
    # this default (locked by test_task_queue_coverage.py).
    temporal_worker_pool_enabled: bool = Field(
        default=True,
        env="TEMPORAL_WORKER_POOL_ENABLED",
    )
    # Wave 17.4: deployment topology hint. Drives worker identity
    # strings (visible in Temporal Web UI -> Workers) and per-mode
    # concurrency defaults (local laptops get half the per-queue slots
    # of an always-on cloud VM; explicit TEMPORAL_<QUEUE>_CONCURRENCY
    # env vars always win).
    #   local       — developer machine; sleeps/hibernates; 4-8 cores
    #   cloud       — always-on server (company deploy)
    #   self_hosted — user-managed always-on box
    deployment_mode: Literal["local", "cloud", "self_hosted"] = Field(
        default="local",
        env="DEPLOYMENT_MODE",
    )

    # gRPC frontend port — the port ``temporal server start-dev``
    # exposes for clients + supervisor readiness probe. Sourced from
    # ``.env.template`` (canonical default lives there). Reference:
    # https://docs.temporal.io/references/configuration
    temporal_frontend_grpc_port: int = Field(
        env="TEMPORAL_FRONTEND_GRPC_PORT",
        ge=1024,
        le=65535,
    )
    # Web UI port. ``temporal server start-dev`` defaults this to
    # ``--port + 1000`` (i.e. 8233 alongside the default 7233 gRPC
    # port); declared explicitly via ``--ui-port`` so the binding is
    # intentional and surfaces in status snapshots.
    temporal_ui_port: int = Field(
        env="TEMPORAL_UI_PORT",
        ge=1024,
        le=65535,
    )
    # SQLite db path passed to ``temporal server start-dev
    # --db-filename ...``. Resolved relative to ``DATA_DIR``
    # (``Settings._resolve_under_data``) unless absolute. Sourced
    # from ``.env.template`` (canonical default lives there).
    temporal_sqlite_path: str = Field(env="TEMPORAL_SQLITE_PATH")
    # Terminate every workflow in ``Running`` state on server boot.
    # Preserves history (workflows show as ``Terminated`` in the
    # Temporal UI, not deleted) but prevents auto-resumption, since
    # OpenCompany's ``DeploymentManager`` has no boot-time reconcile
    # against Temporal Visibility yet — resumed workflows would
    # otherwise be invisible to the UI.
    temporal_terminate_running_on_startup: bool = Field(
        env="TEMPORAL_TERMINATE_RUNNING_ON_STARTUP",
    )

    # Startup resilience (server-ready-before-workers). Unlike the core
    # temporal fields above, these carry Python defaults (matching
    # ``.env.template``) so an existing ``.env`` predating them keeps
    # booting — same convention as ``dlq_enabled`` / ``event_framework_enabled``.
    # Readiness gate: poll the WorkflowService gRPC health check until
    # SERVING before the worker / visibility sweep act.
    temporal_health_check_attempts: int = Field(
        default=5,
        env="TEMPORAL_HEALTH_CHECK_ATTEMPTS",
        ge=1,
    )
    temporal_health_check_delay_seconds: float = Field(
        default=0.5,
        env="TEMPORAL_HEALTH_CHECK_DELAY_SECONDS",
        ge=0,
    )
    temporal_health_check_timeout_seconds: float = Field(
        default=2.0,
        env="TEMPORAL_HEALTH_CHECK_TIMEOUT_SECONDS",
        gt=0,
    )
    # Boot-time terminate-running sweep: retry the Visibility query that
    # races shard acquisition ("shard status unknown") before giving up.
    temporal_sweep_attempts: int = Field(
        default=4,
        env="TEMPORAL_SWEEP_ATTEMPTS",
        ge=1,
    )
    temporal_sweep_backoff_seconds: float = Field(
        default=0.5,
        env="TEMPORAL_SWEEP_BACKOFF_SECONDS",
        ge=0,
    )
    # Embedded worker self-restart backoff (doubles from base to max)
    # when the Temporal worker shuts down on a transient poll failure.
    temporal_worker_restart_backoff_seconds: float = Field(
        default=1.0,
        env="TEMPORAL_WORKER_RESTART_BACKOFF_SECONDS",
        gt=0,
    )
    temporal_worker_restart_backoff_max_seconds: float = Field(
        default=30.0,
        env="TEMPORAL_WORKER_RESTART_BACKOFF_MAX_SECONDS",
        gt=0,
    )

    # API Keys (all optional, injected at runtime)
    google_maps_api_key: Optional[str] = Field(default=None, env="GOOGLE_MAPS_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, env="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    google_ai_api_key: Optional[str] = Field(default=None, env="GOOGLE_AI_API_KEY")

    # Node.js Executor Configuration
    nodejs_executor_url: str = Field(default="http://localhost:3020", env="NODEJS_EXECUTOR_URL")
    nodejs_executor_timeout: int = Field(default=30, env="NODEJS_EXECUTOR_TIMEOUT", ge=5, le=300)
    nodejs_executor_port: int = Field(default=3020, env="NODEJS_EXECUTOR_PORT", ge=1024, le=65535)

    # AI Proxy Configuration (Ollama-style proxy)
    ai_proxy_default_port: int = Field(default=11434, env="AI_PROXY_DEFAULT_PORT")

    # Residential Proxy Configuration
    proxy_enabled: bool = Field(default=False, env="PROXY_ENABLED")
    proxy_budget_daily_usd: float = Field(default=50.0, env="PROXY_BUDGET_DAILY_USD")
    proxy_default_country: str = Field(default="", env="PROXY_DEFAULT_COUNTRY")

    # WebSocket Configuration
    websocket_url: str = Field(default="", env="WEBSOCKET_URL")
    websocket_api_key: Optional[str] = Field(default=None, env="WEBSOCKET_API_KEY")

    # Android Relay Configuration (passed to Vite frontend)
    vite_android_relay_url: Optional[str] = Field(default=None, env="VITE_ANDROID_RELAY_URL")

    # Frontend Auth Configuration (passed to Vite frontend)
    vite_auth_enabled: Optional[str] = Field(default=None, env="VITE_AUTH_ENABLED")

    # API Key Security
    api_key_encryption_key: str = Field(env="API_KEY_ENCRYPTION_KEY", min_length=32)
    api_key_cache_ttl: int = Field(default=2592000, env="API_KEY_CACHE_TTL", ge=3600)

    # Data directory (base for all persistent storage: DBs, workspaces,
    # workflows, claude state). Default is the user's home dir
    # (``~/.opencompany``) — same convention claude code itself uses
    # (``~/.claude/``), Stripe (``~/.config/stripe/``), ngrok
    # (``~/.ngrok2/``). Survives ``rm -rf`` of the repo. Resolution
    # rules (``~``, absolute, repo-relative) live in
    # :func:`core.paths.opencompany_root`. The path resolver falls back to
    # an existing sibling ``.machina`` root so upgrades retain state.
    # pre-cutover ``data/`` + ``workflows/`` layout — operators
    # either set ``DATA_DIR=data`` to keep the old layout or move
    # the contents manually (see ``paths.py`` docstring).
    data_dir: str = Field(default="~/.opencompany", env="DATA_DIR")

    # Credentials Database (separate encrypted database for API keys and OAuth tokens)
    # Resolved relative to data_dir unless absolute
    credentials_db_path: str = Field(default="credentials.db", env="CREDENTIALS_DB_PATH")

    # Credential Backend Selection
    # Options: fernet (default), keyring (OS-native), aws (AWS Secrets Manager)
    credential_backend: Literal["fernet", "keyring", "aws"] = Field(default="fernet", env="CREDENTIAL_BACKEND")
    aws_secret_arn: Optional[str] = Field(default=None, env="AWS_SECRET_ARN")
    aws_region: Optional[str] = Field(default=None, env="AWS_REGION")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(default="json", env="LOG_FORMAT")
    log_file: Optional[str] = Field(default=None, env="LOG_FILE")
    # File-rotation knobs — only consulted when LOG_FILE is set.
    # Default ceiling = 10 MiB × 5 backups = ~50 MiB on disk.
    log_file_max_bytes: int = Field(
        default=10 * 1024 * 1024,
        env="LOG_FILE_MAX_BYTES",
        ge=1024,
    )
    log_file_backup_count: int = Field(
        default=5,
        env="LOG_FILE_BACKUP_COUNT",
        ge=0,
    )

    # Rate Limiting
    rate_limit_enabled: bool = Field(default=True, env="RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(default=100, env="RATE_LIMIT_REQUESTS", ge=10)
    rate_limit_window: int = Field(default=60, env="RATE_LIMIT_WINDOW", ge=10)

    # Service Timeouts
    ai_timeout: int = Field(default=30, env="AI_TIMEOUT", ge=5, le=300)
    ai_max_retries: int = Field(default=3, env="AI_MAX_RETRIES", ge=0, le=5)
    ai_retry_delay: float = Field(default=1.0, env="AI_RETRY_DELAY", ge=0.1, le=10.0)

    maps_timeout: int = Field(default=10, env="MAPS_TIMEOUT", ge=5, le=60)
    maps_max_requests_per_second: int = Field(default=50, env="MAPS_MAX_RPS", ge=1, le=1000)

    # Health Check
    health_check_interval: int = Field(default=30, env="HEALTH_CHECK_INTERVAL", ge=10)

    # Cleanup Configuration (for long-running daemon)
    cleanup_enabled: bool = Field(default=True, env="CLEANUP_ENABLED")
    cleanup_interval: int = Field(default=3600, env="CLEANUP_INTERVAL", ge=60)
    cleanup_logs_max_count: int = Field(default=1000, env="CLEANUP_LOGS_MAX_COUNT", ge=100)
    cleanup_cache_max_age_hours: int = Field(default=24, env="CLEANUP_CACHE_MAX_AGE_HOURS", ge=1)

    # Feature Toggles
    ws_logging_enabled: bool = Field(default=True, env="WS_LOGGING_ENABLED")

    # Workspace Configuration (per-workflow file storage for nodes and agents)
    # Workspace base -- relative to data_dir unless absolute
    workspace_base_dir: str = Field(default="workspaces", env="WORKSPACE_BASE_DIR")

    # WhatsApp runtime (edgymeow Go binary supervised by the backend so the
    # session DB lives under data_dir, surviving pnpm install / version bumps)
    whatsapp_runtime_enabled: bool = Field(default=True, env="WHATSAPP_RUNTIME_ENABLED")
    whatsapp_data_subdir: str = Field(default="whatsapp", env="WHATSAPP_DATA_SUBDIR")
    whatsapp_port: int = Field(default=9400, env="WHATSAPP_RPC_PORT", ge=1024, le=65535)
    whatsapp_binary_path: Optional[str] = Field(default=None, env="WHATSAPP_BINARY_PATH")
    # `localhost` is the only bind that Windows Firewall's loopback exception
    # silently allows. Override to "0.0.0.0" for container deployments.
    whatsapp_bind_host: str = Field(default="localhost", env="WHATSAPP_BIND_HOST")

    # Browser automation (agent-browser CLI). Canonical values live in
    # .env.template (same pattern as COMPACTION_RATIO — the code default
    # only mirrors the template for bare Settings() construction, e.g.
    # pytest/CI without the CLI env layering). Each distinct session name
    # maps to one Chrome instance; the cap closes the oldest session
    # before a new one would exceed it. Idle timeout (ms) makes the
    # agent-browser daemon shut down its browser after inactivity; 0
    # disables.
    browser_max_instances: int = Field(default=3, env="BROWSER_MAX_INSTANCES", ge=1, le=50)
    browser_idle_timeout_ms: int = Field(default=600_000, env="BROWSER_IDLE_TIMEOUT_MS", ge=0)

    # Compaction Configuration. Threshold = model context_length ×
    # compaction_ratio. Per-user UserSettings row overrides at runtime.
    compaction_enabled: bool = Field(default=True, env="COMPACTION_ENABLED")
    compaction_ratio: float = Field(default=0.8, env="COMPACTION_RATIO", ge=0.05, le=0.99)

    # Agent loop hard step cap. Per-user UserSettings row overrides
    # at runtime; per-agent-node ``parameters.max_iterations`` is the
    # innermost override.
    agent_recursion_limit: int = Field(default=200, env="AGENT_RECURSION_LIMIT", ge=1)

    # Multi-agent delegation guardrails. The concurrency cap applies to all
    # active descendants of one root execution (the root itself is excluded).
    # Team leads may delegate one additional level, but deeper trees are
    # rejected before a child workflow is scheduled.
    max_concurrent_subagents: int = Field(default=3, env="MAX_CONCURRENT_SUBAGENTS", ge=1, le=50)
    max_delegation_depth: int = Field(default=2, env="MAX_DELEGATION_DEPTH", ge=1, le=2)

    # Gunicorn Configuration (for production deployment)
    gunicorn_timeout: int = Field(default=120, env="GUNICORN_TIMEOUT", ge=30)
    gunicorn_graceful_timeout: int = Field(default=30, env="GUNICORN_GRACEFUL_TIMEOUT", ge=10)
    gunicorn_keepalive: int = Field(default=5, env="GUNICORN_KEEPALIVE", ge=1)
    gunicorn_max_requests: int = Field(default=10000, env="GUNICORN_MAX_REQUESTS", ge=0)
    gunicorn_max_requests_jitter: int = Field(default=1000, env="GUNICORN_MAX_REQUESTS_JITTER", ge=0)

    # Filesystem-path fields that may use ``~`` in ``.env``. Expanded
    # once at load time so every downstream consumer
    # (``core.paths.opencompany_root``, ``Settings._resolve_under_data``,
    # the Temporal SQLite path, WhatsApp runtime dir, ...) sees
    # a real path. ``Path.expanduser()`` is a no-op when ``~`` isn't
    # present, so we can run it unconditionally.
    @field_validator("data_dir", "log_file", mode="after")
    @classmethod
    def _expanduser_path(cls, v: Optional[str]) -> Optional[str]:
        return str(Path(v).expanduser()) if v else v

    @property
    def database_url(self) -> str:
        """Compose the SQLAlchemy URL from ``DATA_DIR`` + ``WORKFLOW_DB_FILENAME``.

        Always ``sqlite+aiosqlite:///<abspath>`` — the only DB engine
        OpenCompany uses. Parent dir is mkdir'd here so the DB file can
        be opened on first access without a separate bootstrap step.
        Reads :meth:`_resolve_under_data` so the same DEV / daemon
        toggle (``.env.dev`` swapping ``DATA_DIR=.opencompany``) that moves
        every other state path moves ``workflow.db`` too.
        """
        db_path = Path(self._resolve_under_data(self.workflow_db_filename))
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.debug

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not self.debug

    def _resolve_under_data(self, path: str) -> str:
        """Resolve ``path`` under ``data_dir``, returning an absolute path.

        Thin delegate to :func:`core.paths._resolve_data_path` so the
        single resolution algorithm lives in one place. Used by the
        ``credentials_db_resolved`` / ``workspace_base_resolved``
        properties and any plugin that needs a DATA_DIR-relative path
        without importing ``core.paths`` directly.
        """
        from core.paths import _resolve_data_path

        return str(_resolve_data_path(self.data_dir, path))

    @property
    def credentials_db_resolved(self) -> str:
        """Full credentials DB path, rooted under data_dir."""
        return self._resolve_under_data(self.credentials_db_path)

    @property
    def workspace_base_resolved(self) -> str:
        """Full workspace base path, rooted under data_dir."""
        return self._resolve_under_data(self.workspace_base_dir)

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        # ``ignore`` lets stale ``.env`` files survive obsolete vars
        # (e.g. the postgres-backend Temporal knobs stripped from the
        # supervised cluster path). Without this, existing installs
        # carrying ``TEMPORAL_BACKEND`` / ``TEMPORAL_BIND_LOCAL_ONLY`` /
        # etc. would raise ValidationError on startup.
        "extra": "ignore",
        "env_parse_none_str": "none",
        "env_nested_delimiter": "__",
    }
