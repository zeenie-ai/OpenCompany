"""Readiness gate + transient-error classifier in TemporalClientWrapper.

Locks the two startup-resilience contracts added to
``services/temporal/client.py``:

  - ``connect()`` polls the WorkflowService gRPC health check
    (``service_client.check_health``) until SERVING before returning the
    client, so "connected" means "frontend serving" — the documented
    readiness probe. A never-SERVING server yields ``None`` (folds into
    main.py's reconnect loop) instead of a half-ready client.
  - ``_is_transient_visibility_error`` classifies the shard-warmup /
    visibility races that the sweep retry (test_terminate_sweep) keys on.

The conftest stubs ``core.config.Settings`` as a MagicMock, so the
resilience knobs are injected directly on the instance (real ints/floats)
rather than read from env.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.temporal.client as client_mod
from services.temporal.client import (
    TemporalClientWrapper,
    _is_transient_visibility_error,
)


def _make_wrapper(*, attempts: int = 5, delay: float = 0.0, timeout: float = 1.0) -> TemporalClientWrapper:
    w = TemporalClientWrapper("localhost:7233", "default")
    # Override the MagicMock values the stubbed Settings produced.
    w._health_check_attempts = attempts
    w._health_check_delay_seconds = delay
    w._health_check_timeout_seconds = timeout
    return w


def _fake_client(health) -> MagicMock:
    """A fake Temporal client whose health check is scripted.

    ``health`` is either a bool (constant) or a list of bools consumed
    one per ``check_health`` call.
    """
    c = MagicMock()
    if isinstance(health, list):
        c.service_client.check_health = AsyncMock(side_effect=health)
    else:
        c.service_client.check_health = AsyncMock(return_value=health)
    c.service_client.workflow_service.describe_namespace = AsyncMock()
    return c


def _patch_connect(monkeypatch, fake_client: MagicMock) -> None:
    monkeypatch.setattr(client_mod, "Runtime", MagicMock())
    fake_Client = MagicMock()
    fake_Client.connect = AsyncMock(return_value=fake_client)
    monkeypatch.setattr(client_mod, "Client", fake_Client)
    # Reached only after the gate passes; keep it a no-op.
    monkeypatch.setattr(
        "services.temporal.search_attributes.register_search_attributes",
        AsyncMock(),
    )


async def test_connect_returns_client_when_serving_first(monkeypatch):
    fc = _fake_client(True)
    _patch_connect(monkeypatch, fc)
    w = _make_wrapper()

    result = await w.connect(retries=1, delay=0)

    assert result is fc
    assert fc.service_client.check_health.await_count == 1
    connect_kwargs = client_mod.Client.connect.await_args.kwargs
    assert len(connect_kwargs["interceptors"]) == 1
    assert connect_kwargs["interceptors"][0].__class__.__name__ == "TracingInterceptor"


async def test_connect_polls_until_serving(monkeypatch):
    fc = _fake_client([False, False, False, False, True])
    _patch_connect(monkeypatch, fc)
    w = _make_wrapper(attempts=5, delay=0.0)

    result = await w.connect(retries=1, delay=0)

    assert result is fc
    assert fc.service_client.check_health.await_count == 5


async def test_connect_returns_none_when_never_serving(monkeypatch):
    fc = _fake_client(False)
    _patch_connect(monkeypatch, fc)
    w = _make_wrapper(attempts=5, delay=0.0)

    result = await w.connect(retries=1, delay=0)

    assert result is None
    assert w.is_connected is False
    assert fc.service_client.check_health.await_count == 5


def test_is_transient_visibility_error_status_codes():
    from temporalio.service import RPCError, RPCStatusCode

    assert _is_transient_visibility_error(RPCError("x", RPCStatusCode.UNAVAILABLE, b"")) is True
    assert _is_transient_visibility_error(RPCError("x", RPCStatusCode.DEADLINE_EXCEEDED, b"")) is True
    assert _is_transient_visibility_error(RPCError("x", RPCStatusCode.INVALID_ARGUMENT, b"")) is False


def test_is_transient_visibility_error_substrings():
    assert _is_transient_visibility_error(Exception('error="shard status unknown"')) is True
    assert _is_transient_visibility_error(Exception("frontend temporarily unavailable")) is True
    assert _is_transient_visibility_error(Exception("context canceled")) is True
    assert _is_transient_visibility_error(Exception("genuine bug: KeyError 'x'")) is False
