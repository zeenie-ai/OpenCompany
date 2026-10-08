"""Bootstrap credentials never enter ordinary child processes or restarts."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nodes.filesystem._backend import WorkspaceBackend
from services._supervisor.process import BaseProcessSupervisor
from services.cli_agent.service import AICliService
from services.cli_agent.session import AICliSession
from services.cli_agent.transports.posix import PosixPtyTransport
from services.cli_agent.transports.windows import WindowsPtyTransport
from services.cli_agent.types import ClaudeTaskSpec
from services.events.cli import run_cli_command
from services.process_environment import without_onepassword_environment
from services.process_service import ProcessService


@pytest.fixture
def bootstrap_environment(monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "canary-bootstrap-token")
    monkeypatch.setenv("OP_CONNECT_TOKEN", "canary-connect-token")
    monkeypatch.setenv("OP_CONNECT_HOST", "https://canary.invalid")
    monkeypatch.setenv("OP_SESSION_example", "canary-desktop-session")
    monkeypatch.setenv("OPENCOMPANY_ENV_CANARY", "ordinary\tΩ ")


def _assert_contained(env):
    assert not any(key.upper().startswith("OP_") for key in env)
    assert env["OPENCOMPANY_ENV_CANARY"] == "ordinary\tΩ "


def test_explicit_environment_retains_values_and_does_not_mutate_input():
    source = {
        "op_service_account_token": "canary-token",
        "OP_SESSION_ACCOUNT": "canary-session",
        "OPERATION_MODE": "unchanged",
        "OPENCOMPANY_ENV_CANARY": "ordinary\tΩ ",
        "EMPTY_VALUE": "",
    }
    original = dict(source)
    child = without_onepassword_environment(source)
    _assert_contained(child)
    assert child["OPERATION_MODE"] == "unchanged"
    assert child["EMPTY_VALUE"] == ""
    assert source == original
    # Explicit isolation must not silently turn into ambient inheritance.
    assert without_onepassword_environment({}) == {}


@pytest.mark.asyncio
async def test_supervisor_real_child_cannot_read_bootstrap_after_subclass_override(
    bootstrap_environment,
):
    class EnvironmentProbe(BaseProcessSupervisor):
        pipe_streams = True

        def __init__(self):
            super().__init__()
            self.lines = []

        def binary_path(self):
            return Path(sys.executable)

        def argv(self):
            probe = (
                "import os,json; print(json.dumps({'op_keys':"
                "[k for k in os.environ if k.upper().startswith('OP_')],"
                "'ordinary':os.environ['OPENCOMPANY_ENV_CANARY'],"
                "'override':os.environ['CHILD_OVERRIDE']}))"
            )
            return [sys.executable, "-c", probe]

        def env(self):
            # A subclass may merge ambient variables or add overrides.
            return {**os.environ, "op_override": "canary-override", "CHILD_OVERRIDE": "kept"}

        def stdout_log(self, line):
            self.lines.append(line)

        def stderr_log(self, line):
            raise AssertionError(line)

    supervisor = EnvironmentProbe()
    await supervisor._do_start()
    assert await supervisor._proc.wait() == 0
    await asyncio.gather(*supervisor._drain_tasks)
    observation = json.loads(supervisor.lines[0].partition("] ")[2])
    assert observation == {"op_keys": [], "ordinary": "ordinary\tΩ ", "override": "kept"}
    assert os.environ["OP_SERVICE_ACCOUNT_TOKEN"] == "canary-bootstrap-token"
    await supervisor._do_stop()


@pytest.mark.asyncio
async def test_process_manager_contains_ambient_and_extra_env_across_restart(
    bootstrap_environment, monkeypatch, tmp_path,
):
    calls = []

    async def spawn(*argv, **kwargs):
        calls.append(kwargs["env"])
        return SimpleNamespace(pid=42, stdout=object(), stderr=object())

    service = ProcessService()
    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("services.process_service.shutil.which", lambda _: sys.executable)
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(workspace_base_resolved=str(tmp_path)))
    monkeypatch.setattr("core.paths.daemons_dir", lambda: tmp_path / "daemons")
    monkeypatch.setattr(service, "_read_stream", AsyncMock())

    async def stop(name, workflow_id="default"):
        service._processes[service._key(workflow_id, name)].status = "stopped"
        return {"success": True}

    monkeypatch.setattr(service, "stop", stop)
    extra = {"OP_SERVICE_ACCOUNT_TOKEN": "canary-user-override", "op_session_other": "canary", "APP_MODE": "same"}
    accepted = await service.start("env-probe", "python", working_directory=str(tmp_path), extra_env=extra)
    assert accepted["success"]
    managed = service._processes[("default", "env-probe")]
    assert managed.extra_env == {"APP_MODE": "same"}
    assert (await service.restart("env-probe"))["success"]
    await asyncio.sleep(0)
    assert len(calls) == 2
    for env in calls:
        _assert_contained(env)
        assert env["APP_MODE"] == "same"
        assert env["PYTHONUNBUFFERED"] == "1"
    assert extra["OP_SERVICE_ACCOUNT_TOKEN"] == "canary-user-override"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport_class", [PosixPtyTransport, WindowsPtyTransport])
async def test_pty_boundary_filters_environment_after_any_caller_override(
    transport_class, bootstrap_environment, tmp_path,
):
    observed = []

    class PtyStub:
        @staticmethod
        def spawn(argv, **kwargs):
            observed.append(kwargs)
            return SimpleNamespace(pid=42)

    transport = transport_class()
    transport._pty_process_cls = PtyStub
    caller_env = {**os.environ, "op_override": "canary-override", "TERM": "xterm-256color"}
    original = dict(caller_env)
    handle = await transport.spawn([sys.executable, "-c", "pass"], cwd=tmp_path, env=caller_env)
    assert handle.pid == 42
    _assert_contained(observed[0]["env"])
    assert observed[0]["env"]["TERM"] == "xterm-256color"
    assert observed[0]["cwd"] == str(tmp_path)
    assert observed[0]["dimensions"] == (24, 80)
    assert caller_env == original


@pytest.mark.parametrize("inherit_env", [False, True])
def test_filesystem_shell_filters_final_env_without_changing_inheritance(
    bootstrap_environment, monkeypatch, tmp_path, inherit_env,
):
    observed = []

    def run(argv, **kwargs):
        observed.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="safe-output", stderr="")

    monkeypatch.setattr("nodes.filesystem._backend._find_nu", lambda: None)
    monkeypatch.setattr("nodes.filesystem._backend.subprocess.run", run)
    backend = WorkspaceBackend(
        tmp_path,
        inherit_env=inherit_env,
        env={"OP_SERVICE_ACCOUNT_TOKEN": "canary-explicit", "CHILD_VALUE": "kept"},
    )
    # Even late additions must be contained at the execution boundary.
    backend._env["op_late_addition"] = "canary-late"
    result = backend.execute("echo safe-output")
    assert result.exit_code == 0
    env = observed[0]["env"]
    assert not any(key.upper().startswith("OP_") for key in env)
    assert env["CHILD_VALUE"] == "kept"
    assert ("OPENCOMPANY_ENV_CANARY" in env) == inherit_env
    if inherit_env:
        _assert_contained(env)
    assert observed[0]["cwd"] == str(tmp_path)


def test_cli_session_env_filters_provider_additions_and_keeps_existing_contract(
    bootstrap_environment,
):
    session = AICliSession.__new__(AICliSession)
    session._provider = SimpleNamespace(name="codex", ide_lock_env_var="OP_PROVIDER_OVERRIDE")
    session._lockfile_path = Path("provider-lock")
    session._workflow_id = "workflow"
    session._node_id = "node"
    session._batch_token = "1234567890abcdef"
    env = session.env()
    _assert_contained(env)
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["OPENCOMPANY_PARENT_RUN_ID"] == "workflow:node:12345678"
    assert env["MACHINA_PARENT_RUN_ID"] == env["OPENCOMPANY_PARENT_RUN_ID"]


@pytest.mark.asyncio
async def test_cli_repo_discovery_contains_ambient_bootstrap(
    bootstrap_environment, monkeypatch, tmp_path,
):
    observed = []

    async def run(argv, **kwargs):
        observed.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=str(tmp_path).encode())

    monkeypatch.setattr("services.cli_agent.service.anyio.run_process", run)
    root = await AICliService._resolve_repo_root(workspace_dir=tmp_path, override=tmp_path)
    assert root == tmp_path
    _assert_contained(observed[0])


@pytest.mark.asyncio
async def test_cli_pooled_turn_supplies_filtered_env_and_keeps_parent_ids(
    bootstrap_environment, monkeypatch, tmp_path,
):
    pool = SimpleNamespace(
        start_reaper=AsyncMock(), acquire=AsyncMock(return_value="pooled"),
        send_turn=AsyncMock(return_value="result"), release=AsyncMock(),
    )
    monkeypatch.setattr("services.cli_agent.service.get_session_pool", lambda _: pool)
    result = await AICliService()._run_pooled_turn(
        task=ClaudeTaskSpec(prompt="safe task"), session_key="session", cwd=tmp_path,
        workspace_dir=tmp_path, defaults={}, mcp_port=8000, mcp_bearer_token="abcdefgh-token",
        connected_tools=[], connected_skill_names=[], workflow_id="workflow",
    )
    assert result == "result"
    env = pool.acquire.call_args.kwargs["env"]
    _assert_contained(env)
    assert env["CLAUDE_CONFIG_DIR"]
    assert env["OPENCOMPANY_PARENT_RUN_ID"] == "workflow:session:abcdefgh"
    assert env["MACHINA_PARENT_RUN_ID"] == env["OPENCOMPANY_PARENT_RUN_ID"]
    pool.release.assert_awaited_once_with("pooled")


@pytest.mark.asyncio
@pytest.mark.parametrize("env_mode", ["ambient", "override", "isolated"])
async def test_shared_cli_spawn_contains_bootstrap_and_preserves_env_modes(
    bootstrap_environment, monkeypatch, env_mode,
):
    observed = []
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(b'{"safe":true}', b"")),
    )

    async def spawn(*argv, **kwargs):
        observed.append((argv, kwargs))
        return process

    monkeypatch.setattr("services.events.cli.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("services.events.cli.shutil.which", lambda _: sys.executable)
    env = None
    if env_mode == "override":
        env = {"op_reintroduced": "canary-override", "CUSTOM_VALUE": "whitespace\tΩ "}
    elif env_mode == "isolated":
        env = {}
    original = dict(env) if env is not None else None
    result = await run_cli_command(binary="probe", argv=["--unchanged"], env=env, cwd="same-cwd")
    assert result == {"success": True, "result": {"safe": True}, "stdout": '{"safe":true}', "stderr": "", "error": None}
    argv, kwargs = observed[0]
    assert argv == (sys.executable, "--unchanged")
    assert kwargs["cwd"] == "same-cwd"
    assert not any(key.upper().startswith("OP_") for key in kwargs["env"])
    if env_mode == "ambient":
        _assert_contained(kwargs["env"])
    elif env_mode == "override":
        assert kwargs["env"] == {"CUSTOM_VALUE": "whitespace\tΩ "}
    else:
        assert kwargs["env"] == {}
    assert env == original
    process.communicate.assert_awaited_once_with(input=None)
