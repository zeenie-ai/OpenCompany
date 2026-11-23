"""Versioned employee recipe derived from the shipped AI Employee template.

Selection is deterministic and never accepts a node identifier, credential,
or tool catalogue from job text. The live plugin registry and Hire policy
decide which registered specialist may be instantiated.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from services.graph_build import (
    CONTEXT_TYPE, Edge, GraphAdditions, Labels, NewNode, NodeIds, add_to_graph, context_data, context_edge,
    graph_node, label_key, main_edge, ref, skill_edge, tool_edge,
)

RECIPE_VERSION = 2
TEAM_LEAD_TYPE = "ai_employee"


@dataclass(frozen=True)
class Responsibility:
    key: str
    node_type: str
    label: str
    description: str
    words: tuple[str, ...]


RESPONSIBILITIES = (
    Responsibility("research", "web_agent", "Research", "Researches information", ("research", "search", "news", "brief", "web", "browser", "website", "competitor", "investigat")),
    Responsibility("calendar", "productivity_agent", "Appointments", "Checks your calendar and appointments", ("calendar", "appointment", "meeting", "book", "schedule")),
    Responsibility("coding", "coding_agent", "Development", "Builds and checks software", ("code", "coding", "software", "develop", "program", "github")),
    Responsibility("social", "social_agent", "Content", "Prepares content for your channels", ("social", "post", "content", "marketing", "instagram", "linkedin", "twitter")),
    Responsibility("operations", "tool_agent", "App tasks", "Handles routine work in your apps", ("email", "inbox", "gmail", "outlook", "message", "customer", "reception", "payment", "spreadsheet", "whatsapp", "telegram")),
)
DEFAULT_RESPONSIBILITY = RESPONSIBILITIES[-1]

# A website can be either a research source or the requested deliverable.
# Match construction of an artifact within one clause of the reviewed brief.
_WEB_ARTIFACT = r"(?:web[ -]?sites?|sites?|web[ -]?apps?|web applications?|dashboards?|landing pages?)"
_BUILD_WEB_ARTIFACT = re.compile(
    rf"\b(?:build|create|implement|redesign|develop|make)\b"
    rf"(?:[ \t]+(?!(?:on|from|for|about|using|with|of|and|to)\b)[\w-]+){{0,8}}[ \t]+{_WEB_ARTIFACT}\b"
    rf"|\b{_WEB_ARTIFACT}[ \t]+(?:builder|developer)\b",
    re.IGNORECASE,
)


def select_responsibilities(job: str, apps: Iterable[str] = ()) -> list[Responsibility]:
    """One to three relevant responsibilities, in stable recipe order."""
    text = " ".join((job, *apps)).lower()
    selected = [role for role in RESPONSIBILITIES if any(word in text for word in role.words)
                or (role.key == "coding" and _BUILD_WEB_ARTIFACT.search(job))]
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
    return team_preview("\n".join(text), app_names(raw_reply))


def _repoint(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: _repoint(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_repoint(item, old, new) for item in value]
    return new if value == old else value


def _connect_lead_and_talk_tools(built: Any, workflow_id: str, lead: str, talk: str) -> None:
    from services.employee_team_connection import employee_skill_parameters, task_manager_additions
    from services.employees.builder import BuildError

    by_id = {node["id"]: node for node in built.nodes}
    talk_position = by_id[talk]["position"]
    talk_x, talk_y = talk_position["x"], talk_position["y"]
    lead_manager = task_manager_additions(lead, (360, -240))
    talk_manager = task_manager_additions(talk, (talk_x - 220, talk_y + 180),
                                          ref="talk_task_manager", label="Talk Task Manager")
    nodes = [*lead_manager.nodes, *talk_manager.nodes]
    edges = [*lead_manager.edges, *talk_manager.edges]
    # A base app/schedule hire shares its library holder between both roles.
    # Separate the lead first so Talk keeps the original library node and ID.
    for role, owner, label, position in (
        ("lead", lead, "Lead instructions", (120, -240)),
        ("talk", talk, "Talk instructions", (talk_x + 220, talk_y + 180)),
    ):
        key = f"{role}_skills"
        links = [edge for edge in built.edges if edge["target"] == owner and edge["targetHandle"] == "input-skill"
                 and by_id[edge["source"]]["type"] == "masterSkill"]
        if len(links) > 1:
            raise BuildError("invalid_skill_ownership", f"Choose one Skills node for the {role} before creating this team")
        parameters = {}
        private_holder = None
        if links:
            holder = links[0]["source"]
            shared = any(edge["source"] == holder and edge["target"] != owner for edge in built.edges)
            if not shared or role == "talk":
                parameters = deepcopy(built.parameters.get(holder) or {})
            if shared:
                built.edges.remove(links[0])
            else:
                private_holder = holder
        config = parameters.get("skills_config") or {}
        if not (config.keys() - {"skill"}):
            defaults = employee_skill_parameters(role)
            parameters = {**defaults, **parameters, "skills_config": {**defaults["skills_config"], **config}}
        if private_holder:
            built.parameters[private_holder] = parameters
            built.node_roles[key] = private_holder
        else:
            nodes.append(NewNode(key, "masterSkill", label, parameters, position))
            edges.append(skill_edge(key, owner))
    placed = add_to_graph(workflow_id, {"nodes": built.nodes, "edges": built.edges}, GraphAdditions(nodes=nodes, edges=edges))
    built.nodes, built.edges = placed.graph["nodes"], placed.graph["edges"]
    built.parameters.update(placed.parameters)
    built.node_roles.update(placed.node_ids)


def build_team(built: Any, inputs: Any) -> Any:
    """Compose configured specialists with isolated Context and a Talk contact.

    Delivery nodes and immutable recipient references are retained. The
    employee job boundary decides when reviewed output is eligible to reach
    those nodes; specialists are never wired to delivery nodes.
    """
    from services.employees.builder import BuildError
    from services.employees.policy import SKILL_TOOL_ENTRY, SKILL_TOOL_NAME
    from services.employees.prompt import TALK_TEAM_CHANGES, PromptInputs, build_system_message, neutralize_templates
    from services.node_registry import get_node_class

    built = deepcopy(built)
    if not inputs.allowed(TEAM_LEAD_TYPE):
        raise BuildError("not_allowed", "Team-based employees are not available")
    if get_node_class("taskManager") is None or not inputs.allowed("taskManager"):
        raise BuildError("not_allowed", "The Task Manager required for a team-based employee is not available")
    if get_node_class("masterSkill") is None or not inputs.allowed("masterSkill"):
        raise BuildError("not_allowed", "Team instructions are not available")
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
        lead_label = labels.take(f"{inputs.request.name} team lead")
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
    from services.employee_team_connection import TALK_CONNECTION_VERSION, employee_team_edge
    built.edges.append(employee_team_edge(lead, talk).to_dict())
    built.parameters[talk]["system_message"] += (
        f"\nYou are the owner's conversational contact. {TALK_TEAM_CHANGES}\n"
        "For work the current team can perform, use submit_job(instructions) to give one mission to your team, "
        "acknowledge promptly, and explain that the reviewed result will arrive here later. "
        "An assignment receipt is progress, never the final result. Never impersonate a specialist or bypass approval. "
        "Use get_job_status before claiming work has started, reporting progress, or submitting the same request again. "
        "A queued job has not started, and an acknowledgement is not completed work. "
        "Specialist tools belong to their specialist; ask the team to perform that work rather than attaching their tools to yourself."
    )
    lead_instructions = original_parameters["system_message"]
    if manual:
        lead_instructions = build_system_message(PromptInputs(
            request=inputs.request, owner=inputs.owner, delivery="review",
            unsupported_apps=inputs.unsupported_apps, has_memory=inputs.memory, has_canvas=True,
        ))
    built.parameters[lead]["system_message"] = (
        lead_instructions + "\nYou coordinate and review this employee's specialist team. "
        "Use your connected Task Manager's spawn_<teammate>(instructions, after?) to create independent agents and reviewed tasks. Explain success "
        "in the instructions and declare dependencies explicitly. Review submitted results with get_task or inspect_task; "
        "accept_task only when the instructions are satisfied, otherwise request_changes with feedback or reassign. "
        "Use assign_task to reuse an agent and retry_task to retry existing work. Finish the team only after all required work is accepted. Then provide one reviewed result. "
        "Do not present an assignment acknowledgement as completed work. Do not independently publish through apps."
    )
    selected = select_responsibilities("\n".join((inputs.request.job, inputs.request.role, *(step.title for step in inputs.request.steps))), (app.name for app in inputs.apps))
    members: list[dict[str, Any]] = []
    for index, responsibility in enumerate(selected, 1):
        node_type = responsibility.node_type
        if node_type == "web_agent" and (any(tool.type == "browser" for app in inputs.apps for tool in app.tools)
                                         or any(word in inputs.request.job.lower() for word in ("browser", "website"))):
            node_type = "browser_agent"
        if get_node_class(node_type) is None or not inputs.allowed(node_type):
            if node_type == "coding_agent":
                raise BuildError("not_allowed", "The coding specialist required to build this software is not available")
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
        if node_type == "browser_agent":
            from services.browser_agent_recipe import BROWSER_AGENT_ROLE
            built.parameters[member]["system_message"] = BROWSER_AGENT_ROLE + "\n" + instruction
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
    built.edges = [edge for edge in built.edges if not (edge.get("source") in original_tools and edge.get("target") in {lead, talk} and edge.get("targetHandle") == "input-tools")]
    for tool in dict.fromkeys(original_tools):
        node_type = nodes[tool]["type"]
        text = (app_types.get(node_type, "research") + " " + node_type).lower()
        recipient = next((member for member, role in zip(members, selected) if any(word in text for word in role.words)), None)
        recipient = recipient or next((member for member in members if member["role"] == "operations"), members[0])
        built.edges.append(tool_edge(tool, recipient["node_id"]).to_dict())
        recipient["tools"].append(tool)
    # Complete Browser specialists through the same recipe as canvas/Builder,
    # reusing the private Browser tool already assigned from the saved app.
    from services.browser_agent_recipe import browser_agent_additions
    for member in members:
        if member["node_type"] != "browser_agent":
            continue
        browser = next((tool for tool in member["tools"] if nodes[tool]["type"] == "browser"), None)
        position = next(node["position"] for node in built.nodes if node["id"] == member["node_id"])
        additions = browser_agent_additions(agent_ref=member["node_id"], browser_node_id=browser,
            create_agent=False, create_context=False, create_skills=False,
            position=(position["x"], position["y"]))
        if any(not inputs.allowed(node.type) for node in additions.nodes):
            raise BuildError("not_allowed", "Browser tools are not available")
        placed = add_to_graph(inputs.workflow_id, {"nodes": built.nodes, "edges": built.edges}, additions)
        built.nodes, built.edges = placed.graph["nodes"], placed.graph["edges"]
        built.parameters.update(placed.parameters)
        member["tools"].extend(node["id"] for node in placed.nodes)
        holder = roles[next(key for key, value in roles.items() if key.startswith("specialist_") and value == member["node_id"]) + "_skills"]
        default_skills = browser_agent_additions(create_agent=False, create_context=False, fresh_skills=True).nodes
        config = next(node.params["skills_config"] for node in default_skills if node.type == "masterSkill")
        built.parameters[holder]["skills_config"].update(config)
    # Fresh Coding specialists receive the same Pi tools as canvas/Builder.
    # Reuse any explicitly assigned tools rather than duplicating them.
    from services.coding_agent_recipe import coding_agent_additions
    for member in members:
        if member["node_type"] != "coding_agent":
            continue
        position = next(node["position"] for node in built.nodes if node["id"] == member["node_id"])
        connected_types = tuple(node["type"] for node in built.nodes if node["id"] in member["tools"])
        additions = coding_agent_additions(agent_ref=member["node_id"],
            create_agent=False, create_context=False, exclude_tool_types=connected_types,
            position=(position["x"], position["y"]))
        if any(not inputs.allowed(node.type) for node in additions.nodes):
            raise BuildError("not_allowed", "Pi coding tools are not available")
        placed = add_to_graph(inputs.workflow_id, {"nodes": built.nodes, "edges": built.edges}, additions)
        built.nodes, built.edges = placed.graph["nodes"], placed.graph["edges"]
        built.parameters.update(placed.parameters)
        member["tools"].extend(node["id"] for node in placed.nodes)
    # Keep one memory per member. A legacy Talk line may share the worker's
    # memory: remove that binding and give each agent its own instance.
    memories = {node["id"] for node in built.nodes if node["type"] == "simpleMemory"}
    owned_memories = {agent: next((edge["source"] for edge in built.edges
                                 if edge.get("target") == agent and edge.get("targetHandle") == "input-tools"
                                 and edge.get("source") in memories), None)
                      for agent in [lead, talk]}
    built.edges = [edge for edge in built.edges if not (edge.get("source") in memories and edge.get("targetHandle") == "input-tools")]
    agents = [lead, talk, *(member["node_id"] for member in members)]
    used_memories = set()
    if inputs.memory:
        for index, agent in enumerate(agents):
            original_memory = owned_memories.get(agent)
            memory = original_memory if original_memory not in used_memories else None
            if memory is None:
                if not inputs.allowed("simpleMemory"):
                    raise BuildError("not_allowed", "Employee memory is not available")
                memory = ids.next("simpleMemory")
                built.nodes.append(graph_node(memory, "simpleMemory", labels.take("Memory"), (170 * index, -60)))
                if original_memory in built.parameters:
                    built.parameters[memory] = deepcopy(built.parameters[original_memory])
            used_memories.add(memory)
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
    delivery_ids = [roles[key] for key in ("gate", "reply", "notify", "report_post") if key in roles]
    final_reply = ids.next("chatReply")
    built.nodes.append(graph_node(final_reply, "chatReply", labels.take("Reviewed result"), (1080, -420)))
    built.parameters[final_reply] = {"message": ref(label_key(lead_label), "response")}
    roles["job_reply"] = final_reply
    if manual:
        delivery_ids.append(final_reply)
        # The contact retains its existing chatReply acknowledgement.
        coordination = {"writeTodos", "canvas"}
        for edge in list(built.edges):
            if edge.get("target") == talk and edge.get("targetHandle") == "input-tools" and nodes.get(edge["source"], {}).get("type") in coordination:
                edge["target"] = lead
    else:
        built.edges = [edge for edge in built.edges if not (
            edge.get("targetHandle") == "input-main" and (
                edge.get("target") in delivery_ids and edge.get("source") in {lead, roles["trigger"]}
                or edge.get("source") == roles["trigger"] and edge.get("target") == lead
            )
        )]
        built.edges.append(main_edge(roles["trigger"], lead).to_dict())
    built.edges = [edge for edge in built.edges if not (
        edge.get("target") == talk and edge.get("targetHandle") == "input-tools"
        and nodes.get(edge["source"], {}).get("type") in {"writeTodos", "canvas"}
    )]
    # Date-sensitive work preserves the owner's timezone, with one clock
    # instance per agent rather than a shared callable node.
    clock = next((node["id"] for node in built.nodes if node["type"] == "currentTimeTool"), None)
    if clock:
        for index, agent in enumerate(agents):
            if any(edge.get("target") == agent and edge.get("targetHandle") == "input-tools"
                   and nodes.get(edge.get("source"), {}).get("type") == "currentTimeTool" for edge in built.edges):
                continue
            private_clock = ids.next("currentTimeTool")
            built.nodes.append(graph_node(private_clock, "currentTimeTool", labels.take("Clock"), (170 * index, 120)))
            built.parameters[private_clock] = deepcopy(built.parameters.get(clock, {}))
            built.edges.append(tool_edge(private_clock, agent).to_dict())
    for member in members:
        member["tools"] = list(dict.fromkeys(edge["source"] for edge in built.edges
                                            if edge.get("target") == member["node_id"]
                                            and edge.get("targetHandle") == "input-tools"))
    # Replaced canonical lead IDs must also be reflected in edge identities.
    for edge in built.edges:
        edge["id"] = f'e-{edge["source"]}-{edge["sourceHandle"]}-{edge["target"]}-{edge["targetHandle"]}'
    _connect_lead_and_talk_tools(built, inputs.workflow_id, lead, talk)
    for node in built.nodes:
        if node["id"] == lead:
            node.setdefault("data", {})["employee_recipe_version"] = RECIPE_VERSION
    built.team_plan = {"version": RECIPE_VERSION, "recipe": "ai_employee", "lead_node_id": lead, "talk_node_id": talk,
                       "task_manager_node_id": roles["task_manager"], "talk_task_manager_node_id": roles["talk_task_manager"],
                       "lead_skill_node_id": roles["lead_skills"], "talk_skill_node_id": roles["talk_skills"],
                       "talk_connection_version": TALK_CONNECTION_VERSION, "members": members,
                       "model": model, "requires": ["temporal", "agent_workflow"], "delivery_node_ids": delivery_ids,
                       "talk_delivery_node_ids": [final_reply]}
    next(node for node in built.nodes if node["id"] == lead)["data"]["employee_team_plan"] = deepcopy(built.team_plan)
    return built
