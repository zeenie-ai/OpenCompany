"""Private Browser resource admission; never creates files or starts Chrome."""
from __future__ import annotations

import uuid
from typing import Any, Mapping

from sqlalchemy import select

from models.browser_profiles import BrowserProfileReservation, BrowserProfileRow
from services.plugin import NodeUserError

REMEDIATION = "Create separate Browser tool/profile"


class BrowserResourceError(NodeUserError):
    def as_dict(self):
        return {**super().as_dict(), "code": "BROWSER_PRIVATE_PROFILE_REQUIRED", "remediation": "create_separate_browser"}


def _fail():
    raise BrowserResourceError(f"This Browser binding cannot share or transfer a private profile. {REMEDIATION}.")


def _version(parameters):
    # Strict, saved configuration only: no templating or invocation values.
    value = (parameters or {}).get("runtime_behavior_version")
    return 1 if type(value) is int and value == 1 else 0


def _bindings(graph):
    nodes = {str(n.get("id")): n for n in graph.get("nodes", [])}
    result = {}
    for edge in graph.get("edges", []):
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if nodes.get(source, {}).get("type") == "browser" and (edge.get("targetHandle") or edge.get("target_handle")) == "input-tools":
            result.setdefault(source, set()).add(target)
    return nodes, result


class BrowserAgentRequired(NodeUserError):
    def as_dict(self):
        return {**super().as_dict(), "code": "BROWSER_AGENT_REQUIRED"}


def validate_new_browser_connections(graph: Mapping, previous: Mapping | None = None) -> None:
    """Allow legacy miswiring to be edited or removed without admitting more of it."""
    from services.agent_bindings import BROWSER_AGENT_ONLY, agent_can_bind_tool

    def invalid(graph):
        nodes, bindings = _bindings(graph)
        return {(source, target) for source, targets in bindings.items() for target in targets
                if not agent_can_bind_tool(nodes.get(target, {}).get("type"), "browser")}

    if invalid(graph) - invalid(previous or {}):
        raise BrowserAgentRequired(BROWSER_AGENT_ONLY)


async def allocate_new_browser_resources(session, workflow_id: str, principal: str, graph: dict,
                                         parameters: dict, new_node_ids) -> None:
    """Allocate metadata only for fresh tools owned by exactly one saved v1 agent.

    Must run in the caller's graph transaction, after canonical IDs exist.
    Import/copy/save callers must use validation only, never this allocator.
    """
    nodes, bindings = _bindings(graph)
    for browser_id in set(new_node_ids):
        if nodes.get(browser_id, {}).get("type") != "browser":
            continue
        targets = bindings.get(browser_id, set())
        new_targets = [target for target in targets if _version(parameters.get(target)) == 1]
        if not new_targets:
            continue
        if len(targets) != 1:
            _fail()
        agent_id = new_targets[0]
        config = parameters.setdefault(browser_id, {})
        # Resource identity is never cloned, imported, or supplied by a model.
        if config.get("profile_id"):
            _fail()
        profile_id = "bp_" + uuid.uuid4().hex[:16]
        session.add(BrowserProfileRow(id=profile_id, owner_id=principal,
            name=str((nodes[browser_id].get("data") or {}).get("label") or "Browser")[:60], kind="agent", workflow_id=None))
        session.add(BrowserProfileReservation(profile_id=profile_id, owner_id=principal,
            workflow_id=workflow_id, agent_node_id=agent_id, browser_node_id=browser_id, runtime_version=1))
        config["profile_id"] = profile_id
        nodes[browser_id].setdefault("data", {})["private_browser_owner"] = {"agent_node_id": agent_id, "runtime_version": 1}


async def validate_browser_resource_bindings(session, workflow_id: str, principal: str,
                                             graph: Mapping, parameters: Mapping) -> None:
    """Reject mixed/foreign/unreserved bindings, without changing saved rows."""
    nodes, bindings = _bindings(graph)
    for browser_id, node in nodes.items():
        if node.get("type") != "browser":
            continue
        profile_id = str((parameters.get(browser_id) or {}).get("profile_id") or "")
        targets = bindings.get(browser_id, set())
        v1_targets = {target for target in targets if _version(parameters.get(target)) == 1}
        if not profile_id and not v1_targets:
            if isinstance(node.get("data"), dict):
                node["data"].pop("private_browser_owner", None)
            continue
        reservation = await session.get(BrowserProfileReservation, profile_id) if profile_id else None
        profile = await session.get(BrowserProfileRow, profile_id) if profile_id else None
        if reservation is None:
            if v1_targets or profile is not None and profile.kind == "agent":
                _fail()
            if isinstance(node.get("data"), dict):
                node["data"].pop("private_browser_owner", None)
            continue
        if (reservation.runtime_version != 1 or profile is None or profile.kind != "agent"
                or profile.owner_id != principal or reservation.owner_id != principal
                or reservation.workflow_id != workflow_id or reservation.browser_node_id != browser_id
                or targets and (targets != {reservation.agent_node_id} or v1_targets != targets)):
            _fail()
        if isinstance(node, dict):
            node.setdefault("data", {})["private_browser_owner"] = {
                "agent_node_id": reservation.agent_node_id, "runtime_version": 1}


async def assert_browser_resource_access(database: Any, *, profile_id: str, principal: str,
                                         workflow_id: str, browser_node_id: str, agent_node_id: str | None = None,
                                         runtime_version: int = 0, parameter_snapshot=None):
    """Validate both frozen identity and current resource authorization."""
    if not getattr(database, "engine", None):
        if runtime_version == 1:
            _fail()
        if agent_node_id:
            captured = (parameter_snapshot or {}).get(agent_node_id)
            if captured is None:
                captured = await database.get_node_parameters(agent_node_id) or {}
            if _version(captured) == 1:
                _fail()
        return  # Only explicitly legacy adapters may omit persistence.
    async with database.get_session() as session:
        reservation = await session.get(BrowserProfileReservation, profile_id)
        profile = await session.get(BrowserProfileRow, profile_id)
        if reservation is None:
            if profile is not None and profile.kind == "agent":
                _fail()
            if agent_node_id:
                from services.plugin.base import saved_settings
                if _version(await saved_settings(database, agent_node_id, parameter_snapshot)) == 1:
                    _fail()
            return
        if (reservation.runtime_version != 1 or profile is None or profile.kind != "agent"
                or principal != reservation.owner_id or principal != profile.owner_id
                or workflow_id != reservation.workflow_id or browser_node_id != reservation.browser_node_id
                or agent_node_id is not None and agent_node_id != reservation.agent_node_id):
            _fail()
        from models.database import NodeParameter, Workflow
        saved = await session.get(Workflow, workflow_id)
        params = {}
        if saved is None:
            _fail()
        for node in saved.data.get("nodes", []):
            row = await session.get(NodeParameter, node["id"])
            params[node["id"]] = dict(row.parameters or {}) if row else {}
        await validate_browser_resource_bindings(session, workflow_id, principal, saved.data, params)
        return reservation


def browser_runtime_guidance(readiness=None) -> str:
    state = readiness or {}
    normalized = "true" if state.get("needs_observation") is True else "false" if state.get("needs_observation") is False else "unknown"
    observation = (" A fresh snapshot is currently required before navigation or another page action."
                   if state.get("needs_observation") is True else
                   " Take a snapshot to obtain current references; runtime recovery can require a new observation.")
    return (" This is an agent-private Browser profile with separate website logins. "
            "The browser runs on its registered owner; localhost refers to that machine. "
            + f" Browser readiness at binding: needs_observation={normalized}." + observation
            + " An observation-needed response describes recovery readiness; it does not prove an earlier action happened.")


async def browser_readiness(database, profile_id):
    """Nonsecret saved readiness only; never starts or observes a browser."""
    if not getattr(database, "engine", None):
        return {"needs_observation": None}
    from models.browser_owners import BrowserProfileOwner
    async with database.get_session() as session:
        row = await session.get(BrowserProfileOwner, profile_id)
        return {"needs_observation": bool(row.needs_observation) if row is not None else False}


async def validate_saved_browser_resources(database, workflow_id, principal, graph, parameters):
    nodes, bindings = _bindings(graph)
    browser_ids = {key for key, node in nodes.items() if node.get("type") == "browser"}
    if not browser_ids:
        return
    captured = dict(parameters or {})
    relevant = browser_ids | {target for targets in bindings.values() for target in targets}
    for node_id in relevant:
        if node_id not in captured:
            captured[node_id] = await database.get_node_parameters(node_id) or {}
    requires_v1 = any(_version(captured.get(target)) == 1 for targets in bindings.values() for target in targets)
    has_profile = any((captured.get(key) or {}).get("profile_id") for key in browser_ids)
    if not requires_v1 and not has_profile:
        return
    if not getattr(database, "engine", None):
        if requires_v1:
            _fail()
        return
    async with database.get_session() as session:
        await validate_browser_resource_bindings(session, str(workflow_id), str(principal), graph, captured)


async def validate_browser_parameters(database, node_id, proposed):
    """An ordinary parameter edit cannot transfer a reserved identity."""
    if not getattr(database, "engine", None):
        return
    async with database.get_session() as session:
        reserved = (await session.execute(select(BrowserProfileReservation).where(
            BrowserProfileReservation.browser_node_id == node_id))).scalar_one_or_none()
        profile_id = str((proposed or {}).get("profile_id") or "")
        if reserved is not None and profile_id != reserved.profile_id:
            _fail()
        if profile_id:
            target = await session.get(BrowserProfileReservation, profile_id)
            if target is not None and target.browser_node_id != node_id:
                _fail()
