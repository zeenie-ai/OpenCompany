"""Workflow definitions shared by workers and the release replay gate."""

from __future__ import annotations


def framework_workflows() -> list:
    """Return the production framework registrations without starting services."""
    from services.temporal.workflow import MachinaWorkflow
    from services.temporal.agent_workflow import AgentWorkflow, DelegatedTaskWorkflow
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    from services.temporal.pi_inline_delegation import PiInlineDelegationWorkflow
    from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow
    from services.temporal.polling_trigger_workflow import PollingTriggerWorkflow
    from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow
    from services.temporal.approved_tool_call_workflow import ApprovedToolCallWorkflow
    from services.temporal.approval_workflow import ApprovalWorkflow
    from services.temporal.employee_job_workflow import EmployeeJobWorkflow, EmployeeJobDeliveryWorkflow

    return [
        EmployeeJobWorkflow, EmployeeJobDeliveryWorkflow, ApprovedToolCallWorkflow,
        ApprovalWorkflow, NodeInvocationWorkflow, WorkspaceTaskControllerWorkflow,
        MachinaWorkflow, AgentWorkflow, PiCodingAgentWorkflow,
        PiInlineDelegationWorkflow, DelegatedTaskWorkflow, TriggerListenerWorkflow,
        PollingTriggerWorkflow, WorkflowControlWorkflow,
    ]


def replay_workflows() -> list:
    """Include plugin workflows using the same plugin registry as Worker."""
    import nodes  # noqa: F401 -- discover plugins, just as application startup does
    from services.temporal.plugin_registry import temporal_plugins

    config = {"workflows": framework_workflows()}
    for plugin in temporal_plugins():
        config = plugin.configure_replayer(config)
    return list(config["workflows"])
