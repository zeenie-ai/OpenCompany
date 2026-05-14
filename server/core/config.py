"""Environment-driven configuration with Pydantic v2."""

from typing import List, Literal, Optional
from pathlib import Path
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


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
    jwt_cookie_name: str = Field(default="machina_token", env="JWT_COOKIE_NAME")
    jwt_cookie_secure: bool = Field(default=False, env="JWT_COOKIE_SECURE")  # True in production
    jwt_cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax", env="JWT_COOKIE_SAMESITE")

    # Security
    secret_key: str = Field(env="SECRET_KEY", min_length=32)
    cors_origins: List[str] = Field(env="CORS_ORIGINS")

    # Database Configuration
    database_url: str = Field(env="DATABASE_URL")
    database_echo: bool = Field(default=False, env="DATABASE_ECHO")
    database_pool_size: int = Field(default=20, env="DATABASE_POOL_SIZE", ge=5, le=100)
    database_max_overflow: int = Field(default=30, env="DATABASE_MAX_OVERFLOW", ge=10, le=100)

    # Cache Configuration
    redis_url: Optional[str] = Field(default=None, env="REDIS_URL")
    redis_enabled: bool = Field(default=False, env="REDIS_ENABLED")
    cache_ttl: int = Field(default=3600, env="CACHE_TTL", ge=60)

    # Execution Engine
    dlq_enabled: bool = Field(default=False, env="DLQ_ENABLED")

    # Temporal Configuration
    temporal_enabled: bool = Field(default=False, env="TEMPORAL_ENABLED")
    temporal_server_address: str = Field(default="localhost:7233", env="TEMPORAL_SERVER_ADDRESS")
    temporal_namespace: str = Field(default="default", env="TEMPORAL_NAMESPACE")
    temporal_task_queue: str = Field(default="machina-tasks", env="TEMPORAL_TASK_QUEUE")
    # F4.A: per-type activity dispatch. When True, MachinaWorkflow.run() schedules
    # `node.{type}.v{version}` per plugin (with task_queue=cls.task_queue) instead
    # of the single `execute_node_activity` legacy name. Workers register per-type
    # activities alongside the legacy one. Default off so existing deployments
    # behave identically; flip via TEMPORAL_PER_TYPE_DISPATCH=true.
    temporal_per_type_dispatch: bool = Field(default=False, env="TEMPORAL_PER_TYPE_DISPATCH")
    # F4.B: agent-as-child-workflow. When True, MachinaWorkflow.run() schedules
    # AgentWorkflow (child workflow) for the 14 migrating agent types
    # (aiAgent / chatAgent / 11 specialized agents / 2 team leads) instead of
    # an activity. Tool calls inside the agent become per-type Temporal
    # activities. `rlm_agent`, `claude_code_agent` continue to run as F4.A
    # per-type activities (NOT migrated -- their external session state would
    # break across activity boundaries). Default off. Implies
    # temporal_per_type_dispatch=True.
    temporal_agent_workflow_enabled: bool = Field(
        default=False, env="TEMPORAL_AGENT_WORKFLOW_ENABLED",
    )
    # Wave 12 A3: SIGTERM grace window for Temporal workers. Activities
    # mid-flight finish (or hand back to the server for retry) instead of
    # being killed mid-call. Default 30s matches the polling-trigger
    # heartbeat interval — a worker drain completes within one polling
    # cycle. Tune via env when SIGTERM-to-restart latency matters.
    temporal_graceful_shutdown_seconds: int = Field(
        default=30, env="TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", ge=1,
    )
    # Wave 12 A6: feature flag for the Temporal-native event dispatch
    # path (services/events/dispatch.py:emit). Default off in Phase A
    # so the new dispatch + Signal-based consumer fan-out activates only
    # for opt-in dogfooding before Phase B migrates plugin callsites.
    # Phase A acceptance criterion is enabling this flag on a single
    # canary callsite (e.g. whatsappReceive).
    event_framework_enabled: bool = Field(
        default=False, env="EVENT_FRAMEWORK_ENABLED",
    )

    # Persistence backend for the supervised Temporal cluster.
    # 'postgres' (default) — pgserver + temporal-server binary with
    #                         YAML config managed by services/temporal/.
    #                         Cross-platform via pip-installed binaries.
    # 'sqlite'             — single ServiceSpec running `temporal api`.
    #                         Lighter dev path; in-process SQLite, lost on
    #                         restart. Set when you need fast iteration
    #                         and don't care about workflow durability.
    temporal_backend: Literal["sqlite", "postgres"] = Field(
        default="postgres", env="TEMPORAL_BACKEND",
    )
    # Internal Temporal service gRPC ports — exposed for the YAML config
    # renderer and the runtime's readiness probe. Defaults match the
    # ports Temporal documents at https://docs.temporal.io/references/configuration
    temporal_frontend_grpc_port: int = Field(
        default=7233, env="TEMPORAL_FRONTEND_GRPC_PORT", ge=1024, le=65535,
    )
    temporal_matching_grpc_port: int = Field(
        default=7235, env="TEMPORAL_MATCHING_GRPC_PORT", ge=1024, le=65535,
    )
    temporal_history_grpc_port: int = Field(
        default=7234, env="TEMPORAL_HISTORY_GRPC_PORT", ge=1024, le=65535,
    )
    temporal_worker_grpc_port: int = Field(
        default=7239, env="TEMPORAL_WORKER_GRPC_PORT", ge=1024, le=65535,
    )
    temporal_bind_local_only: bool = Field(
        default=True, env="TEMPORAL_BIND_LOCAL_ONLY",
    )
    temporal_num_history_shards: int = Field(
        default=4, env="TEMPORAL_NUM_HISTORY_SHARDS", ge=1, le=4096,
    )
    temporal_default_max_conns: int = Field(
        default=20, env="TEMPORAL_DEFAULT_MAX_CONNS", ge=1, le=500,
    )
    temporal_visibility_max_conns: int = Field(
        default=4, env="TEMPORAL_VISIBILITY_MAX_CONNS", ge=1, le=500,
    )
    # Postgres connection rotation interval. Temporal community
    # recommendation is ~5 minutes (much shorter than the 1h default)
    # because Postgres' default `tcp_keepalives_idle` and pooled-driver
    # state can lead to "context canceled" errors on idle connections
    # being reused — periodic refresh sidesteps the issue. Accepts
    # Go-duration strings (`30s`, `5m`, `1h`).
    temporal_max_conn_lifetime: str = Field(
        default="5m", env="TEMPORAL_MAX_CONN_LIFETIME",
    )
    temporal_binary_version: str = Field(
        default="1.31.0", env="TEMPORAL_BINARY_VERSION",
    )
    temporal_postgres_dsn: Optional[str] = Field(
        default=None, env="TEMPORAL_POSTGRES_DSN",
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

    # Data directory (base for all persistent storage: DBs, workspaces, logs)
    data_dir: str = Field(default="data", env="DATA_DIR")

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
        default=10 * 1024 * 1024, env="LOG_FILE_MAX_BYTES", ge=1024,
    )
    log_file_backup_count: int = Field(
        default=5, env="LOG_FILE_BACKUP_COUNT", ge=0,
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

    # Compaction Configuration. The threshold is fully model-aware and
    # sourced from server/config/llm_defaults.json (context_length ×
    # agent.compaction.ratio). Only the global on/off toggle remains here.
    compaction_enabled: bool = Field(default=True, env="COMPACTION_ENABLED")

    # Gunicorn Configuration (for production deployment)
    gunicorn_timeout: int = Field(default=120, env="GUNICORN_TIMEOUT", ge=30)
    gunicorn_graceful_timeout: int = Field(default=30, env="GUNICORN_GRACEFUL_TIMEOUT", ge=10)
    gunicorn_keepalive: int = Field(default=5, env="GUNICORN_KEEPALIVE", ge=1)
    gunicorn_max_requests: int = Field(default=10000, env="GUNICORN_MAX_REQUESTS", ge=0)
    gunicorn_max_requests_jitter: int = Field(default=1000, env="GUNICORN_MAX_REQUESTS_JITTER", ge=0)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v):
        """Ensure database directory exists for SQLite."""
        if v and v.startswith("sqlite"):
            if ":///" in v:
                db_path = v.split("///")[1]
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        return v


    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.debug

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not self.debug

    def _resolve_under_data(self, path: str) -> str:
        """Resolve a path relative to data_dir, unless already absolute."""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str(Path(self.data_dir) / p)

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
        "extra": "forbid",
        "env_parse_none_str": "none",
        "env_nested_delimiter": "__",
    }