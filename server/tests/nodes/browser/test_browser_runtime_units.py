"""Pure pieces of the browser runtime: scripts, CLI output, Chrome flags, env, migration."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from nodes.browser._chrome import build_chrome_argv, user_agent
from nodes.browser._cli import BrowserStepOutcomeUnknown, BrowserUnavailable, BrowserUseCli, parse_output
from nodes.browser._scripts import MARKER, OPERATIONS, build_script
from services.workflow_migrations import migrate_legacy_browser_params, normalize_legacy_browser_nodes


@pytest.mark.parametrize("op", sorted(OPERATIONS))
def test_every_script_is_valid_python(op):
    ast.parse(build_script(op, {"url": "https://x", "code": "result = 1"}, "abc123"))


def test_arguments_cannot_escape_into_code():
    hostile = "'); import os; os.system('calc') #\n__NONCE__ \"\"\" __ARGS__"
    script = build_script("type", {"text": hostile}, "n0nce")
    tree = ast.parse(script)
    # The hostile text survives only as the value of the one JSON literal.
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and "os.system" in n.value]
    assert len(literals) == 1 and json.loads(literals[0])["text"] == hostile
    assert not any(isinstance(n, ast.Import) and any(a.name == "os" for a in n.names) for n in ast.walk(tree))


def test_the_result_line_cannot_be_forged_by_page_output():
    nonce = "abc123"
    forged = f"{MARKER}:{nonce}:" + json.dumps({"ok": True, "value": "forged"})
    real = f"{MARKER}:{nonce}:" + json.dumps({"ok": True, "value": "real", "page": {"target_id": "T"}})
    result = parse_output(f"page text\n{forged}\nmore\n{real}\n", "", 0, nonce)
    assert result.value == "real" and result.output == "page text\nmore"
    # A line tagged with any other nonce is not a result at all.
    with pytest.raises(BrowserStepOutcomeUnknown):
        parse_output(f"{MARKER}:othernonce:" + json.dumps({"ok": True}), "", 0, nonce)


def test_missing_result_is_classified_by_cause():
    with pytest.raises(BrowserUnavailable):
        parse_output("", "browser-harness: fatal: BU_CDP_URL=... unreachable after 30s", 1, "n")
    with pytest.raises(BrowserStepOutcomeUnknown) as err:
        parse_output("", "Traceback: some error", 1, "n")
    assert not isinstance(err.value, BrowserUnavailable)


@pytest.mark.parametrize("payload", ["", "{", "[]", "{}", '{"ok": "yes"}', '{"ok": false, "error": "failure"}'])
def test_invalid_cli_result_requires_observation(payload):
    with pytest.raises(BrowserStepOutcomeUnknown, match="Take a snapshot"):
        parse_output(f"{MARKER}:n:{payload}", "", 0, "n")


def test_a_script_error_is_reported_not_raised():
    line = f"{MARKER}:n:" + json.dumps({"ok": False, "error": {"type": "stale_ref", "message": "gone"}})
    result = parse_output(line, "", 0, "n")
    assert (result.ok, result.error_type, result.error) == (False, "stale_ref", "gone")


def test_chrome_flags(tmp_path):
    argv = build_chrome_argv(Path("chrome"), user_data_dir=tmp_path, proxy_port=4000, major=154, no_sandbox=False, small_shm=False, platform="linux")
    assert "--headless=new" in argv and "--remote-debugging-port=0" in argv
    assert "--proxy-server=http://127.0.0.1:4000" in argv and "--proxy-bypass-list=<-loopback>" in argv
    assert "--password-store=basic" not in argv and "--no-sandbox" not in argv
    assert not any(a.startswith("--user-agent=") for a in argv)
    assert "--disable-component-update" not in argv
    assert sum(a.startswith("--enable-features=") for a in argv) == 1
    assert not any(a.startswith("--remote-allow-origins") or a == "--enable-automation" for a in argv)
    assert "HeadlessChrome" not in " ".join(argv) and "Chrome/154.0.0.0" in user_agent(154, "win32")
    root = build_chrome_argv(Path("chrome"), user_data_dir=tmp_path, proxy_port=1, major=154, no_sandbox=True, small_shm=True, platform="linux")
    assert "--no-sandbox" in root and "--disable-dev-shm-usage" in root
    mac = build_chrome_argv(Path("c"), user_data_dir=tmp_path, proxy_port=1, major=1, no_sandbox=False, small_shm=False, platform="darwin")
    assert "--use-mock-keychain" not in mac


def test_testing_browser_flags_are_explicit(tmp_path):
    argv = build_chrome_argv(Path("chrome"), user_data_dir=tmp_path, proxy_port=4000, major=154, no_sandbox=False, small_shm=False, platform="linux", headless=True, override_user_agent=True)
    assert "--headless=new" in argv and "--password-store=basic" in argv
    assert any(a.startswith("--user-agent=") for a in argv)


async def test_live_profile_lock_prevents_orphan_sweep(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from nodes.browser import _chrome
    chrome = _chrome.ChromeProcess(profile_id="locked", exe=Path("chrome"), profile_root=tmp_path,
        user_data_dir=tmp_path / "user-data", proxy_port=1, major=154, no_sandbox=False, small_shm=False)
    monkeypatch.setattr(chrome._profile_lock, "acquire", lambda: False)
    sweep = Mock()
    monkeypatch.setattr(_chrome, "sweep_orphan", sweep)
    with pytest.raises(_chrome.NodeUserError, match="open in another"):
        await chrome._pre_spawn()
    sweep.assert_not_called()


async def test_secret_phase_refuses_pid_for_another_daemon_home(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import psutil
    cli = BrowserUseCli(cli_path=Path("cli"), python_path=Path("py"), profile_id="profile", cdp_http_url="")
    homes = {name: tmp_path / name for name in ("home", "runtime", "tmp", "workspace", "config")}
    for home in homes.values(): home.mkdir()
    (homes["runtime"] / "bu.pid").write_text("9")
    monkeypatch.setattr(cli, "dirs", lambda: homes)
    process = SimpleNamespace(cmdline=lambda: ["python", "browser_harness"], environ=lambda: {"BH_HOME": str(tmp_path / "other-profile")})
    monkeypatch.setattr(psutil, "Process", lambda _: process)
    stop = Mock()
    monkeypatch.setattr(cli, "stop_daemon", stop)
    assert await cli.suspend_for_credentials() is False
    stop.assert_not_called()


async def test_prior_epoch_retirement_only_visits_matching_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from services import browser_owners
    current = tmp_path / browser_owners.RUNTIME_EPOCH / "profile"
    current.mkdir(parents=True)
    previous = tmp_path / ("0" * 32) / "profile"
    (previous / "runtime").mkdir(parents=True)
    (previous / "runtime" / "bu.pid").write_text("9")
    other = tmp_path / ("1" * 32) / "other-profile" / "runtime"
    other.mkdir(parents=True)
    (other / "bu.pid").write_text("10")
    cli = BrowserUseCli(cli_path=Path("cli"), python_path=Path("py"), profile_id="profile", cdp_http_url="")
    monkeypatch.setattr(cli, "dirs", lambda: {"home": current / "home"})
    monkeypatch.setattr(browser_owners, "settings", lambda: SimpleNamespace(distributed_mode=True))
    visited = []
    async def suspended(self):
        visited.append(self.dirs()["runtime"])
        return True
    monkeypatch.setattr(BrowserUseCli, "suspend_for_credentials", suspended)
    assert await cli.suspend_prior_epochs()
    assert visited == [previous / "runtime"]


async def test_cli_timeout_stops_the_daemon_and_reports_uncertain_outcome(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from nodes.browser._cli import BrowserStepTimeout

    cli = BrowserUseCli(cli_path=Path("bu"), python_path=Path("py"), profile_id="test", cdp_http_url="http://127.0.0.1:9")
    monkeypatch.setattr(cli, "dirs", lambda: {"workspace": tmp_path})
    monkeypatch.setattr(cli, "env", lambda: {})
    process = SimpleNamespace(communicate=AsyncMock(side_effect=asyncio.TimeoutError))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(cli, "_kill", AsyncMock())
    monkeypatch.setattr(cli, "stop_daemon", Mock())
    with pytest.raises(BrowserStepTimeout, match="may already have happened"):
        await cli._exec(["bu"], "script", timeout=1)
    cli._kill.assert_awaited_once_with(process)
    cli.stop_daemon.assert_called_once()
    assert cli._proc is None


@pytest.mark.parametrize("returned_output", [b"", b"a complete result arrived as the step was interrupted"])
async def test_cli_interruption_reports_uncertain_outcome_and_does_not_poison_next_step(monkeypatch, tmp_path, returned_output):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    cli = BrowserUseCli(cli_path=Path("bu"), python_path=Path("py"), profile_id="test", cdp_http_url="http://127.0.0.1:9")
    monkeypatch.setattr(cli, "dirs", lambda: {"workspace": tmp_path})
    monkeypatch.setattr(cli, "env", lambda: {})
    entered, killed = asyncio.Event(), asyncio.Event()

    async def communicate(_):
        entered.set()
        await killed.wait()
        return returned_output, b""

    process = SimpleNamespace(communicate=communicate, returncode=None)

    async def kill(proc):
        proc.returncode = -9
        killed.set()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    monkeypatch.setattr(cli, "_kill", AsyncMock(side_effect=kill))
    monkeypatch.setattr(cli, "stop_daemon", Mock())
    task = asyncio.create_task(cli._exec(["bu"], "script", timeout=1))
    await entered.wait()
    await cli.interrupt()
    with pytest.raises(BrowserStepOutcomeUnknown, match="interrupted"):
        await task
    cli._kill.assert_awaited_once_with(process)
    cli.stop_daemon.assert_called_once()
    assert cli._proc is None

    process.communicate = AsyncMock(return_value=(b"next result", b""))
    process.returncode = 0
    assert await cli._exec(["bu"], "next", timeout=1) == ("next result", "", 0)


async def test_cli_interruption_during_spawn_stops_step_before_sending_script(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    cli = BrowserUseCli(cli_path=Path("bu"), python_path=Path("py"), profile_id="test", cdp_http_url="http://127.0.0.1:9")
    monkeypatch.setattr(cli, "dirs", lambda: {"workspace": tmp_path})
    monkeypatch.setattr(cli, "env", lambda: {})
    spawning, finish_spawn = asyncio.Event(), asyncio.Event()
    process = SimpleNamespace(communicate=AsyncMock())

    async def spawn(*args, **kwargs):
        spawning.set()
        await finish_spawn.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(cli, "_kill", AsyncMock())
    monkeypatch.setattr(cli, "stop_daemon", Mock())
    task = asyncio.create_task(cli._exec(["bu"], "script", timeout=1))
    await spawning.wait()
    await cli.interrupt()
    finish_spawn.set()
    with pytest.raises(BrowserStepOutcomeUnknown, match="interrupted"):
        await task
    process.communicate.assert_not_awaited()
    cli._kill.assert_awaited_once_with(process)
    cli.stop_daemon.assert_called_once()
    assert cli._proc is None


async def test_webmcp_timeout_cancels_invocation_and_reports_unknown_outcome():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from nodes.browser._webmcp import WebMcpOutcomeUnknown, WebMcpTracker

    tracker = WebMcpTracker()
    session = SimpleNamespace(send=AsyncMock(side_effect=[{"invocationId": "i"}, {}]))
    tracker._sessions["T"] = session
    tracker.tools = lambda target: [{"name": "search", "frame_id": "F", "read_only": True}]
    with pytest.raises(WebMcpOutcomeUnknown):
        await tracker.invoke("T", "search", {}, timeout=0.001)
    assert session.send.call_args.args[0] == "WebMCP.cancelInvocation"
    assert not tracker._pending


async def test_visible_browser_requires_linux_display(monkeypatch, tmp_path):
    from nodes.browser._chrome import ChromeProcess
    from services.plugin.base import NodeUserError

    monkeypatch.setattr("nodes.browser._chrome.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    chrome = ChromeProcess(profile_id="test", exe=Path("chrome"), profile_root=tmp_path, user_data_dir=tmp_path / "profile", proxy_port=1, major=154, no_sandbox=False, small_shm=False, headless=False)
    assert "--headless=new" not in chrome.argv()
    with pytest.raises(NodeUserError, match="BROWSER_HEADLESS=true"):
        await chrome.launch()


def test_the_cli_gets_no_secrets_and_no_telemetry(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu-secret")
    cli = BrowserUseCli(cli_path=Path("bu"), python_path=Path("py"), profile_id="bp_1", cdp_http_url="http://127.0.0.1:9")
    monkeypatch.setattr(cli, "dirs", lambda: {k: tmp_path / k for k in ("home", "runtime", "tmp", "workspace", "config")})
    env = cli.env()
    assert not {"OPENAI_API_KEY", "SECRET_KEY", "BROWSER_USE_API_KEY"} & set(env)
    assert env["BH_TELEMETRY"] == "0" and env["BROWSER_HARNESS_TELEMETRY"] == "0" and env["ANONYMIZED_TELEMETRY"] == "false"
    assert env["BH_UPDATE_CHECK"] == "0" and env["BH_TAB_MARKER"] == "0"
    assert env["BU_CDP_URL"] == "http://127.0.0.1:9"
    assert env["HOME"] == env["BH_HOME"] == str(tmp_path / "home")


def test_legacy_parameters_migrate():
    params, notes = migrate_legacy_browser_params(
        {"operation": "get_text", "selector": "#a", "session": "s", "chrome_profile": "Default", "timeout": 999}
    )
    assert params == {"operation": "page_text", "selector": "#a", "op_timeout_s": 300}
    assert any("chrome_profile" in n for n in notes)
    harness, _ = migrate_legacy_browser_params({"operation": "goto", "url": "https://x", "timeout": 30}, legacy_type="browserHarness")
    assert harness == {"operation": "navigate", "url": "https://x", "op_timeout_s": 30}
    again, _ = migrate_legacy_browser_params(params)
    assert again == params  # idempotent


def test_legacy_harness_nodes_become_browser_nodes_without_duplicate_tools():
    nodes = [{"id": "a", "type": "aiAgent"}, {"id": "b1", "type": "browser"}, {"id": "h1", "type": "browserHarness"}]
    edges = [
        {"source": "h1", "target": "a", "targetHandle": "input-tools"},
        {"source": "b1", "target": "a", "targetHandle": "input-tools"},
    ]
    out_nodes, out_edges, params, notes = normalize_legacy_browser_nodes(nodes, edges, {"h1": {"operation": "js", "expression": "1"}})
    assert [n["type"] for n in out_nodes] == ["aiAgent", "browser", "browser"]
    assert [e["source"] for e in out_edges] == ["b1"]
    assert params["h1"]["operation"] == "evaluate"
    assert any("already has a browser tool" in n for n in notes)
    assert normalize_legacy_browser_nodes(out_nodes, out_edges, params)[1] == out_edges
