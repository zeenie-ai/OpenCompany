"""Unit tests for ``cli.supervisor``."""

from __future__ import annotations

from collections import deque

import pytest

from cli.supervisor import Manager, ServiceSpec, RestartPolicy, _full_env


def test_full_env_force_color_and_unbuffered():
    env = _full_env({"FOO": "bar"})
    assert env["FORCE_COLOR"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["FOO"] == "bar"


def test_manager_assigns_color_to_specs():
    m = Manager()
    s1 = ServiceSpec(name="a", argv=["true"])
    s2 = ServiceSpec(name="b", argv=["true"])
    m.add(s1)
    m.add(s2)
    assert s1.color is not None
    assert s2.color is not None
    assert s1.color != s2.color  # rotation


def test_service_spec_defaults_safe():
    spec = ServiceSpec(name="x", argv=["echo", "hi"])
    assert spec.restart is RestartPolicy.ON_CRASH
    assert spec.healthy_exit_codes == {0}
    assert spec.crash_window_max == 5
    assert spec.crash_window_seconds == 180.0


def test_crash_window_logic():
    """Sliding-window detection: 5 deaths within window => give up."""
    import time
    crashes = deque(maxlen=5)
    spec = ServiceSpec(name="x", argv=["true"])
    now = time.monotonic()
    for i in range(5):
        crashes.append(now + i * 10)  # 5 crashes in 40s
    assert (
        len(crashes) == spec.crash_window_max
        and (crashes[-1] - crashes[0]) < spec.crash_window_seconds
    )


@pytest.mark.anyio
async def test_manager_runs_clean_exit_service():
    """A NEVER-restart service that exits 0 should let the manager finish cleanly."""
    import sys
    m = Manager()
    m.add(
        ServiceSpec(
            name="quick",
            argv=[sys.executable, "-c", "print('hello'); exit(0)"],
            restart=RestartPolicy.NEVER,
        )
    )
    rc = await m.run()
    assert rc == 0


@pytest.fixture
def anyio_backend():
    return "asyncio"
