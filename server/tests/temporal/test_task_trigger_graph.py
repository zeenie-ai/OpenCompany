"""Regression tests for taskTrigger runtime data entering Temporal agents."""

from services.temporal.workflow import MachinaWorkflow


def test_task_trigger_input_task_is_runtime_dependency() -> None:
    trigger = {"id": "task-trigger", "type": "taskTrigger", "_pre_executed": True}
    agent = {"id": "lead", "type": "orchestrator_agent"}
    edge = {
        "id": "task-to-lead",
        "source": "task-trigger",
        "target": "lead",
        "targetHandle": "input-task",
    }

    nodes, edges = MachinaWorkflow()._filter_executable_graph([trigger, agent], [edge])

    assert nodes == [trigger, agent]
    assert edges == [edge]
    dependencies, _ = MachinaWorkflow()._build_dependency_maps(nodes, edges)
    assert dependencies["lead"] == {"task-trigger"}


def test_non_trigger_input_task_remains_configuration() -> None:
    task_config = {"id": "task-config", "type": "taskManager"}
    agent = {"id": "lead", "type": "orchestrator_agent"}
    edge = {
        "id": "config-to-lead",
        "source": "task-config",
        "target": "lead",
        "targetHandle": "input-task",
    }

    nodes, edges = MachinaWorkflow()._filter_executable_graph([task_config, agent], [edge])

    assert nodes == [agent]
    assert edges == []
