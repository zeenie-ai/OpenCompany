"""``is_hire_allowed``: which node types Normal mode's Hire may build.

``enabled_nodes`` governs Hire; the absolute blocklists (exact type or any
of the node's groups) always win; an empty list allows everything not
blocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services import node_registry
from services.node_allowlist import CONFIG_PATH, NodeAllowlistService


def _service(tmp_path: Path, **config) -> NodeAllowlistService:
    path = tmp_path / "node_allowlist.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return NodeAllowlistService(config_path=path)


@pytest.fixture()
def grouped_types(monkeypatch):
    monkeypatch.setitem(node_registry.NODE_METADATA, "fakeSend", {"group": ["social", "tool"]})
    monkeypatch.setitem(node_registry.NODE_METADATA, "fakeAgent", {"group": ["agent"]})


def test_allows_listed_types_only(tmp_path, grouped_types):
    service = _service(tmp_path, enabled_nodes=["fakeAgent"])
    assert service.is_hire_allowed("fakeAgent") is True
    assert service.is_hire_allowed("fakeSend") is False


def test_an_empty_list_allows_everything_not_blocked(tmp_path, grouped_types):
    service = _service(tmp_path, enabled_nodes=[], disabled_nodes=["fakeAgent"])
    assert service.is_hire_allowed("fakeSend") is True
    assert service.is_hire_allowed("fakeAgent") is False


def test_a_blocked_group_wins_over_the_allowlist_on_any_group(tmp_path, grouped_types):
    service = _service(tmp_path, enabled_nodes=["fakeSend", "fakeAgent"], disabled_groups=["tool"])
    assert service.is_hire_allowed("fakeSend") is False
    assert service.is_hire_allowed("fakeAgent") is True


def test_a_missing_config_allows_everything(tmp_path, grouped_types):
    service = NodeAllowlistService(config_path=tmp_path / "missing.json")
    assert service.is_hire_allowed("fakeSend") is True


def test_the_shipped_config_allows_the_v1_employee_building_blocks():
    service = NodeAllowlistService(config_path=CONFIG_PATH)
    for node_type in (
        "aiAgent",
        "chatTrigger",
        "cronScheduler",
        "console",
        "context",
        "canvas",
        "masterSkill",
        "simpleMemory",
        "writeTodos",
        "currentTimeTool",
        "duckduckgoSearch",
        "approvalGate",
        "whatsappReceive",
        "whatsappSend",
        "whatsappBusinessReceive",
        "whatsappBusinessSend",
        "telegramReceive",
        "telegramSend",
        "discordReceive",
        "discordSend",
        "googleGmail",
        "googleGmailReceive",
        "googleCalendar",
        "googleDrive",
        "googleSheets",
        "emailReceive",
        "emailSend",
        "msMail",
        "msMailReceive",
        "msCalendar",
        "stripeAction",
    ):
        assert service.is_hire_allowed(node_type), node_type
