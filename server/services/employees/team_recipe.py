"""Versioned employee recipe derived from the shipped AI Employee template.

Selection is deterministic and never accepts a node identifier, credential,
or tool catalogue from job text. The live plugin registry and Hire policy
decide which registered specialist may be instantiated.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from services.graph_build import (
    CONTEXT_TYPE, Edge, Labels, NodeIds, context_data, context_edge,
    graph_node, label_key, main_edge, ref, skill_edge, tool_edge,
)

RECIPE_VERSION = 1
TEAM_LEAD_TYPE = "ai_employee"


@dataclass(frozen=True)
class Responsibility:
    key: str
    node_type: str
    label: str
    description: str
    words: tuple[str, ...]


RESPONSIBILITIES = (
    Responsibility("research", "web_agent", "Research", "Researches information", ("research", "search", "news", "brief", "web", "competitor", "investigat")),
    Responsibility("calendar", "productivity_agent", "Appointments", "Checks your calendar and appointments", ("calendar", "appointment", "meeting", "book", "schedule")),
    Responsibility("coding", "coding_agent", "Development", "Builds and checks software", ("code", "coding", "software", "develop", "program", "github")),
    Responsibility("social", "social_agent", "Content", "Prepares content for your channels", ("social", "post", "content", "marketing", "instagram", "linkedin", "twitter")),
    Responsibility("operations", "tool_agent", "App tasks", "Handles routine work in your apps", ("email", "inbox", "gmail", "outlook", "message", "customer", "reception", "payment", "spreadsheet", "whatsapp", "telegram")),
)
DEFAULT_RESPONSIBILITY = RESPONSIBILITIES[-1]


def select_responsibilities(job: str, apps: Iterable[str] = ()) -> list[Responsibility]:
    """One to three relevant responsibilities, in stable recipe order."""
    text = " ".join((job, *apps)).lower()
    selected = [role for role in RESPONSIBILITIES if any(word in text for word in role.words)]
    return (selected or [DEFAULT_RESPONSIBILITY])[:3]


def team_preview(job: str, apps: Iterable[str] = ()) -> list[dict[str, str]]:
    """Only ordinary-language responsibilities cross the normal-mode UI."""
    return [{"responsibility": role.description} for role in select_responsibilities(job, apps)]


def preview_from_reply(job: str, raw_reply: str) -> list[dict[str, str]]:
    """Use the reviewed routine, so ordinary-language changes change the team."""
    from services.employees.setup_reply import app_names, parse_reply, usable_elements

    text = [job]
    for element in usable_elements(parse_reply(raw_reply).spec):
        props = element["props"]
        if element["type"] == "AgentCard":
            text.append(str(props.get("role") or ""))
        elif element["type"] == "Plan":
            text.extend(str(step.get("title") or "") for step in props.get("steps", []) if isinstance(step, dict))
    return team_preview(" ".join(text), app_names(raw_reply))


def _repoint(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _repoint(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_repoint(item, old, new) for item in value]
    return new if value == old else value


def build_team(built: Any, inputs: Any) -> Any:
    """Compose configured specialists with isolated Context and a Talk contact.

    Delivery nodes and immutable recipient references are retained. The
    employee job boundary decides when reviewed output is eligible to reach
    those nodes; specialists are never wired to delivery nodes.
    """
    from services.employees.builder import BuildError
    from services.employees.policy import SKILL_TOOL_ENTRY, SKILL_TOOL_NAME
    from services.employees.prompt import neutralize_templates
    from services.node_registry import get_node_class

    built = deepcopy(built)
    if not inputs.allowed(TEAM_LEAD_TYPE):
        raise BuildError("not_allowed", "Team-based employees are not available")
    roles = built.node_roles
    original = roles["agent"]
    manual = built.trigger.get("kind") == "manual"
    ids = NodeIds(inputs.workflow_id, built.nodes)
    labels = Labels(node["data"]["label"] for node in built.nodes)
    model = {"provider": inputs.llm.provider if inputs.llm else "openai", "model": inputs.llm.model if inputs.llm else ""}
    original_parameters = deepcopy(built.parameters[original])
    # A manual employee already has the desired conversational contact. A
    # separately triggered employee already has a distinct Talk line.
    if manual:
        lead = ids.next(TEAM_LEAD_TYPE)
        lead_label = labels.take(f"{inputs.request.name}'s work")
        built.nodes.append(graph_node(lead, TEAM_LEAD_TYPE, lead_label, (360, -420)))
        built.parameters[lead] = {**original_parameters, **model}
        roles["agent"] = lead
    else:
        lead = ids.next(TEAM_LEAD_TYPE)
        built.nodes = _repoint(built.nodes, original, lead)
        built.edges = _repoint(built.edges, original, lead)
        built.parameters[lead] = built.parameters.pop(original)
        built.node_roles = roles = _repoint(roles, original, lead)
        next(node for node in built.nodes if node["id"] == lead)["type"] = TEAM_LEAD_TYPE
        lead_label = next(node for node in built.nodes if node["id"] == lead)["data"]["label"]
    # Every member owns its Context, including leads reached by public apps.
    if manual or "context" not in roles:
        context_id = ids.next(CONTEXT_TYPE)
        built.nodes.append(graph_node(context_id, CONTEXT_TYPE, labels.take("Work context"), (80, -420), context_data(lead)))
        built.edges.append(context_edge(context_id, lead).to_dict())
        roles["context"] = context_id
    talk = roles["talk_agent"]
    built.parameters[talk]["system_message"] += (
        "\nYou are the owner's conversational contact. For substantive work, submit one employee job to your team, "
        "acknowledge promptly, and explain that the reviewed result will arrive here later. "
        "An assignment receipt is progress, never the final result. Never impersonate a specialist or bypass approval."
    )
    built.parameters[lead]["system_message"] = (
        original_parameters["system_message"] + "\nYou coordinate and review this employee's specialist team. "
        "Use the intrinsic Task Manager to assign durable tasks to your connected teammates. Each assignment must "
        "state its mission, context, dependencies and acceptance criteria. Review submitted results; accept only "
        "results meeting the criteria, otherwise request revision or reassign. Reuse the existing task identity for "
        "retries. Finish the team only after all required work is accepted. Then provide one reviewed result. "
        "Do not present an assignment acknowledgement as completed work. Do not independently publish through apps."
    )
    selected = select_responsibilities(" ".join((inputs.request.job, inputs.request.role, *(step.title for step in inputs.request.steps))), (app.name for app in inputs.apps))
    members: list[dict[str, Any]] = []
    for index, responsibility in enumerate(selected, 1):
        node_type = responsibility.node_type
        if get_node_class(node_type) is None or not inputs.allowed(node_type):
            node_type = "aiAgent"
        if not inputs.allowed(node_type):
            raise BuildError("not_allowed", f"A teammate for {responsibility.description.lower()} is not available")
        member = ids.next(node_type)
        member_label = labels.take(responsibility.label)
        instruction = neutralize_templates(
            f"Your responsibility is: {responsibility.description}. You work for {inputs.request.name}, whose job is: {inputs.request.job}. "
            "Work only on the assigned mission and supplied context. Respect its dependencies and acceptance criteria. "
            "Return evidence, the result, and any unresolved limitations to the lead for review. "
            "Do not send final replies to the owner or app recipients. Sending or changing things still requires the employee's approval rule."
        )
        built.nodes.append(graph_node(member, node_type, member_label, (360 + 360 * index, -420), {"responsibility": responsibility.description}))
        built.parameters[member] = {"system_message": instruction, "prompt": "Complete your assigned task and submit the result for lead review.", **model}
        built.edges.append(Edge(member, "output-top", lead, "input-teammates").to_dict())
        context_id = ids.next(CONTEXT_TYPE)
        built.nodes.append(graph_node(context_id, CONTEXT_TYPE, labels.take(f"{responsibility.label} context"), (360 + 360 * index, -600), context_data(member)))
        built.edges.append(context_edge(context_id, member).to_dict())
        roles[f"specialist_{index}"] = member
        roles[f"specialist_{index}_context"] = context_id
        skill_id = ids.next("masterSkill")
        skill_name = f"employee-{responsibility.key}"
        config = {SKILL_TOOL_NAME: dict(SKILL_TOOL_ENTRY), skill_name: {"enabled": True, "instructions": instruction, "description": responsibility.description, "isCustomized": False}}
        # Library instructions go only to specialists whose responsibilities
        # match their declared name/description, never to the whole catalogue.
        for skill in inputs.skills:
            if any(word in (skill.name + " " + skill.description).lower() for word in responsibility.words):
                from services.employees.policy import check_skill

                if check_skill(skill.name).allowed and skill.instructions.strip():
                    config[skill.name] = {"enabled": True, "instructions": skill.instructions, "description": skill.description, "isCustomized": False}
        if not inputs.allowed("masterSkill"):
            raise BuildError("not_allowed", "Team instructions are not available")
        built.nodes.append(graph_node(skill_id, "masterSkill", labels.take(f"{responsibility.label} instructions"), (360 + 360 * index, -240)))
        built.parameters[skill_id] = {"skill_folder": "assistant", "skills_config": config}
        built.edges.append(skill_edge(skill_id, member).to_dict())
        roles[f"specialist_{index}_skills"] = skill_id
        members.append({"node_id": member, "node_type": node_type, "responsibility": responsibility.description, "role": responsibility.key, "context_node_id": context_id, "tools": [], "skills": list(config)[1:]})
    # Move app and research tools from the lead/contact to the relevant
    # specialist. The lead keeps coordination tools; Talk keeps Builder/UI
    # and submits jobs for app work. Per-member memory never shares a
    # Context or conversational state with another member.
    nodes = {node["id"]: node for node in built.nodes}
    app_types: dict[str, str] = {tool.type: app.name for app in inputs.apps for tool in app.tools}
    app_types.update({app.talk_send.type: app.name for app in inputs.apps if app.talk_send is not None})
    movable = {"duckduckgoSearch", *app_types}
    original_tools = [edge["source"] for edge in built.edges if edge.get("target") in {lead, talk} and edge.get("targetHandle") == "input-tools" and nodes[edge["source"]]["type"] in movable]
    # The lead reviews work rather than consuming every library instruction.
    # Talk's existing library remains intact for conversational requests.
    built.edges = [edge for edge in built.edges if not (edge.get("target") == lead and edge.get("targetHandle") == "input-skill")]
    built.edges = [edge for edge in built.edges if not (edge.get("source") in original_tools and edge.get("target") in {lead, talk} and edge.get("targetHandle") == "input-tools")]
    for tool in dict.fromkeys(original_tools):
        node_type = nodes[tool]["type"]
        text = (app_types.get(node_type, "research") + " " + node_type).lower()
        recipient = next((member for member, role in zip(members, selected) if any(word in text for word in role.words)), None)
        recipient = recipient or next((member for member in members if member["role"] == "operations"), members[0])
        built.edges.append(tool_edge(tool, recipient["node_id"]).to_dict())
        recipient["tools"].append(tool)
    # Keep one memory per member. A legacy Talk line may share the worker's
    # memory: remove that binding and give each agent its own instance.
    memories = {node["id"] for node in built.nodes if node["type"] == "simpleMemory"}
    built.edges = [edge for edge in built.edges if not (edge.get("source") in memories and edge.get("targetHandle") == "input-tools")]
    agents = [lead, talk, *(member["node_id"] for member in members)]
    if inputs.memory:
        for index, agent in enumerate(agents):
            memory = next(iter(memories), None) if index == 0 else None
            if memory is None:
                if not inputs.allowed("simpleMemory"):
                    raise BuildError("not_allowed", "Employee memory is not available")
                memory = ids.next("simpleMemory")
                built.nodes.append(graph_node(memory, "simpleMemory", labels.take("Memory"), (170 * index, -60)))
            built.edges.append(tool_edge(memory, agent).to_dict())
            if index > 1:
                members[index - 2]["tools"].append(memory)
    # Task events are scoped to this lead, and use the canonical task input.
    if not inputs.allowed("taskTrigger"):
        raise BuildError("not_allowed", "Team review is not available")
    task_trigger = ids.next("taskTrigger")
    built.nodes.append(graph_node(task_trigger, "taskTrigger", labels.take("Review completed work"), (0, -600)))
    built.parameters[task_trigger] = {"parent_node_id": lead, "status_filter": "all"}
    built.edges.append(Edge(task_trigger, "output-main", lead, "input-task").to_dict())
    roles["task_trigger"] = task_trigger
    # A receipt and a reviewed result have different paths. Only the
    # durable job delivery node may enter the existing approval/delivery
    # subgraph. Captured trigger outputs, including recipients, are restored
    # by that boundary rather than reconstructed from an agent's text.
    if not inputs.allowed("employeeJob"):
        raise BuildError("not_allowed", "Team work is not available")
    delivery_ids = [roles[key] for key in ("gate", "reply", "notify", "report_post") if key in roles]
    final_reply = ids.next("chatReply")
    built.nodes.append(graph_node(final_reply, "chatReply", labels.take("Reviewed result"), (1080, -420)))
    built.parameters[final_reply] = {"message": ref(label_key(lead_label), "response")}
    roles["job_reply"] = final_reply
    if manual:
        delivery_ids.append(final_reply)
        # The contact retains its existing chatReply acknowledgement.
        coordination = {"writeTodos", "canvas", "currentTimeTool"}
        for edge in list(built.edges):
            if edge.get("target") == talk and edge.get("targetHandle") == "input-tools" and nodes.get(edge["source"], {}).get("type") in coordination:
                built.edges.append(tool_edge(edge["source"], lead).to_dict())
    else:
        built.edges = [edge for edge in built.edges if not (
            edge.get("targetHandle") == "input-main" and (
                edge.get("target") in delivery_ids and edge.get("source") in {lead, roles["trigger"]}
                or edge.get("source") == roles["trigger"] and edge.get("target") == lead
            )
        )]
        intake = ids.next("employeeJob")
        intake_label = labels.take("Start work")
        built.nodes.append(graph_node(intake, "employeeJob", intake_label, (180, 200)))
        built.parameters[intake] = {"operation": "intake", "lead_node_id": lead, "mission": original_parameters["prompt"], "delivery_node_ids": delivery_ids}
        built.edges.extend((main_edge(roles["trigger"], intake).to_dict(), main_edge(intake, lead).to_dict()))
        built.parameters[lead]["prompt"] = ref(label_key(intake_label), "mission")
        roles["job_intake"] = intake
    built.edges = [edge for edge in built.edges if not (
        edge.get("target") == talk and edge.get("targetHandle") == "input-tools"
        and nodes.get(edge["source"], {}).get("type") in {"writeTodos", "canvas"}
    )]
    # Date-sensitive work uses the same configured clock; sharing a read
    # tool does not share a member's Context, memory, or callable identity.
    clock = next((node["id"] for node in built.nodes if node["type"] == "currentTimeTool"), None)
    if clock:
        for member in members:
            built.edges.append(tool_edge(clock, member["node_id"]).to_dict())
            member["tools"].append(clock)
    submit = ids.next("employeeJob")
    built.nodes.append(graph_node(submit, "employeeJob", labels.take("Give work to their team"), (0, 780)))
    built.parameters[submit] = {"operation": "submit", "lead_node_id": lead, "delivery_node_ids": [final_reply]}
    built.edges.append(tool_edge(submit, talk).to_dict())
    roles["job_submit"] = submit
    deliver = ids.next("employeeJob")
    built.nodes.append(graph_node(deliver, "employeeJob", labels.take("Deliver reviewed work"), (720, -420)))
    built.parameters[deliver] = {"operation": "deliver", "lead_node_id": lead, "delivery_node_ids": delivery_ids}
    built.edges.append(main_edge(lead, deliver).to_dict())
    roles["job_delivery"] = deliver
    # Replaced canonical lead IDs must also be reflected in edge identities.
    for edge in built.edges:
        edge["id"] = f'e-{edge["source"]}-{edge["sourceHandle"]}-{edge["target"]}-{edge["targetHandle"]}'
    built.team_plan = {"version": RECIPE_VERSION, "recipe": "ai_employee", "lead_node_id": lead, "talk_node_id": talk, "members": members, "model": model, "requires": ["temporal", "agent_workflow"]}
    return built
