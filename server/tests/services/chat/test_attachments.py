"""Files the owner attaches to a chat message (services/chat/attachments.py):
rebuilt from what was uploaded under ``uploads/``, never taken from the
client; carried on the message, to the employee as a bracketed line after
the owner's words and, for images, on the message itself; and what the
message box offers (``get_chat_context``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.chat import attachments, guide, ledger
from tests.services.chat._helpers import talking

CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "workspace"
    (root / "uploads").mkdir(parents=True)

    async def fake_root(workflow_id, database, *, allow_default=True):
        if workflow_id != "wf":
            raise ValueError("no such workflow")
        return root

    monkeypatch.setattr("services.workspace_locator.resolve_workspace_root", fake_root)
    return root


def upload(root: Path, name: str, data: bytes = b"data") -> str:
    (root / "uploads" / name).write_bytes(data)
    return f"uploads/{name}"


async def test_attachments_are_rebuilt_from_the_files(database, workspace):
    photo = upload(workspace, "shelf.png", b"\x89PNG" + b"0" * 60)
    sheet = upload(workspace, "prices.txt", b"a,b\n1,2\n")
    claimed = [{"path": photo, "size_bytes": 1, "mime_type": "text/html", "url": "https://evil.example"}, sheet, photo]
    refs = await attachments.check_attachments(database, "wf", claimed)
    assert [ref["path"] for ref in refs] == [photo, sheet]
    shelf, prices = refs
    assert (shelf["kind"], shelf["mime_type"], shelf["size_bytes"], shelf["filename"]) == ("image", "image/png", 64, "shelf.png")
    assert shelf["url"] == f"/api/workspace/wf/files/{photo}" and shelf["workflow_id"] == "wf"
    assert (prices["kind"], prices["mime_type"]) == ("file", "text/plain")
    assert await attachments.check_attachments(database, "wf", None) == []


@pytest.mark.parametrize(
    "raw, reason",
    [
        ("uploads/shelf.png", "list"),
        (["notes.txt"], "uploaded"),
        (["../uploads/x.png"], "uploaded"),
        (["uploads/../secret.txt"], "uploaded"),
        ([{"path": 5}], "names a file"),
        (["uploads/missing.png"], "no longer there"),
        ([f"uploads/f{i}.txt" for i in range(7)], "at most 6"),
    ],
)
async def test_what_cannot_be_attached(database, workspace, raw, reason):
    with pytest.raises(attachments.AttachmentRefused, match=reason):
        await attachments.check_attachments(database, "wf", raw)


def test_what_the_employee_reads_and_sees():
    refs = [
        {"path": "uploads/shelf.png", "filename": "shelf.png", "mime_type": "image/png", "size_bytes": 64, "workflow_id": "wf"},
        {"path": "uploads/prices.csv", "filename": "prices.csv", "mime_type": "text/csv", "size_bytes": 8, "workflow_id": "wf"},
    ]
    line = attachments.attachments_line(refs)
    assert line.startswith("[attachments]{") and line.endswith("}[/attachments]")
    assert '"path": "uploads/prices.csv"' in line and '"type": "image/png"' in line
    assert attachments.attachments_line([]) == ""
    assert [ref["path"] for ref in attachments.image_refs(refs)] == ["uploads/shelf.png"]


# ----- sending ------------------------------------------------------------------------


async def test_a_message_carries_its_files_to_the_employee(chat, workspace):
    await talking(chat.database)
    photo = upload(workspace, "shelf.png", b"\x89PNG" + b"0" * 60)
    sent = await chat.handlers.handle_send_chat_message(
        {"message": "Is this stock right?", "session_id": "wf", "attachments": [{"path": photo}], "options": {"web": False}}, None
    )
    assert sent["success"] is True
    [row] = await chat.database.read_chat_messages("wf")
    assert [ref["path"] for ref in row["attachments"]] == [photo]
    assert chat.dispatched[-1]["data"]["attachments"][0]["path"] == photo
    run = await ledger.get_run(chat.database, sent["run_id"])
    assert run.options == {"web": False}

    # The answering agent reads them after the owner's words, and gets the image.
    stream = {"run_id": run.run_id, "session_id": "wf", "workflow_id": "wf"}
    _, prompt = await guide.chat_turn(chat.database, stream, system_message="", prompt="Is this stock right?")
    assert prompt.startswith("Is this stock right?\n\n[attachments]{")
    assert [ref["path"] for ref in attachments.image_refs(await guide.run_attachments(chat.database, stream))] == [photo]


async def test_files_alone_are_a_message_but_not_in_the_default_chat(chat, workspace):
    await talking(chat.database)
    sheet = upload(workspace, "prices.csv")
    alone = await chat.handlers.handle_send_chat_message({"message": "", "session_id": "wf", "attachments": [sheet]}, None)
    assert alone["success"] is True
    empty = await chat.handlers.handle_send_chat_message({"message": " ", "session_id": "wf"}, None)
    assert empty["error"] == "invalid_request"
    default = await chat.handlers.handle_send_chat_message({"message": "x", "session_id": "default", "attachments": [sheet]}, None)
    assert default["error"] == "invalid_request"
    refused = await chat.handlers.handle_send_chat_message({"message": "x", "session_id": "wf", "attachments": ["notes.txt"]}, None)
    assert refused["error"] == "attachment_rejected"


async def test_an_edit_keeps_the_original_files(chat, workspace):
    await talking(chat.database)
    sheet = upload(workspace, "prices.csv")
    sent = await chat.handlers.handle_send_chat_message({"message": "Check these", "session_id": "wf", "attachments": [sheet]}, None)
    run = await ledger.start_run(chat.database, run_id=sent["run_id"], **CLAIM)
    await ledger.post_reply(chat.database, run=run, node_id="n", text="Done.", execution_id="gen-1")
    await ledger.finish_run(chat.database, run_id=run.run_id, success=True, **CLAIM)
    edited = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": sent["message_id"], "message": "Check these again"}, None)
    assert edited["success"] is True
    assert chat.dispatched[-1]["data"]["attachments"][0]["path"] == sheet


# ----- what the box offers ---------------------------------------------------------------


async def test_the_box_offers_commands_attachments_and_web(chat, monkeypatch):
    import nodes  # noqa: F401 - the plugin registry

    graph = {
        "nodes": [{"id": "wf:googleGmail:1", "type": "googleGmail"}, {"id": "wf:duckduckgoSearch:1", "type": "duckduckgoSearch"}],
        "edges": [],
    }

    async def get_workflow(workflow_id):
        from types import SimpleNamespace

        return SimpleNamespace(name="Maya", data=graph) if workflow_id == "wf" else None

    monkeypatch.setattr(chat.database, "get_workflow", get_workflow)
    context = await chat.handlers.handle_get_chat_context({"session_id": "wf"}, None)
    commands = {item["command"]: item for item in context["commands"]}
    assert commands["/inbox"]["suggest"] is True
    assert commands["/remember"]["description"] == "Teach Maya something"
    assert context["capabilities"] == {"attachments": True, "web": True}
    assert context["limits"]["max_attachments"] == 6

    plain = await chat.handlers.handle_get_chat_context({"session_id": "default"}, None)
    assert plain["commands"] == [] and plain["capabilities"] == {"attachments": False, "web": False}


async def test_web_off_keeps_the_answering_agent_off_search(database):
    import nodes  # noqa: F401
    from services.temporal.agent_activities import _without_web_tools

    await talking(database)
    admission = await ledger.admit_message(
        database, session_id="wf", workflow_id="wf", execution_id="gen-1", text="Hi", track=True, options={"web": False}
    )
    tools = [{"node_type": "duckduckgoSearch"}, {"node_type": "calculatorTool"}]
    context = {"run_scope": {"run_id": admission.run.run_id, "session_id": "wf"}}
    kept = await _without_web_tools(database, context, tools)
    assert [tool["node_type"] for tool in kept] == ["calculatorTool"]
    # Web on, or no chat run: every tool stays.
    assert await _without_web_tools(database, {}, tools) == tools
