"""The attention store behind Home's line for work that keeps retrying (1b.30)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import attention
from services.authz.ws_surface import INTERNAL_SOCKET_HANDLERS
from services.ws_handler_registry import get_ws_handlers

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def announced(monkeypatch):
    stages = []

    async def announce(stage, kind):
        stages.append((stage, kind))

    monkeypatch.setattr(attention, "_announce", announce)
    return stages


async def test_touch_opens_once_and_counts_and_resolve_closes(real_database, announced):
    later = NOW + timedelta(minutes=5)
    await attention.touch("stuck_retry", "wf:llm-step-3", database=real_database, now=NOW)
    await attention.touch("stuck_retry", "wf:llm-step-3", database=real_database, now=later)

    [item] = await attention.open_items(real_database, now=later)
    assert (item["kind"], item["ref"], item["count"], item["severity"]) == ("stuck_retry", "wf:llm-step-3", 2, "warning")
    assert announced == [("opened", "stuck_retry")]

    await attention.resolve("stuck_retry", "wf:llm-step-3", database=real_database)
    assert await attention.open_items(real_database, now=later) == []
    assert announced[-1] == ("resolved", "stuck_retry")

    await attention.touch("stuck_retry", "wf:llm-step-3", database=real_database, now=later)
    [item] = await attention.open_items(real_database, now=later)
    assert item["count"] == 1
    assert announced[-1] == ("opened", "stuck_retry")


async def test_an_item_drops_off_an_hour_after_it_was_last_seen(real_database, announced):
    await attention.touch("stuck_retry", "wf:a", database=real_database, now=NOW)
    assert len(await attention.open_items(real_database, now=NOW + timedelta(minutes=59))) == 1
    assert await attention.open_items(real_database, now=NOW + timedelta(minutes=61)) == []

    # Seen again after the window, it opens again instead of counting on.
    await attention.touch("stuck_retry", "wf:a", database=real_database, now=NOW + timedelta(minutes=90))
    [item] = await attention.open_items(real_database, now=NOW + timedelta(minutes=90))
    assert item["count"] == 1
    assert announced == [("opened", "stuck_retry"), ("opened", "stuck_retry")]


async def test_a_store_failure_is_logged_never_raised():
    def unavailable():
        raise RuntimeError("database down")

    broken = SimpleNamespace(reserved_session=unavailable)
    await attention.touch("stuck_retry", "wf:a", database=broken)
    await attention.resolve("stuck_retry", "wf:a", database=broken)


def test_the_command_is_registered_and_not_internal():
    assert "list_attention_items" in get_ws_handlers()
    assert "list_attention_items" not in INTERNAL_SOCKET_HANDLERS


async def test_the_command_lists_the_open_items(real_database, announced, monkeypatch):
    monkeypatch.setattr(attention, "_database", lambda: real_database)
    await attention.touch("stuck_retry", "wf:a", database=real_database)

    reply = await attention.handle_list_attention_items({}, None)

    assert reply["success"] is True
    assert [item["ref"] for item in reply["items"]] == ["wf:a"]
