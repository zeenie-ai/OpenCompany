"""talk.py: whether the owner can talk to a graph, and the additions that
let them: "Reply in Chat" after a chat-fed agent, or a whole talk line
beside the worker with independent tools, shared skills and its own
Context. The line runs only on the owner's messages."""

from __future__ import annotations

import pytest

import nodes  # noqa: F401 - registers every plugin (agents, the validator)
from services.approvals.contract import send_condition
from services.employees.talk import TalkAgent, find_talk_line, plan_talk_line, talk_state
from services.graph_build import add_to_graph, context_data, context_edge, main_edge, skill_edge, tool_edge
from services.workflow_validator import validate_workflow

SEND = send_condition()


def node(node_id, node_type, label, x=0, y=0, **data):
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "data": {"label": label, **data}}


def graph(nodes, edges):
    return {"nodes": nodes, "edges": [edge.to_dict() for edge in edges]}


#: A WhatsApp receptionist as Hire builds her: a worker with tools, skills
#: and a reply, and no chat trigger.
RECEPTIONIST = graph(
    [
        node("7:whatsappReceive:1", "whatsappReceive", "New WhatsApp message", 0, 200),
        node("7:aiAgent:1", "aiAgent", "Maya", 360, 200),
        node("7:writeTodos:1", "writeTodos", "Checklist", 120, 440),
        node("7:canvas:1", "canvas", "Canvas", 290, 440),
        node("7:masterSkill:1", "masterSkill", "Skills", -60, 440),
        node("7:whatsappSend:1", "whatsappSend", "Reply on WhatsApp", 720, 200),
    ],
    [
        main_edge("7:whatsappReceive:1", "7:aiAgent:1"),
        tool_edge("7:writeTodos:1", "7:aiAgent:1"),
        tool_edge("7:canvas:1", "7:aiAgent:1"),
        skill_edge("7:masterSkill:1", "7:aiAgent:1"),
        main_edge("7:aiAgent:1", "7:whatsappSend:1", SEND),
    ],
)

#: A Chat hire: the chat trigger feeds the agent, which has its Context.
CHAT = graph(
    [
        node("7:chatTrigger:1", "chatTrigger", "Chat", 0, 200),
        node("7:aiAgent:1", "aiAgent", "Sam", 360, 200),
        node("7:writeTodos:1", "writeTodos", "Checklist", 120, 440),
        node("7:context:1", "context", "Context", 360, 20, **context_data("7:aiAgent:1")),
    ],
    [
        main_edge("7:chatTrigger:1", "7:aiAgent:1"),
        tool_edge("7:writeTodos:1", "7:aiAgent:1"),
        context_edge("7:context:1", "7:aiAgent:1"),
    ],
)

TALK_AGENT = TalkAgent("Talk with Maya", {"provider": "openai", "model": "gpt-x", "system_message": "You are Maya."})


# ----- the state -----


def test_a_chat_fed_agent_that_replies_is_on():
    talking = {
        "nodes": CHAT["nodes"] + [node("7:chatReply:1", "chatReply", "Reply in Chat")],
        "edges": CHAT["edges"] + [main_edge("7:aiAgent:1", "7:chatReply:1", SEND).to_dict()],
    }
    state = talk_state(talking)
    assert (state.state, state.agent_node_id) == ("on", "7:aiAgent:1")
    assert state.summary() == {"state": "on", "agent_node_id": "7:aiAgent:1"}


@pytest.mark.parametrize("agent_type", ["aiAgent", "ai_employee", "claude_code_agent"])
def test_a_chat_fed_agent_without_a_reply_is_off(agent_type):
    state = talk_state(graph([node("c", "chatTrigger", "Chat"), node("a", agent_type, "Agent")], [main_edge("c", "a")]))
    assert state.state == "off" and state.line.agent == "a" and state.line.reply is None
    # Not answering yet: the page follows no agent.
    assert state.agent_node_id is None


def test_without_a_chat_trigger_the_worker_is_copied():
    state = talk_state(RECEPTIONIST, worker="7:aiAgent:1")
    assert (state.state, state.worker, state.line) == ("off", "7:aiAgent:1", None)
    # A worker that cannot be copied gives way to one that can.
    mixed = graph([node("code", "claude_code_agent", "Coder"), node("chat", "chatAgent", "Zeenie")], [])
    assert talk_state(mixed, worker="code").worker == "chat"
    assert talk_state(graph([node("code", "claude_code_agent", "Coder")], [])).state == "unsupported"


def test_a_chat_trigger_feeding_no_agent_is_unsupported():
    # A second chat trigger would start two runs for every message.
    state = talk_state(graph([node("c", "chatTrigger", "Chat"), node("log", "console", "Log"), node("a", "aiAgent", "A")], [main_edge("c", "log")]))
    assert state.state == "unsupported"
    assert talk_state(graph([], [])).state == "unsupported"


def test_only_the_main_flow_and_enabled_nodes_count():
    # A chat trigger into the task handle is not the owner talking.
    assert talk_state(graph([node("c", "chatTrigger", "Chat"), node("a", "aiAgent", "A")], [])).state == "unsupported"
    task = {**main_edge("c", "a").to_dict(), "targetHandle": "input-task"}
    assert talk_state({"nodes": [node("c", "chatTrigger", "Chat"), node("a", "aiAgent", "A")], "edges": [task]}).state == "unsupported"
    off_reply = graph(
        [node("c", "chatTrigger", "Chat"), node("a", "aiAgent", "A"), node("r", "chatReply", "Reply", disabled=True)],
        [main_edge("c", "a"), main_edge("a", "r")],
    )
    assert talk_state(off_reply).state == "off"
    off_trigger = graph([node("c", "chatTrigger", "Chat", disabled=True), node("a", "aiAgent", "A")], [main_edge("c", "a")])
    assert talk_state(off_trigger).worker == "a"


def test_the_line_that_replies_wins():
    two = graph(
        [node("c1", "chatTrigger", "Chat"), node("a1", "aiAgent", "A"), node("c2", "chatTrigger", "Chat 2"), node("a2", "aiAgent", "B"), node("r", "chatReply", "R")],
        [main_edge("c1", "a1"), main_edge("c2", "a2"), main_edge("a2", "r")],
    )
    line = find_talk_line(two)
    assert (line.trigger, line.agent, line.reply) == ("c2", "a2", "r")


# ----- the additions -----


def test_a_chat_hire_gets_a_reply_after_its_own_agent():
    state = talk_state(CHAT, worker="7:aiAgent:1")
    plan = plan_talk_line(CHAT, state, workflow_id="7", hired=True)
    placed = add_to_graph("7", CHAT, plan.additions)
    assert [(n["type"], n["data"]["label"]) for n in placed.nodes] == [("chatReply", "Reply in Chat"), ("agentBuilder", "Agent Builder")]
    reply, builder = placed.node_ids["talk_reply"], placed.node_ids["builder"]
    assert placed.parameters[reply] == {"message": "{{sam.response}}"}
    edges = {(e["source"], e["sourceHandle"], e["target"], e["targetHandle"]): (e.get("data") or {}).get("condition") for e in placed.edges}
    assert edges == {
        ("7:aiAgent:1", "output-main", reply, "input-main"): SEND,
        (builder, "output-tool", "7:aiAgent:1", "input-tools"): None,
    }
    # No second chat trigger: the worker is the talk agent.
    assert plan.role_ids(placed.node_ids) == {
        "talk_trigger": "7:chatTrigger:1",
        "talk_agent": "7:aiAgent:1",
        "talk_context": "7:context:1",
        "talk_reply": reply,
        "builder": builder,
    }


def test_the_agent_builder_is_for_hires_and_never_added_twice():
    state = talk_state(CHAT)
    assert [n.type for n in plan_talk_line(CHAT, state, workflow_id="7").additions.nodes] == ["chatReply"]
    with_builder = {
        "nodes": CHAT["nodes"] + [node("b", "agentBuilder", "Agent Builder")],
        "edges": CHAT["edges"] + [tool_edge("b", "7:aiAgent:1").to_dict()],
    }
    plan = plan_talk_line(with_builder, talk_state(with_builder), workflow_id="7", hired=True)
    assert [n.type for n in plan.additions.nodes] == ["chatReply"] and "builder" not in plan.roles


def test_a_whole_line_beside_the_worker():
    state = talk_state(RECEPTIONIST, worker="7:aiAgent:1")
    plan = plan_talk_line(RECEPTIONIST, state, workflow_id="7", agent=TALK_AGENT, hired=True)
    placed = add_to_graph("7", RECEPTIONIST, plan.additions)
    roles = plan.role_ids(placed.node_ids)
    by_id = {n["id"]: n for n in placed.nodes}
    assert {role: (by_id[node_id]["type"], by_id[node_id]["data"]["label"]) for role, node_id in roles.items()} == {
        "talk_trigger": ("chatTrigger", "Talk"),
        "talk_agent": ("aiAgent", "Talk with Maya"),
        "talk_context": ("context", "Context"),
        "talk_reply": ("chatReply", "Reply in Chat"),
        "builder": ("agentBuilder", "Agent Builder"),
    }
    # It listens on the workflow's own session and answers from the message.
    assert placed.parameters[roles["talk_trigger"]] == {"session_id": "7"}
    assert placed.parameters[roles["talk_agent"]] == {**TALK_AGENT.params, "prompt": "{{talk.message}}"}
    assert placed.parameters[roles["talk_reply"]] == {"message": "{{talkwithmaya.response}}"}
    # Its own Context, linked the way Hire links one.
    assert by_id[roles["talk_context"]]["data"]["agentNodeId"] == roles["talk_agent"]

    def into(target):
        return {(e["source"], e["targetHandle"]) for e in placed.edges if e["target"] == target}

    talk = roles["talk_agent"]
    assert into(talk) == {
        (roles["talk_trigger"], "input-main"),
        (roles["talk_context"], "input-context"),
        ("7:writeTodos:2", "input-tools"),
        ("7:canvas:2", "input-tools"),
        ("7:masterSkill:1", "input-skill"),
        (roles["builder"], "input-tools"),
    }
    # The worker gets nothing new: no builder for a worker strangers write to.
    assert into("7:aiAgent:1") == set()
    assert [e for e in placed.edges if e["source"] == talk and e["target"] == roles["talk_reply"]][0]["data"]["condition"] == SEND


async def test_a_whole_line_is_a_valid_graph_whose_run_is_only_the_line():
    from services.temporal.trigger_listener_workflow import _build_run_graph

    state = talk_state(RECEPTIONIST, worker="7:aiAgent:1")
    plan = plan_talk_line(RECEPTIONIST, state, workflow_id="7", agent=TALK_AGENT, hired=True)
    placed = add_to_graph("7", RECEPTIONIST, plan.additions)
    report = await validate_workflow(nodes=placed.graph["nodes"], edges=placed.graph["edges"], parameters_by_id=placed.parameters)
    assert report["errors"] == []

    roles = plan.role_ids(placed.node_ids)
    run_nodes, _ = _build_run_graph(trigger_node_id=roles["talk_trigger"], trigger_output={"message": "hi"}, nodes=placed.graph["nodes"], edges=placed.graph["edges"])
    ran = {n["id"] for n in run_nodes}
    assert {roles["talk_agent"], roles["talk_reply"], roles["talk_context"], "7:writeTodos:2", "7:masterSkill:1"} <= ran
    assert not ran & {"7:aiAgent:1", "7:whatsappSend:1", "7:whatsappReceive:1", "7:writeTodos:1", "7:canvas:1"}


def test_hired_talk_copies_tool_configuration_without_reusing_worker_nodes():
    params = {"7:canvas:1": {"title": "Owner board"}, "7:writeTodos:1": {"todos": ["Keep appointments"]}}
    plan = plan_talk_line(RECEPTIONIST, talk_state(RECEPTIONIST), workflow_id="7", agent=TALK_AGENT,
                          hired=True, parameters_by_id=params)
    placed = add_to_graph("7", RECEPTIONIST, plan.additions)
    assert placed.parameters["7:canvas:2"] == params["7:canvas:1"]
    assert placed.parameters["7:writeTodos:2"] == params["7:writeTodos:1"]
    assert not any(edge["source"] in params and edge["targetHandle"] == "input-tools" for edge in placed.edges)


def test_a_schedule_workers_reports_are_posted_to_talk():
    schedule = graph(
        [node("7:cronScheduler:1", "cronScheduler", "Schedule"), node("7:aiAgent:1", "aiAgent", "Nora", 360, 200)],
        [main_edge("7:cronScheduler:1", "7:aiAgent:1")],
    )
    state = talk_state(schedule, worker="7:aiAgent:1")
    plan = plan_talk_line(schedule, state, workflow_id="7", agent=TALK_AGENT, hired=True, report_from="7:aiAgent:1")
    placed = add_to_graph("7", schedule, plan.additions)
    post = placed.node_ids["report_post"]
    assert next(n for n in placed.nodes if n["id"] == post)["data"]["label"] == "Post to Talk"
    assert placed.parameters[post] == {"message": "{{nora.response}}"}
    [edge] = [e for e in placed.edges if e["target"] == post]
    assert (edge["source"], edge["data"]["condition"]) == ("7:aiAgent:1", SEND)
    # A worker that already posts to the thread is left alone.
    posted = {"nodes": placed.graph["nodes"], "edges": placed.graph["edges"]}
    assert "report_post" not in plan_talk_line(posted, talk_state(posted), workflow_id="7", hired=True, report_from="7:aiAgent:1").roles


def test_a_taken_label_gets_a_number_and_templates_follow():
    # "Talk!" slugs like "Talk": a second trigger with that slug would never
    # be registered, so the new one is "Talk 2".
    taken = {
        "nodes": RECEPTIONIST["nodes"] + [node("7:webhookTrigger:1", "webhookTrigger", "Talk!")],
        "edges": RECEPTIONIST["edges"],
    }
    plan = plan_talk_line(taken, talk_state(taken, worker="7:aiAgent:1"), workflow_id="7", agent=TALK_AGENT)
    placed = add_to_graph("7", taken, plan.additions)
    assert placed.labels["talk_trigger"] == "Talk 2"
    assert placed.parameters[placed.node_ids["talk_agent"]]["prompt"] == "{{talk2.message}}"


def test_nothing_to_add_when_on_and_nothing_possible_when_unsupported():
    on = {
        "nodes": CHAT["nodes"] + [node("7:chatReply:1", "chatReply", "Reply in Chat")],
        "edges": CHAT["edges"] + [main_edge("7:aiAgent:1", "7:chatReply:1").to_dict()],
    }
    plan = plan_talk_line(on, talk_state(on), workflow_id="7", hired=True)
    assert plan.additions.nodes == () and plan.additions.edges == ()
    assert plan.roles == {"talk_trigger": "7:chatTrigger:1", "talk_agent": "7:aiAgent:1", "talk_reply": "7:chatReply:1", "talk_context": "7:context:1"}
    unsupported = graph([node("c", "chatTrigger", "Chat")], [])
    with pytest.raises(ValueError):
        plan_talk_line(unsupported, talk_state(unsupported), workflow_id="7")
    with pytest.raises(ValueError):
        plan_talk_line(RECEPTIONIST, talk_state(RECEPTIONIST), workflow_id="7")  # a whole line needs its agent
