"""Recorded, provider-neutral agent instance protocol.

Version selection is made at generation admission, never from live settings
while replaying a workflow. Both names are accepted during the tool-manifest
cutover; newly admitted inputs carry the canonical lifecycle spelling.
"""
from __future__ import annotations

from typing import Any, Mapping

VERSION = 1
CONTEXT_KEYS = (
    "agent_lifecycle_version", "agent_protocol_version", "agent_instance_id",
    "agent_run_id", "agent_instance_scope", "agent_admission_state",
    "agent_delivery_ids", "agent_transcript_revision",
)

# Configuration and provenance only. Parent chat, active-run input and Python
# service objects must never become a new instance's conversation or config.
RUNTIME_CONTEXT_KEYS = (
    "nodes", "edges", "parameter_snapshot", "user_id", "credential_customer_id",
    "session_id", "workflow_slug", "workspace_dir", "workflow_id", "generation",
    "execution_id", "root_execution_id", "team_execution_id", "employee_job_id",
    "execution_control_version", "control_id", "control_revision", "controller_workflow_id",
    "execution_control_state", "execution_control_revision", "execution_control_producers_held",
    "run_scope", "data_scope_id", "graphVersion", "context_execution_id", "context_session_id",
    "pi_harness_version", "pi_local_control_version", "pi_local_control_state", "pi_local_control_revision",
    "native_workspace_version", "agent_harness_version", "approval_wait_version", "temporal_worker_pool_enabled",
    "browser_routing_version", "browser_runtime_version", "browser_bindings",
    "runtime_behavior_version", "credential_scope_version", "outputs",
    "max_concurrent_subagents", "max_delegation_depth", "tool_result_max_chars",
)


def capture_runtime_context(context: Mapping[str, Any]) -> dict[str, Any]:
    from services.workflow_sanitizer import sanitize_runtime_payload
    return {**sanitize_runtime_payload({key: context[key] for key in RUNTIME_CONTEXT_KEYS if key in context}),
            "agent_lifecycle_version": VERSION, "agent_protocol_version": VERSION}


def enabled(context: Mapping[str, Any] | None) -> bool:
    context = context or {}
    return context.get("agent_lifecycle_version", context.get("agent_protocol_version")) == VERSION


def instance_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return {key: context[key] for key in CONTEXT_KEYS if key in context}


def session_workflow_id(instance_id: str) -> str:
    if not instance_id or len(instance_id) > 255:
        raise ValueError("An agent instance identifier is required")
    return f"agent-instance:{instance_id}"


def should_bootstrap(context: Mapping[str, Any]) -> bool:
    return (enabled(context) and not context.get("agent_instance_id")
            and context.get("node_type") in {"ai_employee", "orchestrator_agent"})
