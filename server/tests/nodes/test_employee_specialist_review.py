"""Approving Talk's team change finishes the exact reviewed Builder operation."""
from types import SimpleNamespace

import pytest

from services.chat.ledger import admit_message
from services.employees import store
from services.employees.handlers import handle_decide_employee_access
from services.employees.permissions import list_access
from services.employees.team_runtime import admit_team_plan
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_workflow_graph
from tests.nodes._agent_builder_harness import builder_fixture, database_fixture, call, ctx, save_graph, saved
from tests.services.employees.test_team_plan_admission import inbox_assistant, hierarchical_inbox_assistant


@pytest.fixture
async def team(builder, database, request):
    built = hierarchical_inbox_assistant() if getattr(request, "param", None) == "v3" else inbox_assistant()
    normalized = normalize_workflow_graph('7', built.nodes, built.edges, built.parameters)
    await save_graph(database, sanitize_workflow_graph({**normalized.graph_data(), 'owner_id': 'owner'}), normalized.node_parameters)
    row, _ = await store.reserve(database, owner_id='owner', idempotency_key='marina', payload_hash='case',
        fields={'role': 'Assistant', 'team_plan': built.team_plan})
    await store.mark_ready(database, row.id, workflow_id='7', node_roles=built.node_roles)
    admission = await admit_message(database, session_id='7', workflow_id='7', execution_id='generation-1',
        text='Add a coding specialist to build the site.', track=True)
    contact = ctx(built.node_roles['talk_agent'], call='add-coder', run_scope={'run_id': admission.run.run_id})
    contact.node_id = built.node_roles['builder']
    return SimpleNamespace(contact=contact, lead=built.node_roles['agent'],
        socket=SimpleNamespace(scope={'path': '/ws/status'}, state=SimpleNamespace(user_id='owner')))


@pytest.mark.parametrize("team", ["v3"], indirect=True)
async def test_approved_nested_manager_then_worker_keeps_exact_parent_and_review_board(database, team):
    proposed = await call("add_subagent", team.contact, agent_type="ai_employee", purpose="Review financial data")
    [grant] = proposed.required_access
    decision = {"access_request_id": grant["request_id"], "allow": True}
    assert (await handle_decide_employee_access(decision, team.socket))["activation_state"] == "saved"
    plan = (await store.get_by_workflow(database, "7")).team_plan
    manager = next(member for member in plan["members"] if member["node_type"] == "ai_employee")
    assert manager["parent_node_id"] == plan["orchestrator_node_id"]
    assert (await database.get_node_parameters(manager["task_trigger_node_id"]))["parent_node_id"] == manager["node_id"]
    contact = ctx(plan["talk_node_id"], call="add-analyst", run_scope=team.contact.raw["run_scope"])
    contact.node_id = team.contact.node_id
    proposed = await call("add_subagent", contact, agent_type="aiAgent", target_member_id=manager["node_id"], purpose="Check arithmetic")
    [grant] = proposed.required_access
    decision = {"access_request_id": grant["request_id"], "allow": True}
    assert (await handle_decide_employee_access(decision, team.socket))["activation_state"] == "saved"
    assert await handle_decide_employee_access(decision, team.socket) == {"success": True}
    graph = await saved(database)
    admitted = await admit_team_plan(database, "7", graph["nodes"], graph["edges"])
    plan = next(node for node in admitted if node["id"] == plan["talk_node_id"])["data"]["employee_team_plan"]
    assert [(m["node_type"], m["parent_node_id"]) for m in plan["members"] if m["node_type"] == "aiAgent"] == [("aiAgent", manager["node_id"])]


async def test_allow_adds_and_registers_specialist_without_another_chat_turn(database, team):
    graph = await saved(database)
    review = await call('add_subagent', team.contact, agent_type='coding_agent', purpose='Build the weather website')
    [grant] = review.required_access
    assert (await saved(database)) == graph
    assert any(r['id'] == grant['request_id'] and r['change_pending'] for r in await list_access(database, '7', 'owner'))
    allow = {'access_request_id': grant['request_id'], 'allow': True}
    assert (await handle_decide_employee_access(allow, SimpleNamespace(state=SimpleNamespace(user_id='someone-else')))) == {'success': False, 'error': 'not_found'}
    assert await handle_decide_employee_access(allow, team.socket) == {'success': True, 'activation_state': 'saved'}
    updated = await saved(database)
    [coder] = [n['id'] for n in updated['nodes'] if n['type'] == 'coding_agent']
    assert any(e['source'] == coder and e['target'] == team.lead and e['targetHandle'] == 'input-teammates' for e in updated['edges'])
    admitted = await admit_team_plan(database, '7', updated['nodes'], updated['edges'])
    plan = next(n for n in admitted if n['id'] == team.lead)['data']['employee_team_plan']
    member = next(m for m in plan['members'] if m['node_id'] == coder)
    types = {n['id']: n['type'] for n in updated['nodes']}
    assert {'piRead', 'piWrite', 'piEdit'} <= {types[t] for t in member['tools']}
    assert await handle_decide_employee_access(allow, team.socket) == {'success': True}
    assert (await saved(database)) == updated


async def test_refusal_and_disconnected_builder_keep_the_graph_unchanged(database, team):
    review = await call('add_subagent', team.contact, agent_type='coding_agent')
    [grant] = review.required_access
    decision = {'access_request_id': grant['request_id'], 'allow': False}
    assert await handle_decide_employee_access(decision, team.socket) == {'success': True}
    graph = await saved(database)
    assert all(n['type'] != 'coding_agent' for n in graph['nodes'])
    graph['edges'] = [e for e in graph['edges'] if e['source'] != team.contact.node_id]
    await save_graph(database, graph)
    result = await handle_decide_employee_access({**decision, 'allow': True}, team.socket)
    assert result['error'] == 'builder_disconnected'
    assert await saved(database) == graph


@pytest.mark.parametrize('setting', ['hire_off', 'workflow_off', 'workflow_on'])
async def test_talk_uses_current_ask_first_for_specialist_changes(database, team, setting):
    from models.employees import Employee
    from services.approvals.rules import set_ask_first

    row = await store.get_by_workflow(database, '7')
    async with database.reserved_session() as session:
        employee = await session.get(Employee, row.id)
        employee.rules = {'ask_first': setting == 'workflow_off'}
        await session.commit()
    if setting != 'hire_off':
        await set_ask_first(database, '7', setting == 'workflow_on')
    inspection = await call('inspect_canvas', team.contact)
    assert inspection.employee['asks_first'] == (setting == 'workflow_on')
    if setting != 'workflow_on':
        preview = await call('add_subagent', team.contact, agent_type='coding_agent', dry_run=True)
        assert preview.activation_state == 'planned' and not preview.required_access
        assert not any(r['capability'] == 'coding_agent' for r in await list_access(database, '7', 'owner'))
        public = ctx(team.contact.raw['parent_node_id'], call='public-input')
        public_result = await call('add_subagent', public, agent_type='coding_agent')
        assert public_result.activation_state == 'blocked' and not public_result.required_access
    result = await call('add_subagent', team.contact, agent_type='coding_agent')
    if setting == 'workflow_on':
        assert result.activation_state == 'blocked' and len(result.required_access) == 1
        return
    assert result.activation_state == 'saved' and not result.required_access, result.summary
    grants = await list_access(database, '7', 'owner')
    coding_grants = [g for g in grants if g['capability'] == 'coding_agent']
    assert coding_grants and all(g['approved'] and not g['change_pending'] for g in coding_grants)
    employee = await store.get_by_workflow(database, '7')
    [member] = [m for m in employee.team_plan['members'] if m['node_type'] == 'coding_agent']
    followup = ctx(team.contact.raw['parent_node_id'], call='add-clock', run_scope=team.contact.raw['run_scope'])
    followup.node_id = team.contact.node_id
    equipped = await call('add_tool', followup, node_type='currentTimeTool', target_member_id=member['node_id'])
    assert equipped.activation_state == 'saved' and not equipped.required_access, equipped.summary
    graph = await saved(database)
    assert any(e['source'].startswith('7:currentTimeTool:') and e['target'] == member['node_id'] for e in graph['edges'])


async def test_ask_first_off_preserves_an_explicit_denial(database, team):
    from services.approvals.rules import set_ask_first

    request = await call('add_subagent', team.contact, agent_type='coding_agent', purpose='Build the site')
    [grant] = request.required_access
    decision = {'access_request_id': grant['request_id'], 'allow': False}
    assert await handle_decide_employee_access(decision, team.socket) == {'success': True}
    await set_ask_first(database, '7', False)
    for purpose in ['Build the site', 'Finish the site']:
        result = await call('add_subagent', team.contact, agent_type='coding_agent', purpose=purpose)
        assert result.activation_state == 'blocked' and result.required_access
    assert all(node['type'] != 'coding_agent' for node in (await saved(database))['nodes'])


async def test_later_request_equips_the_existing_coder(database, team):
    from services.approvals.rules import set_ask_first

    await set_ask_first(database, '7', False)
    coder = await call('add_subagent', team.contact, agent_type='coding_agent')
    followup = ctx(team.contact.raw['parent_node_id'], call='equip-coder', run_scope=team.contact.raw['run_scope'])
    followup.node_id = team.contact.node_id
    request = {'agent_type': 'coding_agent', 'tool_types': ['duckduckgoSearch', 'currentTimeTool'],
               'tool_parameters': {'duckduckgoSearch': {'max_results': 3}}}
    for _ in range(2):
        result = await call('add_subagent', followup, **request)
        assert result.activation_state == 'saved' and not result.required_access, result.summary
        assert result.member_id == coder.member_id
    graph = await saved(database)
    assert [n['id'] for n in graph['nodes'] if n['type'] == 'coding_agent'] == [coder.member_id]
    types = {n['id']: n['type'] for n in graph['nodes']}
    private = [e['source'] for e in graph['edges'] if e['target'] == coder.member_id and e['targetHandle'] == 'input-tools']
    [search] = [node for node in private if types[node] == 'duckduckgoSearch']
    assert sum(types[node] == 'currentTimeTool' for node in private) == 1
    assert (await database.get_node_parameters(search))['max_results'] == 3


async def test_browser_specialist_uses_its_own_browser_and_vision_nodes(database, builder, team):
    from services.approvals.rules import set_ask_first

    await set_ask_first(database, '7', False)
    builder.connected = ['web']
    result = await call('add_subagent', team.contact, agent_type='browser_agent')
    assert result.activation_state == 'saved' and not result.required_access, result.summary
    graph = await saved(database)
    types = {n['id']: n['type'] for n in graph['nodes']}
    private = [e['source'] for e in graph['edges'] if e['target'] == result.member_id and e['targetHandle'] == 'input-tools']
    assert sorted(types[node] for node in private) == ['browser', 'visionAnalyze']
    assert all(e['target'] == result.member_id for e in graph['edges'] if e['source'] in private and e['targetHandle'] == 'input-tools')
    assert any(e['source'] == result.member_id and e['target'] == team.lead and e['targetHandle'] == 'input-teammates' for e in graph['edges'])


async def test_rule_changed_before_save_leaves_no_specialist_or_grant(database, team, monkeypatch):
    from nodes.tool import agent_builder
    from services.approvals.rules import set_ask_first

    await set_ask_first(database, '7', False)
    save = agent_builder._save

    async def enable_ask_first_then_save(*args, **kwargs):
        await set_ask_first(database, '7', True)
        return await save(*args, **kwargs)

    monkeypatch.setattr(agent_builder, '_save', enable_ask_first_then_save)
    result = await call('add_subagent', team.contact, agent_type='coding_agent')
    assert result.validation_issues and not result.operations
    assert all(node['type'] != 'coding_agent' for node in (await saved(database))['nodes'])
    assert not any(g['capability'] == 'coding_agent' for g in await list_access(database, '7', 'owner'))
