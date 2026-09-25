"""Employee rows: the hire reservation is idempotent per owner key, becomes
``ready`` once its workflow exists, and goes away with the workflow."""

from __future__ import annotations

import asyncio

import pytest

from services.employees import store


FIELDS = {"role": "Receptionist", "job": "Answer customers on WhatsApp", "apps": ["whatsapp"], "rules": {"ask_first": True}}


async def test_reserve_is_idempotent_per_owner_key(real_database):
    first, created = await store.reserve(real_database, owner_id="owner", idempotency_key="k1", payload_hash="h", fields=FIELDS)
    assert created is True
    assert first.hire_state == "building"
    assert first.workflow_id is None

    again, created = await store.reserve(real_database, owner_id="owner", idempotency_key="k1", payload_hash="h", fields=FIELDS)
    assert created is False
    assert again.id == first.id

    other_owner, created = await store.reserve(real_database, owner_id="someone", idempotency_key="k1", payload_hash="h", fields=FIELDS)
    assert created is True
    assert other_owner.id != first.id


async def test_concurrent_reservations_with_one_key_make_one_row(real_database):
    results = await asyncio.gather(
        *[
            store.reserve(real_database, owner_id="owner", idempotency_key="race", payload_hash="h", fields=FIELDS)
            for _ in range(5)
        ]
    )
    assert len({row.id for row, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1


async def test_mark_ready_attaches_the_workflow(real_database):
    row, _ = await store.reserve(real_database, owner_id="owner", idempotency_key="k2", payload_hash="h", fields=FIELDS)
    ready = await store.mark_ready(real_database, row.id, workflow_id="17", node_roles={"agent": "17:aiAgent:1"})
    assert ready.hire_state == "ready"
    assert ready.workflow_id == "17"
    assert ready.hired_at is not None

    loaded = await store.get_by_workflow(real_database, "17")
    assert loaded.id == row.id
    assert loaded.node_roles == {"agent": "17:aiAgent:1"}
    assert loaded.role == "Receptionist"
    assert loaded.apps == ["whatsapp"]


async def test_mark_failed_never_downgrades_a_ready_row(real_database):
    row, _ = await store.reserve(real_database, owner_id="owner", idempotency_key="k3", payload_hash="h", fields=FIELDS)
    await store.mark_failed(real_database, row.id)
    assert (await store.get_by_idempotency_key(real_database, "owner", "k3")).hire_state == "failed"

    await store.mark_ready(real_database, row.id, workflow_id="18", node_roles={})
    await store.mark_failed(real_database, row.id)
    assert (await store.get_by_workflow(real_database, "18")).hire_state == "ready"


async def test_list_by_workflow_ids_skips_unknown_and_unattached(real_database):
    a, _ = await store.reserve(real_database, owner_id="owner", idempotency_key="a", payload_hash="h", fields=FIELDS)
    await store.reserve(real_database, owner_id="owner", idempotency_key="b", payload_hash="h", fields=FIELDS)
    await store.mark_ready(real_database, a.id, workflow_id="21", node_roles={})
    rows = await store.list_by_workflow_ids(real_database, ["21", "22", ""])
    assert list(rows) == ["21"]


async def test_update_and_delete(real_database):
    row, _ = await store.reserve(real_database, owner_id="owner", idempotency_key="k4", payload_hash="h", fields=FIELDS)
    await store.mark_ready(real_database, row.id, workflow_id="30", node_roles={})
    updated = await store.update_employee(real_database, "30", {"role": "Front desk"})
    assert updated.role == "Front desk"
    assert await store.delete_for_workflow(real_database, "30") == 1
    assert await store.get_by_workflow(real_database, "30") is None
    assert await store.delete_for_workflow(real_database, "30") == 0


async def test_rejects_fields_it_does_not_own(real_database):
    with pytest.raises(ValueError):
        await store.reserve(real_database, owner_id="owner", idempotency_key="k5", payload_hash="h", fields={"hire_state": "ready"})
    with pytest.raises(ValueError):
        await store.update_employee(real_database, "30", {"workflow_id": "x"})


async def test_latest_controls_come_back_one_per_workflow(real_database):
    from models.database import WorkflowControlExecution

    async with real_database.get_session() as session:
        for workflow_id, generation, status in (("40", 1, "reset"), ("40", 2, "running"), ("41", 1, "paused")):
            session.add(
                WorkflowControlExecution(
                    id=f"{workflow_id}-{generation}",
                    workflow_id=workflow_id,
                    generation=generation,
                    execution_id=f"e{workflow_id}{generation}",
                    root_execution_id=f"e{workflow_id}{generation}",
                    graph_hash="0" * 64,
                    status=status,
                    idempotency_key=f"i{workflow_id}{generation}",
                )
            )
        await session.commit()

    latest = await real_database.list_latest_workflow_controls(["40", "41", "42"])
    assert {k: (v.generation, v.status) for k, v in latest.items()} == {"40": (2, "running"), "41": (1, "paused")}
    assert await real_database.list_latest_workflow_controls([]) == {}
