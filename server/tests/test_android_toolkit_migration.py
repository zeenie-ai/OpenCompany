from services.workflow_migrations import normalize_legacy_android_toolkit


def _node(node_id, node_type):
    return {"id": node_id, "type": node_type, "data": {"label": node_id}}


def test_legacy_android_toolkit_becomes_direct_agent_tool_edges():
    nodes = [
        _node("battery", "batteryMonitor"),
        _node("location", "location"),
        _node("toolkit", "androidTool"),
        _node("android", "android_agent"),
        _node("custom", "aiAgent"),
    ]
    edges = [
        {"id": "a", "source": "battery", "target": "toolkit", "targetHandle": "input-main"},
        {"id": "b", "source": "location", "target": "toolkit", "target_handle": "input-main"},
        {"id": "c", "source": "toolkit", "target": "android", "targetHandle": "input-tools"},
        {"id": "d", "source": "toolkit", "target": "custom", "target_handle": "input-tools"},
    ]

    migrated_nodes, migrated_edges, params, warnings = normalize_legacy_android_toolkit(
        nodes, edges, {"toolkit": {"old": True}, "battery": {"action": "status"}}
    )

    assert "toolkit" not in {node["id"] for node in migrated_nodes}
    assert "toolkit" not in params
    assert params["battery"] == {"action": "status"}
    assert warnings == []
    assert {
        (edge["source"], edge["target"], edge["targetHandle"])
        for edge in migrated_edges
    } == {
        ("battery", "android", "input-tools"),
        ("battery", "custom", "input-tools"),
        ("location", "android", "input-tools"),
        ("location", "custom", "input-tools"),
    }


def test_migration_is_idempotent_and_deduplicates_direct_edges():
    nodes = [_node("battery", "batteryMonitor"), _node("toolkit", "androidTool"), _node("agent", "aiAgent")]
    edges = [
        {"source": "battery", "target": "toolkit"},
        {"source": "toolkit", "target": "agent", "targetHandle": "input-tools"},
        {"id": "direct", "source": "battery", "target": "agent", "targetHandle": "input-tools"},
    ]
    first = normalize_legacy_android_toolkit(nodes, edges)
    second = normalize_legacy_android_toolkit(first[0], first[1], first[2])

    assert first[:3] == second[:3]
    assert len(first[1]) == 1
    assert first[1][0]["id"] == "direct"


def test_orphan_toolkit_is_removed_with_warning_and_non_android_inputs_are_not_rewired():
    nodes = [_node("http", "httpRequest"), _node("toolkit", "androidTool")]
    edges = [{"source": "http", "target": "toolkit"}]

    migrated_nodes, migrated_edges, _, warnings = normalize_legacy_android_toolkit(nodes, edges)

    assert migrated_nodes == [nodes[0]]
    assert migrated_edges == []
    assert warnings and "without a valid destination agent" in warnings[0]
