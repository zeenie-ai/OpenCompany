"""Edge-walking utilities for agent plugins.

Pure functions extracted from ``services/handlers/ai.py`` so every
agent plugin can call them without depending on the legacy handler
module. Used by :class:`AIAgentNode`, :class:`ChatAgentNode`,
:class:`SpecializedAgentBase`, :class:`RLMAgentNode`,
:class:`ClaudeCodeAgentNode`.

Three helpers:

- :func:`collect_agent_connections` — walks ``input-memory``,
  ``input-skill``, ``input-tools``, ``input-main`` / ``input-chat``,
  ``input-task`` edges into a single tuple. Knows about the
  ``masterSkill`` expansion + direct Android service tools +
  child-agent tool discovery.
- :func:`collect_teammate_connections` — walks ``input-teammates``
  edges (orchestrator / ai_employee team-lead pattern).
- :func:`format_task_context` — renders ``taskTrigger`` payload as a
  prompt prepend block.

Wave 11.I, X3: the Master-Skill expansion logic moved out of this
module (it used to ``from services.skill_loader import get_skill_loader``
inline). The :mod:`nodes.skill` package registers an expander callback
via :func:`register_master_skill_expander` from its ``__init__.py``;
edge_walker calls the registered callback instead of importing
``skill_loader`` directly.

These functions have **no service dependencies** beyond the
``Database`` instance and the registered expander callback. They
mutate nothing. Wave 11.D.6 inlines the old ``handlers/ai.py:_collect_*``
shims to call here directly.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

from constants import AI_AGENT_TYPES
from core.logging import get_logger

if TYPE_CHECKING:
    from core.database import Database

logger = get_logger(__name__)

TEAM_LEAD_TYPES = frozenset({"orchestrator_agent", "ai_employee"})
TEAMMATE_HANDLE = "input-teammates"


def edge_target_handle(edge: Dict[str, Any]) -> Optional[str]:
    """Return the canonical ReactFlow handle, accepting legacy imports."""
    return edge.get("targetHandle") or edge.get("target_handle")


def _delegate_slug(value: Any) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip()).strip("_").lower()
    return slug or "ai_agent"


def build_teammate_descriptors(node_id: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build deterministic, LLM-visible identities for a lead's teammates.

    Specialized agents retain their stable type-derived name. Custom
    ``aiAgent`` nodes are label-addressable and receive a node-id suffix when
    their label is empty or collides on the same lead surface.
    """
    nodes = context.get("nodes", []) or []
    edges = context.get("edges", []) or []
    node_by_id = {n.get("id"): n for n in nodes if n.get("id")}
    candidates: List[Dict[str, Any]] = []
    for edge in edges:
        if edge.get("target") != node_id or edge_target_handle(edge) != TEAMMATE_HANDLE:
            continue
        source = node_by_id.get(edge.get("source"))
        if not source or source.get("type") not in AI_AGENT_TYPES:
            continue
        node_type = source.get("type", "")
        raw_label = (source.get("data") or {}).get("label")
        label = str(raw_label or node_type)
        candidates.append({
            "node_id": source["id"],
            "node_type": node_type,
            "label": label,
            "_raw_label": raw_label,
            "_slug": _delegate_slug(raw_label),
        })

    custom_counts = Counter(c["_slug"] for c in candidates if c["node_type"] == "aiAgent" and c["_raw_label"])
    for item in candidates:
        if item["node_type"] == "aiAgent":
            needs_suffix = not item["_raw_label"] or custom_counts[item["_slug"]] > 1
            stable_id = hashlib.sha1(str(item["node_id"]).encode("utf-8")).hexdigest()[:8]
            suffix = f"_{stable_id}" if needs_suffix else ""
            item["delegate_tool_name"] = f"delegate_to_{item['_slug']}{suffix}"
        else:
            item["delegate_tool_name"] = f"delegate_to_{item['node_type']}"
        item.pop("_raw_label", None)
        item.pop("_slug", None)
    return candidates


# ---- Master-Skill expander callback registry ----------------------------

MasterSkillExpander = Callable[
    [str, Dict[str, Any]],
    Awaitable[List[Dict[str, Any]]],
]
"""Async callable that expands a Master Skill node's ``skills_config``
into a list of per-skill entries (the same shape ``_append_skill_entries``
produces for non-master skills). Signature:
``(source_node_id, skills_config) -> list[entry]``."""

_MASTER_SKILL_EXPANDER: Optional[MasterSkillExpander] = None


def register_master_skill_expander(fn: MasterSkillExpander) -> None:
    """Publish the Master-Skill expander callback.

    Idempotent on equality. The :mod:`nodes.skill` plugin package
    registers its expander from ``__init__.py`` on package import.
    Edge-walking code calls :func:`get_master_skill_expander` and runs
    whatever's registered; the framework has no skill_loader coupling.
    """
    global _MASTER_SKILL_EXPANDER
    if _MASTER_SKILL_EXPANDER is not None and _MASTER_SKILL_EXPANDER != fn:
        raise ValueError("register_master_skill_expander: callback already registered " "by a different callable; refusing to overwrite")
    _MASTER_SKILL_EXPANDER = fn


def get_master_skill_expander() -> Optional[MasterSkillExpander]:
    """Return the registered expander callback, or None when no skill
    plugin has wired it (in which case Master-Skill nodes silently
    expand to no entries -- the agent still runs without skills)."""
    return _MASTER_SKILL_EXPANDER


async def collect_agent_connections(
    node_id: str,
    context: Dict[str, Any],
    database: "Database",
    log_prefix: str = "[Agent]",
) -> Tuple[
    Optional[Dict[str, Any]],  # memory_data
    List[Dict[str, Any]],  # skill_data
    List[Dict[str, Any]],  # tool_data
    Optional[Dict[str, Any]],  # input_data
    Optional[Dict[str, Any]],  # task_data
]:
    """Walk edges targeting ``node_id`` and collect everything an agent
    needs from its connected nodes.

    Returns ``(memory, skills, tools, input, task)``. See module
    docstring for behaviour notes.
    """
    nodes = context.get("nodes")
    edges = context.get("edges")
    workflow_id = context.get("workflow_id")

    memory_data: Optional[Dict[str, Any]] = None
    skill_data: List[Dict[str, Any]] = []
    tool_data: List[Dict[str, Any]] = []
    input_data: Optional[Dict[str, Any]] = None
    task_data: Optional[Dict[str, Any]] = None

    logger.info(
        f"{log_prefix} Processing node {node_id}, "
        f"edges={len(edges) if edges else 0}, "
        f"nodes={len(nodes) if nodes else 0}, "
        f"workflow_id={workflow_id}"
    )

    if not edges or not nodes:
        return memory_data, skill_data, tool_data, input_data, task_data

    incoming_edges = [e for e in edges if e.get("target") == node_id]
    logger.info(f"{log_prefix} Incoming edges to {node_id}: {len(incoming_edges)}")
    if not incoming_edges:
        edge_targets = set(e.get("target") for e in edges)
        logger.debug(f"{log_prefix} All edge targets in graph: {edge_targets}")
    for e in incoming_edges:
        logger.debug(f"{log_prefix} Edge: source={e.get('source')}, " f"targetHandle={e.get('targetHandle')}")

    tool_incoming = [e for e in incoming_edges if e.get("targetHandle") == "input-tools"]
    logger.info(f"{log_prefix} Tool edges (input-tools handle): {len(tool_incoming)}")

    for edge in edges:
        if edge.get("target") != node_id:
            continue

        target_handle = edge.get("targetHandle")
        source_node_id = edge.get("source")
        source_node = next((n for n in nodes if n.get("id") == source_node_id), None)
        if not source_node:
            continue

        if target_handle == "input-memory":
            if source_node.get("type") == "simpleMemory":
                memory_data = await _build_memory_entry(
                    node_id,
                    source_node_id,
                    database,
                    log_prefix,
                )

        elif target_handle == "input-skill":
            await _append_skill_entries(
                source_node,
                source_node_id,
                database,
                skill_data,
                log_prefix,
            )

        elif target_handle == "input-tools":
            await _append_tool_entry(
                source_node,
                source_node_id,
                edges,
                nodes,
                database,
                tool_data,
                log_prefix,
            )

        elif target_handle in ("input-main", "input-chat") or target_handle is None:
            source_output = context.get("outputs", {}).get(source_node_id)
            if source_output:
                input_data = source_output
                logger.debug(
                    f"{log_prefix} Input from {source_node.get('type')}: "
                    f"{list(source_output.keys()) if isinstance(source_output, dict) else type(source_output)}"
                )

        elif target_handle == "input-task":
            task_data = await _resolve_task_payload(
                source_node_id,
                source_node,
                context,
                log_prefix,
            )

    # A team lead must always be able to manage the durable work it delegates.
    # This is an intrinsic, non-removable team-lead capability.  It is present
    # before the first teammate is connected so the lead and its human panel
    # have one stable control surface for the whole execution lifecycle.
    current_node = next(
        (node for node in (context.get("nodes") or []) if node.get("id") == node_id),
        None,
    )
    if (current_node or {}).get("type") in TEAM_LEAD_TYPES and not any(
        entry.get("node_type") == "taskManager" for entry in tool_data
    ):
        tool_data.append(
            {
                "node_id": f"builtin_task_manager_{node_id}",
                "node_type": "taskManager",
                "parameters": {},
                "label": "Task Manager",
                "builtin": True,
            }
        )
        logger.info("%s Auto-bound durable Task Manager for team lead %s", log_prefix, node_id)

    logger.info(
        f"{log_prefix} Collected: {len(skill_data)} skills, {len(tool_data)} tools, "
        f"memory={'yes' if memory_data else 'no'}, "
        f"input={'yes' if input_data else 'no'}, "
        f"task={'yes' if task_data else 'no'}"
    )
    for sd in skill_data:
        logger.debug(f"{log_prefix} Skill: type={sd.get('node_type')}, label={sd.get('label')}")
    for td in tool_data:
        logger.info(f"{log_prefix} Tool: type={td.get('node_type')}, node_id={td.get('node_id')}")

    return memory_data, skill_data, tool_data, input_data, task_data


async def _build_memory_entry(
    agent_node_id: str,
    memory_node_id: str,
    database: "Database",
    log_prefix: str,
) -> Dict[str, Any]:
    memory_params = await database.get_node_parameters(memory_node_id) or {}
    # Plugin Pydantic models emit JSON Schema property names in
    # snake_case (post-Wave-11; see simple_memory.py "now that aliases
    # are gone"). Reads canonicalize on snake_case -- the saved-params
    # dict is the schema's domain.
    configured_session = memory_params.get("session_id", "")
    if configured_session and configured_session != "default":
        session_id = configured_session
    else:
        session_id = agent_node_id
    entry = {
        "node_id": memory_node_id,
        "session_id": session_id,
        "window_size": int(memory_params.get("window_size", 10)),
        "memory_content": memory_params.get(
            "memory_content",
            "# Conversation History\n\n*No messages yet.*\n",
        ),
        "long_term_enabled": memory_params.get("long_term_enabled", False),
        "retrieval_count": int(memory_params.get("retrieval_count", 3)),
        # Claude Code CLI session continuity: the UUID claude returned on
        # the previous successful run. claude_code_agent passes this as
        # `--resume <UUID>` so claude finds its own JSONL transcript at
        # `<CLAUDE_CONFIG_DIR>/projects/<cwd-encoded>/<UUID>.jsonl`.
        "last_session_id": memory_params.get("last_session_id"),
    }
    logger.info(
        "%s Connected memory node: node=%s session=%s (auto=%s) " "content_length=%d last_session_id=%s",
        log_prefix,
        memory_node_id,
        session_id,
        not configured_session or configured_session == "default",
        len(entry["memory_content"]),
        entry["last_session_id"],
    )
    return entry


async def _append_skill_entries(
    source_node: Dict[str, Any],
    source_node_id: str,
    database: "Database",
    skill_data: List[Dict[str, Any]],
    log_prefix: str,
) -> None:
    skill_type = source_node.get("type")
    skill_params = await database.get_node_parameters(source_node_id) or {}

    if skill_type == "masterSkill":
        expander = get_master_skill_expander()
        if expander is None:
            logger.warning(
                f"{log_prefix} Master Skill node found but no expander "
                "registered. Skipping expansion -- ensure nodes.skill "
                "is on the import path."
            )
            return

        skills_config = skill_params.get("skills_config", {})
        logger.debug(f"{log_prefix} Master Skill found with {len(skills_config)} configured skills")
        entries = await expander(source_node_id, skills_config)
        skill_data.extend(entries)
        for entry in entries:
            logger.debug(f"{log_prefix} Master Skill enabled: {entry['skill_name']}")
    else:
        skill_data.append(
            {
                "node_id": source_node_id,
                "node_type": skill_type,
                "skill_name": skill_params.get("skill_name", skill_type),
                "parameters": skill_params,
                "label": source_node.get("data", {}).get("label", skill_type),
            }
        )
        logger.debug(f"{log_prefix} Connected skill: {skill_type}")


async def _append_tool_entry(
    source_node: Dict[str, Any],
    source_node_id: str,
    edges: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    database: "Database",
    tool_data: List[Dict[str, Any]],
    log_prefix: str,
) -> None:
    tool_type = source_node.get("type")
    logger.info(f"{log_prefix} Found tool connected via input-tools: type={tool_type}, node_id={source_node_id}")
    tool_params = await database.get_node_parameters(source_node_id) or {}

    tool_entry: Dict[str, Any] = {
        "node_id": source_node_id,
        "node_type": tool_type,
        "parameters": tool_params,
        "label": source_node.get("data", {}).get("label", tool_type),
    }

    if tool_type in AI_AGENT_TYPES:
        child_tools: List[Dict[str, Any]] = []
        child_incoming_edges = [e for e in edges if e.get("target") == source_node_id]
        child_tool_edges = [e for e in child_incoming_edges if e.get("targetHandle") == "input-tools"]
        logger.debug(
            f"{log_prefix} Child agent {source_node_id}: "
            f"{len(child_incoming_edges)} incoming edges, "
            f"{len(child_tool_edges)} input-tools edges"
        )
        if child_incoming_edges:
            handles = [e.get("targetHandle", "None") for e in child_incoming_edges]
            logger.debug(f"{log_prefix} Child agent {source_node_id} incoming handles: {handles}")
        for child_edge in edges:
            if child_edge.get("target") != source_node_id:
                continue
            if child_edge.get("targetHandle") != "input-tools":
                continue
            child_tool_id = child_edge.get("source")
            child_tool_node = next((n for n in nodes if n.get("id") == child_tool_id), None)
            logger.debug(
                f"{log_prefix} Child agent {source_node_id}: " f"tool edge from {child_tool_id}, node found: {child_tool_node is not None}"
            )
            if child_tool_node:
                child_tool_type = child_tool_node.get("type", "")
                child_tool_label = child_tool_node.get("data", {}).get("label", child_tool_type)
                child_tools.append({"node_type": child_tool_type, "label": child_tool_label})
        if child_tools:
            tool_entry["child_tools"] = child_tools
            logger.debug(f"{log_prefix} Child agent {source_node_id} has tools: " f"{[t['label'] for t in child_tools]}")

    tool_data.append(tool_entry)
    logger.debug(f"{log_prefix} Connected tool: {tool_type}")


async def _resolve_task_payload(
    source_node_id: str,
    source_node: Dict[str, Any],
    context: Dict[str, Any],
    log_prefix: str,
) -> Optional[Dict[str, Any]]:
    logger.info(f"{log_prefix} Found input-task edge from {source_node_id} (type={source_node.get('type')})")

    source_output = context.get("outputs", {}).get(source_node_id)
    logger.info(f"{log_prefix} Context outputs check for {source_node_id}: {source_output is not None}")

    if not source_output:
        get_output_fn = context.get("get_output_fn")
        session_id = context.get("session_id", "default")
        if get_output_fn:
            try:
                source_output = await get_output_fn(session_id, source_node_id, "output_0")
                logger.info(f"{log_prefix} DB lookup for {source_node_id}: {source_output is not None}")
            except Exception as e:
                logger.warning(f"{log_prefix} Failed to get output from DB: {e}")
        else:
            logger.warning(f"{log_prefix} No get_output_fn in context, cannot retrieve task output")

    logger.info(
        f"{log_prefix} Source output for {source_node_id}: {source_output is not None}, "
        f"type={type(source_output).__name__ if source_output else 'None'}"
    )
    if not source_output:
        return None

    task_data = extract_task_event_payload(source_output) or source_output
    if isinstance(task_data, dict):
        logger.info(
            f"{log_prefix} Task completion data: task_id={task_data.get('task_id')}, "
            f"status={task_data.get('status')}, agent_name={task_data.get('agent_name')}"
        )
    return task_data


def extract_task_event_payload(value: Any) -> Optional[Dict[str, Any]]:
    """Unwrap task data from executor and CloudEvent envelopes."""
    current = value
    seen: set[int] = set()
    while isinstance(current, dict) and id(current) not in seen:
        seen.add(id(current))
        if current.get("task_id") and current.get("status"):
            return current
        nested = current.get("data")
        if not isinstance(nested, dict):
            nested = current.get("result")
        if not isinstance(nested, dict):
            return None
        current = nested
    return None


async def collect_teammate_connections(
    node_id: str,
    context: Dict[str, Any],
    database: "Database",
) -> List[Dict[str, Any]]:
    """Walk ``input-teammates`` edges and return connected agents.

    Used by ``orchestrator_agent`` / ``ai_employee``.
    """
    teammates: List[Dict[str, Any]] = []
    descriptors = build_teammate_descriptors(node_id, context)
    nodes = context.get("nodes", []) or []
    edges = context.get("edges", []) or []
    for descriptor in descriptors:
        source_id = descriptor["node_id"]
        params = await database.get_node_parameters(source_id) or {}
        child_tools: List[Dict[str, Any]] = []
        for edge in edges:
            if edge.get("target") != source_id or edge_target_handle(edge) != "input-tools":
                continue
            child = next((n for n in nodes if n.get("id") == edge.get("source")), None)
            if child:
                child_tools.append({
                    "node_id": child.get("id"),
                    "node_type": child.get("type"),
                    "label": (child.get("data") or {}).get("label", child.get("type")),
                })
        teammates.append({**descriptor, "parameters": params, "child_tools": child_tools})
        logger.debug(f"[Teams] Found teammate: {descriptor['node_type']} ({source_id})")

    return teammates


def format_task_context(task_data: Dict[str, Any]) -> str:
    """Render a ``taskTrigger`` payload as a prompt-prepend block.

    Tells the lead to review the delegated result and choose the next
    lifecycle action. Three branches: completed / error / other.
    """
    status = task_data.get("status", "unknown")
    agent_name = task_data.get("agent_name", "Unknown Agent")
    task_id = task_data.get("task_id", "")

    if status == "completed":
        result = task_data.get("result", "No result provided")
        return (
            "A delegated task has completed:\n"
            f"- Agent: {agent_name}\n"
            f"- Task ID: {task_id}\n"
            "- Status: Completed Successfully\n"
            f"- Result: {result}\n\n"
            "This taskTrigger run is the lead's completion review. Use Task Manager "
            "list_tasks and get_task to read the durable task, verify the submitted "
            "result against its mission and acceptance criteria, then produce the "
            "requested user-facing output. Do not create a duplicate assignment."
        )

    if status == "error":
        error = task_data.get("error", "Unknown error")
        return (
            "A delegated task has failed:\n"
            f"- Agent: {agent_name}\n"
            f"- Task ID: {task_id}\n"
            "- Status: Error\n"
            f"- Error: {error}\n\n"
            "This taskTrigger run is the lead's failure review. Use Task Manager "
            "list_tasks and get_task to inspect the durable attempt before reporting, "
            "retrying, or reassigning it. Do not create a duplicate assignment."
        )

    return f"Task update received:\n" f"- Agent: {agent_name}\n" f"- Task ID: {task_id}\n" f"- Status: {status}\n" f"- Data: {task_data}"
