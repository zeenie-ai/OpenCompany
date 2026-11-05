"""The setup a hire gets while the owner has Generated UI off
(services/employees/setup_form.py): the model fills a fixed form, with no
catalogue in its prompt, and the server draws the setup card from it. The
card is the one the client's corpus holds to its renderer's rules
(client/src/features/home/genui/__fixtures__/replies.json); a form that
names no one draws nothing; a change sends the latest setup back as the
form it describes."""

from __future__ import annotations

import json
from pathlib import Path

from services.employees.context import SetupContext
from services.employees.setup_form import MAX_RULES, fields_from_reply, fields_prompt, fill_form, form_messages
from services.employees.setup_prompt import HistoryTurn
from services.employees.setup_reply import app_names, is_salvageable, parse_reply

CORPUS = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "home" / "genui" / "__fixtures__" / "replies.json"
CASES = json.loads(CORPUS.read_text(encoding="utf-8"))
DRAWN = next(case for case in CASES if "fields" in case)
SCREEN = next(case["reply"] for case in CASES if case["name"] == "clean minified reply")
JOB = "Answer customer messages on WhatsApp and book appointments"


def form(**changes):
    return json.dumps({**json.loads(DRAWN["fields"]), **changes})


def elements(reply):
    return parse_reply(reply).spec["elements"]


def test_the_card_is_the_one_the_client_corpus_holds():
    assert fill_form(DRAWN["fields"], JOB) == DRAWN["reply"]
    assert is_salvageable(DRAWN["reply"])
    assert app_names(DRAWN["reply"]) == ["WhatsApp", "Google Calendar"]


def test_the_card_holds_the_core_of_a_setup_screen():
    drawn = elements(fill_form(DRAWN["fields"], JOB))
    assert sorted(element["type"] for element in drawn.values()) == ["AgentCard", "Button", "Button", "Card", "Plan", "Stack", "Toggle", "Toggle"]
    hire = drawn["hire"]["on"]["press"]
    assert hire == {
        "action": "hire_employee",
        "params": {"name": "Maya", "role": "Receptionist", "apps": ["WhatsApp", "Google Calendar"], "trigger": {"kind": "app_event", "app": "WhatsApp"}, "sendsVia": "WhatsApp"},
    }
    assert drawn["change"]["on"]["press"] == {"action": "refine"}


def test_a_form_that_names_no_one_draws_nothing():
    for unusable in ("", "Sorry, I can't help with that.", form(name=""), form(role=None), form(steps=[]), form(steps=[{"detail": "no title"}])):
        assert fill_form(unusable, JOB) == ""
    # A hire needs the job too.
    assert fill_form(DRAWN["fields"], "") == ""


def test_ask_first_is_there_once_and_on_and_the_other_rules_are_capped():
    rules = [{"label": "Ask first before you send", "value": False}] + [{"label": f"Rule {n}", "value": n % 2 == 0} for n in range(5)]
    reply = fill_form(form(rules=rules), JOB)
    drawn = elements(reply)
    toggles = [element["props"]["label"] for element in drawn.values() if element["type"] == "Toggle"]
    assert toggles == ["Ask me before sending anything", "Rule 0", "Rule 1", "Rule 2"]
    assert len(toggles) == MAX_RULES + 1
    assert parse_reply(reply).spec["state"] == {"rules": {"askFirst": True, "rule1": True, "rule2": False, "rule3": True}}


def test_a_cut_off_form_is_read_up_to_where_it_stops():
    cut = DRAWN["fields"][: DRAWN["fields"].index('"rules"')]
    drawn = elements(fill_form(cut, JOB))
    assert drawn["agent"]["props"]["name"] == "Maya"
    assert [element["props"]["label"] for element in drawn.values() if element["type"] == "Toggle"] == ["Ask me before sending anything"]


def test_a_setup_reads_back_as_the_form_it_describes():
    assert fields_from_reply(DRAWN["reply"]) == {
        "text": "Meet Maya, your new receptionist.",
        "name": "Maya",
        "role": "Receptionist",
        "description": "Answers WhatsApp and books visits.",
        "apps": ["WhatsApp", "Google Calendar"],
        "steps": json.loads(DRAWN["fields"])["steps"],
        "trigger": {"kind": "app_event", "app": "WhatsApp"},
        "sends_via": "WhatsApp",
        "rules": [{"label": "Only reply 9 to 6", "value": False}],
    }
    # A screen a model wrote while Generated UI was on reads back too.
    screen = fields_from_reply(SCREEN)
    assert (screen["name"], screen["rules"]) == ("Maya", [{"label": "Only reply 9 to 6", "value": False}])
    assert fields_from_reply("not a setup") == {}


def test_the_form_prompt_carries_no_catalogue():
    [system, user] = form_messages(SetupContext(team_names=["Leo"]), JOB)
    assert fields_prompt() in system.content and "Names already on the team (don't reuse): Leo." in system.content
    assert "Components (use ONLY these)" not in system.content and "UISpec" not in system.content
    assert user.content == "The job: " + JOB


def test_a_change_sends_the_latest_setup_back_as_the_form():
    history = [HistoryTurn(reply=SCREEN), HistoryTurn(reply=DRAWN["reply"], change="Book on weekdays only")]
    messages = form_messages(SetupContext(), JOB, change="Reply in English", history=history)
    assert [message.role for message in messages] == ["system", "user", "assistant", "user"]
    assert json.loads(messages[2].content) == fields_from_reply(DRAWN["reply"])
    assert messages[3].content == "Change the setup: Reply in English"
    # Without a setup to read back, the change rides the job message.
    [_, user] = form_messages(SetupContext(), JOB, change="Reply in English", history=[HistoryTurn(reply="?")])
    assert user.content == f"The job: {JOB}\n\nChange the setup: Reply in English"
