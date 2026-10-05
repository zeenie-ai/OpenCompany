import pytest

from models.employees import EmployeeGrant
from services.employees.permissions import assert_runtime_access, decide_access, list_access
from services.plugin.base import NodeUserError


async def install(database, *, parent_owner="owner", parent_id="bundle", leaf_parent="bundle"):
    async with database.get_session() as session:
        session.add(EmployeeGrant(id=parent_id, workflow_id="7", owner_id=parent_owner,
            capability="web_agent", member_id="7:ai_employee:1", limits={"approved": True, "purpose": "Research evidence"}))
        session.add(EmployeeGrant(id="tool", workflow_id="7", owner_id="owner", capability="httpRequest", member_id="7:web_agent:1",
            limits={"approved": True, "parent_grant_id": leaf_parent, "tool_node_id": "7:httpRequest:1"}))
        await session.commit()


async def use(database):
    await assert_runtime_access(database, "7", "7:httpRequest:1", "httpRequest", {"parent_node_id": "7:web_agent:1"})


async def test_bundle_revocation_disables_nested_tools_and_reallow_restores(real_database):
    await install(real_database)
    await use(real_database)
    assert not await decide_access(real_database, "bundle", "another-owner", False)
    await use(real_database)
    assert await decide_access(real_database, "bundle", "owner", False)
    with pytest.raises(NodeUserError, match="owner removed permission"):
        await use(real_database)
    assert await decide_access(real_database, "bundle", "owner", True)
    await use(real_database)


@pytest.mark.parametrize("parent_owner,leaf_parent", [("another-owner", "bundle"), ("owner", "missing"), ("owner", "tool")])
async def test_invalid_bundle_authority_is_blocked(real_database, parent_owner, leaf_parent):
    await install(real_database, parent_owner=parent_owner, leaf_parent=leaf_parent)
    with pytest.raises(NodeUserError):
        await use(real_database)


async def test_multilevel_parent_revocation_is_recursive(real_database):
    await install(real_database, leaf_parent="child-bundle")
    async with real_database.get_session() as session:
        session.add(EmployeeGrant(id="child-bundle", workflow_id="7", owner_id="owner", capability="web_agent", member_id="7:web_agent:1",
            limits={"approved": True, "parent_grant_id": "bundle"}))
        await session.commit()
    await use(real_database)
    await decide_access(real_database, "bundle", "owner", False)
    with pytest.raises(NodeUserError):
        await use(real_database)


async def test_legacy_without_managed_grants_keeps_runtime_behavior(real_database):
    await use(real_database)


async def test_access_listing_is_owner_scoped(real_database):
    await install(real_database)
    assert await list_access(real_database, "7", "another-owner") == []
    assert await list_access(real_database, "another-workflow", "owner") == []
    assert len(await list_access(real_database, "7", "owner")) == 2
