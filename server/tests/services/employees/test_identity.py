"""Who an employee is (services/employees/handlers.py): renaming renames
their workflow and the opening of the instructions a hire wrote, and their
photo is an image the owner uploaded, served by the workspace file route."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models.employees import Employee
from services.employees import handlers

HIRED = "You are Maya, front desk for Sam. You are an AI employee inside OpenCompany: you do real work with the owner's apps."
AGENT = "1:aiAgent:1"


@pytest.fixture
def setup(monkeypatch, real_database, tmp_path):
    class Auth:
        async def has_valid_key(self, key, *, principal=None):
            return False

        async def get_oauth_tokens(self, provider):
            return None

        async def list_api_key_providers(self, *, principal=None):
            return []

    import core.container as container_module
    import services.plugin.deps as deps

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database, auth_service=lambda: Auth()))
    monkeypatch.setattr(deps, "get_auth_service", lambda: Auth())

    frames = []

    class Broadcaster:
        async def broadcast(self, message):
            frames.append(message)

        async def broadcast_node_parameters_updated(self, node_id, **kwargs):
            frames.append({"type": "node_parameters_updated", "node_id": node_id, **kwargs})

        async def broadcast_workflow_lifecycle(self, stage, **data):
            frames.append({"type": "workflow_lifecycle", "stage": stage, **data})

    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: Broadcaster())
    moves = []
    monkeypatch.setattr("services.workflow_storage.handlers._move_workspace", lambda old, new: moves.append((old, new)))
    changed = []
    monkeypatch.setattr(handlers, "employee_changed_now", changed.append)

    root = tmp_path / "workspace"
    (root / "uploads").mkdir(parents=True)

    async def workspace_root(workflow_id, database, *, allow_default=True):
        return root

    monkeypatch.setattr("services.workspace_locator.resolve_workspace_root", workspace_root)
    return SimpleNamespace(database=real_database, frames=frames, moves=moves, changed=changed, root=root)


async def hire(database, *, system_message=HIRED, employee=True):
    graph = {"nodes": [{"id": AGENT, "type": "aiAgent", "data": {"label": "Maya"}}], "edges": []}
    assert await database.save_workflow(workflow_id="1", name="Maya", slug="Maya_1", data=graph)
    if employee:
        async with database.get_session() as session:
            session.add(Employee(id="e1", workflow_id="1", owner_id="owner", hire_state="ready"))
            await session.commit()
    await database.save_node_parameters(AGENT, {"provider": "openai", "system_message": system_message})


def types(frames):
    return [frame["type"] for frame in frames]


# ----- the name -----


async def test_renaming_renames_the_workflow_and_the_instructions(setup):
    await hire(setup.database)
    answer = await handlers.handle_rename_employee({"workflow_id": "1", "name": "  Ana   Lopez "}, None)
    assert answer["success"] is True and answer["employee"]["name"] == "Ana Lopez"
    workflow = await setup.database.get_workflow("1")
    assert workflow.name == "Ana Lopez" and workflow.slug != "Maya_1"
    assert setup.moves == [("Maya_1", workflow.slug)]
    params = await setup.database.get_node_parameters(AGENT)
    assert params["system_message"].startswith("You are Ana Lopez, front desk for Sam. You are an AI employee")
    assert params["provider"] == "openai"
    assert types(setup.frames) == ["workflow_lifecycle", "node_parameters_updated"]
    renamed = setup.frames[0]
    assert (renamed["stage"], renamed["name"], renamed["old_slug"]) == ("renamed", "Ana Lopez", "Maya_1")


async def test_instructions_the_owner_rewrote_keep_their_words(setup):
    await hire(setup.database, system_message="Be brief and kind.")
    assert (await handlers.handle_rename_employee({"workflow_id": "1", "name": "Ana"}, None))["success"] is True
    assert (await setup.database.get_node_parameters(AGENT))["system_message"] == "Be brief and kind."
    assert types(setup.frames) == ["workflow_lifecycle"]


async def test_the_same_name_changes_nothing(setup):
    await hire(setup.database)
    assert (await handlers.handle_rename_employee({"workflow_id": "1", "name": "Maya"}, None))["success"] is True
    assert setup.frames == [] and setup.moves == []


async def test_a_rename_that_cannot_be_made(setup):
    await hire(setup.database)
    assert await handlers.handle_rename_employee({"workflow_id": "1", "name": "   "}, None) == {"success": False, "error": "invalid_request"}
    assert await handlers.handle_rename_employee({"name": "Ana"}, None) == {"success": False, "error": "invalid_request"}
    missing = await handlers.handle_rename_employee({"workflow_id": "9", "name": "Ana"}, None)
    assert (missing["success"], missing["error"]) == (False, "not_found")
    # Cut to the hire's own limit.
    long = await handlers.handle_rename_employee({"workflow_id": "1", "name": "A" * 60}, None)
    assert long["employee"]["name"] == "A" * 40


# ----- the photo -----


def upload(root, name, data=b"\x89PNG" + b"0" * 60):
    (root / "uploads" / name).write_bytes(data)
    return f"uploads/{name}"


async def test_the_photo_is_an_image_the_owner_uploaded(setup):
    await hire(setup.database)
    path = upload(setup.root, "me.png")
    answer = await handlers.handle_set_employee_photo({"workflow_id": "1", "path": path}, None)
    assert answer["success"] is True
    assert answer["employee"]["photo_url"].startswith("/api/workspace/1/files/uploads/me.png?v=")
    assert setup.changed == ["1"]
    cleared = await handlers.handle_set_employee_photo({"workflow_id": "1", "path": None}, None)
    assert cleared["success"] is True and cleared["employee"]["photo_url"] is None


@pytest.mark.parametrize(
    "name, data",
    [
        ("drawing.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("notes.txt", b"hello"),
    ],
)
async def test_only_a_raster_image_can_be_a_photo(setup, name, data):
    await hire(setup.database)
    answer = await handlers.handle_set_employee_photo({"workflow_id": "1", "path": upload(setup.root, name, data)}, None)
    assert (answer["success"], answer["error"]) == (False, "invalid_photo")


async def test_what_else_cannot_be_a_photo(setup, monkeypatch):
    await hire(setup.database)
    for path in ("uploads/missing.png", "elsewhere/me.png", "../me.png"):
        answer = await handlers.handle_set_employee_photo({"workflow_id": "1", "path": path}, None)
        assert (answer["success"], answer["error"]) == (False, "invalid_photo"), path
    monkeypatch.setattr(handlers, "EMPLOYEE_PHOTO_MAX_BYTES", 10)
    big = await handlers.handle_set_employee_photo({"workflow_id": "1", "path": upload(setup.root, "big.png")}, None)
    assert (big["error"], big["detail"]) == ("invalid_photo", "A photo is at most 0 MB.")
    assert (await handlers.handle_set_employee_photo({"workflow_id": "1", "path": 5}, None))["error"] == "invalid_request"
    assert setup.changed == []


async def test_a_workflow_built_in_the_editor_keeps_its_initial(setup):
    await hire(setup.database, employee=False)
    answer = await handlers.handle_set_employee_photo({"workflow_id": "1", "path": upload(setup.root, "me.png")}, None)
    assert (answer["success"], answer["error"]) == (False, "unsupported")
