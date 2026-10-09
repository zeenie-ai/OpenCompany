"""The Workspace's step log (services/workspace_steps.py) against real SQLite:
steps kept in order and pruned to the newest, the identity-only broadcast,
best effort when the database fails, the owner-only list and the cleanup on
workflow delete; and what the Browser, Canvas and phone record."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import workspace_steps
from services.workspace_steps import list_steps, record_step


@pytest.fixture
async def database(tmp_path: Path):
    module_name = f"tests._workspace_steps_db_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "core" / "database.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db = module.Database(
        SimpleNamespace(
            database_url=f"sqlite+aiosqlite:///{(tmp_path / 'steps.db').as_posix()}",
            database_echo=False,
            database_pool_size=5,
            database_max_overflow=5,
        )
    )
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()
        sys.modules.pop(module_name, None)


@pytest.fixture
def frames(monkeypatch):
    import services.status_broadcaster as status_broadcaster

    sent = []

    class Broadcaster:
        async def broadcast(self, message):
            sent.append(message)

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    return sent


async def test_steps_are_kept_in_order_and_pruned_to_the_newest(database, frames, monkeypatch):
    monkeypatch.setattr(workspace_steps, "MAX_STEPS_PER_WORKFLOW", 3)
    for index in range(5):
        await record_step(database, workflow_id="wf", surface="browser", text=f"Step {index}", node_id="wf:browser:1")
    await record_step(database, workflow_id="other", surface="canvas", text="Elsewhere")
    steps = await list_steps(database, "wf")
    assert [step["text"] for step in steps] == ["Step 2", "Step 3", "Step 4"]
    assert steps[0]["surface"] == "browser" and steps[0]["node_id"] == "wf:browser:1" and steps[0]["at"].endswith("+00:00")
    # Identity only: which workflow, which surface, which step.
    last = frames[-2]
    assert last["type"] == "workspace_step"
    assert last["data"]["type"] == "com.opencompany.workspace.step" and last["data"]["subject"] == "wf"
    assert last["data"]["data"] == {"workflow_id": "wf", "surface": "browser", "step_id": steps[-1]["id"]}


async def test_what_is_not_a_step_is_not_kept(database, frames):
    await record_step(database, workflow_id=None, surface="browser", text="Unsaved run")
    await record_step(database, workflow_id="wf", surface="browser", text="  ")
    await record_step(database, workflow_id="wf", surface="desktop", text="Not a surface")  # type: ignore[arg-type]
    assert await list_steps(database, "wf") == [] and frames == []


async def test_a_step_that_cannot_be_saved_never_raises(frames):
    class Broken:
        engine = object()

        def get_session(self):
            raise RuntimeError("database down")

    await record_step(Broken(), workflow_id="wf", surface="canvas", text="Showed a note on the Canvas")
    assert frames == []


async def test_only_the_owner_reads_the_steps_and_deleting_the_workflow_deletes_them(database, frames, monkeypatch):
    import core.container

    await database.save_workflow("wf", "Maya", "Maya_1", {"nodes": [], "edges": [], "owner_id": "owner"})
    await record_step(database, workflow_id="wf", surface="mobile", text="Tapped the screen")
    monkeypatch.setattr(core.container, "container", SimpleNamespace(database=lambda: database))
    owner = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))

    listed = await workspace_steps.handle_workspace_steps_list({"workflow_id": "wf"}, owner)
    assert listed["success"] is True and [step["text"] for step in listed["steps"]] == ["Tapped the screen"]
    stranger = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="someone-else"))
    worker = SimpleNamespace(scope={"path": "/ws/internal"}, state=SimpleNamespace(user_id="owner"))
    for socket, request in ((stranger, {"workflow_id": "wf"}), (worker, {"workflow_id": "wf"}), (owner, {"workflow_id": "default"})):
        assert await workspace_steps.handle_workspace_steps_list(request, socket) == {"success": False, "error": "access_denied"}
    assert (await workspace_steps.handle_workspace_steps_list({}, owner))["success"] is False

    await workspace_steps._on_workflow_deleted(database, "wf")
    assert await list_steps(database, "wf") == []


def test_the_browser_names_the_action_and_the_site_never_what_was_typed():
    from nodes.browser._steps import step_text

    assert step_text("navigate", "list", "https://shop.example.com/cart") == "Opened shop.example.com"
    assert step_text("type", "list", "https://shop.example.com/login") == "Typed into a field on shop.example.com"
    assert step_text("screenshot", "list", None) == "Took a screenshot of the page"
    assert step_text("tabs", "new", "https://example.com") == "Opened a new tab on example.com"
    assert step_text("tabs", "list", "https://example.com") is None
    assert step_text("snapshot", "list", "https://example.com") is None


async def test_the_browser_records_nothing_during_a_protected_login(monkeypatch):
    from nodes.browser._steps import record_browser_step

    recorded = []

    async def record(database, **step):
        recorded.append(step)

    monkeypatch.setattr("services.workspace_steps.record_step", record)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: "db")
    ctx = SimpleNamespace(workflow_id="wf", node_id="wf:browser:1")
    call = SimpleNamespace(tab_action="list")
    page = SimpleNamespace(success=True, url="https://bank.example.com")
    await record_browser_step(ctx, "type", call, page, SimpleNamespace(sensitive_login=True))
    await record_browser_step(ctx, "click", call, SimpleNamespace(success=False, url=page.url), SimpleNamespace(sensitive_login=False))
    assert recorded == []
    await record_browser_step(ctx, "click", call, page, SimpleNamespace(sensitive_login=False))
    assert recorded == [{"workflow_id": "wf", "surface": "browser", "text": "Clicked on bank.example.com", "node_id": "wf:browser:1"}]


async def test_a_phone_task_records_its_actions_never_its_text(monkeypatch):
    from nodes.mobile._runtime import MobileRuntime

    recorded = []

    async def record(database, **step):
        recorded.append(step)

    monkeypatch.setattr("services.workspace_steps.record_step", record)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: "db")
    runtime = MobileRuntime()
    runtime.active = {"run_id": "run-1", "workflow_id": "wf", "node_id": "wf:mobile_agent:1"}
    await runtime._record_task_step("run-1", "text", {"text": "my secret"})
    await runtime._record_task_step("run-1", "key", {"key": "back"})
    await runtime._record_task_step("run-1", "touch", {"action": "down"})
    await runtime._record_task_step("another-run", "tap", {})
    assert [step["text"] for step in recorded] == ["Typed on the phone", "Went back"]
    assert all(step["workflow_id"] == "wf" and step["surface"] == "mobile" for step in recorded)
