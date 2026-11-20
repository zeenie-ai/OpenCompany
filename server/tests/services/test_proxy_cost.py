"""What a proxied request costs (``ProxyService.request_cost``)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.proxy.models import ProxyResult
from services.proxy.service import ProxyService

ONE_GB = 1024**3


@pytest.fixture
async def service():
    """A proxy service with one provider, ``brightdata``, at $8 per GB."""
    database = MagicMock()
    database.get_proxy_providers = AsyncMock(
        return_value=[{"name": "brightdata", "cost_per_gb": 8.0, "url_template": "{}", "geo_coverage": "[]"}]
    )
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value="credential")
    service = ProxyService(auth, database, SimpleNamespace(proxy_budget_daily_usd=None))
    await service.reload_providers()
    return service


async def test_a_request_costs_its_bytes_at_the_providers_price(service):
    assert service.request_cost("brightdata", ONE_GB) == 8.0


async def test_a_name_that_is_no_provider_costs_nothing(service):
    assert service.request_cost("auto", ONE_GB) == 0.0


async def test_a_reported_request_adds_its_cost_to_the_days_spend(service):
    service.report_result("brightdata", ProxyResult(success=True, bytes_transferred=ONE_GB // 2))

    assert service.get_stats()["daily_spend_usd"] == 4.0
