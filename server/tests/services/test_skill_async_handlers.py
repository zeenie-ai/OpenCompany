"""Filesystem skill operations must not stall the shared asyncio loop."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import skill_loader, skill_runtime
from services.skill_loader import SkillLoader
from services.skills import handlers


@pytest.fixture
def skill_files(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    directory = root / "custom" / "example"
    directory.mkdir(parents=True)
    path = directory / "SKILL.md"
    path.write_text("---\nname: example\ndescription: Example skill\n---\nOriginal body", encoding="utf-8")
    loader = SkillLoader([root])
    loader.scan_skills()
    monkeypatch.setattr(skill_loader, "_skill_loader", loader)
    monkeypatch.setattr(skill_loader, "_resolve_database", lambda: None)
    monkeypatch.setattr(handlers, "__file__", str(tmp_path / "services" / "skills" / "handlers.py"))
    return loader, path


async def test_skill_tool_keeps_loop_responsive_during_content_loading(skill_files, monkeypatch):
    loader, _ = skill_files
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original = loader.load_skill

    def blocked_load(name):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(5), "The event loop did not release the filesystem read"
        return original(name)

    monkeypatch.setattr(loader, "load_skill", blocked_load)
    monkeypatch.setattr(skill_runtime, "_event", AsyncMock())
    monkeypatch.setattr(skill_runtime, "_loaded", {})
    descriptor = {"skill_name": "example", "node_id": "holder_example", "parameters": {}}
    config = {"parameters": {"skill_descriptors": [descriptor]}, "parent_node_id": "agent"}
    pending = asyncio.create_task(skill_runtime.execute_skill_tool({"skill_name": "example"}, config))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not pending.done()
    finally:
        release.set()
        result = await pending
    assert result["instructions"] == "Original body"
    assert result["status"] == "loaded"


async def test_folder_refresh_runs_off_loop_and_publishes_metadata(skill_files, monkeypatch):
    loader, path = skill_files
    path.write_text("---\nname: example\ndescription: Updated description\n---\nUpdated body", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    original = loader._parse_skill_metadata

    def blocked_parse(path):
        assert threading.get_ident() != loop_thread
        entered.set()
        assert release.wait(5), "The event loop did not release the metadata read"
        return original(path)

    monkeypatch.setattr(loader, "_parse_skill_metadata", blocked_parse)
    pending = asyncio.create_task(handlers.handle_scan_skill_folder({"folder": "custom"}, None))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        assert not pending.done()
        assert loader.get_registry()["example"].description == "Example skill"
    finally:
        release.set()
        result = await pending
    assert result["success"], result
    assert result["skills"][0]["description"] == "Updated description"
    assert loader.get_registry()["example"].description == "Updated description"


async def test_empty_metadata_lookup_does_not_trigger_another_scan(tmp_path, monkeypatch):
    loader = SkillLoader([tmp_path])
    loader.scan_skills()
    monkeypatch.setattr(skill_loader, "_skill_loader", loader)
    monkeypatch.setattr(skill_loader, "_resolve_database", lambda: None)

    def unexpected_scan():
        raise AssertionError("A cached empty registry is valid")

    monkeypatch.setattr(loader, "scan_skills", unexpected_scan)
    result = await handlers.handle_lookup_skill_metadata({"names": ["missing"]}, None)
    assert result == {"success": True, "skills": []}


async def test_builtin_save_invalidates_loaded_content_and_preserves_frontmatter(skill_files, monkeypatch):
    from nodes.skill.master_skill import _events

    loader, path = skill_files
    monkeypatch.setattr(_events, "broadcast_skill_lifecycle", AsyncMock())
    assert (await loader.load_skill_async("example")).instructions == "Original body"
    result = await handlers.handle_save_skill_content({"skill_name": "example", "instructions": "Revised body"}, None)
    assert result["success"], result
    assert path.read_text(encoding="utf-8").startswith("---\nname: example\ndescription: Example skill\n---\n")
    loaded = await handlers.handle_get_skill_content({"skill_name": "example"}, None)
    assert loaded["instructions"] == "Revised body"
    reset = await handlers.handle_reset_skill({"skill_name": "example"}, None)
    assert reset["original_content"] == "Revised body"


async def test_user_skill_edits_and_deletion_invalidate_loaded_content(real_database, monkeypatch):
    from nodes.skill.master_skill import _events

    monkeypatch.setattr(handlers, "container", SimpleNamespace(database=lambda: real_database))
    monkeypatch.setattr(skill_loader, "_skill_loader", SkillLoader(database=real_database))
    monkeypatch.setattr(_events, "broadcast_skill_lifecycle", AsyncMock())
    name = "review-skill"
    content = {"skill_name": name}
    created = await handlers.handle_create_user_skill(
        {"name": name, "display_name": "Review", "instructions": "Draft"}, None
    )
    assert created["success"]
    assert (await handlers.handle_get_skill_content(content, None))["instructions"] == "Draft"

    saved = await handlers.handle_save_skill_content({**content, "instructions": "Review"}, None)
    assert saved["success"]
    assert (await handlers.handle_get_skill_content(content, None))["instructions"] == "Review"

    updated = await handlers.handle_update_user_skill({"name": name, "instructions": "Publish"}, None)
    assert updated["success"]
    assert (await handlers.handle_get_skill_content(content, None))["instructions"] == "Publish"

    deleted = await handlers.handle_delete_user_skill({"name": name}, None)
    assert deleted["success"]
    assert (await handlers.handle_get_skill_content(content, None))["success"] is False
