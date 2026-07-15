from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import ai as ai_module
from services.ai import AIService
from services.tool_identity import (
    DuplicateToolNameError,
    duplicate_tool_name_error,
    ensure_unique_tool_names,
)


def test_duplicate_tool_error_is_deterministic_and_structured():
    identities = [
        {"name": "search", "node_id": "node-b", "label": "Second"},
        {"name": "clock", "node_id": "clock-1", "label": "Clock"},
        {"name": "search", "node_id": "node-a", "label": "First"},
    ]

    error = duplicate_tool_name_error(identities)

    assert isinstance(error, DuplicateToolNameError)
    assert "'search': First (node-a), Second (node-b)" in str(error)
    assert error.as_dict()["error_type"] == "DuplicateToolNameError"
    assert [item["node_id"] for item in error.conflicts["search"]] == [
        "node-a",
        "node-b",
    ]


def test_unique_names_are_accepted():
    ensure_unique_tool_names(
        [
            {"name": "search", "node_id": "node-a"},
            {"name": "clock", "node_id": "node-b"},
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name,node_type", [("execute_agent", "aiAgent"), ("execute_chat_agent", "chatAgent")])
async def test_agent_surfaces_reject_duplicates_before_model_invocation(
    monkeypatch,
    method_name,
    node_type,
):
    service = object.__new__(AIService)
    service.auth = SimpleNamespace(get_api_key=AsyncMock(return_value=None))
    service.database = SimpleNamespace()
    fake_model = SimpleNamespace(ainvoke=AsyncMock())
    service.create_model = MagicMock(return_value=fake_model)

    async def build_tool(tool_info):
        return (
            SimpleNamespace(name="shared_name"),
            {
                "node_id": tool_info["node_id"],
                "node_type": tool_info["node_type"],
                "label": tool_info["label"],
            },
        )

    service._build_tool_from_node = AsyncMock(side_effect=build_tool)
    monkeypatch.setattr(ai_module, "is_model_valid_for_provider", lambda model, provider: True)
    monkeypatch.setattr(ai_module, "_resolve_max_tokens", lambda *args, **kwargs: 1024)
    monkeypatch.setattr(ai_module, "_resolve_temperature", lambda *args, **kwargs: 0.2)

    result = await getattr(service, method_name)(
        "agent-1",
        {
            "prompt": "hello",
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
        },
        tool_data=[
            {"node_id": "tool-a", "node_type": "firstTool", "label": "First"},
            {"node_id": "tool-b", "node_type": "secondTool", "label": "Second"},
        ],
    )

    assert result["success"] is False
    assert result["node_type"] == node_type
    assert result.get("error_type") == "DuplicateToolNameError", result
    assert [entry["node_id"] for entry in result["conflicts"]["shared_name"]] == [
        "tool-a",
        "tool-b",
    ]
    fake_model.ainvoke.assert_not_awaited()
