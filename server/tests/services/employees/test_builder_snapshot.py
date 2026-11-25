"""The Hire builder's exact output for four hires, held against a golden
snapshot (``tests/fixtures/employee_builder_snapshot.json``): node ids,
labels, positions and data, every parameter, edge ids in order, the roles,
the trigger record and the warnings.

The other builder tests check shapes; this one catches any drift, so a
refactor that must not change the graph can show that it did not. A
deliberate change (a new ``BUILDER_VERSION``) rewrites the file:

    UPDATE_BUILDER_SNAPSHOT=1 uv run pytest tests/services/employees/test_builder_snapshot.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

from services.employees.apps import get_app
from services.employees.builder import BuildInputs, LibrarySkill, build_employee_graph
from services.employees.hire_request import HireEmployeeRequest
from services.employees.llm import LLMChoice
from services.employees.prompt import OwnerProfile

SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "employee_builder_snapshot.json"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _inputs(request: Dict[str, Any], *app_ids: str, **fields: Any) -> BuildInputs:
    values: Dict[str, Any] = dict(
        workflow_id="7",
        request=HireEmployeeRequest.model_validate({"idempotency_key": "k", **request}),
        apps=[get_app(app_id) for app_id in app_ids],
        owner=OwnerProfile(name="Alex", role="Owner", preferences="Short answers"),
        owner_values={"google_email": "alex@example.com"},
        timezone="Europe/London",
        llm=LLMChoice(provider="openai", model="gpt-x", local=False),
        memory=True,
        now=NOW,
    )
    values.update(fields)
    return BuildInputs(**values)


def _chat() -> BuildInputs:
    """A manual research hire with the base search tool."""
    return _inputs(
        {
            "job": "Help me research suppliers and draft replies",
            "name": "Sam",
            "role": "Assistant",
            "steps": [{"title": "Research suppliers", "role": "agent"}],
            "trigger": {"kind": "manual"},
            "rules": {"ask_first": True, "items": [{"key": "short", "label": "Keep it short", "value": True}]},
        },
        unsupported_apps=["Notion"],
    )


def _schedule() -> BuildInputs:
    """A weekday routine that reports on Telegram, not asking first (so it
    keeps the money and send tools), without memory, in a zone the
    scheduler does not list."""
    return _inputs(
        {
            "job": "Every weekday morning, check yesterday's payments and my calendar, then send me a summary",
            "name": "Nora",
            "role": "Bookkeeper",
            "apps": ["Telegram", "Stripe", "Google Calendar"],
            "steps": [{"title": "Every weekday at 8", "role": "trigger"}, {"title": "Check payments", "role": "agent", "app": "Stripe"}],
            "trigger": {"kind": "schedule", "every": "weekday", "at": "08:15"},
            "sends_via": "Telegram",
            "rules": {"ask_first": False},
        },
        "telegram",
        "stripe",
        "google_calendar",
        connected_app_ids={"telegram", "stripe", "google_calendar"},
        timezone="America/Chicago",
        memory=False,
    )


def _app_event_asking_first() -> BuildInputs:
    """A WhatsApp receptionist whose replies wait at the approval gate."""
    return _inputs(
        {
            "job": "Answer customer messages on WhatsApp and book appointments",
            "name": "Maya",
            "role": "Receptionist",
            "apps": ["WhatsApp", "Google Sheets", "Google Calendar", "Stripe"],
            "steps": [{"title": "When a message arrives", "role": "trigger", "app": "WhatsApp"}, {"title": "Answer it", "role": "agent"}],
            "rules": {"ask_first": True},
        },
        "whatsapp",
        "google_sheets",
        "google_calendar",
        "stripe",
        connected_app_ids={"whatsapp"},
    )


def _skills() -> BuildInputs:
    """A Gmail inbox assistant given the owner's skill library, reserved
    names, a duplicate and a blank skill included."""
    return _inputs(
        {
            "job": "Read new email, answer what you can and flag the rest",
            "name": "Leo",
            "role": "Inbox assistant",
            "apps": ["Gmail"],
            "steps": [{"title": "Read new email", "role": "trigger", "app": "Gmail"}],
            "rules": {"ask_first": True},
        },
        "gmail",
        connected_app_ids={"gmail"},
        skills=(
            LibrarySkill(name="book-appointments", description="Offers free times.", instructions="# Book\nOffer real times."),
            LibrarySkill(name="my-tone", description="How I write.", instructions="Write warmly."),
            LibrarySkill(name="skill", description="Takes over the Skill tool", instructions="x"),
            LibrarySkill(name="pirate-personality", description="Replaces the system message", instructions="x"),
            LibrarySkill(name="book-appointments", description="A second copy", instructions="y"),
            LibrarySkill(name="blank", description="Nothing", instructions="  "),
        ),
    )


SCENARIOS: Dict[str, Callable[[], BuildInputs]] = {
    "chat": _chat,
    "schedule": _schedule,
    "app_event_asking_first": _app_event_asking_first,
    "skills": _skills,
}


def _built(name: str) -> Dict[str, Any]:
    built = build_employee_graph(SCENARIOS[name]())
    return json.loads(
        json.dumps(
            {
                "nodes": built.nodes,
                "edges": built.edges,
                "parameters": built.parameters,
                "node_roles": built.node_roles,
                "trigger": built.trigger,
                "delivery": built.delivery,
                "delivery_app": built.delivery_app,
                "app_ids": built.app_ids,
                "warnings": built.warnings,
            }
        )
    )


def test_update_snapshot_when_asked():
    if os.environ.get("UPDATE_BUILDER_SNAPSHOT") != "1":
        pytest.skip("set UPDATE_BUILDER_SNAPSHOT=1 to rewrite the snapshot")
    snapshot = {name: _built(name) for name in SCENARIOS}
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=False) + "\n", encoding="utf-8")


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_builder_output_matches_the_snapshot(name):
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert _built(name) == snapshot[name], (
        f"The Hire builder's {name!r} graph changed. If that is deliberate, bump BUILDER_VERSION and rewrite "
        "the snapshot with UPDATE_BUILDER_SNAPSHOT=1."
    )
