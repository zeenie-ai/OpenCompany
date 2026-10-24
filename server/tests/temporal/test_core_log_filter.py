"""Core's logs keep the slow workflow task warning.

SDK 1.34 moved [TMPRL1104], a workflow task over 5 s, from Python logging
into Core, which writes its own logs to stderr. Both of the app's runtimes
filtered Core at ERROR, so the upgrade hid the warning. On the pinned dev
server a 6 s workflow task printed it with ``CORE_LOG_FILTER`` and nothing
with ERROR alone.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.temporal.client as client_mod
import services.temporal.worker as worker_mod


def _record_runtimes(monkeypatch, module) -> list:
    """Replace the runtime constructors with recorders of the Core filter."""
    built: list = []
    monkeypatch.setattr(module, "LoggingConfig", lambda filter: filter)
    monkeypatch.setattr(module, "TelemetryConfig", lambda logging: logging)
    monkeypatch.setattr(
        module,
        "Runtime",
        lambda telemetry, worker_heartbeat_interval: built.append(telemetry) or MagicMock(),
    )
    return built


def test_the_filter_keeps_core_errors_and_the_slow_task_warning():
    directives = client_mod.CORE_LOG_FILTER.split(",")
    assert directives[0] == "ERROR"
    assert "temporalio_sdk_core::worker::workflow::managed_run=WARN" in directives


def test_the_standalone_worker_runtime_uses_the_filter(monkeypatch):
    built = _record_runtimes(monkeypatch, worker_mod)
    worker_mod.create_runtime()
    assert built == [client_mod.CORE_LOG_FILTER]


@pytest.mark.asyncio
async def test_the_client_runtime_uses_the_filter(monkeypatch):
    built = _record_runtimes(monkeypatch, client_mod)
    fake_client_class = MagicMock()
    fake_client_class.connect = AsyncMock(side_effect=RuntimeError("no server"))
    monkeypatch.setattr(client_mod, "Client", fake_client_class)

    wrapper = client_mod.TemporalClientWrapper("localhost:5681", "default")
    assert await wrapper.connect(retries=1, delay=0) is None
    assert built == [client_mod.CORE_LOG_FILTER]
