"""The setup model's prompt: every component and action from the manifest,
the ground-rule and trigger additions, the owner's context, and a
conversation whose roles alternate however much history is trimmed."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from services.employees.context import SetupContext, local_time_text, render_context_block
from services.employees.genui_catalog import load_genui_catalog
from services.employees.setup_prompt import (
    CHANGE_PREFIX,
    HISTORY_MAX_CHARS,
    JOB_PREFIX,
    PREAMBLE,
    HistoryTurn,
    build_messages,
    catalog_prompt,
    system_prompt,
    trim_history,
)


def context(**patch) -> SetupContext:
    base = SetupContext(
        connected_apps=["WhatsApp"],
        available_apps=["Gmail", "Stripe"],
        team_names=["Maya"],
        owner_name="Alex",
        owner_role="Founder",
        owner_preferences="Keep replies short.",
        timezone="Europe/London",
        local_time="Friday 25 September 2026, 14:05",
        has_ai=True,
    )
    for key, value in patch.items():
        setattr(base, key, value)
    return base


def test_catalogue_prompt_lists_every_component_and_action():
    prompt = catalog_prompt()
    catalog = load_genui_catalog()
    for name, spec in catalog["components"].items():
        assert f"- {name} {spec['props']} — {spec['description']}" in prompt
    for name, description in catalog["actions"].items():
        assert f"- {name} {description}" in prompt
    assert prompt.startswith('Reply ONLY with a JSON object, no code fences: {"text": string, "spec": UISpec}.')
    assert prompt.endswith("When asked to change the setup, return the full updated JSON in the same format.")


def test_prompt_carries_the_additions():
    prompt = catalog_prompt()
    assert '"Ask me before sending anything" bound to "/rules/askFirst", true in "state"' in prompt
    assert 'Toggles bound to state under "/rules"' in prompt
    assert 'one Choice bound under "/choices"' in prompt
    assert "actionParams {name, role, apps, trigger, sendsVia}" in prompt
    assert '"kind": "app_event"|"schedule"|"manual"' in prompt
    assert 'names it in "app"' in prompt


def test_system_prompt_ends_with_the_owner_context():
    prompt = system_prompt(context())
    assert prompt.startswith(PREAMBLE)
    block = render_context_block(context())
    assert prompt.endswith(block)
    assert "Connected apps: WhatsApp." in block
    assert "Apps that can be connected: Gmail, Stripe." in block
    assert "Names already on the team (don't reuse): Maya." in block
    assert "The owner is Alex." in block
    assert "They are: Founder." in block
    assert "Their preferences: Keep replies short." in block
    assert "It is now Friday 25 September 2026, 14:05 (Europe/London)." in block


def test_an_empty_world_says_none():
    block = render_context_block(context(connected_apps=[], available_apps=[], team_names=[], owner_name="", owner_role="", owner_preferences=""))
    assert "Connected apps: none." in block
    assert "The owner is" not in block


def test_local_time_is_in_the_owner_zone():
    moment = datetime(2026, 9, 25, 13, 5, tzinfo=timezone.utc)
    assert local_time_text(moment, ZoneInfo("Asia/Kolkata")) == "Friday 25 September 2026, 18:35"


def test_a_new_job_is_one_user_message_and_never_in_the_system_prompt():
    messages = build_messages(context(), "Answer my WhatsApp")
    assert [m.role for m in messages] == ["system", "user"]
    assert messages[1].content == JOB_PREFIX + "Answer my WhatsApp"
    assert "Answer my WhatsApp" not in messages[0].content


def test_a_change_replays_the_kept_replies_with_alternating_roles():
    history = [
        HistoryTurn(reply="r0"),
        HistoryTurn(reply="r1", change="c1"),
        HistoryTurn(reply="r2", change="c2"),
        HistoryTurn(reply="r3", change="c3"),
    ]
    messages = build_messages(context(), "the job", change="c4", history=history)
    roles = [m.role for m in messages]
    assert roles == ["system", "user", "assistant", "user", "assistant", "user", "assistant", "user"]
    assert [m.content for m in messages[1:]] == [
        JOB_PREFIX + "the job",
        "r1",
        CHANGE_PREFIX + "c2",
        "r2",
        CHANGE_PREFIX + "c3",
        "r3",
        CHANGE_PREFIX + "c4",
    ]


def test_history_keeps_the_latest_reply_and_what_fits():
    big = "x" * HISTORY_MAX_CHARS
    kept = trim_history([HistoryTurn(reply="old"), HistoryTurn(reply=big, change="a"), HistoryTurn(reply="new", change="b")])
    assert [turn.reply for turn in kept] == ["new"]
    assert trim_history([]) == []
    oversized = trim_history([HistoryTurn(reply="y" * (HISTORY_MAX_CHARS * 2))])
    assert len(oversized) == 1  # the latest reply always stays


def test_a_turn_without_its_change_is_skipped_to_keep_roles_alternating():
    messages = build_messages(context(), "job", change="now", history=[HistoryTurn(reply="a"), HistoryTurn(reply="b")])
    assert [m.role for m in messages] == ["system", "user", "assistant", "user"]
