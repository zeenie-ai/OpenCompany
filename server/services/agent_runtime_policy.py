"""Creation-only agent policy, independent of workflow/harness versions.

Never infer eligibility from a parent, a model argument, or a generation.
Missing metadata belongs to an existing agent and always means legacy.
One v1 rule applies to every agent: Process Manager resolves a relative
``cwd`` against the workflow workspace, since the legacy rule only refused it.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

RUNTIME_BEHAVIOR_FIELD = "runtime_behavior_version"


class AgentRuntimeParams(BaseModel):
    runtime_behavior_version: Literal[0, 1] = Field(
        default=0, json_schema_extra={"hidden": True},
    )


def runtime_behavior_version(parameters: Mapping[str, Any] | None) -> int:
    """Resolve only an authoritative saved/captured parameter mapping."""
    value = parameters.get(RUNTIME_BEHAVIOR_FIELD) if isinstance(parameters, Mapping) else None
    return 1 if type(value) is int and value == 1 else 0


def new_agent_runtime_enabled() -> bool:
    """Release switch. Turning it off never downgrades admitted agents."""
    from core.env_defaults import env_value

    return env_value("NEW_AGENT_RUNTIME_V1_ENABLED").strip().lower() in {"1", "true", "yes", "on"}


def new_agent_parameters(parameters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Only fresh, authorized creation paths may call this helper."""
    result = dict(parameters or {})
    result.pop(RUNTIME_BEHAVIOR_FIELD, None)
    if new_agent_runtime_enabled():
        result[RUNTIME_BEHAVIOR_FIELD] = 1
    return result


def preserve_runtime_version(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, Any]:
    """An ordinary edit preserves identity, including an omitted legacy flag."""
    result = dict(proposed)
    if RUNTIME_BEHAVIOR_FIELD in current:
        result[RUNTIME_BEHAVIOR_FIELD] = current[RUNTIME_BEHAVIOR_FIELD]
    else:
        result.pop(RUNTIME_BEHAVIOR_FIELD, None)
    return result


def runtime_context(parameters: Mapping[str, Any]) -> dict[str, int]:
    """Keep legacy payload and digest inputs byte-for-byte compatible."""
    return {RUNTIME_BEHAVIOR_FIELD: 1} if runtime_behavior_version(parameters) else {}


def stamp_new_graph_parameters(graph, parameters, new_node_ids):
    """Explicit fresh-creation finalizer, never called by import/save/copy."""
    from services.node_registry import get_node_class
    identifiers = set(new_node_ids)
    for node in graph.get("nodes", []):
        identifier = node.get("id")
        if identifier not in identifiers:
            continue
        cls = get_node_class(node.get("type", ""))
        if cls is None or not issubclass(getattr(cls, "Params", BaseModel), AgentRuntimeParams):
            continue
        current = parameters.get(identifier) or {}
        if getattr(cls, "component_kind", "") == "agent":
            parameters[identifier] = new_agent_parameters(current)
    for node in graph.get("nodes", []):
        identifier = node.get("id")
        if identifier not in identifiers or node.get("type") != "masterSkill":
            continue
        targets = [edge.get("target") for edge in graph.get("edges", []) if edge.get("source") == identifier
                   and (edge.get("targetHandle") or edge.get("target_handle")) == "input-skill"]
        if any(runtime_behavior_version(parameters.get(target)) for target in targets):
            from services.skill_defaults import new_skill_parameters
            parameters[identifier] = new_skill_parameters(parameters.get(identifier) or {})


def prepare_new_agent_additions(additions, existing_parameters=None):
    """Stamp recipes only where the caller explicitly creates new identities."""
    from dataclasses import replace
    parameters = {**(existing_parameters or {}), **{node.ref: dict(node.params) for node in additions.nodes}}
    graph = {"nodes": [{"id": node.ref, "type": node.type} for node in additions.nodes],
             "edges": [edge.to_dict() for edge in additions.edges]}
    stamp_new_graph_parameters(graph, parameters, {node.ref for node in additions.nodes})
    return replace(additions, nodes=[replace(node, params=parameters[node.ref]) for node in additions.nodes])
