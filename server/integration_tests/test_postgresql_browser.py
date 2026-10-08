"""Real PostgreSQL migration, admission/claims and replica notifications.

Set TEST_POSTGRES_URL to a PostgreSQL database where the test role may create
temporary databases. Existing databases and tables are never cleared.
"""
import asyncio
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import select

from core.database import Database
from integration_tests.test_browser_foundations import settings, saved_browser, assert_owner_registry_upgrade
from models.browser_owners import BrowserOwner, BrowserProfileOwner
from models.database import NodeParameter
from services.distributed_notifications import BrowserNotifications
from services.plugin import NodeUserError
from services.user_auth import UserAuthService
from services.workspace_task_history import admit, transition, list_tasks
from tools.database_transfer import export_manifest, import_manifest


@pytest.fixture
async def postgres():
    base = os.environ.get("TEST_POSTGRES_URL")
    if not base:
        pytest.skip("TEST_POSTGRES_URL is required for live shared database validation")
    admin = await asyncpg.connect(base.replace("postgresql+asyncpg://", "postgresql://"))
    name = "oc_browser_test_" + uuid4().hex
    await admin.execute(f'CREATE DATABASE "{name}"')
    from sqlalchemy.engine import make_url
    url = make_url(base).set(drivername="postgresql+asyncpg", database=name).render_as_string(hide_password=False)
    database = Database(settings(database_url_override=url))
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()


async def test_postgresql_import_and_sequence_reseed(postgres, tmp_path):
    source = Database(settings(database_url_override=f"sqlite+aiosqlite:///{(tmp_path / 'transfer.db').as_posix()}"))
    await source.startup()
    try:
        await saved_browser(source)
        from models.browser_profiles import BrowserProfileRow
        async with source.get_session() as session:
            session.add(BrowserProfileRow(id="legacy-profile", owner_id="owner", name="Imported profile"))
            await session.commit()
        exported = await export_manifest(source)
        await import_manifest(postgres, exported)
        assert (await postgres.get_workflow("7")).data == (await source.get_workflow("7")).data
        original_id = exported["tables"]["node_parameters"][0]["id"]
        await postgres.save_node_parameters("7:visionAnalyze:1", {})
        async with postgres.get_session() as session:
            row = (await session.execute(select(NodeParameter).where(NodeParameter.node_id == "7:visionAnalyze:1"))).scalar_one()
            assert row.id > original_id
            assert (await session.get(BrowserProfileOwner, "legacy-profile")).owner_id == "local"
            assert (await session.get(BrowserOwner, "local")).machine_id == exported["local_owner_machine_id"]
    finally:
        await source.shutdown()


async def test_postgresql_atomic_claims_and_stale_cleanup(postgres, monkeypatch):
    from nodes.browser._profiles import ProfileStore
    from services import browser_owners as owners
    cfg = SimpleNamespace(distributed_mode=False)
    monkeypatch.setattr(owners, "settings", lambda: cfg)
    profile = await ProfileStore(postgres).create("owner", "Concurrent claim")
    binding = await owners.bind_profile(postgres, profile.id, "owner")
    results = await asyncio.gather(
        owners.claim_browser_task(postgres, binding, "owner", "task-a"),
        owners.claim_browser_task(postgres, binding, "owner", "task-b"), return_exceptions=True)
    assert sum(isinstance(result, NodeUserError) for result in results) == 1
    winner = "task-a" if not isinstance(results[0], Exception) else "task-b"
    assert (await owners.cleanup_browser_task(postgres, binding, "stale-task"))["released"] is False
    async with postgres.get_session() as session:
        assert (await session.get(BrowserProfileOwner, profile.id)).task_id == winner
    await owners.cleanup_browser_task(postgres, binding, winner)
    async with postgres.get_session() as session:
        assert (await session.get(BrowserProfileOwner, profile.id)).task_id is None


async def test_postgresql_owner_registry_upgrade_preserves_recovery_latches(postgres):
    await assert_owner_registry_upgrade(postgres)


async def test_postgresql_first_owner_registration_is_exclusive(postgres):
    auth = UserAuthService(postgres, postgres.settings, None, None)
    results = await asyncio.gather(auth.register("a@example.test", "fixture-password", "A"),
                                   auth.register("b@example.test", "fixture-password", "B"))
    assert sum(user is not None and user.is_owner for user, _ in results) == 1
    assert sum(error is not None for _, error in results) == 1


async def test_postgresql_history_admission_retry_and_terminal_updates(postgres):
    await saved_browser(postgres)
    payload = {"history_record": {"invocation_id": "invocation", "submission_id": str(uuid4()), "workflow_id": "7",
        "node_id": "removed", "principal": "owner", "fingerprint": "hash", "prompt": "Read fixture account"}}
    await asyncio.gather(admit(postgres, payload), admit(postgres, payload))
    await transition(postgres, {"invocation_id": "invocation", "status": "completed", "result": {"response": "done"}})
    await asyncio.gather(transition(postgres, {"invocation_id": "invocation", "status": "running"}),
                         transition(postgres, {"invocation_id": "invocation", "status": "failed"}))
    rows = (await list_tasks(postgres, "owner", "7"))["items"]
    assert len(rows) == 1 and rows[0]["status"] == "completed" and rows[0]["result"]["response"] == "done"


async def test_postgresql_replicas_deliver_identifier_only_invalidation(postgres):
    class Broadcaster:
        def __init__(self):
            self.messages, self.received = [], asyncio.Event()
        async def broadcast(self, message, **kwargs):
            self.messages.append(message)
            self.received.set()
    first, second = Broadcaster(), Broadcaster()
    a, b = BrowserNotifications(postgres, first), BrowserNotifications(postgres, second)
    await a.start()
    await b.start()
    try:
        await a.publish({"type": "browser_updated", "data": {"data": {"workflow_id": "7", "node_id": "7:browser:1",
            "session_id": "browser-session", "url": "SECRET_CANARY", "frames": "SECRET_CANARY"}}})
        await asyncio.wait_for(second.received.wait(), 5)
        assert first.messages == []
        assert second.messages == [{"type": "browser_invalidated", "data": {"workflow_id": "7", "node_id": "7:browser:1", "session_id": "browser-session"}}]
        assert "SECRET_CANARY" not in json.dumps(second.messages)
    finally:
        await a.stop()
        await b.stop()
