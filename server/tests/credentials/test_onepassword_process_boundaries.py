"""Resolver bootstrap credentials must not reach ordinary CLI runtimes."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def bootstrap(monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "bootstrap-canary")
    monkeypatch.setenv("OP_CONNECT_TOKEN", "connect-canary")
    monkeypatch.setenv("op_session_personal", "session-canary")
    monkeypatch.setenv("ORDINARY_RUNTIME_VAR", "keep-me")


@pytest.mark.parametrize("module_name,function,args", [
    ("nodes.github._service", "gh_env", ()),
    ("nodes.github._service", "login_env", ()),
    ("nodes.gcloud._service", "gcloud_env", ()),
    ("nodes.gcloud._service", "login_env", ()),
    ("nodes.cloudflare._service", "cf_env", ("approved-api-token", "account")),
    ("nodes.cloudflare._service", "login_env", ()),
    ("nodes.vercel._service", "vercel_env", ("approved-api-token",)),
    ("nodes.agent.claude_code_agent._oauth", "_claude_env", ()),
])
def test_cli_auth_and_operation_environments_exclude_bootstrap(bootstrap, monkeypatch, module_name, function, args):
    from importlib import import_module
    module = import_module(module_name)
    if module_name == "nodes.gcloud._service":
        monkeypatch.setattr(module, "_config_dir", lambda: "isolated-gcloud-config")
    if module_name.endswith("._oauth"):
        monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    env = getattr(module, function)(*args)
    assert env["ORDINARY_RUNTIME_VAR"] == "keep-me"
    assert not any(key.upper().startswith("OP_") for key in env)
    if function == "cf_env":
        assert env["CLOUDFLARE_API_TOKEN"] == "approved-api-token"
    if function == "vercel_env":
        assert env["VERCEL_TOKEN"] == "approved-api-token"


async def test_mobile_final_spawn_filters_overrides(bootstrap, monkeypatch):
    from nodes.mobile import _process
    output = asyncio.StreamReader()
    output.feed_data(b"completed")
    output.feed_eof()
    process = SimpleNamespace(stdout=output, returncode=0, wait=AsyncMock(return_value=0))
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(_process.asyncio, "create_subprocess_exec", spawn)
    assert await _process.command(["installer"], env={"OP_SERVICE_ACCOUNT_TOKEN": "override-canary", "op_session_other": "override", "PATH": "private-bin"}) == "completed"
    assert spawn.await_args.kwargs["env"] == {"PATH": "private-bin"}


async def test_worktree_final_git_spawn_filters_ambient_bootstrap(bootstrap, monkeypatch):
    from services.cli_agent import worktree
    run = AsyncMock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(worktree.anyio, "run_process", run)
    monkeypatch.setattr(Path, "exists", lambda self: False)
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: None)
    await worktree.add_worktree(Path("repo"), Path("workspace/worktree"), "branch")
    await worktree.remove_worktree(Path("repo"), Path("workspace/worktree"))
    for call in run.await_args_list:
        env = call.kwargs["env"]
        assert env["ORDINARY_RUNTIME_VAR"] == "keep-me"
        assert not any(key.upper().startswith("OP_") for key in env)


def test_dependency_install_final_spawn_filters_bootstrap(bootstrap, monkeypatch):
    from core import js_runtime
    monkeypatch.setattr(js_runtime, "require_bun", lambda purpose: "bun")
    monkeypatch.setattr(js_runtime, "ensure_shared_tree", lambda root: Path("shared-tree"))
    calls = []
    monkeypatch.setattr(js_runtime.subprocess, "run", lambda *args, **kwargs: calls.append(kwargs))
    js_runtime.add_package("@approved/cli@1.2.3")
    assert calls[0]["env"]["ORDINARY_RUNTIME_VAR"] == "keep-me"
    assert not any(key.upper().startswith("OP_") for key in calls[0]["env"])
