"""End-to-end tests for the workflow rename flow.

``handle_save_workflow`` IS the rename path: when the display name
changes, the handler must (1) allocate a fresh slug, (2) atomically
update DB name + slug while keeping the UUID id stable, (3) rename
the on-disk workspace dir if it exists, (4) broadcast a CloudEvents
``workflow.renamed`` envelope so other clients refresh.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fake Database — in-memory store with the same async surface
# ---------------------------------------------------------------------------


class _FakeDatabase:
    def __init__(self) -> None:
        self._rows: Dict[str, SimpleNamespace] = {}

    async def get_workflow(self, workflow_id: str):
        return self._rows.get(workflow_id)

    async def save_workflow(
        self,
        workflow_id: str,
        name: str,
        slug: str,
        data: Dict[str, Any],
        description: Optional[str] = None,
    ) -> bool:
        self._rows[workflow_id] = SimpleNamespace(
            id=workflow_id,
            name=name,
            slug=slug,
            description=description,
            data=data,
        )
        return True

    async def list_workflow_slugs(self) -> List[Tuple[str, str]]:
        return [(r.id, r.slug) for r in self._rows.values()]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_db():
    return _FakeDatabase()


@pytest.fixture
def workspace_root(tmp_path: Path):
    """Tmpdir as the ``workspace_base_resolved`` for the rename test."""
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


@pytest.fixture
def patched_handler_deps(fake_db, workspace_root):
    """Wire the handler's container + Settings + broadcaster to fakes.

    The handler reaches into:
      * ``core.container.container.database()`` — DB row store
      * ``core.config.Settings().workspace_base_resolved`` — folder root
      * ``services.status_broadcaster.get_status_broadcaster()`` — broadcast

    All three are patched here so the handler runs against the in-memory
    store + tmpdir workspace + spy broadcaster. ``status_broadcaster`` is
    stubbed at the ``sys.modules`` level because the real module pulls in
    ``orjson`` (not installed in the unit-test env) — the handler does a
    lazy ``from services.status_broadcaster import get_status_broadcaster``
    inside the function body, so the sys.modules entry intercepts cleanly.
    """
    from services.workflow_storage import handlers

    broadcaster_spy = MagicMock()
    broadcaster_spy.broadcast_workflow_lifecycle = AsyncMock()

    stub_module = types.ModuleType("services.status_broadcaster")
    stub_module.get_status_broadcaster = lambda: broadcaster_spy
    sentinel = object()
    original = sys.modules.get("services.status_broadcaster", sentinel)
    sys.modules["services.status_broadcaster"] = stub_module

    settings_stub = MagicMock()
    settings_stub.workspace_base_resolved = str(workspace_root)

    with patch.object(handlers, "container") as mock_container, \
         patch.object(handlers, "Settings", return_value=settings_stub):
        mock_container.database.return_value = fake_db
        try:
            yield SimpleNamespace(
                db=fake_db,
                workspace_root=workspace_root,
                broadcaster=broadcaster_spy,
            )
        finally:
            if original is sentinel:
                sys.modules.pop("services.status_broadcaster", None)
            else:
                sys.modules["services.status_broadcaster"] = original


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_new_workflow_allocates_slug(patched_handler_deps) -> None:
    from services.workflow_storage.handlers import handle_save_workflow

    result = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {"nodes": []}},
        websocket=None,
    )

    assert result["success"] is True
    assert result["workflow_id"] == "uuid-1"
    assert result["name"] == "AI Assistant"
    assert result["slug"] == "AI_Assistant_1"

    row = await patched_handler_deps.db.get_workflow("uuid-1")
    assert row.slug == "AI_Assistant_1"
    assert row.name == "AI Assistant"


@pytest.mark.asyncio
async def test_save_existing_workflow_same_name_keeps_slug(patched_handler_deps) -> None:
    """Re-saving with the same name must NOT bump the slug counter."""
    from services.workflow_storage.handlers import handle_save_workflow

    await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {"nodes": []}},
        websocket=None,
    )
    # Second save with same name — slug stays as _1.
    result = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {"nodes": [], "marker": "a"}},
        websocket=None,
    )

    assert result["slug"] == "AI_Assistant_1"
    # And no broadcast since nothing changed visibly.
    patched_handler_deps.broadcaster.broadcast_workflow_lifecycle.assert_not_called()


@pytest.mark.asyncio
async def test_rename_via_save_recomputes_slug(patched_handler_deps) -> None:
    """The whole user story: create, rename, observe new slug + UUID stable."""
    from services.workflow_storage.handlers import handle_save_workflow

    create = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {"nodes": []}},
        websocket=None,
    )
    assert create["slug"] == "AI_Assistant_1"

    rename = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "Cool Bot", "data": {"nodes": []}},
        websocket=None,
    )

    # UUID unchanged; slug + name updated.
    assert rename["workflow_id"] == "uuid-1"
    assert rename["name"] == "Cool Bot"
    assert rename["slug"] == "Cool_Bot_1"

    # DB reflects the same.
    row = await patched_handler_deps.db.get_workflow("uuid-1")
    assert row.id == "uuid-1"
    assert row.name == "Cool Bot"
    assert row.slug == "Cool_Bot_1"


@pytest.mark.asyncio
async def test_rename_moves_workspace_directory(patched_handler_deps) -> None:
    """When a workspace exists under the old slug, it follows the rename."""
    from services.workflow_storage.handlers import handle_save_workflow

    await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {"nodes": []}},
        websocket=None,
    )
    # Simulate execution having created the workspace folder.
    old_ws = patched_handler_deps.workspace_root / "AI_Assistant_1"
    old_ws.mkdir()
    (old_ws / "marker.txt").write_text("preserved")

    await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "Cool Bot", "data": {"nodes": []}},
        websocket=None,
    )

    new_ws = patched_handler_deps.workspace_root / "Cool_Bot_1"
    assert not old_ws.exists()
    assert new_ws.is_dir()
    assert (new_ws / "marker.txt").read_text() == "preserved"


@pytest.mark.asyncio
async def test_rename_without_workspace_dir_is_harmless(patched_handler_deps) -> None:
    """A workflow that never executed has no workspace dir to move."""
    from services.workflow_storage.handlers import handle_save_workflow

    await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {}},
        websocket=None,
    )

    # No folder on disk yet.
    assert not (patched_handler_deps.workspace_root / "AI_Assistant_1").exists()

    # Rename should still succeed.
    result = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "Cool Bot", "data": {}},
        websocket=None,
    )

    assert result["slug"] == "Cool_Bot_1"


@pytest.mark.asyncio
async def test_rename_broadcasts_workflow_lifecycle(patched_handler_deps) -> None:
    """Every rename emits ``workflow.renamed`` so other clients refresh."""
    from services.workflow_storage.handlers import handle_save_workflow

    await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {}},
        websocket=None,
    )
    patched_handler_deps.broadcaster.broadcast_workflow_lifecycle.reset_mock()

    await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "Cool Bot", "data": {}},
        websocket=None,
    )

    bcast = patched_handler_deps.broadcaster.broadcast_workflow_lifecycle
    bcast.assert_called_once()
    args, kwargs = bcast.call_args
    assert args == ("renamed",)
    assert kwargs == {
        "workflow_id": "uuid-1",
        "name": "Cool Bot",
        "slug": "Cool_Bot_1",
        "old_slug": "AI_Assistant_1",
    }


@pytest.mark.asyncio
async def test_three_same_name_workflows_get_sequential_slugs(patched_handler_deps) -> None:
    """Always-suffix semantics + sequential counter on collision."""
    from services.workflow_storage.handlers import handle_save_workflow

    r1 = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {}}, websocket=None,
    )
    r2 = await handle_save_workflow(
        {"workflow_id": "uuid-2", "name": "AI Assistant", "data": {}}, websocket=None,
    )
    r3 = await handle_save_workflow(
        {"workflow_id": "uuid-3", "name": "AI Assistant", "data": {}}, websocket=None,
    )

    assert (r1["slug"], r2["slug"], r3["slug"]) == (
        "AI_Assistant_1",
        "AI_Assistant_2",
        "AI_Assistant_3",
    )


@pytest.mark.asyncio
async def test_save_returns_slug_for_frontend_sync(patched_handler_deps) -> None:
    """Frontend reads ``slug`` from the save response to update its store
    without waiting for the broadcast. Contract: shape includes
    ``workflow_id``, ``name``, ``slug``, ``success``.
    """
    from services.workflow_storage.handlers import handle_save_workflow

    result = await handle_save_workflow(
        {"workflow_id": "uuid-1", "name": "AI Assistant", "data": {}},
        websocket=None,
    )

    assert set(result.keys()) >= {"success", "workflow_id", "name", "slug"}
    assert result["workflow_id"] == "uuid-1"
    assert result["slug"] == "AI_Assistant_1"
