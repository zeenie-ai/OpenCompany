"""Colon-namespaced node ids must never reach a path join raw.

Regression: on Windows, ``ntpath.join(workspace, "2:processManager:4")``
parses ``2:`` as a drive and discards the workspace prefix, so the
processManager ``agent_dir`` default resolved outside every containment
root and the ProcessService guardrail rejected every cwd-less start —
the multi-agent "Working directory must be inside workspace" loop.
"""

from __future__ import annotations

import ntpath
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.paths import safe_path_component
from nodes.utility.process_manager import ProcessManagerNode, ProcessManagerParams
from services.plugin import NodeContext
from services.process_service import ProcessService

WORKSPACE_BASE = r"D:\ws\AI_Employee_1"
COLON_NODE_ID = "2:processManager:4"


def test_safe_path_component_neutralizes_colon_namespaced_ids() -> None:
    assert safe_path_component(COLON_NODE_ID) == "2_processManager_4"
    assert ":" not in safe_path_component("321151185156422f965c63a299945da7:processManager:4")


def test_safe_path_component_collapses_dot_only_values() -> None:
    assert safe_path_component("..", "node") == "node"
    assert safe_path_component("", "node") == "node"
    assert safe_path_component(".", "node") == "node"


def test_ntpath_drive_hazard_is_neutralized_by_sanitize_first() -> None:
    # The hazard this fix exists for: the raw id replaces the base outright.
    assert ntpath.join(WORKSPACE_BASE, COLON_NODE_ID) == COLON_NODE_ID
    assert ntpath.join(WORKSPACE_BASE, safe_path_component(COLON_NODE_ID)).startswith(WORKSPACE_BASE)


class _CaptureService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def start(self, name, command, workflow_id, working_directory="", *, ports=None, extra_env=None):
        self.calls.append({"name": name, "working_directory": working_directory})
        return {"success": True, "pid": 1, "status": "running"}


async def test_cwd_less_start_defaults_to_sanitized_agent_dir(monkeypatch, tmp_path) -> None:
    svc = _CaptureService()
    monkeypatch.setattr("services.process_service.get_process_service", lambda: svc)

    ctx = NodeContext(
        node_id=COLON_NODE_ID,
        node_type="processManager",
        workflow_id="2",
        workspace_dir=str(tmp_path),
    )
    result = await ProcessManagerNode().dispatch(
        ctx, ProcessManagerParams(operation="start", name="web", command="python -V")
    )

    assert result["success"] is True
    wd = Path(svc.calls[0]["working_directory"])
    assert wd == tmp_path / "2_processManager_4"
    assert wd.is_relative_to(tmp_path)


async def test_explicit_cwd_passes_through_unchanged(monkeypatch, tmp_path) -> None:
    svc = _CaptureService()
    monkeypatch.setattr("services.process_service.get_process_service", lambda: svc)

    explicit = str(tmp_path / "app")
    ctx = NodeContext(
        node_id=COLON_NODE_ID,
        node_type="processManager",
        workflow_id="2",
        workspace_dir=str(tmp_path),
    )
    await ProcessManagerNode().dispatch(
        ctx, ProcessManagerParams(operation="start", name="web", command="python -V", cwd=explicit)
    )

    assert svc.calls[0]["working_directory"] == explicit


@pytest.mark.parametrize("raw", [{}, {"runtime_behavior_version": 1}], ids=["legacy", "v1"])
async def test_relative_cwd_resolves_inside_the_workspace_for_every_agent(monkeypatch, tmp_path, raw) -> None:
    svc = _CaptureService()
    monkeypatch.setattr("services.process_service.get_process_service", lambda: svc)

    ctx = NodeContext(
        node_id=COLON_NODE_ID,
        node_type="processManager",
        workflow_id="2",
        workspace_dir=str(tmp_path),
        raw=raw,
    )
    node = ProcessManagerNode()
    result = await node.dispatch(
        ctx, ProcessManagerParams(operation="start", name="site", command="python -V", cwd="weather-site")
    )
    await node.dispatch(ctx, ProcessManagerParams(operation="start", name="root", command="python -V", cwd="."))

    assert Path(svc.calls[0]["working_directory"]) == (tmp_path / "weather-site").resolve()
    assert Path(svc.calls[1]["working_directory"]) == tmp_path.resolve()
    # Runtime facts stay a version-1 feature.
    assert ("runtime_context" in result) == bool(raw)


async def test_guardrail_rejection_names_the_rejected_path(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspaces"
    workspace.mkdir()
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(workspace_base_resolved=str(workspace)))
    monkeypatch.setattr("core.paths.daemons_dir", lambda: tmp_path / "daemons")

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    result = await ProcessService().start("web", "python -V", working_directory=str(outside))

    assert result["success"] is False
    assert "elsewhere" in result["error"]
    assert "relative to the workflow workspace" in result["error"]
