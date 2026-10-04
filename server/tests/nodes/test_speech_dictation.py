"""Dictation for the chat's message box (nodes/speech/_handlers.py): it can
work only with a stored key for one of the configured providers, tried in
order; a recording must be one the chat uploaded under ``uploads/``; it is
transcribed with that provider and deleted, and only the workflow's owner
may ask."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nodes.speech import _handlers

from ._mocks import patched_container, patched_pricing


def database(owner: str = "") -> MagicMock:
    db = MagicMock(name="Database")
    # A saved workflow; with no owner, anyone (login off) may use it.
    db.get_workflow = AsyncMock(return_value=SimpleNamespace(data={"owner_id": owner} if owner else {}))
    db.save_api_usage_metric = AsyncMock(return_value=None)
    return db


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "workspace"
    (root / "uploads").mkdir(parents=True)

    async def fake_root(workflow_id, db, *, allow_default=True):
        return root

    monkeypatch.setattr("services.workspace_locator.resolve_workspace_root", fake_root)
    return root


def test_the_providers_come_from_the_config_in_order():
    assert _handlers.dictation_providers() == ["openai", "groq", "deepgram"]


async def test_dictation_needs_a_stored_key():
    with patched_container(auth_api_keys={"groq": "gsk-test"}, database=database()):
        assert await _handlers.handle_dictation_status({"session_id": "wf"}, None) == {
            "success": True,
            "available": True,
            "provider": "groq",
        }
    with patched_container(auth_api_keys={}, database=database()):
        status = await _handlers.handle_dictation_status({"session_id": "wf"}, None)
        assert (status["available"], status["provider"]) == (False, None)
        # The editor's chat with no workflow has nowhere to put a recording.
        assert (await _handlers.handle_dictation_status({"session_id": "default"}, None))["success"] is False


async def test_a_recording_becomes_text_and_is_deleted(workspace, monkeypatch):
    recording = workspace / "uploads" / "dictation-1.webm"
    recording.write_bytes(b"webm-audio")
    transcribe = AsyncMock(return_value=SimpleNamespace(text="Send reminders for tomorrow", language="en", billed_units=None, billed_unit="seconds", duration_seconds=3.2))
    monkeypatch.setattr(_handlers._unifier, "transcribe", transcribe)
    with patched_container(auth_api_keys={"openai": "sk-test"}, database=database()), patched_pricing():
        result = await _handlers.handle_transcribe_audio({"session_id": "wf", "path": "uploads/dictation-1.webm"}, None)
    assert result == {"success": True, "text": "Send reminders for tomorrow", "language": "en", "provider": "openai"}
    assert not recording.exists()
    call = transcribe.await_args.kwargs
    assert (call["provider"], call["api_key"], call["request"].audio, call["request"].filename) == ("openai", "sk-test", b"webm-audio", "dictation-1.webm")


@pytest.mark.parametrize("path, message", [("notes/dictation.webm", "uploaded"), ("uploads/missing.webm", "no longer there")])
async def test_what_cannot_be_transcribed(workspace, path, message):
    with patched_container(auth_api_keys={"openai": "sk-test"}, database=database()):
        result = await _handlers.handle_transcribe_audio({"session_id": "wf", "path": path}, None)
    assert result["success"] is False and message in result["error"]


async def test_without_a_key_or_access_nothing_is_transcribed(workspace):
    recording = workspace / "uploads" / "dictation-2.webm"
    recording.write_bytes(b"webm-audio")
    with patched_container(auth_api_keys={}, database=database()):
        assert await _handlers.handle_transcribe_audio({"session_id": "wf", "path": "uploads/dictation-2.webm"}, None) == {
            "success": False,
            "error": "speech_unavailable",
        }
    socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="someone-else"))
    with patched_container(auth_api_keys={"openai": "sk-test"}, database=database(owner="owner")):
        denied = await _handlers.handle_transcribe_audio({"session_id": "wf", "path": "uploads/dictation-2.webm"}, socket)
    assert denied == {"success": False, "error": "access_denied"}
    assert recording.exists()
