"""Cross-worker claims and atomic save failures, using real SQLite."""
import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from sqlmodel import select
from models.employees import Employee, EmployeeActivation
from models.database import NodeParameter
from services.employees import store


async def claim(database, key="one", digest="a"):
    return await store.claim_hire(database, owner_id="owner", idempotency_key=key, payload_hash=digest, fields={"job": "Research"})


async def test_parallel_claims_have_one_winner(real_database):
    claims = await asyncio.gather(*(claim(real_database) for _ in range(8)))
    assert sum(token is not None for _, token in claims) == 1
    assert len({row.id for row, _ in claims}) == 1
    assert len({row.workflow_id for row, _ in claims}) == 1


async def test_stale_claim_cannot_commit_or_fail_new_attempt(real_database):
    row, old = await claim(real_database)
    async with real_database.get_session() as session:
        current = await session.get(Employee, row.id)
        current.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    replacement, new = await claim(real_database)
    assert old != new and row.workflow_id == replacement.workflow_id
    await store.mark_failed(real_database, row.id, old)
    assert (await store.get_by_idempotency_key(real_database, "owner", "one")).claim_token == new
    with pytest.raises(RuntimeError, match="hire_claim_lost"):
        await store.commit_hire(real_database, row.id, old, name="A", description="", nodes=[], edges=[], parameters={}, node_roles={}, fields={})


async def test_payload_conflict_does_not_acquire_claim(real_database):
    first, token = await claim(real_database)
    second, other = await claim(real_database, digest="b")
    assert first.id == second.id and other is None and second.claim_token == token


async def test_graph_parameters_metadata_outbox_roll_back_together(real_database):
    row, token = await claim(real_database)
    # A conflicting parameter identity fails flush/commit after all inserts.
    nodes = [{"id": f"{row.workflow_id}:aiAgent:1", "type": "aiAgent", "position": {"x": 0, "y": 0}, "data": {"label": "Agent"}}]
    async with real_database.get_session() as session:
        session.add(NodeParameter(node_id=nodes[0]["id"], parameters={"before": True}))
        await session.commit()
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        await store.commit_hire(real_database, row.id, token, name="A", description="", nodes=nodes, edges=[], parameters={nodes[0]["id"]: {"model": "configured"}}, node_roles={}, fields={})
    assert await real_database.get_workflow(row.workflow_id) is None
    async with real_database.get_session() as session:
        assert not (await session.execute(select(EmployeeActivation))).scalars().all()
        params = (await session.execute(select(NodeParameter))).scalars().all()
        assert len(params) == 1 and params[0].parameters == {"before": True}
    assert (await store.get_by_idempotency_key(real_database, "owner", "one")).hire_state == "building"


async def test_commit_records_graph_metadata_and_activation_together(real_database):
    row, token = await claim(real_database)
    node_id = f"{row.workflow_id}:aiAgent:1"
    nodes = [{"id": node_id, "type": "aiAgent", "position": {"x": 0, "y": 0}, "data": {"label": "Agent"}}]
    result = await store.commit_hire(real_database, row.id, token, name="Research", description="Checks sources", nodes=nodes,
        edges=[], parameters={node_id: {"model": "configured"}}, node_roles={"agent": node_id}, fields={"llm": {"model": "configured"}})
    assert result == row.workflow_id
    employee = await store.get_by_workflow(real_database, result)
    assert employee.hire_state == "ready" and employee.claim_token is None
    assert await real_database.get_workflow(result)
    assert (await real_database.get_node_parameters(node_id))["model"] == "configured"
    async with real_database.get_session() as session:
        intent = (await session.execute(select(EmployeeActivation))).scalar_one()
        assert intent.workflow_id == result and intent.state == "saved"
