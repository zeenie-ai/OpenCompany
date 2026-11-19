"""The usage summaries the Credentials panels read."""

from types import SimpleNamespace

from services.usage import api_usage_summary, provider_usage_summary
from tests.services.usage._helpers import add_api_call, add_model_call, unavailable

BROKEN_DATABASE = SimpleNamespace(get_session=unavailable)


async def test_provider_summary_adds_up_each_model_and_the_provider(database):
    await add_model_call(
        database, model="gpt-a", input_tokens=100, output_tokens=20, total_tokens=120,
        input_cost=0.001, output_cost=0.002, cache_cost=0.0005, total_cost=0.0035,
    )
    await add_model_call(
        database, model="gpt-a", input_tokens=50, output_tokens=10, total_tokens=60,
        input_cost=0.0005, output_cost=0.001, total_cost=0.0015,
    )
    await add_model_call(
        database, model="gpt-b", input_tokens=10, output_tokens=1, total_tokens=11,
        input_cost=0.01, output_cost=0.02, total_cost=0.03,
    )

    summary = await provider_usage_summary(database)

    assert summary == [
        {
            "provider": "openai",
            "total_input_tokens": 160,
            "total_output_tokens": 31,
            "total_tokens": 191,
            "total_input_cost": 0.0115,
            "total_output_cost": 0.023,
            "total_cache_cost": 0.0005,
            "total_cost": 0.035,
            "execution_count": 3,
            "models": [
                {
                    "model": "gpt-a", "input_tokens": 150, "output_tokens": 30, "total_tokens": 180,
                    "input_cost": 0.0015, "output_cost": 0.003, "cache_cost": 0.0005, "total_cost": 0.005,
                    "execution_count": 2,
                },
                {
                    "model": "gpt-b", "input_tokens": 10, "output_tokens": 1, "total_tokens": 11,
                    "input_cost": 0.01, "output_cost": 0.02, "cache_cost": 0.0, "total_cost": 0.03,
                    "execution_count": 1,
                },
            ],
        }
    ]


async def test_provider_summary_lists_providers_and_models_by_name(database):
    await add_model_call(database, provider="openrouter", model="z-model")
    await add_model_call(database, provider="anthropic", model="claude-b")
    await add_model_call(database, provider="anthropic", model="claude-a")

    summary = await provider_usage_summary(database)

    assert [entry["provider"] for entry in summary] == ["anthropic", "openrouter"]
    assert [model["model"] for model in summary[0]["models"]] == ["claude-a", "claude-b"]


async def test_provider_summary_rounds_costs_to_six_places(database):
    for _ in range(3):
        await add_model_call(database, total_cost=0.0000004)

    summary = await provider_usage_summary(database)

    assert summary[0]["total_cost"] == 0.000001
    assert summary[0]["models"][0]["total_cost"] == 0.000001


async def test_provider_summary_of_an_empty_table_is_empty(database):
    assert await provider_usage_summary(database) == []


async def test_provider_summary_is_empty_when_the_read_fails():
    assert await provider_usage_summary(BROKEN_DATABASE) == []


async def test_api_summary_adds_up_each_operation_and_the_service(database):
    await add_api_call(database, operation="content_create", resource_count=1, cost=0.01)
    await add_api_call(database, operation="content_create", resource_count=1, cost=0.01)
    await add_api_call(database, operation="posts_read", endpoint="search", resource_count=20, cost=0.1)

    summary = await api_usage_summary(database)

    assert summary == [
        {
            "service": "twitter",
            "total_resources": 22,
            "total_cost": 0.12,
            "execution_count": 3,
            "operations": [
                {"operation": "content_create", "resource_count": 2, "total_cost": 0.02, "execution_count": 2},
                {"operation": "posts_read", "resource_count": 20, "total_cost": 0.1, "execution_count": 1},
            ],
        }
    ]


async def test_api_summary_lists_services_by_name(database):
    await add_api_call(database, service="twitter")
    await add_api_call(database, service="google_maps", operation="geocode", endpoint="geocode")

    summary = await api_usage_summary(database)

    assert [entry["service"] for entry in summary] == ["google_maps", "twitter"]


async def test_api_summary_keeps_one_service_when_asked(database):
    await add_api_call(database, service="twitter")
    await add_api_call(database, service="google_maps", operation="geocode", endpoint="geocode")

    summary = await api_usage_summary(database, "google_maps")

    assert [entry["service"] for entry in summary] == ["google_maps"]


async def test_api_summary_of_an_empty_table_is_empty(database):
    assert await api_usage_summary(database) == []


async def test_api_summary_is_empty_when_the_read_fails():
    assert await api_usage_summary(BROKEN_DATABASE) == []
