"""Fixtures for the chat run tests: a real database on a throwaway file, the
chat handlers' container pointed at it, a fresh hub, and the broadcaster,
dispatch and Clear listeners captured."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest


@pytest.fixture
async def database(tmp_path: Path):
    """A real ``core.database.Database`` (the root conftest stubs the module,
    so the real one is loaded privately)."""
    module_name = f"tests._chat_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[3] / "core" / "database.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"chat-{uuid.uuid4().hex}.db"
    db = module.Database(
        SimpleNamespace(
            database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
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
def hub():
    from services.chat.hub import ChatRunHub, reset_chat_hub_for_tests

    fresh = ChatRunHub(queue_size=8)
    previous = reset_chat_hub_for_tests(fresh)
    try:
        yield fresh
    finally:
        reset_chat_hub_for_tests(previous)


@pytest.fixture
def frames(monkeypatch):
    """Everything the status broadcaster would have sent to every socket."""
    import services.status_broadcaster as status_broadcaster

    sent: List[Dict[str, Any]] = []

    class Broadcaster:
        async def broadcast(self, message):
            sent.append(message)

    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: Broadcaster())
    return sent


@pytest.fixture
def chat(monkeypatch, database, hub, frames):
    """The chat handlers on the test database, with the dispatch captured."""
    import services.chat.handlers as handlers
    from services import chat_thread

    dispatched: List[Dict[str, Any]] = []

    async def dispatch(event_data, *, workflow_id=None, event_id=None):
        dispatched.append({"data": dict(event_data), "workflow_id": workflow_id, "event_id": event_id})

    # Temporal, which delivers the messages; a test may disconnect it.
    engine = SimpleNamespace(is_connected=True)
    # The credential service the model picker asks which providers are
    # connected; the picker's tests decide that (services/chat/choice.py).
    auth = SimpleNamespace()
    monkeypatch.setattr(
        handlers,
        "container",
        SimpleNamespace(database=lambda: database, temporal_client=lambda: engine, auth_service=lambda: auth),
    )
    monkeypatch.setattr(handlers, "dispatch_chat_message_received", dispatch)
    monkeypatch.setattr(chat_thread, "_CLEARED_LISTENERS", [])
    return SimpleNamespace(database=database, hub=hub, frames=frames, dispatched=dispatched, handlers=handlers, engine=engine, auth=auth)
