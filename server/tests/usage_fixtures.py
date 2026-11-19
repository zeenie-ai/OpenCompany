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
