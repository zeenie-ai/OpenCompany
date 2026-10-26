"""Recorded Coding Agent runtime selection, shared by admission and workflows.

Absence of the marker is intentional: histories and generations admitted before
Pi was installed keep the native v1 implementation, including later firings.
This module contains no settings reads or IO and is safe in workflow code.

:func:`freeze_generation_harness` also freezes whether held calls wait for
the owner (``approval_wait_version``, RFC-0007), a generation flag the same
way.
"""

from typing import Any, Mapping

PI_HARNESS_VERSION = 1
PI_PACKAGE_VERSION = "1.1.0"
PI_HARNESS_KEY = "pi_harness_version"
PI_WORKFLOW_NAME = "PiCodingAgentWorkflow"
APPROVAL_WAIT_KEY = "approval_wait_version"


def uses_pi(node_type: str, context: Mapping[str, Any]) -> bool:
    if node_type == "coding_agent" and context.get(PI_HARNESS_KEY) not in {None, PI_HARNESS_VERSION}:
        raise ValueError("Unsupported recorded Coding Agent harness version")
    return node_type == "coding_agent" and context.get(PI_HARNESS_KEY) == PI_HARNESS_VERSION


def agent_workflow_name(node_type: str, context: Mapping[str, Any]) -> str:
    return PI_WORKFLOW_NAME if uses_pi(node_type, context) else "AgentWorkflow"


def freeze_pi_local_control(payload: dict) -> None:
    """Admit one-off Pi control without claiming a deployment controller.

    Call only while admitting a new run. Recorded deployment generations and
    prior invocation inputs keep the control contract they already selected.
    """
    routing = payload.get("_temporal_routing_v1") or {}
    if payload.get(PI_HARNESS_KEY, routing.get(PI_HARNESS_KEY)) != PI_HARNESS_VERSION:
        return
    if payload.get("execution_control_version") or payload.get("controller_workflow_id"):
        return
    payload["pi_local_control_version"] = 1


def _freeze(payload: dict, key: str, value: Any) -> None:
    """Set ``key`` to ``value`` (None: absent) on the payload and its routing."""
    routing = payload.get("_temporal_routing_v1")
    routing = dict(routing) if isinstance(routing, dict) else None
    for target in (payload, routing):
        if target is None:
            continue
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value
    if routing is not None:
        payload["_temporal_routing_v1"] = routing


def freeze_generation_harness(payload: dict, manifest: Mapping[str, Any] | None) -> None:
    """Replace a starter's defaults with the admitted generation's selection."""
    version = (manifest or {}).get(PI_HARNESS_KEY)
    if version not in {None, PI_HARNESS_VERSION}:
        raise ValueError("Unsupported recorded Coding Agent harness version")
    _freeze(payload, PI_HARNESS_KEY, version)
    _freeze(payload, APPROVAL_WAIT_KEY, 1 if (manifest or {}).get(APPROVAL_WAIT_KEY) == 1 else None)


def enabled_coding_agents(nodes: list[dict]) -> bool:
    return any(node.get("type") == "coding_agent" and not (node.get("data") or {}).get("disabled") for node in nodes)


def coding_agent_ancestors(nodes: list[dict], edges: list[dict]) -> set[str]:
    """Agents that need a durable coordinator to dispatch Coding teammates."""
    visited = {str(node.get("id")) for node in nodes
               if node.get("type") == "coding_agent" and not (node.get("data") or {}).get("disabled")}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            if ((edge.get("targetHandle") or edge.get("target_handle")) in {"input-tools", "input-teammates"}
                    and edge.get("source") in visited and edge.get("target") not in visited):
                visited.add(edge["target"])
                changed = True
    return visited


def assert_pi_admissions_enabled(settings=None) -> None:
    if settings is None:
        from core.config import Settings
        settings = Settings()
    if getattr(settings, "coding_pi_admissions_enabled", True) is False:
        from services.plugin import NodeUserError
        class PiAdmissionDisabled(NodeUserError):
            def as_dict(self):
                return {**super().as_dict(), "error_type": "PiAdmissionDisabled"}
        raise PiAdmissionDisabled(
            "New Coding Agent executions are disabled. Enable CODING_PI_ADMISSIONS_ENABLED after installing the Pi runtime and starting its workers.",
            requires_user_action=True,
        )
