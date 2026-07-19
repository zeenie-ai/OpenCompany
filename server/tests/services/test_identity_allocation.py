import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.workflow_naming import canonicalize_node_ids


def _load_database(db_path: Path):
    module_name = f"tests._identity_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().parents[2] / "core" / "database.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.Database(SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        database_echo=False, database_pool_size=5, database_max_overflow=5,
    ))


def test_node_ids_are_plugin_derived_repeatable_and_idempotent():
    nodes = [
        {"id": "old-a", "type": "aiAgent"},
        {"id": "old-b", "type": "aiAgent"},
        {"id": "old-start", "type": "start"},
    ]
    edges = [{"id": "edge", "source": "old-start", "target": "old-a"}]
    migrated_nodes, migrated_edges, aliases = canonicalize_node_ids("7", nodes, edges)
    assert [node["id"] for node in migrated_nodes] == [
        "7:aiAgent:1", "7:aiAgent:2", "7:start:1",
    ]
    assert migrated_edges[0]["source"] == "7:start:1"
    assert migrated_edges[0]["target"] == "7:aiAgent:1"
    assert canonicalize_node_ids("7", migrated_nodes, migrated_edges)[2] == {}
    assert aliases["old-b"] == "7:aiAgent:2"


@pytest.mark.asyncio
async def test_allocators_are_atomic_and_execution_ids_are_workflow_scoped():
    path = Path.cwd() / f".identity-{uuid.uuid4().hex}.db"
    database = _load_database(path)
    await database.startup()
    try:
        allocated = await asyncio.gather(*(database.allocate_workflow_id() for _ in range(12)))
        assert sorted(map(int, allocated)) == list(range(1, 13))
        executions = await asyncio.gather(*(database.allocate_execution_id("3") for _ in range(4)))
        assert sorted(executions) == [
            "3:execution:1", "3:execution:2", "3:execution:3", "3:execution:4",
        ]
    finally:
        await database.shutdown()
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            candidate.unlink(missing_ok=True)
