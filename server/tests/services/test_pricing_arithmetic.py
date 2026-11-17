"""The cost of one LLM call, whatever shape the provider's usage takes.

``total_tokens`` is the whole prompt (cache reads and writes included) plus
the output on every provider, while ``input_tokens`` counts cached tokens on
OpenAI-shaped usage but not on Anthropic's. ``calculate_cost`` prices the
uncached input from the total, so the same call costs the same either way.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture
def pricing_module():
    # tests/conftest.py stubs services.pricing; load the real module.
    spec = importlib.util.spec_from_file_location("_real_pricing_arithmetic", SERVER_DIR / "services" / "pricing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pricing(pricing_module):
    return pricing_module.PricingService()


def _parts_add_up(cost: dict) -> bool:
    parts = cost["input_cost"] + cost["output_cost"] + cost["cache_cost"] + cost["reasoning_cost"]
    return abs(parts - cost["total_cost"]) < 1e-6


def test_the_same_call_costs_the_same_in_both_usage_shapes(pricing):
    # 100 uncached, 900 read from cache, 50 written to it, 20 out.
    anthropic_shaped = pricing.calculate_cost(
        "anthropic", "claude-sonnet-5-5",
        input_tokens=100, output_tokens=20, cache_read_tokens=900, cache_creation_tokens=50, total_tokens=1070,
    )
    openai_shaped = pricing.calculate_cost(
        "anthropic", "claude-sonnet-5-5",
        input_tokens=1050, output_tokens=20, cache_read_tokens=900, cache_creation_tokens=50, total_tokens=1070,
    )

    assert anthropic_shaped == openai_shaped
    assert _parts_add_up(anthropic_shaped)


def test_cache_reads_and_writes_take_their_own_prices(pricing):
    # claude-sonnet-5-5: $2 input, $10 output, $0.10 cache read, $2.50 cache write.
    million = 1_000_000
    cost = pricing.calculate_cost(
        "anthropic", "claude-sonnet-5-5",
        input_tokens=million, output_tokens=million, cache_read_tokens=million,
        cache_creation_tokens=million, total_tokens=4 * million,
    )

    assert cost["input_cost"] == pytest.approx(2.0)
    assert cost["output_cost"] == pytest.approx(10.0)
    assert cost["cache_cost"] == pytest.approx(0.10 + 2.50)
    assert cost["total_cost"] == pytest.approx(14.60)


def test_a_model_without_cache_prices_charges_cached_tokens_at_the_input_price(pricing, pricing_module, monkeypatch):
    monkeypatch.setattr(pricing, "get_pricing", lambda provider, model: pricing_module.ModelPricing(2.0, 10.0))

    cost = pricing.calculate_cost(
        "some", "model",
        input_tokens=1_000_000, output_tokens=0, cache_read_tokens=500_000,
        cache_creation_tokens=500_000, total_tokens=1_000_000,
    )

    # Nothing is uncached; the whole prompt is charged once, at the input price.
    assert cost["input_cost"] == 0
    assert cost["cache_cost"] == pytest.approx(2.0)
    assert cost["total_cost"] == pytest.approx(2.0)


def test_without_a_total_the_input_is_taken_as_uncached(pricing):
    cost = pricing.calculate_cost(
        "anthropic", "claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=0, cache_read_tokens=1_000_000,
    )

    assert cost["input_cost"] == pytest.approx(2.0)
    assert cost["cache_cost"] == pytest.approx(0.10)


def test_the_uncached_input_never_goes_below_zero(pricing):
    cost = pricing.calculate_cost(
        "anthropic", "claude-sonnet-5-5",
        input_tokens=10, output_tokens=10, cache_read_tokens=500, total_tokens=20,
    )

    assert cost["input_cost"] == 0
    assert _parts_add_up(cost)
