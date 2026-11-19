"""The usage commands: served by the usage package, for the owner's sockets."""

from pathlib import Path

import pytest

from services.authz.ws_surface import INTERNAL_SOCKET_HANDLERS
from services.usage import handlers
from services.ws_handler_registry import get_ws_handlers
from tests.services.usage._helpers import add_api_call, add_model_call

COMMANDS = ("get_provider_usage_summary", "get_api_usage_summary")
MAIN_PY = Path(__file__).resolve().parents[3] / "main.py"


@pytest.fixture
def served(monkeypatch, database):
    """The commands reading the test database."""
    monkeypatch.setattr(handlers, "_database", lambda: database)
    return database


@pytest.mark.parametrize("command", COMMANDS)
def test_a_command_is_served_by_this_package(command):
    assert get_ws_handlers()[command] is handlers.WS_HANDLERS[command]


@pytest.mark.parametrize("command", COMMANDS)
def test_a_command_is_not_reachable_on_the_internal_socket(command):
    assert command not in INTERNAL_SOCKET_HANDLERS


def test_main_imports_the_handlers():
    assert "import services.usage.handlers" in MAIN_PY.read_text(encoding="utf-8")


async def test_provider_usage_answers_the_providers(served):
    await add_model_call(served, provider="anthropic", model="claude-a", total_cost=0.5)

    reply = await handlers.handle_get_provider_usage_summary({}, None)

    assert reply["success"] is True
    assert [entry["provider"] for entry in reply["providers"]] == ["anthropic"]
    assert reply["providers"][0]["total_cost"] == 0.5


async def test_api_usage_answers_every_service(served):
    await add_api_call(served, service="twitter")
    await add_api_call(served, service="google_maps", operation="geocode", endpoint="geocode")

    reply = await handlers.handle_get_api_usage_summary({}, None)

    assert reply["success"] is True
    assert [entry["service"] for entry in reply["services"]] == ["google_maps", "twitter"]


async def test_api_usage_answers_one_service_when_asked(served):
    await add_api_call(served, service="twitter")
    await add_api_call(served, service="google_maps", operation="geocode", endpoint="geocode")

    reply = await handlers.handle_get_api_usage_summary({"service": "twitter"}, None)

    assert [entry["service"] for entry in reply["services"]] == ["twitter"]
