"""A CLI agent answering the owner's chat reads the session's untold notes
first, as the native agent's turn does (RFC-0007 §4.9): among them the
outcomes of its held calls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.chat import notes
from services.cli_agent.service import with_chat_notes
from services.cli_agent.types import ClaudeTaskSpec

pytestmark = pytest.mark.asyncio

STREAM = {"session_id": "wf", "run_id": "r_1"}


async def test_the_notes_go_ahead_of_each_prompt_claimed_for_the_run(monkeypatch):
    claimed = []

    async def claim_notes(database, *, session_id, run_id):
        claimed.append((session_id, run_id))
        return [SimpleNamespace(text='[update]{"result": "sent"}[/update]')]

    monkeypatch.setattr(notes, "claim_notes", claim_notes)
    monkeypatch.setattr(notes, "notes_prompt", lambda found: "\n".join(note.text for note in found))
    tasks = await with_chat_notes(object(), [ClaudeTaskSpec(prompt="Any news?")], STREAM)
    assert tasks[0].prompt == '[update]{"result": "sent"}[/update]\n\nAny news?'
    assert claimed == [("wf", "r_1")]


async def test_notes_that_cannot_be_read_wait_for_the_next_turn(monkeypatch):
    async def claim_notes(database, *, session_id, run_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(notes, "claim_notes", claim_notes)
    tasks = await with_chat_notes(object(), [ClaudeTaskSpec(prompt="Any news?")], STREAM)
    assert tasks[0].prompt == "Any news?"
