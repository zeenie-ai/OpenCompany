"""The price list (``config/pricing.json``) prices what the plugins book.

An action with no ``operation_map`` entry is priced at zero without an error,
so each name a plugin passes to ``record_api_usage`` is checked here.
"""

from typing import get_args

import pytest

from nodes.twitter.twitter_send import TwitterSendParams


@pytest.mark.parametrize("action", get_args(TwitterSendParams.model_fields["action"].annotation))
def test_every_action_twitter_send_books_has_a_price(real_prices, action):
    assert real_prices.calculate_api_cost("twitter", action)["total_cost"] > 0


@pytest.mark.parametrize(
    "service, action",
    [("serper", "web_search"), ("brave_search", "web_search"), ("perplexity", "sonar_search")],
)
def test_a_web_search_has_a_price(real_prices, service, action):
    assert real_prices.calculate_api_cost(service, action)["total_cost"] > 0


@pytest.mark.parametrize("service", ["typesafe", "openrouter"])
def test_a_judgment_is_priced_per_input_token(real_prices, service):
    """Jev charges $0.042 per million input tokens; output is free."""
    assert real_prices.calculate_api_cost(service, "judge", 1_000_000)["total_cost"] == pytest.approx(0.042)
