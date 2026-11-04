"""Saved nullable CLI-list defaults through direct and snapshot execution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from nodes.agent.codex_agent import CodexAgentParams
from services.cli_agent.protocol import BatchResult, SessionResult
from tests.nodes._mocks import patched_broadcaster, patched_container


@pytest.mark.parametrize(
    "saved",
    [
        {},
        {"tasks": None, "allowed_credentials": None},
        {"tasks": [], "allowed_credentials": []},
    ],
)
def test_codex_empty_list_defaults_accept_saved_nulls(saved):
    params = CodexAgentParams.model_validate(saved)
    assert params.tasks == []
    assert params.allowed_credentials == []


def test_codex_populated_lists_keep_task_validation_and_credentials():
    params = CodexAgentParams.model_validate(
        {
            "tasks": [{"prompt": "Inspect the repository", "sandbox": "read-only"}],
            "allowed_credentials": ["openai"],
        }
    )
    assert params.tasks[0].prompt == "Inspect the repository"
    assert params.tasks[0].sandbox == "read-only"
    assert params.allowed_credentials == ["openai"]


@pytest.mark.parametrize("saved", [{}, {"model": None}, {"model": ""}])
def test_codex_model_defaults_to_cli_auto(saved):
    assert not CodexAgentParams.model_validate(saved).model


def test_codex_explicit_model_is_preserved():
    assert CodexAgentParams.model_validate({"model": "gpt-5.2-codex"}).model == "gpt-5.2-codex"


@pytest.mark.parametrize(
    "field,value",
    [
        ("tasks", "[]"),
        ("tasks", {"prompt": "not a task list"}),
        ("allowed_credentials", "openai"),
        ("allowed_credentials", {"openai": True}),
    ],
)
def test_codex_non_null_non_lists_still_fail(field, value):
    with pytest.raises(ValidationError) as exc:
        CodexAgentParams.model_validate({field: value})
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.node_contract
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "node_type,provider",
    [
        ("codex_agent", "codex"),
        ("claude_code_agent", "claude"),
    ],
)
@pytest.mark.parametrize("execution_path", ["saved-parameters", "temporal-snapshot", "temporal-activity"])
async def test_cli_null_defaults_reach_prompt_execution(harness, node_type, provider, execution_path):
    frozen = execution_path != "saved-parameters"
    node_id = "workflow:agent:1"
    saved = {"prompt": "Inspect the repository", "tasks": None, "allowed_credentials": None}
    harness.database.get_node_parameters.return_value = {"prompt": "Changed after admission"} if frozen else saved
    service = MagicMock()
    service.run_batch = AsyncMock(
        return_value=BatchResult(
            tasks=[SessionResult(task_id="t1", provider=provider, response="done", success=True)],
            n_tasks=1,
            n_succeeded=1,
            provider=provider,
        )
    )
    context = harness.build_context(
        nodes=[{"id": node_id, "type": node_type, "data": {"label": node_type}}],
        workspace_dir="test-workspace",
        extra={"parameter_snapshot": {node_id: saved}} if frozen else {},
    )

    with (
        patched_container(auth_api_keys={}) as container,
        patched_broadcaster() as broadcaster,
        patch("services.cli_agent.service.get_ai_cli_service", return_value=service),
    ):
        if execution_path == "temporal-activity":
            from temporalio.testing import ActivityEnvironment
            from services.node_registry import get_node_class

            broadcaster.update_node_output = AsyncMock()

            async def execute_activity_node(**kwargs):
                # Keep the production as_activity -> execute_node boundary,
                # routing into the real executor without starting a worker.
                return await harness.execute(
                    kwargs["node_type"],
                    kwargs["parameters"],
                    node_id=kwargs["node_id"],
                    context=harness.build_context(
                        nodes=kwargs["nodes"],
                        edges=kwargs["edges"],
                        workspace_dir="test-workspace",
                        extra=kwargs.get("extras") or {},
                    ),
                )

            container.workflow_service.return_value.execute_node = AsyncMock(side_effect=execute_activity_node)
            result = await ActivityEnvironment().run(
                get_node_class(node_type).as_activity(),
                {**context, "node_id": node_id, "node_data": saved},
            )
        else:
            result = await harness.execute(node_type, {}, node_id=node_id, context=context)

    harness.assert_envelope(result, success=True)
    args = service.run_batch.await_args
    assert args.args == (provider,)
    assert args.kwargs["allowed_credentials"] == []
    tasks = list(args.kwargs["tasks"])
    assert len(tasks) == 1
    assert tasks[0].prompt == "Inspect the repository"
    if provider == "codex":
        assert tasks[0].model is None
    # Validation must not mutate a captured Temporal snapshot.
    assert saved["tasks"] is None
    assert saved["allowed_credentials"] is None
