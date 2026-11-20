"""Stand-ins for the usage recorders (``services/usage``).

A caller imports a recorder inside the function that calls it, so replacing
the name on the package is what the call reaches. A test then checks the call
with the mock's own assertions (``assert_awaited_once``, ``await_args.kwargs``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def llm_usage(monkeypatch):
    """``services.usage.record_llm_usage`` replaced by a mock that books
    nothing and returns a zero cost."""
    import services.usage
    from services.usage.recording import NO_COST

    recorder = AsyncMock(return_value=dict(NO_COST))
    monkeypatch.setattr(services.usage, "record_llm_usage", recorder)
    return recorder


@pytest.fixture
def api_usage(monkeypatch):
    """``services.usage.record_api_usage`` replaced by a mock that books
    nothing and returns a cost of 0.0."""
    import services.usage

    recorder = AsyncMock(return_value=0.0)
    monkeypatch.setattr(services.usage, "record_api_usage", recorder)
    return recorder


@pytest.fixture
def spend_limit(monkeypatch):
    """``services.usage.spend_limit_notice`` replaced by a mock: no chat run
    has reached its limit, until a test sets ``return_value`` to a notice."""
    import services.usage

    notice = AsyncMock(return_value=None)
    monkeypatch.setattr(services.usage, "spend_limit_notice", notice)
    return notice


@pytest.fixture
def monthly_budget(monkeypatch):
    """``services.usage.monthly_budget_reached`` replaced by a mock: no
    employee has reached its budget, until a test sets ``return_value`` to
    the budget."""
    import services.usage

    reached = AsyncMock(return_value=None)
    monkeypatch.setattr(services.usage, "monthly_budget_reached", reached)
    return reached
