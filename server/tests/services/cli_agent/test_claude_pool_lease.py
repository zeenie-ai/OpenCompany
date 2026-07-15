"""Turn-lease isolation contracts for Claude's warm process pool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodes.agent.claude_code_agent import _skills
from nodes.agent.claude_code_agent._pool import (
    ClaudeSessionPool,
    PooledClaudeSession,
)
from services.cli_agent.types import ClaudeTaskSpec
from services.cli_agent.mcp_server import (
    BatchContext,
    _reset_for_tests,
    issue_token,
    lookup_batch,
    register_batch,
    unregister_batch,
)


def _session(memory_node_id: str, pid: int) -> PooledClaudeSession:
    return PooledClaudeSession(
        memory_node_id=memory_node_id,
        process=SimpleNamespace(returncode=None, pid=pid),
        cwd=Path.cwd(),
    )


def _pool_with(*sessions: PooledClaudeSession) -> ClaudeSessionPool:
    # Avoid provider construction/subprocess setup; warm acquire only needs
    # the entry map and its map lock.
    pool = object.__new__(ClaudeSessionPool)
    pool._pool = {session.memory_node_id: session for session in sessions}
    pool._pool_lock = asyncio.Lock()
    return pool


async def _acquire(pool: ClaudeSessionPool, memory_node_id: str):
    return await pool.acquire(
        memory_node_id,
        spec=ClaudeTaskSpec(prompt="test"),
        cwd=Path.cwd(),
        env={},
        defaults={},
        mcp_endpoint_url=None,
        mcp_bearer_token=None,
    )


async def test_same_memory_serializes_without_blocking_different_memory():
    first_session = _session("memory-1", 1)
    other_session = _session("memory-2", 2)
    pool = _pool_with(first_session, other_session)

    first = await _acquire(pool, "memory-1")
    queued_same = asyncio.create_task(_acquire(pool, "memory-1"))
    await asyncio.sleep(0)
    assert not queued_same.done()

    # Waiting for memory-1's lease must not retain the global pool lock.
    other = await asyncio.wait_for(_acquire(pool, "memory-2"), timeout=0.5)
    assert other is other_session
    await pool.release(other)

    await pool.release(first)
    second = await asyncio.wait_for(queued_same, timeout=0.5)
    assert second is first_session
    await pool.release(second)


async def test_acquire_error_releases_turn_lease(monkeypatch):
    session = _session("memory-1", 1)
    session.workspace_dir = Path.cwd()
    pool = _pool_with(session)
    monkeypatch.setattr(
        _skills,
        "materialise_skills",
        AsyncMock(side_effect=RuntimeError("skill update failed")),
    )

    with pytest.raises(RuntimeError, match="skill update failed"):
        await _acquire(pool, "memory-1")

    assert session.turn_lease.locked() is False


async def test_cancellation_while_rechecking_pool_releases_turn_lease():
    session = _session("memory-1", 1)
    pool = _pool_with(session)
    # Hold the turn lease so acquire can pass its first map lookup, then take
    # the map lock before handing the lease to the queued turn.
    await session.turn_lease.acquire()
    acquiring = asyncio.create_task(_acquire(pool, "memory-1"))
    await asyncio.sleep(0)
    await pool._pool_lock.acquire()
    session.turn_lease.release()
    async with asyncio.timeout(1):
        while not session.turn_lease.locked():
            await asyncio.sleep(0)

    acquiring.cancel()
    pool._pool_lock.release()
    with pytest.raises(asyncio.CancelledError):
        await acquiring

    assert session.turn_lease.locked() is False


async def test_warm_reuse_rebinds_full_batch_identity():
    _reset_for_tests()
    session = _session("memory-1", 1)
    pool = _pool_with(session)
    old_token = issue_token()
    new_token = issue_token()
    old_broadcaster = object()
    new_broadcaster = object()
    session.batch_token = old_token
    register_batch(
        old_token,
        BatchContext(
            workflow_id="workflow-old",
            node_id="node-old",
            execution_id="execution-old",
            workspace_dir=Path("old-workspace"),
            broadcaster=old_broadcaster,
        ),
    )
    register_batch(
        new_token,
        BatchContext(
            workflow_id="workflow-new",
            node_id="node-new",
            execution_id="execution-new",
            workspace_dir=Path("new-workspace"),
            broadcaster=new_broadcaster,
        ),
    )

    acquired = await pool.acquire(
        "memory-1",
        spec=ClaudeTaskSpec(prompt="test"),
        cwd=Path.cwd(),
        env={},
        defaults={},
        mcp_endpoint_url=None,
        mcp_bearer_token=new_token,
    )
    rebound = lookup_batch(old_token)
    assert rebound is not None
    assert rebound.workflow_id == "workflow-new"
    assert rebound.node_id == "node-new"
    assert rebound.execution_id == "execution-new"
    assert rebound.workspace_dir == Path("new-workspace").resolve()
    assert rebound.broadcaster is new_broadcaster
    assert lookup_batch(new_token) is None

    await pool.release(acquired)
    unregister_batch(old_token)
