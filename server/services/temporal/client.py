"""Temporal client wrapper for OpenCompany.

Manages the Temporal client connection lifecycle with retry support.
"""

import asyncio
from datetime import timedelta
from typing import Optional
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.runtime import LoggingConfig, Runtime, TelemetryConfig

from core.config import Settings
from core.logging import get_logger

logger = get_logger(__name__)

# Core (the SDK's Rust side) writes its own logs to stderr: errors only, plus
# the [TMPRL1104] warning for a workflow task over 5 s, which blocks every
# workflow on the worker. SDK 1.34 moved that warning from Python into Core,
# so an ERROR-only filter would hide it.
CORE_LOG_FILTER = "ERROR,temporalio_sdk_core::worker::workflow::managed_run=WARN"


def _is_transient_visibility_error(exc: Exception) -> bool:
    """True if ``exc`` is a transient server-warmup/visibility error a
    retry is likely to clear (shard still acquiring, frontend briefly
    unavailable). Keyed on the typed ``RPCError.status`` with a substring
    backstop for non-RPCError wrappers."""
    try:
        from temporalio.service import RPCError, RPCStatusCode

        if isinstance(exc, RPCError) and exc.status in (
            RPCStatusCode.UNAVAILABLE,
            RPCStatusCode.DEADLINE_EXCEEDED,
        ):
            return True
    except Exception:  # noqa: BLE001 — import/shape guard; fall through to substring
        pass
    msg = str(exc).lower()
    return "shard" in msg or "unavailable" in msg or "context canceled" in msg


class TemporalClientWrapper:
    """Wrapper around Temporal client for lifecycle management."""

    def __init__(self, server_address: str, namespace: str = "default"):
        self.server_address = server_address
        self.namespace = namespace
        self._client: Optional[Client] = None
        self._runtime: Optional[Runtime] = None
        self.worker_manager = None
        # Startup-resilience knobs read once from Settings (env-driven,
        # canonical defaults in .env.template). Readiness gate + sweep retry.
        _s = Settings()
        self._health_check_attempts = _s.temporal_health_check_attempts
        self._health_check_delay_seconds = _s.temporal_health_check_delay_seconds
        self._health_check_timeout_seconds = _s.temporal_health_check_timeout_seconds
        self._sweep_attempts = _s.temporal_sweep_attempts
        self._sweep_backoff_seconds = _s.temporal_sweep_backoff_seconds
        # Set while ``_client`` is set, so startup work can wait for the
        # connection instead of racing it (see wait_connected).
        self._connected = asyncio.Event()

    @property
    def client(self) -> Optional[Client]:
        """Get the underlying Temporal client."""
        return self._client

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._client is not None

    async def wait_connected(self, timeout: float) -> bool:
        """Wait until the client is connected, for at most ``timeout`` seconds.

        True once connected (at once if it already is), False on timeout.
        Event dispatch drops events while the client is not connected, so
        startup work that makes events (a reconnected bot delivering its
        held messages) waits here first.
        """
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def connect(self, retries: int = 3, delay: float = 2.0) -> Optional[Client]:
        """Connect to the Temporal server with retries.

        Returns:
            The connected Temporal client, or None if connection failed.
        """
        if self._client is not None:
            return self._client

        # Create runtime once (reusable across reconnects)
        if self._runtime is None:
            self._runtime = Runtime(
                telemetry=TelemetryConfig(
                    logging=LoggingConfig(filter=CORE_LOG_FILTER),
                ),
                worker_heartbeat_interval=None,
            )

        for attempt in range(1, retries + 1):
            try:
                logger.info(
                    f"Connecting to Temporal server (attempt {attempt}/{retries})",
                    server_address=self.server_address,
                    namespace=self.namespace,
                )
                client = await Client.connect(
                    self.server_address,
                    namespace=self.namespace,
                    runtime=self._runtime,
                    interceptors=[TracingInterceptor()],
                )
                # Verify namespace is ready (gRPC port may accept connections
                # before the server finishes registering namespaces)
                await client.service_client.workflow_service.describe_namespace(DescribeNamespaceRequest(namespace=self.namespace))
                # Readiness gate: poll the WorkflowService gRPC health check
                # until SERVING so "connected" means "frontend serving", not
                # just "port bound". Documented readiness probe
                # (https://docs.temporal.io/troubleshooting/deadline-exceeded-error).
                # A miss raises and is caught by the ``except`` below, folding
                # into both retry layers (this loop + main.py's 3s reconnect).
                for hc_attempt in range(1, self._health_check_attempts + 1):
                    try:
                        serving = await client.service_client.check_health(
                            timeout=timedelta(seconds=self._health_check_timeout_seconds),
                        )
                    except Exception:  # noqa: BLE001 — treat unreachable as not-serving
                        serving = False
                    if serving:
                        break
                    if hc_attempt < self._health_check_attempts:
                        await asyncio.sleep(self._health_check_delay_seconds)
                else:
                    raise RuntimeError(
                        "Temporal frontend not SERVING after "
                        f"{self._health_check_attempts} health checks",
                    )
                self._client = client
                self._connected.set()
                logger.info(f"Connected to Temporal server at {self.server_address}")
                # Wave 12 A4: idempotently register the event-framework
                # Search Attributes. A failure here is logged, not raised,
                # but it is not harmless: a start that sets an unregistered
                # attribute fails (every controller sets EventWorkflowId),
                # and dispatch.emit's Visibility query finds no consumers.
                try:
                    from services.temporal.search_attributes import (
                        register_search_attributes,
                    )

                    await register_search_attributes(self._client, self.namespace)
                except Exception as sa_exc:  # noqa: BLE001 — non-fatal
                    logger.warning(f"Search-attribute registration failed (non-fatal): {sa_exc}")
                return self._client
            except Exception as e:
                logger.warning(f"Temporal connection attempt {attempt}/{retries} failed: {e}")
                if attempt < retries:
                    await asyncio.sleep(delay)

        logger.error(f"Failed to connect to Temporal server at {self.server_address} after {retries} attempts")
        return None

    async def disconnect(self) -> None:
        """Disconnect from the Temporal server."""
        if self._client is not None:
            self._client = None
            self._connected.clear()
            logger.info("Disconnected from Temporal server")

    async def check_health(self) -> bool:
        """One gRPC WorkflowService health probe against the connected client.

        False when disconnected or the frontend is not SERVING. Used by
        the lifecycle watchdog (``services.temporal.lifecycle``); the
        bounded readiness gate inside :meth:`connect` is separate.
        """
        if self._client is None:
            return False
        try:
            return bool(
                await self._client.service_client.check_health(
                    timeout=timedelta(seconds=self._health_check_timeout_seconds),
                )
            )
        except Exception:  # noqa: BLE001 — unreachable == not serving
            return False

    async def terminate_running_workflows(
        self,
        *,
        reason: str = "OpenCompany startup: auto-resumption disabled",
    ) -> int:
        """Terminate every workflow in ``Running`` state in our namespace.

        Preserves history — terminated workflows remain visible in the
        Temporal UI as ``Terminated``, only the active execution stops.
        Run from ``main.py`` lifespan between client connect and worker
        start so the worker doesn't accept activities from workflows
        that are about to be terminated. Gated by
        ``Settings.temporal_terminate_running_on_startup`` at the call
        site; this method always terminates when invoked.

        Returns the count of workflows terminated. Failures on
        individual workflows are logged but don't abort the sweep —
        terminating a workflow that completed mid-query is a benign
        race that produces ``WorkflowNotFoundError``.
        """
        if self._client is None:
            logger.warning("terminate_running_workflows called before connect; no-op")
            return 0

        # The Visibility query below races shard acquisition right after
        # boot (visibility-queue-processor reports "shard status unknown"
        # until shard-1 is acquired). Retry the whole query+iteration a
        # few times so the sweep isn't silently abandoned for that boot.
        # ``terminate`` is idempotent, so restarting the scan clean (count
        # reset) on retry is safe — re-terminating an already-terminated
        # workflow raises a benign error caught by the inner ``except``.
        count = 0
        for sweep_attempt in range(1, self._sweep_attempts + 1):
            try:
                count = 0
                async for wf in self._client.list_workflows(
                    "ExecutionStatus = 'Running' AND WorkflowType != 'WorkflowControlWorkflow'",
                ):
                    try:
                        handle = self._client.get_workflow_handle(
                            wf.id,
                            run_id=wf.run_id,
                        )
                        await handle.terminate(reason=reason)
                        count += 1
                    except Exception as exc:  # noqa: BLE001 — best-effort sweep
                        logger.debug(f"Failed to terminate workflow id={wf.id} " f"run_id={wf.run_id}: {exc}")
                break  # iteration completed cleanly
            except Exception as list_exc:  # noqa: BLE001 — Visibility query failed
                if _is_transient_visibility_error(list_exc) and sweep_attempt < self._sweep_attempts:
                    logger.warning(
                        f"Visibility list_workflows transient "
                        f"({sweep_attempt}/{self._sweep_attempts}); retrying: {list_exc}",
                    )
                    await asyncio.sleep(self._sweep_backoff_seconds * sweep_attempt)
                    continue
                raise
        if count:
            logger.info(f"Terminated {count} running workflow(s) at startup " "(history preserved; resumption disabled)")
        return count
