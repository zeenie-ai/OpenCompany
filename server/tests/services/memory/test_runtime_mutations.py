"""Concurrency contracts for durable shared runtime mutations."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.memory.runtime import append_memory_turns_atomic


@pytest.fixture
async def runtime_database():
    # The suite's root conftest intentionally stubs ``core.database`` for
    # fast node-contract tests. Load the real implementation under a private
    # module name for these SQLite integration tests.
    module_name = "tests._real_runtime_database"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[3] / "core" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    db_path = Path.cwd() / f".runtime-mutations-{uuid.uuid4().hex}.db"
    settings = SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        database_echo=False,
        database_pool_size=5,
        database_max_overflow=5,
    )
    database = module.Database(settings)
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)
        for candidate in (
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
        ):
            candidate.unlink(missing_ok=True)


async def test_parallel_memory_appends_preserve_every_turn_and_node_isolation(
    runtime_database,
):
    db = runtime_database
    await db.save_node_parameters("memory-a", {"label": "A"})
    await db.save_node_parameters("memory-b", {"label": "B"})

    async def append(node_id: str, index: int):
        return await append_memory_turns_atomic(
            db,
            node_id,
            [("human", f"question-{node_id}-{index}"), ("ai", f"answer-{node_id}-{index}")],
            window_size=100,
            mutation_id=f"test-memory:{node_id}:{index}",
        )

    await asyncio.gather(
        *(append("memory-a", index) for index in range(12)),
        *(append("memory-b", index) for index in range(7)),
    )

    a = (await db.get_node_parameters("memory-a"))["memory_content"]
    b = (await db.get_node_parameters("memory-b"))["memory_content"]
    for index in range(12):
        assert f"question-memory-a-{index}" in a
        assert f"answer-memory-a-{index}" in a
        assert f"question-memory-a-{index}" not in b
    for index in range(7):
        assert f"question-memory-b-{index}" in b
        assert f"answer-memory-b-{index}" in b
        assert f"question-memory-b-{index}" not in a


async def test_memory_retry_is_durably_idempotent(runtime_database):
    db = runtime_database
    kwargs = dict(
        database=db,
        memory_node_id="memory-retry",
        turns=[("human", "only-once-human"), ("ai", "only-once-ai")],
        window_size=10,
        mutation_id="test-memory:retry-1",
    )

    first = await append_memory_turns_atomic(**kwargs)
    second = await append_memory_turns_atomic(**kwargs)

    assert first[2] is True
    assert second[2] is False
    content = (await db.get_node_parameters("memory-retry"))["memory_content"]
    assert content.count("only-once-human") == 1
    assert content.count("only-once-ai") == 1


async def test_mutation_identity_is_scoped_to_resource(runtime_database):
    db = runtime_database
    shared_transport_id = "transport-retry-1"

    first, second = await asyncio.gather(
        append_memory_turns_atomic(
            db,
            "memory-scope-a",
            [("human", "turn-a")],
            window_size=10,
            mutation_id=shared_transport_id,
        ),
        append_memory_turns_atomic(
            db,
            "memory-scope-b",
            [("human", "turn-b")],
            window_size=10,
            mutation_id=shared_transport_id,
        ),
    )

    assert first[2] is True
    assert second[2] is True
    assert "turn-a" in (await db.get_node_parameters("memory-scope-a"))["memory_content"]
    assert "turn-b" in (await db.get_node_parameters("memory-scope-b"))["memory_content"]


async def test_retry_does_not_return_trimmed_blocks_for_rearchival(runtime_database):
    db = runtime_database
    await append_memory_turns_atomic(
        db,
        "memory-trim-retry",
        [("human", "first"), ("ai", "answer-first")],
        window_size=1,
        mutation_id="seed",
    )
    kwargs = dict(
        database=db,
        memory_node_id="memory-trim-retry",
        turns=[("human", "second"), ("ai", "answer-second")],
        window_size=1,
        mutation_id="trim-retry",
    )

    first = await append_memory_turns_atomic(**kwargs)
    retry = await append_memory_turns_atomic(**kwargs)

    assert first[1]
    assert retry[1] == []
    assert retry[2] is False

    async with db.get_session() as session:
        from models.database import RuntimeMutation
        from sqlmodel import select

        selected = await session.execute(
            select(RuntimeMutation).where(
                RuntimeMutation.mutation_id == "trim-retry",
                RuntimeMutation.resource_id == "memory-trim-retry",
            )
        )
        ledger_row = selected.scalar_one()
        assert ledger_row.result["trimmed_count"] > 0
        assert "removed_texts" not in ledger_row.result
        assert "first" not in str(ledger_row.result)


async def test_parallel_workflow_mutations_do_not_replace_each_other(
    runtime_database,
):
    db = runtime_database
    assert await db.save_workflow(
        "workflow-1",
        "Workflow",
        "Workflow",
        {"nodes": [], "edges": [], "markers": []},
    )

    async def add_marker(index: int):
        def transform(data):
            data["markers"] = [*(data.get("markers") or []), index]
            return data, {"marker": index}

        return await db.mutate_workflow_data_atomic(
            "workflow-1",
            transform,
            mutation_id=f"test-workflow:{index}",
            operation="add_marker",
        )

    await asyncio.gather(*(add_marker(index) for index in range(20)))
    # A retry of an already committed mutation cannot append it twice.
    _workflow, _metadata, applied = await add_marker(3)
    assert applied is False

    workflow = await db.get_workflow("workflow-1")
    assert sorted(workflow.data["markers"]) == list(range(20))


async def test_parallel_team_task_claim_has_one_winner(runtime_database):
    db = runtime_database
    await db.add_team_task(
        "task-1",
        "team-1",
        "Exclusive task",
        "lead-1",
    )

    claims = await asyncio.gather(
        *(db.claim_task("task-1", f"agent-{index}") for index in range(20))
    )
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1

    winner = winners[0]["assigned_to"]
    retry = await db.claim_task("task-1", winner)
    assert retry is not None
    assert retry["assigned_to"] == winner

    tasks = await db.get_team_tasks("team-1")
    assert tasks[0]["status"] == "in_progress"
    assert tasks[0]["assigned_to"] == winner


async def test_team_task_transitions_are_atomic_and_retry_safe(runtime_database):
    db = runtime_database
    await db.add_team_task("complete-1", "team-1", "Complete", "lead-1")
    assert await db.claim_task("complete-1", "agent-1") is not None
    completions = await asyncio.gather(
        *(db.complete_task("complete-1", {"value": 1}) for _ in range(8))
    )
    assert all(completions)

    await db.add_team_task("retry-1", "team-1", "Retry", "lead-1")
    assert await db.claim_task("retry-1", "agent-1") is not None
    assert await db.fail_task("retry-1", "same failure") is True
    assert await db.fail_task("retry-1", "same failure") is True

    async with db.get_session() as session:
        from models.database import TeamTask
        from sqlmodel import select

        selected = await session.execute(
            select(TeamTask).where(TeamTask.id == "retry-1")
        )
        task = selected.scalar_one()
        assert task.retry_count == 1
        assert task.status == "pending"
