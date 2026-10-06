from tests.nodes._agent_builder_harness import (
    agents_graph, builder_fixture, call, ctx, database_fixture, save_graph,  # noqa: F401 - pytest fixtures
)
from nodes.tool import agent_builder as ab


async def test_inspection_exposes_contract_and_docs_without_saved_secrets(builder, database):
    graph = agents_graph()
    await save_graph(database, graph, {"7:aiAgent:1": {"api_key": "PRIVATE-KEY", "model": "test-model"}})
    result = await call("inspect_node", ctx(), node_id="7:aiAgent:1")
    contract = result.contract
    assert contract["type"] == "aiAgent"
    assert "model" in contract["parameters_schema"]["properties"]
    assert contract["metadata"]["handles"]
    assert contract["documentation_ids"]
    assert contract["examples"]
    assert "PRIVATE-KEY" not in result.model_dump_json()


async def test_search_and_read_are_bounded_and_only_manifest_ids(builder, database):
    await save_graph(database, agents_graph())
    found = await call("search_docs", ctx(), query="task manager", limit=2)
    assert len(found.documents) == 2
    result = await call("read_doc", ctx(), document_id=found.documents[0]["id"], max_chars=20)
    assert len(result.document["content"]) == 20
    invalid = await call("read_doc", ctx(), document_id="../../.env")
    assert invalid.operations == []
    assert "manifest ID" in invalid.summary


async def test_specialist_inherits_model_and_gets_scoped_tools(builder, database):
    from tests.nodes._agent_builder_harness import node, saved

    lead = "7:orchestrator_agent:1"
    await save_graph(database, {"nodes": [node(lead, "orchestrator_agent", "Lead")], "edges": []},
                     {lead: {"provider": "openai", "model": "configured-model"}})
    result = await call("add_subagent", ctx(lead), agent_type="web_agent", purpose="Research sources and return citations for review", tool_types=["httpRequest"], tool_parameters={"httpRequest": {"url": "https://example.com"}})
    assert result.operations, result.summary
    graph = await saved(database)
    agent = result.operations[0]["minted_id"]
    params = await database.get_node_parameters(agent)
    assert params["model"] == "configured-model"
    assert "Research sources" in params["system_message"]
    assert any(n["type"] == "context" and n["data"]["agentNodeId"] == agent for n in graph["nodes"])
    assert any(e["target"] == agent and e["targetHandle"] == "input-tools" for e in graph["edges"])
    assert not any(n["type"] == "taskManager" for n in graph["nodes"])
    assert not any(op.get("node_type") == "httpRequest" for op in result.operations)


async def test_public_app_message_cannot_expand_employee_access(builder, database):
    from tests.nodes.test_agent_builder_employee import employee_graph, hire, talk
    from tests.nodes._agent_builder_harness import saved

    graph = employee_graph()
    await save_graph(database, graph)
    await hire(database)
    result = await call("add_tool", talk(), node_type="duckduckgoSearch")
    assert result.activation_state == "blocked"
    assert result.required_access == []
    assert "owner in Talk" in result.summary
    assert await saved(database) == graph


async def test_owner_reviews_scoped_access_before_mutation(builder, database, monkeypatch):
    from services.employees.permissions import decide_access
    from tests.nodes.test_agent_builder_employee import employee_graph, hire, talk
    from tests.nodes._agent_builder_harness import saved

    async def trusted(*args):
        return True

    monkeypatch.setattr("services.employees.permissions.trusted_owner_request", trusted)
    graph = employee_graph()
    await save_graph(database, graph)
    await hire(database)
    first = await call("add_tool", talk(), node_type="duckduckgoSearch")
    assert first.activation_state == "blocked"
    assert len(first.required_access) == 1  # only the selected Talk member
    assert await saved(database) == graph
    for request in first.required_access:
        assert not await decide_access(database, request["request_id"], "another-owner", True)
        assert await decide_access(database, request["request_id"], "owner", True)
    second = await call("add_tool", talk(), node_type="duckduckgoSearch")
    assert second.activation_state == "saved"
    assert len([node for node in (await saved(database))["nodes"] if node["type"] == "duckduckgoSearch"]) == 1


async def test_plan_does_not_save_graph_and_apply_uses_same_configuration(builder, database):
    from tests.nodes._agent_builder_harness import saved

    graph = agents_graph()
    await save_graph(database, graph)
    plan = await call("plan_update", ctx(), change_operation="add_tool", node_type="calculatorTool")
    assert plan.activation_state == "planned"
    assert plan.validation_issues == []
    assert await saved(database) == graph
    applied = await call("apply_update", ctx(), change_operation="add_tool", node_type="calculatorTool")
    assert applied.activation_state == "saved"
    assert any(node["type"] == "calculatorTool" for node in (await saved(database))["nodes"])


async def test_revocation_between_preflight_and_commit_rolls_back(builder, database, monkeypatch):
    from services.employees.permissions import decide_access
    from tests.nodes.test_agent_builder_employee import employee_graph, hire, talk
    from tests.nodes._agent_builder_harness import saved

    async def trusted(*args):
        return True

    monkeypatch.setattr("services.employees.permissions.trusted_owner_request", trusted)
    graph = employee_graph()
    await save_graph(database, graph)
    await hire(database)
    review = await call("add_tool", talk(), node_type="duckduckgoSearch")
    for access in review.required_access:
        await decide_access(database, access["request_id"], "owner", True)
    original_save = ab._save

    async def revoke_before_commit(*args, **kwargs):
        await decide_access(database, review.required_access[0]["request_id"], "owner", False)
        return await original_save(*args, **kwargs)

    monkeypatch.setattr(ab, "_save", revoke_before_commit)
    result = await call("add_tool", talk(), node_type="duckduckgoSearch")
    assert "permission changed" in result.summary
    assert await saved(database) == graph
    assert await database.get_node_parameters("7:duckduckgoSearch:1") is None


async def test_apply_saved_uses_owner_control_and_keeps_stop_explicit(builder, database, monkeypatch):
    from unittest.mock import AsyncMock

    await save_graph(database, agents_graph())
    trusted = AsyncMock(return_value=True)
    apply = AsyncMock(return_value={"success": True, "activation_state": "waiting"})
    monkeypatch.setattr("services.employees.permissions.trusted_owner_request", trusted)
    monkeypatch.setattr("services.employees.safe_apply.apply_saved_changes", apply)
    result = await call("apply_update", ctx(), change_operation="apply_saved")
    assert result.activation_state == "waiting"
    assert apply.await_args.kwargs["owner_id"] == "owner"
    assert apply.await_args.kwargs["stop_work"] is False


async def test_approved_specialist_commits_team_metadata_and_linked_access(builder, database, monkeypatch):
    from services.employees import store
    from services.employees.permissions import decide_access
    from models.employees import EmployeeGrant
    from sqlmodel import select
    from tests.nodes._agent_builder_harness import node

    lead = "7:ai_employee:1"
    await save_graph(database, {"nodes": [node(lead, "ai_employee", "Lead")], "edges": []}, {lead: {"provider": "openai", "model": "configured-model"}})
    row, _ = await store.reserve(database, owner_id="owner", idempotency_key="team-test", payload_hash="team-hash", fields={"role": "Researcher", "team_plan": {"version": 1, "members": []}})
    await store.mark_ready(database, row.id, workflow_id="7", node_roles={"agent": lead})

    async def trusted(*args):
        return True

    monkeypatch.setattr("services.employees.permissions.trusted_owner_request", trusted)
    config = {"agent_type": "web_agent", "purpose": "Research evidence", "tool_types": ["httpRequest"], "tool_parameters": {"httpRequest": {"url": "https://example.com"}}}
    review = await call("add_subagent", ctx(lead), **config)
    assert len(review.required_access) == 1
    request_id = review.required_access[0]["request_id"]
    await decide_access(database, request_id, "owner", True)
    result = await call("add_subagent", ctx(lead), **config)
    assert result.saved_revision
    employee = await store.get_by_workflow(database, "7")
    [member] = employee.team_plan["members"]
    assert employee.node_roles["specialist_1"] == member["node_id"]
    assert employee.node_roles["specialist_1_context"] == member["context_node_id"]
    assert len(member["tools"]) == 1
    async with database.get_session() as session:
        grants = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.member_id == member["node_id"]))).scalars().all()
    assert {grant.capability for grant in grants} == {"web_agent", "httpRequest"}
    assert all(grant.limits["parent_grant_id"] == request_id for grant in grants)
