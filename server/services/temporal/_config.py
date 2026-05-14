"""Temporal YAML config rendering + Postgres schema bootstrap.

Renders a minimal ``services``/``persistence``/``global`` YAML config
pointing at the pgserver-managed Postgres. Runs ``temporal-sql-tool``
to create + version-init the ``temporal`` + ``temporal_visibility``
databases — idempotent, safe to call on every supervisor start.

Reference for the YAML shape:
https://github.com/temporalio/temporal/tree/main/config
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from core.config import Settings
from core.logging import get_logger

logger = get_logger(__name__)


def parse_postgres_uri(uri: str) -> dict[str, Any]:
    """Parse a pgserver-style ``postgresql://user@host:port/db`` URI.

    Single source of truth for URI decomposition — reused by
    :mod:`services.temporal._runtime` (port property, status snapshot)
    and the SQL-tool subprocess invocations below.
    """
    parsed = urlparse(uri)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
    }


# Membership ports = gRPC port + this offset. Mirrors the convention
# Temporal's reference config uses (frontend 7233/6933, history
# 7234/6934, matching 7235/6935, worker 7239/6939) so subtraction
# rather than another tunable settings group keeps the surface small.
# https://docs.temporal.io/references/configuration#services
_MEMBERSHIP_PORT_OFFSET = -300

# DB names. Required by temporal-sql-tool's `--database` and the
# YAML `datastores` block; not tunable because the schema files
# bundled with the binary hard-reference them.
_HISTORY_DB_NAME = "temporal"
_VISIBILITY_DB_NAME = "temporal_visibility"


def _sql_datastore(
    pg: dict[str, Any], db_name: str, *,
    max_conns: int, max_conn_lifetime: str,
) -> dict[str, Any]:
    # ``maxIdleConns`` matches ``maxConns`` so the pool stays fully
    # warm — Temporal's matching-engine periodic UpdateTaskQueue
    # writes are bursty, and a too-small idle pool causes "context
    # canceled" errors when the burst arrives faster than the pool
    # can create new connections. Mirrors the Temporal community
    # recommendation; see docs-internal/TEMPORAL_ARCHITECTURE.md.
    return {
        "sql": {
            "pluginName": "postgres12",
            "databaseName": db_name,
            "connectAddr": f"{pg['host']}:{pg['port']}",
            "connectProtocol": "tcp",
            "user": pg["user"],
            "password": pg["password"],
            "maxConns": max_conns,
            "maxIdleConns": max_conns,
            "maxConnLifetime": max_conn_lifetime,
        },
    }


def _service_rpc(*, grpc_port: int, bind_local: bool) -> dict[str, Any]:
    return {
        "rpc": {
            "grpcPort": grpc_port,
            "membershipPort": grpc_port + _MEMBERSHIP_PORT_OFFSET,
            "bindOnLocalHost": bind_local,
        },
    }


def render_temporal_config(*, settings: Settings, postgres_uri: str) -> Path:
    """Render Temporal YAML config to ``data/_temporal/config.yaml``.

    Every tunable — ports, pool sizes, history shards, bind scope —
    lives in :class:`core.config.Settings`. Single-node cluster
    (frontend / matching / history / worker on separate gRPC ports
    of one process); multi-node scaling is a separate sprint.
    """
    pg = parse_postgres_uri(postgres_uri)
    bind_local = bool(settings.temporal_bind_local_only)
    rpc_host = "127.0.0.1" if bind_local else "0.0.0.0"
    frontend_port = settings.temporal_frontend_grpc_port
    conn_lifetime = settings.temporal_max_conn_lifetime

    config: dict[str, Any] = {
        "log": {"stdout": True, "level": "info"},
        "persistence": {
            "defaultStore": "default",
            "visibilityStore": "visibility",
            "numHistoryShards": settings.temporal_num_history_shards,
            "datastores": {
                "default": _sql_datastore(
                    pg, _HISTORY_DB_NAME,
                    max_conns=settings.temporal_default_max_conns,
                    max_conn_lifetime=conn_lifetime,
                ),
                "visibility": _sql_datastore(
                    pg, _VISIBILITY_DB_NAME,
                    max_conns=settings.temporal_visibility_max_conns,
                    max_conn_lifetime=conn_lifetime,
                ),
            },
        },
        "global": {
            "membership": {
                "name": "temporal",
                "broadcastAddress": rpc_host,
                "maxJoinDuration": "30s",
            },
            "pprof": {"port": 0},  # disable pprof; eliminates rand-port noise
        },
        "services": {
            "frontend": _service_rpc(grpc_port=frontend_port, bind_local=bind_local),
            "matching": _service_rpc(
                grpc_port=settings.temporal_matching_grpc_port, bind_local=bind_local,
            ),
            "history": _service_rpc(
                grpc_port=settings.temporal_history_grpc_port, bind_local=bind_local,
            ),
            "worker": _service_rpc(
                grpc_port=settings.temporal_worker_grpc_port, bind_local=bind_local,
            ),
        },
        "clusterMetadata": {
            "enableGlobalNamespace": False,
            "failoverVersionIncrement": 10,
            "masterClusterName": "active",
            "currentClusterName": "active",
            "clusterInformation": {
                "active": {
                    "enabled": True,
                    "initialFailoverVersion": 1,
                    "rpcName": "frontend",
                    "rpcAddress": f"{rpc_host}:{frontend_port}",
                },
            },
        },
        "dcRedirectionPolicy": {"policy": "noop"},
        "archival": {
            "history": {"state": "disabled"},
            "visibility": {"state": "disabled"},
        },
        "namespaceDefaults": {
            "archival": {
                "history": {"state": "disabled"},
                "visibility": {"state": "disabled"},
            },
        },
    }

    out_dir = Path(settings.data_dir) / "_temporal"
    out_dir.mkdir(parents=True, exist_ok=True)
    config_path = out_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    logger.info("[Temporal config] rendered %s", config_path)
    return config_path


async def bootstrap_temporal_schemas(
    *,
    sql_tool: Path,
    postgres_uri: str,
    binary_path: Path,
) -> None:
    """Create + version-init both Temporal databases.

    Idempotent. ``create-database`` is a no-op when the DB exists;
    ``setup-schema`` / ``update-schema`` apply schema-version
    migrations bundled with the Temporal binary.

    Schema versioned-DDL directory lives next to the binary in the
    extracted archive at ``schema/postgresql/v12/{temporal,visibility}/versioned``.
    """
    pg = parse_postgres_uri(postgres_uri)
    schema_root = binary_path.parent / "schema" / "postgresql" / "v12"

    common = [
        str(sql_tool),
        "--plugin", "postgres12",
        "--ep", pg["host"],
        "--port", str(pg["port"]),
        "--user", pg["user"],
        "--password", pg["password"],
    ]

    for db_name, schema_dir in (
        ("temporal", "temporal/versioned"),
        ("temporal_visibility", "visibility/versioned"),
    ):
        # create-database — idempotent (treats "already exists" as success)
        await _run_sql_tool(
            [*common, "--database", db_name, "create-database", "--database", db_name],
            ok_if_substring="already exists",
        )
        # setup-schema — v0.0 baseline; idempotent (no-op when schema_version table exists)
        await _run_sql_tool(
            [*common, "--database", db_name, "setup-schema", "-v", "0.0"],
            ok_if_substring="already exists",
        )
        # update-schema — apply versioned DDL files; idempotent
        schema_path = schema_root / schema_dir
        if not schema_path.exists():
            raise FileNotFoundError(
                f"[Temporal config] schema dir missing: {schema_path}. "
                f"Expected to ship with temporal-server binary at "
                f"{binary_path.parent}/schema/postgresql/v12/"
            )
        await _run_sql_tool(
            [*common, "--database", db_name, "update-schema", "-d", str(schema_path)],
        )
    logger.info("[Temporal config] schemas bootstrapped: temporal + temporal_visibility")


async def _run_sql_tool(argv: list[str], *, ok_if_substring: str | None = None) -> None:
    """Run a temporal-sql-tool subcommand. Tolerates idempotency
    failures (e.g. "database already exists") when ``ok_if_substring``
    matches stderr."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        return
    err = (stderr or b"").decode(errors="replace")
    if ok_if_substring and ok_if_substring in err:
        return
    raise RuntimeError(
        f"[Temporal config] {' '.join(argv[:4])}... failed (rc={proc.returncode}): {err}"
    )


__all__ = [
    "render_temporal_config",
    "bootstrap_temporal_schemas",
    "parse_postgres_uri",
]
