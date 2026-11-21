"""Skill preparation stays responsive and refresh publishes complete snapshots."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from nodes.skill._expander import expand_master_skill
from services import skill_loader as module
from services.skill_loader import SkillLoader


def write_skill(root, name, description="A test skill", body="Original instructions"):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )
    return folder


@pytest.mark.asyncio
async def test_concurrent_cold_expansion_scans_once_off_the_event_loop(tmp_path, monkeypatch):
    write_skill(tmp_path, "research-skill", description="Find cited facts")
    monkeypatch.setattr(module, "_skill_loader", None)
    monkeypatch.setattr(module, "_default_skill_dirs", lambda: [tmp_path])
    monkeypatch.setattr(module, "_resolve_database", lambda: None)
    started, release = threading.Event(), threading.Event()
    scan_threads = []
    original = SkillLoader._scan_filesystem

    def blocked_scan(loader):
        scan_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3), "The event loop could not release the filesystem scan"
        return original(loader)

    monkeypatch.setattr(SkillLoader, "_scan_filesystem", blocked_scan)
    config = {"research-skill": {"enabled": True}}
    tasks = [asyncio.create_task(expand_master_skill("skills", config)) for _ in range(3)]
    try:
        assert await asyncio.to_thread(started.wait, 2)
        # We can run this coroutine while disk discovery remains blocked.
        assert not any(task.done() for task in tasks)
        assert scan_threads == [scan_threads[0]]
        assert scan_threads[0] != threading.get_ident()
    finally:
        release.set()
        results = await asyncio.gather(*tasks)
    assert all(entries[0]["description"] == "Find cited facts" for entries in results)
    assert len(scan_threads) == 1
    assert (await expand_master_skill("skills", config)) == results[0]
    assert len(scan_threads) == 1  # Warm expansion uses metadata, not another scan.


@pytest.mark.asyncio
async def test_personality_content_load_does_not_block_loop(tmp_path, monkeypatch):
    write_skill(tmp_path, "editor-personality", body="Be a precise editor")
    loader = SkillLoader([tmp_path])
    loader.scan_skills()
    monkeypatch.setattr(module, "_skill_loader", loader)
    monkeypatch.setattr(module, "_resolve_database", lambda: None)
    started, release = threading.Event(), threading.Event()
    original = loader.load_skill
    load_threads = []

    def blocked_load(name):
        load_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3), "The event loop could not release the content load"
        return original(name)

    monkeypatch.setattr(loader, "load_skill", blocked_load)
    task = asyncio.create_task(expand_master_skill("skills", {"editor-personality": {"enabled": True}}))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert not task.done()
        assert load_threads[0] != threading.get_ident()
    finally:
        release.set()
        entries = await task
    assert entries[0]["parameters"]["instructions"] == "Be a precise editor"


def test_refresh_replaces_snapshot_preserves_priority_and_invalidates_content(tmp_path, monkeypatch):
    builtin, project = tmp_path / "builtin", tmp_path / "project"
    write_skill(builtin, "shared-skill", description="Builtin")
    removed = write_skill(builtin, "removed-skill")
    write_skill(project, "shared-skill", description="Project", body="Old project instructions")
    loader = SkillLoader([builtin, project])
    loader.scan_skills()
    old_snapshot = loader.get_registry()
    assert loader.load_skill("shared-skill").instructions == "Old project instructions"
    write_skill(project, "shared-skill", description="Updated project", body="New instructions")
    (removed / "SKILL.md").unlink()
    write_skill(project, "added-skill")
    started, release = threading.Event(), threading.Event()
    original = loader._scan_filesystem

    def blocked_scan():
        registry = original()
        started.set()
        assert release.wait(3)
        return registry

    monkeypatch.setattr(loader, "_scan_filesystem", blocked_scan)
    with ThreadPoolExecutor(max_workers=1) as executor:
        refresh = executor.submit(loader.scan_skills)
        try:
            assert started.wait(2)
            assert loader.get_registry() == old_snapshot
        finally:
            release.set()
        refreshed = refresh.result(timeout=3)
    assert set(refreshed) == {"shared-skill", "added-skill"}
    assert refreshed["shared-skill"].description == "Updated project"
    assert old_snapshot["shared-skill"].description == "Project"
    assert loader.load_skill("shared-skill").instructions == "New instructions"
    assert loader.load_skill("removed-skill") is None


@pytest.mark.asyncio
async def test_inflight_database_load_cannot_repopulate_invalidated_cache():
    started, release = asyncio.Event(), asyncio.Event()

    class Database:
        async def get_user_skill(self, name):
            started.set()
            await release.wait()
            return {"name": name, "description": "Database skill", "instructions": "Old body"}

    loader = SkillLoader(database=Database())
    task = asyncio.create_task(loader.load_skill_async("user-skill"))
    await started.wait()
    loader.clear_cache()
    release.set()
    assert (await task).instructions == "Old body"
    assert "user-skill" not in loader._cache


def test_folder_import_merges_snapshot_and_invalidates_replaced_content(tmp_path):
    default, imported = tmp_path / "default", tmp_path / "imported"
    write_skill(default, "shared-skill", body="Default body")
    write_skill(default, "kept-skill")
    write_skill(imported, "shared-skill", body="Imported body")
    write_skill(imported, "extra-skill")
    loader = SkillLoader([default])
    loader.scan_skills()
    old_snapshot = loader.get_registry()
    assert loader.load_skill("shared-skill").instructions == "Default body"
    discovered = loader.scan_skill_folder(imported)
    assert set(discovered) == {"shared-skill", "extra-skill"}
    assert set(loader.get_registry()) == {"shared-skill", "kept-skill", "extra-skill"}
    assert set(old_snapshot) == {"shared-skill", "kept-skill"}
    assert loader.load_skill("shared-skill").instructions == "Imported body"


def test_folder_refresh_removes_deleted_and_renamed_skills(tmp_path):
    default, imported = tmp_path / "default", tmp_path / "imported"
    write_skill(default, "kept-skill")
    removed = write_skill(imported, "removed-skill")
    renamed = write_skill(imported, "old-name")
    loader = SkillLoader([default])
    loader.scan_skills()
    loader.scan_skill_folder(imported)
    assert loader.load_skill("removed-skill") is not None
    assert loader.load_skill("old-name") is not None
    old_snapshot = loader.get_registry()

    (removed / "SKILL.md").unlink()
    (renamed / "SKILL.md").write_text(
        "---\nname: new-name\ndescription: Renamed skill\n---\nNew body", encoding="utf-8"
    )
    discovered = loader.scan_skill_folder(imported)

    assert set(discovered) == {"new-name"}
    assert set(loader.get_registry()) == {"kept-skill", "new-name"}
    assert set(old_snapshot) == {"kept-skill", "removed-skill", "old-name"}
    assert loader.load_skill("removed-skill") is None
    assert loader.load_skill("old-name") is None
    assert loader.load_skill("new-name").instructions == "New body"


def test_scoped_refresh_preserves_priority_and_restores_shadowed_sources(tmp_path):
    builtin, project, imported = [tmp_path / name for name in ("builtin", "project", "imported")]
    write_skill(builtin, "shared", body="Builtin")
    project_skill = write_skill(project, "shared", body="Project")
    imported_skill = write_skill(imported, "shared", body="Imported")
    loader = SkillLoader([builtin, project])
    loader.scan_skills()
    loader.scan_skill_folder(imported)
    write_skill(builtin, "shared", body="Revised builtin")
    loader.scan_skill_folder(builtin)
    assert loader.load_skill("shared").instructions == "Imported"

    (imported_skill / "SKILL.md").unlink()
    loader.scan_skill_folder(imported)
    assert loader.load_skill("shared").instructions == "Project"
    (project_skill / "SKILL.md").unlink()
    loader.scan_skill_folder(project)
    assert loader.load_skill("shared").instructions == "Revised builtin"


async def test_database_override_survives_scoped_filesystem_refresh(tmp_path, real_database):
    write_skill(tmp_path, "shared", body="Builtin")
    await real_database.create_user_skill(
        "shared", "Shared", "Database copy", "Library instructions",
        allowed_tools="clock,todos", metadata_json={"source": "library"},
    )
    loader = SkillLoader([tmp_path], database=real_database)
    metadata = (await loader.scan_skills_with_database())["shared"]
    assert metadata.path is None
    assert metadata.allowed_tools == ["clock", "todos"]
    assert metadata.metadata == {"source": "library"}
    loader.scan_skill_folder(tmp_path)
    assert loader.get_registry()["shared"] == metadata
    assert (await loader.load_skill_async("shared")).instructions == "Library instructions"
