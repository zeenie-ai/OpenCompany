"""The chat model picker's saved choice (``UserSettings.chat_model`` /
``chat_effort``, services/chat/choice.py) against real SQLite: it round-trips
with the model's defaults, an older database gains the columns, and a save
keeps to what the picker offers (services/settings/handlers.py)."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

SONNET = "anthropic::claude-sonnet-5-5"


@pytest.fixture
async def settings_database(tmp_path: Path):
    module_name = f"tests._chat_choice_settings_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[2] / "core" / "database.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"chat-choice-{uuid.uuid4().hex}.db"
    database = module.Database(
        SimpleNamespace(
            database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
            database_echo=False,
            database_pool_size=5,
            database_max_overflow=5,
        )
    )
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)


async def test_the_choice_round_trips_with_its_defaults(settings_database):
    assert await settings_database.save_user_settings({"getting_started_said_hello": True}, user_id="default")
    settings = await settings_database.get_user_settings("default")
    assert (settings["chat_model"], settings["chat_effort"]) == ("auto", "")
    assert await settings_database.save_user_settings({"chat_model": SONNET, "chat_effort": "high"}, user_id="default")
    settings = await settings_database.get_user_settings("default")
    assert (settings["chat_model"], settings["chat_effort"]) == (SONNET, "high")


async def test_an_older_database_gains_the_columns_with_their_defaults(settings_database):
    assert await settings_database.save_user_settings({"getting_started_said_hello": True}, user_id="default")
    async with settings_database.engine.begin() as conn:
        await conn.execute(text("ALTER TABLE user_settings DROP COLUMN chat_model"))
        await conn.execute(text("ALTER TABLE user_settings DROP COLUMN chat_effort"))
    await settings_database._migrate_user_settings()
    settings = await settings_database.get_user_settings("default")
    assert (settings["chat_model"], settings["chat_effort"]) == ("auto", "")


async def test_a_save_keeps_to_what_the_picker_offers(settings_database, monkeypatch):
    from services.settings.handlers import handle_save_user_settings

    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: settings_database))
    refused = await handle_save_user_settings({"settings": {"chat_model": "openai::gpt-4.1"}}, None)
    assert refused == {"success": False, "error": "chat_choice_refused", "detail": "That model isn’t offered any more. Pick another one."}
    refused = await handle_save_user_settings({"settings": {"chat_effort": "max"}}, None)
    assert refused["success"] is False and refused["detail"] == "Pick Quick, Balanced or Thorough."
    assert await settings_database.get_user_settings("default") is None

    saved = await handle_save_user_settings({"settings": {"chat_model": SONNET, "chat_effort": "low"}}, None)
    assert saved["success"] is True
    assert (saved["settings"]["chat_model"], saved["settings"]["chat_effort"]) == (SONNET, "low")
