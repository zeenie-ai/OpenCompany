"""The graph behind a hired employee: the right trigger, its tools (those
that send wait for the owner per call while they ask first), replies that
go to whoever wrote in (always behind the gate, which reads the live rule),
reports to the owner, Talk (the owner's own line to them, whose agent alone
can send and show interfaces), instructions that read the same with the
rule on or off, warnings an owner can act on, and a graph the real
validator accepts."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import nodes  # noqa: F401 - registers every plugin for the validator
from services.employees.apps import get_app
from services.employees.builder import (
    APPROVED_CONDITION,
    SEND_CONDITION,
    BuildError,
    BuildInputs,
    LibrarySkill,
    build_employee_graph,
    label_key,
    schedule_params,
)
from services.employees.hire_request import HireEmployeeRequest, HireTrigger
from services.employees.llm import LLMChoice
from services.employees.prompt import OwnerProfile
from services.workflow_validator import validate_workflow

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def hire(**patch) -> HireEmployeeRequest:
    base = {
        "idempotency_key": "k",
        "job": "Answer customer messages on WhatsApp and book appointments",
        "name": "Maya",
        "role": "Receptionist",
        "apps": ["WhatsApp"],
        "steps": [{"title": "When a message arrives", "role": "trigger", "app": "WhatsApp"}, {"title": "Answer it", "role": "agent"}],
        "rules": {"ask_first": True, "items": []},
    }
    base.update(patch)
    return HireEmployeeRequest.model_validate(base)


def inputs(request: HireEmployeeRequest, *apps: str, connected=(), **patch) -> BuildInputs:
    fields = dict(
        workflow_id="7",
        request=request,
        apps=[get_app(app_id) for app_id in apps],
        connected_app_ids=set(connected),
        owner=OwnerProfile(name="Alex"),
        owner_values={"google_email": "alex@example.com"},
        timezone="Europe/London",
        llm=LLMChoice(provider="openai", model="gpt-x", local=False),
        memory=True,
        now=NOW,
    )
    fields.update(patch)
    return BuildInputs(**fields)


def node(built, role):
    node_id = built.node_roles[role]
    return next(n for n in built.nodes if n["id"] == node_id)


def edges_between(built, source_role, target_role):
    source, target = built.node_roles[source_role], built.node_roles[target_role]
    return [e for e in built.edges if e["source"] == source and e["target"] == target]


async def assert_valid(built):
    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    assert report["errors"] == [], report["errors"]


async def test_whatsapp_receptionist_asks_before_replying():
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}))
    assert node(built, "trigger")["type"] == "whatsappReceive"
    assert node(built, "gate")["type"] == "approvalGate"
    assert node(built, "reply")["type"] == "whatsappSend"
    trigger_key = label_key(node(built, "trigger")["data"]["label"])
    gate_key = label_key(node(built, "gate")["data"]["label"])
    gate = built.parameters[built.node_roles["gate"]]
    assert gate["recipient"] == "{{" + trigger_key + ".sender_phone}}"
    assert gate["draft"] == "{{maya.response}}"
    reply = built.parameters[built.node_roles["reply"]]
    # Who it goes to comes from the gate (from this run's trigger), never the agent.
    assert reply["phone"] == "{{" + gate_key + ".recipient}}"
    assert reply["message"] == "{{" + gate_key + ".text}}"
    assert [e["data"]["condition"] for e in edges_between(built, "agent", "gate")] == [SEND_CONDITION]
    assert [e["data"]["condition"] for e in edges_between(built, "gate", "reply")] == [APPROVED_CONDITION]
    assert edges_between(built, "trigger", "gate") and edges_between(built, "trigger", "reply")
    assert "context" not in built.node_roles  # strangers write in: no shared conversation
    assert built.delivery == "reply" and built.delivery_app == "WhatsApp"
    await assert_valid(built)


async def test_without_asking_first_the_reply_still_goes_through_the_gate():
    # The gate reads the live rule: with it off the draft goes straight
    # through, and turning it on later needs no rebuild.
    asking = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}))
    built = build_employee_graph(inputs(hire(rules={"ask_first": False}), "whatsapp", connected={"whatsapp"}))
    assert "gate" in built.node_roles
    gate_key = label_key(node(built, "gate")["data"]["label"])
    reply = built.parameters[built.node_roles["reply"]]
    assert reply["message"] == "{{" + gate_key + ".text}}"
    assert [e["data"]["condition"] for e in edges_between(built, "gate", "reply")] == [APPROVED_CONDITION]
    assert (built.nodes, built.edges, built.parameters) == (asking.nodes, asking.edges, asking.parameters)
    await assert_valid(built)


@pytest.mark.parametrize(
    ("request_patch", "apps"),
    [
        ({}, ("whatsapp",)),
        ({"apps": ["Telegram"], "trigger": {"kind": "schedule", "every": "day"}, "sends_via": "Telegram"}, ("telegram",)),
        ({"apps": ["Web browser"], "trigger": None}, ("web",)),
    ],
)
def test_the_instructions_read_the_same_with_the_rule_on_or_off(request_patch, apps):
    def instructions(ask_first):
        built = build_employee_graph(
            inputs(hire(**request_patch, rules={"ask_first": ask_first, "items": []}), *apps, connected=set(apps))
        )
        return {role: built.parameters[built.node_roles[role]]["system_message"] for role in ("agent", "talk_agent") if role in built.node_roles}

    assert instructions(True) == instructions(False)


async def test_gmail_inbox_replies_with_a_subject():
    request = hire(name="Leo", role="Inbox assistant", apps=["Gmail"], steps=[{"title": "Read new email", "role": "trigger", "app": "Gmail"}])
    built = build_employee_graph(inputs(request, "gmail", connected={"gmail"}))
    assert node(built, "trigger")["type"] == "googleGmailReceive"
    gate = built.parameters[built.node_roles["gate"]]
    assert gate["subject"].startswith("Re: {{")
    reply = built.parameters[built.node_roles["reply"]]
    gate_key = label_key(node(built, "gate")["data"]["label"])
    assert reply["subject"] == "{{" + gate_key + ".subject}}"
    assert reply["operation"] == "send"
    await assert_valid(built)


async def test_a_scheduled_briefer_reports_through_the_app_it_named():
    request = hire(
        name="Nora",
        role="Bookkeeper",
        apps=["Telegram", "Stripe"],
        steps=[{"title": "Every weekday at 8", "role": "trigger"}],
        trigger={"kind": "schedule", "every": "day", "at": "08:15"},
        sends_via="Telegram",
    )
    built = build_employee_graph(inputs(request, "telegram", "stripe", connected={"telegram", "stripe"}))
    schedule = built.parameters[built.node_roles["trigger"]]
    assert schedule == {"frequency": "days", "daily_time": "08:00", "timezone": "Europe/London"}
    notify = built.parameters[built.node_roles["notify"]]
    assert node(built, "notify")["type"] == "telegramSend" and notify["recipient_type"] == "self"
    assert "context" in built.node_roles
    assert [e["data"]["condition"] for e in edges_between(built, "agent", "notify")] == [SEND_CONDITION]
    # Asking first: the Stripe tool stays, and each call is refused while
    # they ask first (services/approvals/tool_calls.py).
    assert "stripeAction" in {n["type"] for n in built.nodes}
    # Recorded as it runs, and the owner is told it moved.
    assert built.trigger == {"kind": "schedule", "every": "day", "at": "08:00"}
    assert "they'll work every day at 08:00 (you asked for every day at 08:15)" in " ".join(built.warnings)
    await assert_valid(built)


async def test_money_tools_come_back_when_not_asking_first():
    request = hire(apps=["Stripe"], steps=[{"title": "Check payments", "role": "agent"}], trigger={"kind": "schedule", "every": "week", "day": "Monday"}, rules={"ask_first": False})
    built = build_employee_graph(inputs(request, "stripe", connected={"stripe"}))
    assert "stripeAction" in {n["type"] for n in built.nodes}
    assert built.parameters[built.node_roles["trigger"]]["weekday"] == "1"
    await assert_valid(built)


async def test_the_browser_reads_only_per_call_while_asking_first():
    # Its node is saved whole; while they ask first each call runs read-only.
    request = hire(apps=["Web browser"], steps=[{"title": "Check prices online", "role": "agent"}], trigger=None)
    built = build_employee_graph(inputs(request, "web"))
    assert built.parameters[built.node_roles["browser"]]["interaction"] == "full"
    instructions = built.parameters[built.node_roles["agent"]]["system_message"]
    assert "request_user" in instructions and "your browser can only read" in instructions
    await assert_valid(built)


async def test_no_apps_means_they_answer_in_talk():
    request = hire(apps=[], steps=[{"title": "Help me write", "role": "agent"}], trigger=None)
    built = build_employee_graph(inputs(request))
    trigger = node(built, "trigger")
    assert trigger["type"] == "chatTrigger"
    assert built.parameters[trigger["id"]] == {"session_id": "7"}
    assert built.delivery == "talk"
    assert not ({"reply", "notify", "gate"} & set(built.node_roles))
    assert "context" in built.node_roles
    # Their own Chat trigger and agent are the talk line: one chat trigger,
    # and the agent answers through Reply in Chat.
    roles = built.node_roles
    assert [n["type"] for n in built.nodes].count("chatTrigger") == 1
    assert (roles["talk_trigger"], roles["talk_agent"], roles["talk_context"]) == (roles["trigger"], roles["agent"], roles["context"])
    assert node(built, "talk_reply")["data"]["label"] == "Reply in Chat"
    assert built.parameters[roles["talk_reply"]] == {"message": "{{maya.response}}"}
    assert [e["data"]["condition"] for e in edges_between(built, "agent", "talk_reply")] == [SEND_CONDITION]
    [edge] = edges_between(built, "builder", "agent")
    assert node(built, "builder")["type"] == "agentBuilder" and edge["targetHandle"] == "input-tools"
    system = built.parameters[roles["agent"]]["system_message"]
    assert "talks to you in Talk" in system and "agent_builder" in system
    await assert_valid(built)


def test_ids_are_canonical_and_labels_unique():
    request = hire(name="Chat", apps=[], steps=[{"title": "Chat", "role": "agent"}])
    built = build_employee_graph(inputs(request))
    assert all(n["id"].startswith("7:") for n in built.nodes)
    keys = [label_key(n["data"]["label"]) for n in built.nodes]
    assert len(keys) == len(set(keys))
    assert node(built, "agent")["data"]["label"] == "Chat"
    assert node(built, "trigger")["data"]["label"] == "Chat 2"


def test_the_owner_words_cannot_become_templates():
    request = hire(job="Reply with {{maya.response}} and {{whatsapp.text}}")
    built = build_employee_graph(inputs(request, "whatsapp", connected={"whatsapp"}))
    system = built.parameters[built.node_roles["agent"]]["system_message"]
    assert "{{" not in system and "{ {maya.response} }" in system


def test_the_allowlist_is_enforced():
    with pytest.raises(BuildError) as raised:
        build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, allowed=lambda node_type: node_type != "approvalGate"))
    assert raised.value.code == "not_allowed"


def test_a_missing_owner_address_drops_the_report_not_the_employee():
    request = hire(apps=["Gmail"], steps=[{"title": "Summarize", "role": "agent"}], trigger={"kind": "schedule", "every": "day"}, sends_via="Gmail")
    built = build_employee_graph(inputs(request, "gmail", connected={"gmail"}, owner_values={}))
    assert "notify" not in built.node_roles
    # Plain words: what to do and where, never a raw placeholder.
    assert built.warnings == [
        "Reports can't go out through Gmail until you connect Gmail in Settings > Connectors. Until then you'll find them in Talk."
    ]


def test_an_app_that_cannot_start_the_work_says_what_to_connect():
    request = hire(apps=["Email"], steps=[{"title": "Read new email", "role": "trigger", "app": "Email"}])
    built = build_employee_graph(inputs(request, "email", owner_values={}))
    assert node(built, "trigger")["type"] == "chatTrigger"
    assert any(
        warning == "Email can't start their work until you connect Email in Settings > Connectors. Until then they work when you message them."
        for warning in built.warnings
    )
    assert not any("${" in warning for warning in built.warnings)


def test_a_schedule_is_recorded_as_it_runs_in_the_owners_time():
    # Sydney is not a zone the scheduler lists: it runs in UTC at the
    # nearest set time, which is Tuesday morning in Sydney.
    request = hire(apps=[], steps=[{"title": "Plan the week", "role": "agent"}], trigger={"kind": "schedule", "every": "week", "day": "monday", "at": "09:00"})
    built = build_employee_graph(inputs(request, timezone="Australia/Sydney"))
    assert built.parameters[built.node_roles["trigger"]] == {"frequency": "weeks", "weekday": "1", "weekly_time": "22:00", "timezone": "UTC"}
    assert built.trigger == {"kind": "schedule", "every": "week", "day": "tuesday", "at": "08:00"}
    assert "they'll work every Tuesday at 08:00 (you asked for every Monday at 09:00)" in " ".join(built.warnings)
    assert "(every Tuesday at 08:00)" in built.parameters[built.node_roles["agent"]]["prompt"]

    # A listed zone at a time the scheduler offers runs as asked, silently.
    request = hire(apps=[], steps=[{"title": "Brief me", "role": "agent"}], trigger={"kind": "schedule", "every": "month", "day": "L", "at": "09:00"})
    built = build_employee_graph(inputs(request))
    assert built.trigger == {"kind": "schedule", "every": "month", "day": "L", "at": "09:00"}
    assert not any("Schedules" in warning for warning in built.warnings)


# ----- Talk -----


def test_new_hires_never_share_concrete_tools_between_worker_and_talk():
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}))
    owners = {}
    for edge in built.edges:
        if edge.get("targetHandle") == "input-tools":
            owners.setdefault(edge["source"], set()).add(edge["target"])
    assert all(len(targets) == 1 for targets in owners.values()), owners


async def test_an_app_event_hire_gets_a_talk_line_beside_its_work():
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}))
    roles = built.node_roles
    assert (node(built, "talk_trigger")["type"], node(built, "talk_trigger")["data"]["label"]) == ("chatTrigger", "Talk")
    assert built.parameters[roles["talk_trigger"]] == {"session_id": "7"}
    talk = built.parameters[roles["talk_agent"]]
    assert node(built, "talk_agent")["data"]["label"] == "Talk with Maya"
    assert (talk["prompt"], talk["provider"], talk["model"]) == ("{{talk.message}}", "openai", "gpt-x")
    assert "talks to you in Talk" in talk["system_message"] and "agent_builder" in talk["system_message"]
    assert "agent_builder" not in built.parameters[roles["agent"]]["system_message"]
    # Its own Context and configured tool instances; the worker's callable
    # nodes are never rebound to the contact (strangers write to the worker).
    assert node(built, "talk_context")["data"]["agentNodeId"] == roles["talk_agent"]
    worker_tools = {e["source"] for e in built.edges if e["target"] == roles["agent"] and e["targetHandle"] == "input-tools"}
    talk_tools = {e["source"] for e in built.edges if e["target"] == roles["talk_agent"] and e["targetHandle"] == "input-tools"}
    only_talk = {n["type"]: n for n in built.nodes if n["id"] in talk_tools - worker_tools}
    worker_by_type = {n["type"]: n for n in built.nodes if n["id"] in worker_tools}
    assert set(only_talk) == set(worker_by_type) | {"agentBuilder", "chatUi", "whatsappSend"}
    assert not worker_tools & talk_tools
    for kind, worker in worker_by_type.items():
        assert built.parameters.get(only_talk[kind]["id"], {}) == built.parameters.get(worker["id"], {})
    send = built.parameters[only_talk["whatsappSend"]["id"]]
    assert send == {"recipient_type": "phone", "message_type": "text", "format_markdown": True}
    assert "send it with your send tools" in talk["system_message"]
    assert "send tools" not in built.parameters[roles["agent"]]["system_message"]
    assert built.parameters[roles["talk_reply"]] == {"message": "{{talkwithmaya.response}}"}
    assert "report_post" not in roles
    await assert_valid(built)


async def test_a_schedule_hire_posts_its_reports_to_talk():
    request = hire(apps=["Telegram"], steps=[{"title": "Brief me", "role": "agent"}], trigger={"kind": "schedule", "every": "day", "at": "09:00"}, sends_via="Telegram")
    built = build_employee_graph(inputs(request, "telegram", connected={"telegram"}))
    post = node(built, "report_post")
    assert (post["type"], post["data"]["label"]) == ("chatReply", "Post to Talk")
    assert built.parameters[post["id"]] == {"message": "{{maya.response}}"}
    assert [e["data"]["condition"] for e in edges_between(built, "agent", "report_post")] == [SEND_CONDITION]
    # The worker's Context and the talk agent's are two.
    assert node(built, "talk_context")["data"]["label"] == "Context 2"
    await assert_valid(built)


@pytest.mark.parametrize("missing", ["chatReply", "agentBuilder"])
def test_the_talk_line_needs_the_allowlist(missing):
    with pytest.raises(BuildError) as raised:
        build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, allowed=lambda node_type: node_type != missing))
    assert raised.value.code == "not_allowed"


def test_schedule_mapping():
    london = schedule_params(HireTrigger(kind="schedule", every="month", day="31", at="21:10"), "Europe/London", NOW)
    assert london == {"frequency": "months", "month_day": "1", "monthly_time": "22:00", "timezone": "Europe/London"}
    hourly = schedule_params(HireTrigger(kind="schedule", every="hour"), "Asia/Kolkata", NOW)
    assert hourly == {"frequency": "hours", "interval_hours": 1, "timezone": "Asia/Kolkata"}
    # Paris shares Berlin's offset.
    assert schedule_params(HireTrigger(kind="schedule", every="day", at="09:00"), "Europe/Paris", NOW)["timezone"] == "Europe/Berlin"
    # Sydney matches nothing listed: expressed in UTC (09:00 AEST = 23:00 UTC the day before).
    sydney = schedule_params(HireTrigger(kind="schedule", every="day", at="09:00"), "Australia/Sydney", NOW)
    assert sydney == {"frequency": "days", "daily_time": "22:00", "timezone": "UTC"}


# ----- the owner's skill library -----

LIBRARY = (
    LibrarySkill(name="book-appointments", description="Offers free times.", instructions="# Book\nOffer real times."),
    LibrarySkill(name="my-tone", description="How I write.", instructions="Write warmly."),
)


async def test_library_skills_ride_one_skills_node():
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, skills=LIBRARY))
    skills = node(built, "skills")
    assert skills["type"] == "masterSkill"
    params = built.parameters[skills["id"]]
    assert params["skill_folder"] == "assistant"
    config = params["skills_config"]
    assert list(config) == ["skill", "book-appointments", "my-tone"]
    assert config["skill"] == {"enabled": True, "instructions": "", "isCustomized": False, "required": True}
    # The text is copied in: the agent reads a library skill only from here.
    assert config["book-appointments"] == {
        "enabled": True,
        "instructions": "# Book\nOffer real times.",
        "isCustomized": False,
        "description": "Offers free times.",
    }
    [edge] = edges_between(built, "skills", "agent")
    assert (edge["sourceHandle"], edge["targetHandle"]) == ("output-tool", "input-skill")
    await assert_valid(built)


async def test_the_skills_node_expands_into_the_skill_tool():
    from nodes.skill._expander import expand_master_skill
    from services.skill_runtime import skill_tool_info

    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, skills=LIBRARY))
    config = built.parameters[built.node_roles["skills"]]["skills_config"]
    entries = await expand_master_skill(built.node_roles["skills"], config)
    catalogue = skill_tool_info(entries, built.node_roles["agent"])["parameters"]["tool_description"]
    assert "- book-appointments: Offers free times." in catalogue
    assert "- my-tone: How I write." in catalogue


def test_an_empty_library_adds_no_skills_node():
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}))
    assert "skills" not in built.node_roles
    assert not any(n["type"] == "masterSkill" for n in built.nodes)
    blank = (LibrarySkill(name="blank", description="Nothing", instructions="  "),)
    assert "skills" not in build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, skills=blank)).node_roles


def test_reserved_skill_names_never_reach_a_hire():
    skills = (
        LibrarySkill(name="skill", description="Takes over the Skill tool", instructions="x"),
        LibrarySkill(name="pirate-personality", description="Replaces the system message", instructions="x"),
        LibrarySkill(name="ok-skill", description="Fine", instructions="y"),
    )
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, skills=skills))
    config = built.parameters[built.node_roles["skills"]]["skills_config"]
    assert set(config) == {"skill", "ok-skill"}
    assert config["skill"]["instructions"] == ""
    assert sum("can't be given" in warning for warning in built.warnings) == 2


def test_the_skills_node_needs_the_allowlist():
    with pytest.raises(BuildError) as raised:
        build_employee_graph(
            inputs(hire(), "whatsapp", connected={"whatsapp"}, skills=LIBRARY, allowed=lambda node_type: node_type != "masterSkill")
        )
    assert raised.value.code == "not_allowed"


# ----- the canvas the Workspace shows -----


async def test_every_hire_gets_a_canvas():
    built = build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}))
    assert node(built, "canvas")["type"] == "canvas"
    [edge] = edges_between(built, "canvas", "agent")
    assert (edge["sourceHandle"], edge["targetHandle"]) == ("output-tool", "input-tools")
    assert "canvas tool" in built.parameters[built.node_roles["agent"]]["system_message"]
    await assert_valid(built)


def test_the_canvas_needs_the_allowlist():
    with pytest.raises(BuildError) as raised:
        build_employee_graph(inputs(hire(), "whatsapp", connected={"whatsapp"}, allowed=lambda node_type: node_type != "canvas"))
    assert raised.value.code == "not_allowed"


async def test_the_talk_agent_sends_email_as_the_owner_set_it_up():
    request = hire(apps=["Email"], steps=[{"title": "Brief me", "role": "agent"}], trigger={"kind": "schedule", "every": "day"}, sends_via="Email")
    owner = {"email_provider": "gmail", "email_address": "alex@example.com"}
    built = build_employee_graph(inputs(request, "email", owner_values=owner))
    talk_agent = built.node_roles["talk_agent"]
    wired = {e["source"] for e in built.edges if e["target"] == talk_agent and e["targetHandle"] == "input-tools"}
    [send] = [n for n in built.nodes if n["id"] in wired and n["type"] == "emailSend"]
    # Who and what come from the model; which account sends never does.
    assert built.parameters[send["id"]] == {"provider": "gmail", "body_type": "text"}
    await assert_valid(built)

    # Without the owner's account set up, the talk agent cannot send email yet.
    built = build_employee_graph(inputs(request, "email", owner_values={}))
    assert "emailSend" not in {n["type"] for n in built.nodes}
    assert "They can't send on Email for you until you connect Email in Settings > Connectors." in built.warnings
