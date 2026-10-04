"""A workflow's chat thread (services/chat_thread.py) and the chat
WebSocket handlers built on it (services/chat/handlers.py): rows carry the
live generation, every insert and every clear that removed rows is announced
on ``chat.updated``, a message reaches a workflow only while a deployment
would read it, the generation is on every row, a Reset clears the thread
through the chat nodes' Reset hook, and deleting the workflow deletes it.
Chat runs have their own tests in tests/services/chat/."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.chat.handlers as chat_handlers
import services.status_broadcaster as status_broadcaster
from models.database import WorkflowControlExecution
from services import chat_thread
from tests.services.chat._helpers import saved_workflow


@pytest.fixture
async def database(tmp_path: Path):
    """The real Database on a throwaway SQLite file (the root conftest stubs
    ``core.database``, so the module is loaded privately)."""
    module_name = f"tests._chat_thread_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "core" / "database.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"chat-thread-{uuid.uuid4().hex}.db"
    db = module.Database(
        SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}", database_echo=False, database_pool_size=5, database_max_overflow=5)
    )
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()
        sys.modules.pop(module_name, None)


@pytest.fixture
def frames(monkeypatch):
    sent: list = []

    class Broadcaster:
        async def broadcast(self, message):
            sent.append(message)

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    return sent


@pytest.fixture
def router(monkeypatch, database, frames):
    """The chat handlers on the test database, with the chat trigger
    dispatch captured."""
    dispatched: list = []

    async def dispatch(event_data, *, workflow_id=None, event_id=None):
        dispatched.append((dict(event_data), workflow_id))

    engine = SimpleNamespace(is_connected=True)
    monkeypatch.setattr(chat_handlers, "container", SimpleNamespace(database=lambda: database, temporal_client=lambda: engine))
    monkeypatch.setattr(chat_handlers, "dispatch_chat_message_received", dispatch)
    # The Clear's listeners are whatever a test registers, never the plugins'.
    monkeypatch.setattr(chat_thread, "_CLEARED_LISTENERS", [])
    return SimpleNamespace(database=database, frames=frames, dispatched=dispatched)


async def control(database, workflow_id, status, generation=1):
    await saved_workflow(database, workflow_id)
    async with database.get_session() as session:
        session.add(
            WorkflowControlExecution(
                id=f"{workflow_id}-{generation}",
                workflow_id=workflow_id,
                generation=generation,
                execution_id=f"gen-{generation}",
                root_execution_id=f"gen-{generation}",
                graph_hash="0" * 64,
                status=status,
                idempotency_key=f"k-{generation}",
            )
        )
        await session.commit()


def updates(frames):
    return [frame["data"]["data"] for frame in frames if frame["type"] == "chat.updated"]


# ----- the service -----


@pytest.mark.parametrize(
    ("status", "delivery"),
    [
        ("running", "now"),
        ("starting", "now"),
        ("resuming", "now"),
        ("paused", "queued"),
        ("pausing", "queued"),
        ("reset", None),
        ("resetting", None),
        ("failed", None),
    ],
)
def test_delivery_follows_the_control_state(status, delivery):
    assert chat_thread.delivery_for(SimpleNamespace(status=status)) == delivery
    assert chat_thread.delivery_for(None) is None


def test_rows_belong_to_the_live_generation():
    assert chat_thread.chat_execution_id(SimpleNamespace(status="paused", root_execution_id="gen-3")) == "gen-3"
    assert chat_thread.chat_execution_id(SimpleNamespace(status="reset", root_execution_id="gen-3")) is None
    assert chat_thread.chat_execution_id(None) is None


async def test_a_recorded_message_is_stamped_and_announced(database, frames):
    await control(database, "7", "running", generation=2)
    saved = await chat_thread.record_chat_message(database, "7", "assistant", "Done.")
    assert saved["message"] == "Done." and saved["uid"].startswith("m_")
    [row] = await database.read_chat_messages("7")
    assert (row["role"], row["message"], row["execution_id"]) == ("assistant", "Done.", "gen-2")
    [frame] = frames
    assert frame["type"] == "chat.updated"
    assert frame["data"]["type"] == "com.opencompany.chat.updated"
    assert frame["data"]["subject"] == "7"
    # Identity only: the text stays behind get_chat_messages.
    assert frame["data"]["data"] == {"workflow_id": "7", "session_id": "7", "role": "assistant"}


async def test_the_editors_default_chat_is_no_workflows_thread(database, frames):
    await chat_thread.record_chat_message(database, "default", "user", "hi")
    [row] = await database.read_chat_messages("default")
    assert row["execution_id"] is None
    assert updates(frames) == [{"workflow_id": None, "session_id": "default", "role": "user"}]


async def test_clearing_removes_every_generation_and_is_announced(database, frames):
    await database.add_chat_message("7", "user", "old", execution_id="gen-1")
    await database.add_chat_message("7", "user", "new", execution_id="gen-2")
    assert await chat_thread.clear_chat_thread(database, "7") == 2
    assert await database.read_chat_messages("7") == []
    assert updates(frames) == [{"workflow_id": "7", "session_id": "7", "role": None}]


async def test_clearing_an_empty_thread_says_nothing(database, frames):
    assert await chat_thread.clear_chat_thread(database, "7") == 0
    assert frames == []


@pytest.mark.parametrize("node", ["chatTrigger", "chatReply"])
async def test_a_reset_ends_the_workflows_chat_session(database, frames, node):
    from nodes.chat.chat_reply import ChatReplyNode
    from nodes.trigger.chat_trigger import ChatTriggerNode

    reset = {"chatTrigger": ChatTriggerNode, "chatReply": ChatReplyNode}[node].reset_execution_state
    await database.add_chat_message("7", "user", "What's on today?", execution_id="gen-1")
    await database.add_chat_message("7", "assistant", "Two calls.", execution_id="gen-1")
    await database.add_chat_message("8", "user", "another workflow's thread", execution_id="gen-5")
    args = {"node_id": f"7:{node}:1", "workflow_id": "7", "execution_id": "gen-1", "generation": 1, "graph": {}, "database": database}

    assert await reset(**args) == {"reset": True, "cleared_chat_messages": 2}
    assert await database.read_chat_messages("7") == []
    assert [row["message"] for row in await database.read_chat_messages("8")] == ["another workflow's thread"]
    # The next chat node in the same Reset finds nothing, and says nothing.
    assert await reset(**args) == {"reset": False, "cleared_chat_messages": 0}
    assert updates(frames) == [{"workflow_id": "7", "session_id": "7", "role": None}]


async def test_deleting_the_workflow_deletes_its_thread(database, frames, monkeypatch):
    import nodes.chat.chat_reply  # noqa: F401  (registers the hook on import)
    from services.workflow_storage import hooks

    assert chat_thread.clear_chat_thread in hooks._HOOKS
    # Run it alone: other plugins' hooks have nothing to do with this test.
    monkeypatch.setattr(hooks, "_HOOKS", [chat_thread.clear_chat_thread])
    await database.add_chat_message("7", "user", "hello", execution_id="gen-1")
    await hooks.run_workflow_deleted_hooks(database, "7")
    assert await database.read_chat_messages("7") == []


async def test_history_is_the_newest_rows_oldest_first_in_utc(database):
    for text in ("one", "two", "three"):
        await database.add_chat_message("7", "user", text, execution_id="gen-1")
    rows = await database.read_chat_messages("7", limit=2)
    assert [row["message"] for row in rows] == ["two", "three"]
    assert rows[0]["id"] < rows[1]["id"]
    assert datetime.fromisoformat(rows[0]["timestamp"]).utcoffset().total_seconds() == 0


# ----- the WebSocket handlers -----


async def send(message, session_id):
    return await chat_handlers.handle_send_chat_message({"message": message, "session_id": session_id, "timestamp": "t"}, None)


async def test_a_workflow_that_is_not_running_takes_no_message(router):
    # A session must name a saved workflow; one never started takes nothing.
    assert await send("hello?", "7") == {"success": False, "error": "access_denied"}
    await saved_workflow(router.database, "7")
    assert await send("hello?", "7") == {"success": False, "error": "not_running"}
    await control(router.database, "7", "failed")
    assert (await send("hello?", "7"))["error"] == "not_running"
    assert await router.database.read_chat_messages("7") == []
    assert router.dispatched == [] and router.frames == []


@pytest.mark.parametrize(("status", "delivery"), [("running", "now"), ("paused", "queued")])
async def test_a_message_goes_now_or_waits_for_resume(router, status, delivery):
    await control(router.database, "7", status, generation=3)
    result = await send("Book Tuesday", "7")
    assert result["success"] is True and result["delivery"] == delivery and result["timestamp"] == "t"
    [row] = await router.database.read_chat_messages("7")
    assert (row["role"], row["message"], row["execution_id"]) == ("user", "Book Tuesday", "gen-3")
    assert result["message_id"] == row["uid"]
    # No chat trigger answers this graph, so the message starts no run.
    assert result["run_id"] is None
    # Only this workflow's triggers hear it.
    assert router.dispatched == [({"message": "Book Tuesday", "timestamp": "t", "session_id": "7", "message_id": row["uid"]}, "7")]
    assert updates(router.frames) == [{"workflow_id": "7", "session_id": "7", "role": "user"}]


async def test_the_default_session_keeps_its_old_behaviour(router):
    result = await chat_handlers.handle_send_chat_message({"message": "hi"}, None)
    assert result["success"] is True and "delivery" not in result
    assert datetime.fromisoformat(result["timestamp"]).utcoffset() is not None
    assert [row["message"] for row in await router.database.read_chat_messages("default")] == ["hi"]
    assert router.dispatched[0][1] is None


async def test_history_is_one_generation_unless_all_are_asked_for(router):
    await control(router.database, "7", "reset", generation=1)
    await router.database.add_chat_message("7", "user", "before the restart", execution_id="gen-1")
    await router.database.add_chat_message("7", "assistant", "an answer", execution_id="gen-1")
    await control(router.database, "7", "running", generation=2)
    await router.database.add_chat_message("7", "user", "after it", execution_id="gen-2")

    live = await chat_handlers.handle_get_chat_messages({"session_id": "7"}, None)
    assert [message["message"] for message in live["messages"]] == ["after it"]

    everything = await chat_handlers.handle_get_chat_messages({"session_id": "7", "all_generations": True, "limit": 200}, None)
    assert [(m["role"], m["message"], m["run_key"]) for m in everything["messages"]] == [
        ("user", "before the restart", "gen-1"),
        ("assistant", "an answer", "gen-1"),
        ("user", "after it", "gen-2"),
    ]
    assert all(
        m["id"].startswith("m_") and isinstance(m["legacy_id"], int) and datetime.fromisoformat(m["timestamp"]).utcoffset() is not None
        for m in everything["messages"]
    )
    newest = await chat_handlers.handle_get_chat_messages({"session_id": "7", "all_generations": True, "limit": 2}, None)
    assert [m["message"] for m in newest["messages"]] == ["an answer", "after it"]


async def test_after_a_reset_the_live_read_is_empty(router):
    # A row a Reset left (its graph had no chat node to clear it).
    await router.database.add_chat_message("7", "user", "kept", execution_id="gen-1")
    await control(router.database, "7", "reset", generation=1)
    assert (await chat_handlers.handle_get_chat_messages({"session_id": "7"}, None))["messages"] == []
    assert len((await chat_handlers.handle_get_chat_messages({"session_id": "7", "all_generations": True}, None))["messages"]) == 1


async def test_the_owners_clear_lets_the_agent_forget_too(router):
    heard: list = []

    async def forget(*, database, workflow_id):
        heard.append((database, workflow_id))

    async def broken(**_):
        raise RuntimeError("a listener that fails")

    chat_thread.register_chat_cleared_listener(broken)
    chat_thread.register_chat_cleared_listener(forget)
    chat_thread.register_chat_cleared_listener(forget)  # registering twice is a no-op
    await saved_workflow(router.database, "7")
    await router.database.add_chat_message("7", "user", "hello", execution_id="gen-1")

    cleared = await chat_handlers.handle_clear_chat_messages({"session_id": "7"}, None)
    assert cleared["cleared_count"] == 1
    assert await router.database.read_chat_messages("7") == []
    # A failing listener never fails the clear, nor stops the next one.
    assert heard == [(router.database, "7")]

    # The editor's "default" chat is no workflow's: nothing to forget.
    await chat_handlers.handle_clear_chat_messages({"session_id": "default"}, None)
    assert heard == [(router.database, "7")]


async def test_save_and_clear_go_through_the_thread(router):
    await control(router.database, "7", "running", generation=4)
    saved = await chat_handlers.handle_save_chat_message({"message": "An answer", "role": "assistant", "session_id": "7"}, None)
    assert saved["success"] is True
    [row] = await router.database.read_chat_messages("7")
    assert row["execution_id"] == "gen-4"
    cleared = await chat_handlers.handle_clear_chat_messages({"session_id": "7"}, None)
    assert cleared["cleared_count"] == 1
    assert [update["role"] for update in updates(router.frames)] == ["assistant", None]
