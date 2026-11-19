"""Fixtures for the usage tests: a real database on a throwaway file, and the
price service the recorders ask."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SERVER_DIR = Path(__file__).resolve().parents[3]


@pytest.fixture
def database(real_database):
    """The shared real database (``tests/database_fixtures.py``)."""
    return real_database


@pytest.fixture
def prices(monkeypatch):
    """A price service whose answers a test can set."""
    import services.pricing as pricing

    service = MagicMock()
    service.calculate_cost.return_value = {
        "input_cost": 0.01,
        "output_cost": 0.02,
        "cache_cost": 0.003,
        "reasoning_cost": 0.0,
        "total_cost": 0.033,
    }
    service.calculate_api_cost.return_value = {
        "operation": "content_create",
        "unit_cost": 0.01,
        "resource_count": 1,
        "total_cost": 0.01,
    }
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: service)
    return service


@pytest.fixture
def real_prices(monkeypatch):
    """The real price service, reading ``config/pricing.json``. The root
    conftest stubs ``services.pricing``, so the module is loaded from its file."""
    import services.pricing as pricing

    spec = importlib.util.spec_from_file_location("_real_pricing_for_usage_tests", SERVER_DIR / "services" / "pricing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service = module.PricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: service)
    return service
