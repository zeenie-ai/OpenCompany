"""Which app events wake an employee (services/employees/handlers.py
``set_employee_wake_rule``): the rule is saved on the team plan, shown in
the employee's summary, and offered only to a team employee an app event
starts. Triage itself is tested in test_triage.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models.employees import Employee
from services.employees import handlers, store

CHAT = "1:ai_employee:1"
GMAIL = "1:googleGmailReceive:1"
CRON = "1:cronScheduler:1"
PLAN = {"version": 3, "lead_node_id": CHAT, "talk_node_id": CHAT, "intake_trigger_node_id": GMAIL}
RULE = "a customer asks about an order"


@pytest.fixture
def setup(monkeypatch, real_database):
    class Auth:
        async def has_valid_key(self, key, *, principal=None):
            return False

        async def get_oauth_tokens(self, provider):
            return None

        async def list_api_key_providers(self, *, principal=None):
            return []

    import core.container as container_module
    import services.plugin.deps as deps

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth()))
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())
    changed = []
    monkeypatch.setattr(handlers, "employee_changed_now", changed.append)
    return SimpleNamespace(database=real_database, changed=changed)


async def hire(database, *, plan=PLAN, trigger=(GMAIL, "googleGmailReceive")):
    nodes = [{"id": CHAT, "type": "ai_employee", "data": {"label": "Maya"}}]
    if trigger:
        nodes.append({"id": trigger[0], "type": trigger[1], "data": {"label": "When"}})
    assert await database.save_workflow(workflow_id="1", name="Maya", slug="Maya_1", data={"nodes": nodes, "edges": []})
    async with database.get_session() as session:
        session.add(Employee(id="e1", workflow_id="1", owner_id="owner", hire_state="ready", team_plan=plan))
        await session.commit()


async def set_rule(rule, workflow_id="1"):
    return await handlers.handle_set_employee_wake_rule({"workflow_id": workflow_id, "wake_rule": rule}, None)


async def saved_plan(database):
    return (await store.get_by_workflow(database, "1")).team_plan


async def test_a_rule_is_saved_on_the_team_plan_and_shown(setup):
    await hire(setup.database)

    answer = await set_rule(f"  {RULE}\n")

    assert answer["success"] is True
    assert answer["employee"]["wake_rule"] == RULE
    assert await saved_plan(setup.database) == {**PLAN, "wake_rule": RULE}
    assert setup.changed == ["1"]


async def test_a_rule_is_cut_to_its_limit(setup):
    await hire(setup.database)

    answer = await set_rule("x" * 500)

    assert answer["employee"]["wake_rule"] == "x" * 200


async def test_an_empty_rule_clears_it(setup):
    await hire(setup.database, plan={**PLAN, "wake_rule": RULE})

    answer = await set_rule("   ")

    assert answer["employee"]["wake_rule"] == ""
    assert await saved_plan(setup.database) == PLAN


async def test_an_employee_with_no_rule_yet_shows_an_empty_one(setup):
    await hire(setup.database)

    answer = await handlers.handle_get_employee({"workflow_id": "1"}, None)

    assert answer["employee"]["wake_rule"] == ""


@pytest.mark.parametrize(
    "plan, trigger",
    [
        pytest.param({k: v for k, v in PLAN.items() if k != "intake_trigger_node_id"}, None, id="messaged by the owner"),
        pytest.param({**PLAN, "intake_trigger_node_id": CRON}, (CRON, "cronScheduler"), id="on a schedule"),
        pytest.param({**PLAN, "version": 2}, (GMAIL, "googleGmailReceive"), id="an older team"),
    ],
)
async def test_an_employee_no_app_event_starts_takes_no_rule(setup, plan, trigger):
    await hire(setup.database, plan=plan, trigger=trigger)

    answer = await set_rule(RULE)

    assert (answer["success"], answer["error"]) == (False, "unsupported")
    assert await saved_plan(setup.database) == plan
    shown = await handlers.handle_get_employee({"workflow_id": "1"}, None)
    assert shown["employee"]["wake_rule"] is None


async def test_a_request_that_cannot_be_used(setup):
    await hire(setup.database)

    assert await handlers.handle_set_employee_wake_rule({"workflow_id": "1"}, None) == {"success": False, "error": "invalid_request"}
    assert await handlers.handle_set_employee_wake_rule({"wake_rule": RULE}, None) == {"success": False, "error": "invalid_request"}
    assert await handlers.handle_set_employee_wake_rule({"workflow_id": "1", "wake_rule": 7}, None) == {"success": False, "error": "invalid_request"}
    missing = await set_rule(RULE, workflow_id="9")
    assert (missing["success"], missing["error"]) == (False, "not_found")


def test_the_command_is_registered():
    assert handlers.WS_HANDLERS["set_employee_wake_rule"] is handlers.handle_set_employee_wake_rule
