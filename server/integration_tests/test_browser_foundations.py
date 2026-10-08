"""Real application database/creation contracts, isolated from legacy stubs."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlmodel import SQLModel

import nodes  # noqa: F401 - real plugin registry
from core.config import Settings
from core.database import Database
from models.database import NodeParameter
from routers import browser_agents
from services.browser_agent_recipe import browser_tool_id
from services.distributed_notifications import notification
from tools.database_transfer import export_manifest, import_manifest, validate_manifest


def settings(**overrides):
    return Settings(_env_file=None, **{
        "host": "127.0.0.1", "port": 5678, "debug": False, "distributed_mode": False,
        "jwt_secret_key": "test-key-" * 8, "secret_key": "test-key-" * 8, "api_key_encryption_key": "test-key-" * 8,
        "cors_origins": [], "workflow_db_filename": "test.db", "temporal_enabled": False,
        "temporal_server_address": "127.0.0.1:7233", "temporal_namespace": "default", "temporal_task_queue": "test",
        "temporal_per_type_dispatch": True, "temporal_agent_workflow_enabled": True,
        "temporal_graceful_shutdown_seconds": 10, "temporal_frontend_grpc_port": 7233,
        "temporal_ui_port": 8233, "temporal_sqlite_path": "test.db", "temporal_terminate_running_on_startup": False,
        **overrides,
    })


@pytest.fixture
async def database(tmp_path):
    db = Database(settings(database_url_override=f"sqlite+aiosqlite:///{(tmp_path / 'source.db').as_posix()}"))
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()


async def saved_browser(database):
    await database.save_workflow(workflow_id="7", name="Browser", slug="Browser_7", data={
        "owner_id": "owner", "nodes": [{"id": "7:browser:1", "type": "browser", "data": {}, "position": {"x": 0, "y": 0}}], "edges": []})
    await database.save_node_parameters("7:browser:1", {"interaction": "read_only"})


async def test_atomic_creation_concurrent_retry_reuses_selected_tool_and_deleted_context(database, monkeypatch):
    await saved_browser(database)
    monkeypatch.setattr(browser_agents, "container", SimpleNamespace(database=lambda: database))
    request = SimpleNamespace(state=SimpleNamespace(user_id="owner"))
    body = browser_agents.CreateBrowserAgent(workflow_id="7", browser_node_id="7:browser:1", mutation_id=uuid4(), provider="openai", model="inherited-model")
    first, retry = await asyncio.gather(browser_agents.create_browser_agent(body, request), browser_agents.create_browser_agent(body, request))
    assert first["node_ids"] == retry["node_ids"]
    assert sorted([first["applied"], retry["applied"]]) == [False, True]
    graph = (await database.get_workflow("7")).data
    assert sorted(node["type"] for node in graph["nodes"]) == ["browser", "browser_agent", "context", "masterSkill", "visionAnalyze"]
    ids = first["node_ids"]
    assert browser_tool_id(graph, ids["agent"]) == "7:browser:1"
    assert await database.get_node_parameters("7:browser:1") == {"interaction": "read_only"}
    assert (await database.get_node_parameters(ids["agent"]))["model"] == "inherited-model"
    assert (await database.get_node_parameters(ids["skills"]))["skills_config"]["browser-skill"]["enabled"] is True
    assert not any((node.get("data") or {}).get("isBrowserPanel") for node in graph["nodes"] if node["type"] == "browser_agent")
    context = ids["context"]
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] != context]
    graph["edges"] = [edge for edge in graph["edges"] if edge["source"] != context]
    await database.save_workflow(workflow_id="7", name="Browser", slug="Browser_7", data=graph)
    second = await browser_agents.create_browser_agent(body.model_copy(update={"mutation_id": uuid4()}), request)
    assert second["node_ids"]["agent"] == ids["agent"]
    assert not second["operations"]
    assert not any(node["type"] == "context" for node in (await database.get_workflow("7")).data["nodes"])
    # Cached receipts remain authorized after graph ownership changes.
    graph["owner_id"] = "another-owner"
    await database.save_workflow(workflow_id="7", name="Browser", slug="Browser_7", data=graph)
    with pytest.raises(HTTPException) as exc:
        await browser_agents.create_browser_agent(body, request)
    assert exc.value.status_code == 404


async def test_invalid_selected_tool_leaves_no_partial_companions(database, monkeypatch):
    await saved_browser(database)
    monkeypatch.setattr(browser_agents, "container", SimpleNamespace(database=lambda: database))
    with pytest.raises(HTTPException):
        await browser_agents.create_browser_agent(browser_agents.CreateBrowserAgent(workflow_id="7", browser_node_id="missing", mutation_id=uuid4()), SimpleNamespace(state=SimpleNamespace(user_id="owner")))
    assert len((await database.get_workflow("7")).data["nodes"]) == 1
    async with database.get_session() as session:
        assert len((await session.execute(select(NodeParameter))).scalars().all()) == 1


async def test_offline_roundtrip_preserves_graph_ids_and_rejects_nonempty_target(database, tmp_path):
    await saved_browser(database)
    manifest = await export_manifest(database)
    assert "api_keys" not in manifest["tables"] and "browser_owners" not in manifest["tables"]
    target = Database(settings(database_url_override=f"sqlite+aiosqlite:///{(tmp_path / 'target.db').as_posix()}"))
    await target.startup()
    try:
        await import_manifest(target, manifest)
        assert (await target.get_workflow("7")).data == (await database.get_workflow("7")).data
        assert await target.get_node_parameters("7:browser:1") == {"interaction": "read_only"}
        with pytest.raises(ValueError, match="empty target"):
            await import_manifest(target, manifest)
    finally:
        await target.shutdown()
    invalid = deepcopy(manifest)
    invalid["tables"]["node_parameters"][0]["parameters"]["api_key"] = "SECRET_CANARY"
    with pytest.raises(ValueError, match="inline credentials"):
        validate_manifest(invalid)
    invalid = deepcopy(manifest)
    invalid["tables"]["oauth_tokens"] = []
    with pytest.raises(ValueError, match="excluded"):
        validate_manifest(invalid)


async def test_offline_transfer_keeps_legacy_and_gated_profiles_on_original_machine(database, tmp_path):
    from models.browser_profiles import BrowserProfileRow
    from models.browser_owners import BrowserOwner, BrowserProfileOwner
    async with database.get_session() as session:
        session.add(BrowserProfileRow(id="legacy", owner_id="owner", name="Legacy"))
        session.add(BrowserProfileRow(id="gated", owner_id="owner", name="Gated"))
        session.add(BrowserProfileOwner(profile_id="gated", principal_id="owner", owner_id="local",
            sensitive_login=True, needs_observation=True, challenge_required=True))
        await session.commit()
    manifest = await export_manifest(database)
    owners = {row["profile_id"]: row for row in manifest["tables"]["browser_profile_owners"]}
    assert owners["legacy"]["owner_id"] == "local"
    assert owners["gated"]["sensitive_login"] is True
    target = Database(settings(database_url_override=f"sqlite+aiosqlite:///{(tmp_path / 'profiles.db').as_posix()}"))
    await target.startup()
    try:
        await import_manifest(target, manifest)
        async with target.get_session() as session:
            owner = await session.get(BrowserOwner, "local")
            assert owner.machine_id == manifest["local_owner_machine_id"]
            assert owner.runtime_epoch == "offline-import" and owner.heartbeat_at == 0
            assert (await session.get(BrowserProfileOwner, "gated")).sensitive_login is True
    finally:
        await target.shutdown()


async def test_cluster_preflight_only_checks_incoming_capability_and_principal(database, monkeypatch):
    from unittest.mock import AsyncMock
    from core.container import container
    from services import node_registry
    from services.plugin.credential import CREDENTIAL_REGISTRY, ApiKeyCredential
    from services.credentials.preflight import assert_cluster_credentials
    from services.credentials.onepassword import CredentialSourceError
    classes = {"browser_agent": SimpleNamespace(credentials=[]),
        "static": SimpleNamespace(credentials=[CREDENTIAL_REGISTRY["openai"]]),
        "multi": SimpleNamespace(credentials=[type("MultiFieldCredential", (ApiKeyCredential,), {"extra_fields": ["app_secret"]})]),
        "oauth": SimpleNamespace(credentials=[object])}
    monkeypatch.setattr(node_registry, "get_node_class", classes.get)
    auth = SimpleNamespace(distributed_credentials=True, get_credential_source=AsyncMock(return_value={"source": "onepassword"}))
    monkeypatch.setattr(container, "auth_service", lambda: auth)
    graph = {"nodes": [{"id": "agent", "type": "browser_agent"}, {"id": "key", "type": "static"},
        {"id": "lead", "type": "oauth"}], "edges": [
        {"source": "key", "target": "agent", "targetHandle": "input-tools"},
        {"source": "agent", "target": "lead", "targetHandle": "input-teammates"}]}
    await assert_cluster_credentials(database, graph, {"node_id": "agent", "user_id": "member"})
    auth.get_credential_source.assert_awaited_once_with("openai", principal="member")
    with pytest.raises(CredentialSourceError, match="OAuth"):
        await assert_cluster_credentials(database, graph, {"user_id": "member"})
    with pytest.raises(CredentialSourceError, match="additional credential fields"):
        await assert_cluster_credentials(database, {"nodes": [{"id": "multi", "type": "multi"}]}, {"user_id": "member"})


async def assert_owner_registry_upgrade(database):
    from models.browser_owners import BrowserProfileOwner
    async with database.get_session() as session:
        session.add(BrowserProfileOwner(profile_id="prototype-profile", principal_id="owner", owner_id="local",
            sensitive_login=True, needs_observation=True))
        await session.commit()
    async with database.engine.begin() as connection:
        await connection.execute(text("DROP INDEX IF EXISTS ix_browser_profile_owners_workflow_id"))
        await connection.execute(text("DROP INDEX IF EXISTS ix_browser_profile_owners_browser_node_id"))
        await connection.execute(text("ALTER TABLE browser_profile_owners DROP COLUMN workflow_id"))
        await connection.execute(text("ALTER TABLE browser_profile_owners DROP COLUMN browser_node_id"))
    await database.shutdown()
    await database.startup()
    async with database.get_session() as session:
        row = await session.get(BrowserProfileOwner, "prototype-profile")
        assert row.sensitive_login is True and row.needs_observation is True
        assert row.workflow_id is None and row.browser_node_id is None


async def test_sqlite_owner_registry_upgrade_preserves_recovery_latches(database):
    await assert_owner_registry_upgrade(database)


def test_postgresql_notification_excludes_page_and_secret_payloads():
    message = {"type": "browser_updated", "data": {"data": {
        "workflow_id": "7", "node_id": "7:browser:1", "session_id": "session", "state": "awaiting_user",
        "url": "SECRET_CANARY", "title": "SECRET_CANARY", "frames": ["SECRET_CANARY"]}}}
    assert notification(message, "replica") == {"origin": "replica", "kind": "browser_updated", "workflow_id": "7", "node_id": "7:browser:1", "session_id": "session"}
    assert notification({"type": "node_output", "output": "SECRET_CANARY"}, "replica") is None


def test_cluster_settings_require_shared_prerequisites():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="shared PostgreSQL"):
        settings(distributed_mode=True, database_url_override="sqlite+aiosqlite:///local.db")
    configured = settings(distributed_mode=True,
        database_url_override="postgresql://test:test@database.test/opencompany",
        temporal_server_address="temporal.test:7233", temporal_enabled=True,
        temporal_agent_workflow_enabled=True, temporal_per_type_dispatch=True,
        browser_replica_id="backend-a", browser_replica_url="http://backend-a.test:5678",
        onepassword_auth_mode="service_account", workers=1)
    assert configured.database_url.startswith("postgresql+asyncpg://")


def test_every_shared_datetime_column_accepts_legacy_utc_conventions():
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import dialect
    from core.shared_database import install_postgresql_timestamp_types, PostgreSQLUTCDateTime
    install_postgresql_timestamp_types(SQLModel.metadata)
    column = NodeParameter.__table__.c.created_at
    assert isinstance(column.type, PostgreSQLUTCDateTime)
    assert column.type.process_bind_param(datetime(2026, 10, 8), dialect()).tzinfo == timezone.utc
