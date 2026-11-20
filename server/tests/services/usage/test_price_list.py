"""The price list (``config/pricing.json``) prices what the plugins book.

An action with no ``operation_map`` entry is priced at zero without an error,
so each name a plugin passes to ``record_api_usage`` is checked here.
"""

import pytest


@pytest.mark.parametrize(
    "service, action",
    [("serper", "web_search"), ("brave_search", "web_search"), ("perplexity", "sonar_search")],
)
def test_a_web_search_has_a_price(real_prices, service, action):
    assert real_prices.calculate_api_cost(service, action)["total_cost"] > 0
