"""The owner's Generated UI switch (services/genui/switch.py): on only for a
real True, off when the settings row is missing or cannot be read, and an
agent holds the Show UI tool only while it is on."""

from __future__ import annotations

import pytest

from services.genui.switch import CHAT_UI_TYPE, drop_chat_ui_when_off, generated_ui_enabled, generated_ui_on


class Database:
    """A settings row and empty node parameters; counts the settings reads."""

    def __init__(self, row):
        self.row = row
        self.reads = 0

    async def get_user_settings(self, user_id="default"):
        self.reads += 1
        return self.row

    async def get_node_parameters(self, node_id):
        return {}


class Broken:
    async def get_user_settings(self, user_id="default"):
        raise RuntimeError("database down")


SHOW_UI = {"node_id": "ui", "node_type": CHAT_UI_TYPE, "parameters": {}, "label": "Show UI"}
SEARCH = {"node_id": "search", "node_type": "duckduckgoSearch", "parameters": {}, "label": "Search"}


@pytest.mark.parametrize(
    ("row", "on"),
    [(None, False), ({}, False), ({"generated_ui": False}, False), ({"generated_ui": "true"}, False), ({"generated_ui": 1}, False), ({"generated_ui": True}, True)],
)
def test_only_a_real_true_turns_it_on(row, on):
    assert generated_ui_on(row) is on


async def test_a_settings_row_that_cannot_be_read_means_off():
    assert await generated_ui_enabled(Broken()) is False
    assert await generated_ui_enabled(Database({"generated_ui": True})) is True


async def test_show_ui_is_left_out_while_the_switch_is_off():
    assert await drop_chat_ui_when_off([SEARCH, SHOW_UI], Database({"generated_ui": False})) == [SEARCH]
    tools = [SEARCH, SHOW_UI]
    assert await drop_chat_ui_when_off(tools, Database({"generated_ui": True})) is tools


async def test_the_switch_is_read_only_when_show_ui_is_there():
    database = Database({"generated_ui": False})
    tools = [SEARCH]
    assert await drop_chat_ui_when_off(tools, database) is tools
    assert database.reads == 0


async def test_an_agent_holds_show_ui_only_while_the_switch_is_on():
    from services.plugin.edge_walker import collect_agent_connections

    nodes = [
        {"id": "agent", "type": "aiAgent", "data": {"label": "Maya"}},
        {"id": "ui", "type": CHAT_UI_TYPE, "data": {"label": "Show UI"}},
        {"id": "search", "type": "duckduckgoSearch", "data": {"label": "Search"}},
    ]
    edges = [
        {"source": "ui", "target": "agent", "targetHandle": "input-tools"},
        {"source": "search", "target": "agent", "targetHandle": "input-tools"},
    ]
    context = {"nodes": nodes, "edges": edges, "workflow_id": "wf"}
    for row, kept in (({"generated_ui": True}, [CHAT_UI_TYPE, "duckduckgoSearch"]), (None, ["duckduckgoSearch"])):
        _, _, tools, _, _ = await collect_agent_connections("agent", context, Database(row))
        assert [tool["node_type"] for tool in tools] == kept


def _child_with_show_ui(lead_type, handle):
    """A lead with one child agent that holds Show UI and a search tool."""
    nodes = [
        {"id": "lead", "type": lead_type, "data": {"label": "Lead"}},
        {"id": "child", "type": "aiAgent", "data": {"label": "Helper"}},
        {"id": "ui", "type": CHAT_UI_TYPE, "data": {"label": "Show UI"}},
        {"id": "search", "type": "duckduckgoSearch", "data": {"label": "Search"}},
    ]
    edges = [
        {"source": "child", "target": "lead", "targetHandle": handle},
        {"source": "ui", "target": "child", "targetHandle": "input-tools"},
        {"source": "search", "target": "child", "targetHandle": "input-tools"},
    ]
    return {"nodes": nodes, "edges": edges, "workflow_id": "wf"}


async def test_an_agent_tools_child_names_show_ui_only_while_the_switch_is_on():
    from services.plugin.edge_walker import collect_agent_connections

    context = _child_with_show_ui("aiAgent", "input-tools")
    for row, listed in (({"generated_ui": True}, [CHAT_UI_TYPE, "duckduckgoSearch"]), (None, ["duckduckgoSearch"])):
        _, _, [child], _, _ = await collect_agent_connections("lead", context, Database(row))
        assert [tool["node_type"] for tool in child["child_tools"]] == listed


async def test_a_team_lead_roster_names_show_ui_only_while_the_switch_is_on():
    from services.plugin.edge_walker import collect_teammate_connections

    context = _child_with_show_ui("orchestrator_agent", "input-teammates")
    [on] = await collect_teammate_connections("lead", context, Database({"generated_ui": True}))
    [off] = await collect_teammate_connections("lead", context, Database(None))
    assert [tool["node_type"] for tool in on["child_tools"]] == [CHAT_UI_TYPE, "duckduckgoSearch"]
    assert [tool["node_type"] for tool in off["child_tools"]] == ["duckduckgoSearch"]
    assert "Show UI" in on["capabilities"] and "Show UI" not in off["capabilities"]
