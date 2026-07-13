"""Compatibility contracts for the MachinaOS -> OpenCompany rebrand."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_real_paths_module():
    path = Path(__file__).parents[1] / "core" / "paths.py"
    spec = importlib.util.spec_from_file_location("_opencompany_paths_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TestStatePathCompatibility:
    def test_canonical_root_falls_back_to_existing_legacy_sibling(self, tmp_path):
        paths = _load_real_paths_module()
        canonical = tmp_path / ".opencompany"
        legacy = tmp_path / ".machina"
        legacy.mkdir()
        (legacy / "workflow.db").touch()

        assert paths._resolve_data_path(str(canonical)) == legacy.resolve()

    def test_empty_canonical_root_does_not_hide_legacy_state(self, tmp_path):
        paths = _load_real_paths_module()
        canonical = tmp_path / ".opencompany"
        legacy = tmp_path / ".machina"
        canonical.mkdir()
        legacy.mkdir()
        (legacy / "credentials.db").touch()

        assert paths._resolve_data_path(str(canonical)) == legacy.resolve()

    def test_existing_canonical_runtime_state_wins(self, tmp_path):
        paths = _load_real_paths_module()
        canonical = tmp_path / ".opencompany"
        legacy = tmp_path / ".machina"
        canonical.mkdir()
        legacy.mkdir()
        (canonical / "workflow.db").touch()
        (legacy / "workflow.db").touch()

        assert paths._resolve_data_path(str(canonical)) == canonical.resolve()

    def test_shipped_workflows_do_not_hide_legacy_runtime_state(self, tmp_path):
        paths = _load_real_paths_module()
        canonical = tmp_path / ".opencompany"
        legacy = tmp_path / ".machina"
        (canonical / "workflows").mkdir(parents=True)
        legacy.mkdir()
        (legacy / "workflow.db").touch()

        assert paths._resolve_data_path(
            str(canonical), "workflow.db"
        ) == (legacy / "workflow.db").resolve()
        monkeypatch_root = paths._REPO_ROOT
        try:
            paths._REPO_ROOT = tmp_path
            assert paths.example_workflows_dir() == canonical / "workflows"
        finally:
            paths._REPO_ROOT = monkeypatch_root

    def test_legacy_root_function_is_an_alias(self, monkeypatch, tmp_path):
        paths = _load_real_paths_module()
        monkeypatch.setattr(paths, "data_path", lambda *_args: tmp_path)

        assert paths.opencompany_root() == tmp_path
        assert paths.machina_root() == tmp_path

    def test_example_workflows_fall_back_to_legacy(self, monkeypatch, tmp_path):
        paths = _load_real_paths_module()
        monkeypatch.setattr(paths, "_REPO_ROOT", tmp_path)
        legacy = tmp_path / ".machina" / "workflows"
        legacy.mkdir(parents=True)

        assert paths.example_workflows_dir() == legacy

        canonical = tmp_path / ".opencompany" / "workflows"
        canonical.mkdir(parents=True)
        assert paths.example_workflows_dir() == canonical


class TestSessionCookieCompatibility:
    def test_canonical_cookie_wins_and_legacy_is_accepted(self):
        from core.auth_cookies import get_session_token, session_cookie_names

        settings = SimpleNamespace(jwt_cookie_name="opencompany_token")
        assert session_cookie_names(settings) == ("opencompany_token", "machina_token")
        assert get_session_token({"machina_token": "legacy"}, settings) == "legacy"
        assert get_session_token(
            {"opencompany_token": "canonical", "machina_token": "legacy"},
            settings,
        ) == "canonical"


class TestCloudEventsCompatibility:
    def test_canonical_namespace_and_legacy_matching(self):
        from services.events.envelope import WorkflowEvent, equivalent_event_types

        event = WorkflowEvent(
            source="opencompany://services/test",
            type="com.opencompany.test.updated",
        )
        assert event.dataschema == "opencompany://schemas/events/test.updated.json"
        assert event.matches_type("test.*")
        assert equivalent_event_types(event.type) == (
            "com.opencompany.test.updated",
            "com.machinaos.test.updated",
        )

        legacy = WorkflowEvent(
            source="machinaos://services/test",
            type="com.machinaos.test.updated",
        )
        assert legacy.matches_type("test.updated")
        assert legacy.dataschema == "opencompany://schemas/events/test.updated.json"

    @pytest.mark.asyncio
    async def test_temporal_visibility_query_includes_legacy_alias(self, monkeypatch):
        from core.container import container
        from services.events.dispatch import _signal_running_consumers
        from services.events.envelope import WorkflowEvent

        captured = {}

        class FakeClient:
            def list_workflows(self, *, query):
                captured["query"] = query

                async def _empty():
                    if False:
                        yield None

                return _empty()

        monkeypatch.setattr(
            container,
            "temporal_client",
            lambda: SimpleNamespace(client=FakeClient()),
        )
        await _signal_running_consumers(
            WorkflowEvent(
                source="opencompany://services/test",
                type="com.opencompany.test.updated",
            )
        )

        assert "EventType='com.opencompany.test.updated'" in captured["query"]
        assert "EventType='com.machinaos.test.updated'" in captured["query"]
