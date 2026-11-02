"""The dev-server watchdog's stuck-runs count (1b.38b): one attention item
while any run's workflow tasks keep failing."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import attention
from services.temporal import lifecycle


@pytest.fixture
def items(monkeypatch):
    touch, resolve = AsyncMock(), AsyncMock()
    monkeypatch.setattr(attention, "touch", touch)
    monkeypatch.setattr(attention, "resolve", resolve)
    return SimpleNamespace(touch=touch, resolve=resolve)


def _wrapper(count=None, error=None):
    answer = AsyncMock(side_effect=error) if error else AsyncMock(return_value=SimpleNamespace(count=count))
    return SimpleNamespace(client=SimpleNamespace(count_workflows=answer))


async def test_a_stuck_run_raises_the_item(items):
    wrapper = _wrapper(count=2)
    await lifecycle._check_stuck_runs(wrapper)
    assert wrapper.client.count_workflows.await_args.args[0] == lifecycle._STUCK_RUNS_QUERY
    items.touch.assert_awaited_once_with(attention.STUCK_RUNS, "temporal")
    items.resolve.assert_not_awaited()


async def test_no_stuck_run_clears_the_item(items):
    await lifecycle._check_stuck_runs(_wrapper(count=0))
    items.resolve.assert_awaited_once_with(attention.STUCK_RUNS, "temporal")
    items.touch.assert_not_awaited()


async def test_a_failed_count_leaves_the_item_alone(items):
    await lifecycle._check_stuck_runs(_wrapper(error=RuntimeError("Visibility is down")))
    items.touch.assert_not_awaited()
    items.resolve.assert_not_awaited()


async def test_the_pinned_dev_server_answers_the_count():
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to the pinned Temporal CLI binary")
    from temporalio.testing import WorkflowEnvironment

    from services.temporal._install import cli_pin

    extra = [arg for key, value in cli_pin()["dynamic_config"].items() for arg in ("--dynamic-config-value", f"{key}={json.dumps(value)}")]
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli, dev_server_extra_args=extra) as env:
        assert (await env.client.count_workflows(lifecycle._STUCK_RUNS_QUERY)).count == 0
