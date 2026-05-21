"""Temporal workflow orchestration service.

This module provides Temporal integration for durable distributed workflow execution.

Architecture:
- Each workflow node executes as an independent Temporal activity
- Activities can run on ANY worker in the cluster for horizontal scaling
- Workflow only orchestrates - schedules activities and routes outputs
- WebSocket connection to MachinaOs for low-latency node execution

When TEMPORAL_ENABLED=true:
- Workflows are executed via Temporal for durability and distribution
- Each node is a separate activity with its own retry policy
- Parallel branches execute concurrently on available workers

When TEMPORAL_ENABLED=false:
- Falls back to the existing parallel/sequential executor
"""

__all__ = [
    "TemporalExecutor",
    "TemporalClientWrapper",
    "TemporalServerRuntime",
    "get_temporal_server_runtime",
]

from .executor import TemporalExecutor
from .client import TemporalClientWrapper
from ._runtime import (
    TemporalServerRuntime,
    get_temporal_server_runtime,
)

# ---- self-registration (Wave 11 plugin-folder pattern) -------------------
# WS handlers (temporal_status / _start / _stop) + WS-connect refresh
# (broadcasts current Temporal snapshot). Registries are idempotent on
# re-import; same callable for same key is a no-op.
from ._handlers import WS_HANDLERS as _WS_HANDLERS
from ._refresh import refresh_temporal_status as _refresh_temporal_status
from services.ws_handler_registry import register_ws_handlers
from services.status_broadcaster import register_service_refresh

register_ws_handlers(_WS_HANDLERS)
register_service_refresh(_refresh_temporal_status)
