"""treg plugin tests.

The treg CLI owns its sign-in (the GitHub / Cloudflare pattern); these tests
lock what OpenCompany does around it:

- the environment every treg run gets (private config, inherited ``TREG_*``
  removed, analytics off, a ``BROWSER`` that opens nothing, stdin closed);
- the pinned uv install into OpenCompany's own tool folder;
- the sign-in relay: link and code relayed, one sign-in at a time, an
  accepted key skips the sign-in, a stuck sign-in is ended, the link is
  never logged; status keeps the marker in step; logout;
- the credential, its catalogue card and icon.

No real treg CLI or uv is ever started: ``run_cli_command``, the sign-in
spawn and the install subprocess are faked.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import shlex
import subprocess
import sys
import typing
import webbrowser
from pathlib import Path

import pytest

import nodes.treg._handlers as treg_handlers
import nodes.treg._install as treg_install
import nodes.treg._service as treg_service
import nodes.treg.treg_action as treg_action_mod
from nodes.treg import WS_HANDLERS
from nodes.treg._credentials import TregCredential
from nodes.treg.treg_action import TregActionNode, TregActionParams
from services.plugin.base import NodeUserError

PLUGIN_DIR = Path(treg_service.__file__).parent
SERVER_DIR = PLUGIN_DIR.parents[1]
CONFIG_PATH = SERVER_DIR / "config" / "credential_providers.json"

LINK = "https://treg.to/login?cli=KmQXKinP80tWyxAvBWPW4HF5#code=R658"
SIGN_IN_OUTPUT = (
    "Opening your browser to sign in…\n"
    "If it doesn't open, visit:\n"
    f"  {LINK}\n"
    "\n"
    "  The sign-in page shows this code — check it matches:  R658\n"
    "\n"
    "Waiting for authorization…\n"
)
ACCOUNT = {"email": "owner@example.com", "team": "acme"}


def _ok(result=None, stdout=None):
    if stdout is None:
        stdout = json.dumps(result) if result is not None else ""
    return {"success": True, "result": result, "stdout": stdout, "stderr": "", "error": None}


def _failed(stdout="", stderr="treg: not signed in", code=1):
    return {"success": False, "stdout": stdout, "stderr": stderr, "error": stderr or f"exit {code}"}


def _recorder(*replies):
    """A fake ``run_cli_command`` recording each call; answers ``replies`` in order (the last repeats)."""
    calls = []

    async def fake_run(**kwargs):
        calls.append(kwargs)
        return replies[min(len(calls), len(replies)) - 1]

    return calls, fake_run


async def _until(condition, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        assert loop.time() < end, "condition not reached"
        await asyncio.sleep(0.01)


@pytest.fixture
def treg_home(monkeypatch, tmp_path):
    home = tmp_path / "treg"
    home.mkdir()
    monkeypatch.setattr(treg_service, "treg_dir", lambda: home)
    return home


# --- the environment of every treg run ---------------------------------------


def test_env_pins_the_config_and_strips_inherited_treg_variables(monkeypatch, treg_home):
    monkeypatch.setenv("TREG_TOKEN", "treg_from_the_terminal")
    monkeypatch.setenv("TREG_ORG", "other-team")
    monkeypatch.setenv("TREG_URL", "https://example.invalid")
    monkeypatch.setenv("TREG_SESSION_ID", "abc")
    env = treg_service.treg_env()

    assert env["TREG_CONFIG"] == str(treg_home / "config.json")
    assert not {"TREG_TOKEN", "TREG_ORG", "TREG_URL", "TREG_SESSION_ID"} & set(env)
    assert env["TREG_TELEMETRY"] == "0"
    assert env["DO_NOT_TRACK"] == "1"
    assert env["TREG_CLIENT"] == treg_service.CLIENT_LABEL == "opencompany"


def test_browser_command_opens_nothing(monkeypatch, treg_home):
    """``treg login`` opens its page with ``webbrowser``; the ``BROWSER`` we set
    must be the command it picks, and running that command must do nothing
    and succeed. ``webbrowser.open`` itself is never called here."""
    value = treg_service.treg_env()["BROWSER"]
    monkeypatch.setenv("BROWSER", value)
    monkeypatch.setattr(webbrowser, "_tryorder", None)
    monkeypatch.setattr(webbrowser, "_browsers", {})

    browser = webbrowser.get()
    assert isinstance(browser, webbrowser.GenericBrowser)
    assert browser.name == Path(sys.executable).as_posix()
    assert browser.args == ["-c", "pass", "%s"]
    # What webbrowser would run, URL substituted: Python doing nothing, exit 0.
    cmdline = [browser.name, *[arg.replace("%s", LINK) for arg in browser.args]]
    assert subprocess.run(cmdline, capture_output=True, timeout=60).returncode == 0
    assert shlex.split(value)[0] == browser.name


async def test_run_treg_adds_json_closes_stdin_and_runs_from_its_folder(monkeypatch, treg_home):
    calls, fake_run = _recorder(_ok({"logged_in": False}))
    monkeypatch.setattr(treg_service, "run_cli_command", fake_run)
    await treg_service.run_treg(Path("treg"), ["call", "x.y"], timeout=12.0, body=b"{}")

    (call,) = calls
    assert call["argv"] == ["--json", "call", "x.y"]
    assert call["stdin"] == asyncio.subprocess.PIPE
    assert call["input"] == b"{}"
    assert call["cwd"] == str(treg_home)
    assert call["env"]["TREG_CONFIG"] == str(treg_home / "config.json")
    assert call["timeout"] == 12.0


async def test_spawned_sign_in_closes_stdin_and_merges_its_output(monkeypatch, treg_home):
    captured = {}

    class _Stdin:
        closed = False

        def close(self):
            self.closed = True

    class _Proc:
        stdin = _Stdin()

    async def fake_exec(*argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return _Proc()

    monkeypatch.setattr(treg_service.asyncio, "create_subprocess_exec", fake_exec)
    proc = await treg_service.spawn_treg(Path("treg"), ["login"])

    assert captured["argv"] == ("treg", "login")
    assert captured["stdin"] == asyncio.subprocess.PIPE
    assert proc.stdin.closed is True
    assert captured["stderr"] == asyncio.subprocess.STDOUT
    assert captured["env"]["TREG_CONFIG"] == str(treg_home / "config.json")
    assert captured["cwd"] == str(treg_home)


async def test_signed_in_account_reads_the_cli_config(monkeypatch, treg_home):
    config = {"base_url": "https://treg.to", "email": "owner@example.com", "active_org": "acme", "logged_in": True}
    calls, fake_run = _recorder(_ok(config))
    monkeypatch.setattr(treg_service, "run_cli_command", fake_run)
    assert await treg_service.signed_in_account(Path("treg")) == ACCOUNT
    assert calls[0]["argv"] == ["--json", "config"]

    _, signed_out = _recorder(_ok({**config, "logged_in": False}))
    monkeypatch.setattr(treg_service, "run_cli_command", signed_out)
    assert await treg_service.signed_in_account(Path("treg")) is None


async def test_verified_account_needs_whoami_to_name_a_team(monkeypatch, treg_home):
    whoami = {"email": "owner@example.com", "team": "acme", "role": "owner", "registry": "https://treg.to"}
    calls, fake_run = _recorder(_ok(whoami))
    monkeypatch.setattr(treg_service, "run_cli_command", fake_run)
    assert await treg_service.verified_account(Path("treg")) == {**ACCOUNT, "role": "owner"}
    assert calls[0]["argv"] == ["--json", "whoami"]

    for reply in (_failed(), _ok({**whoami, "team": None})):
        _, fake_run = _recorder(reply)
        monkeypatch.setattr(treg_service, "run_cli_command", fake_run)
        assert await treg_service.verified_account(Path("treg")) is None


# --- install ------------------------------------------------------------------


class _FakeUv:
    def __init__(self, on_run=None, returncode=0, stderr=b""):
        self.on_run = on_run
        self.returncode = returncode
        self.stderr = stderr

    async def communicate(self):
        if self.on_run:
            self.on_run()
        return b"", self.stderr


def _tool_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "bin"))
    monkeypatch.setenv("OPENCOMPANY_UV_BIN", str(tmp_path / "uv"))
    return treg_install.TregInstaller()


async def test_install_pins_the_package_into_the_given_tool_folders(monkeypatch, tmp_path):
    installer = _tool_folders(monkeypatch, tmp_path)
    cli = installer.paths()["cli"]
    runs = []

    async def fake_exec(*argv, **kwargs):
        runs.append((argv, kwargs))
        return _FakeUv(on_run=lambda: cli.write_text("treg"))

    monkeypatch.setattr(treg_install.asyncio, "create_subprocess_exec", fake_exec)
    assert installer.installed() is None
    assert await installer.ensure(wait=5) == cli

    ((argv, kwargs),) = runs
    assert argv[0] == str(tmp_path / "uv")
    assert list(argv[1:4]) == ["tool", "install", "--reinstall"]
    assert argv[-1] == treg_install.SPEC == "tools-registry==0.30.0"
    assert kwargs["env"]["UV_TOOL_DIR"] == str(tmp_path / "tools")
    assert kwargs["env"]["UV_TOOL_BIN_DIR"] == str(tmp_path / "bin")
    assert "VIRTUAL_ENV" not in kwargs["env"]
    assert installer.installed() == cli
    # A second ensure finds it installed and runs nothing.
    assert await installer.ensure(wait=5) == cli
    assert len(runs) == 1


async def test_install_defaults_to_opencompany_s_own_package_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.delenv("UV_TOOL_BIN_DIR", raising=False)
    import core.paths

    monkeypatch.setattr(core.paths, "package_dir", lambda name: tmp_path / "packages" / name)
    paths = treg_install.TregInstaller().paths()
    assert paths["tool_dir"] == tmp_path / "packages" / "treg" / "tools"
    assert paths["bin_dir"] == tmp_path / "packages" / "treg" / "bin"


def test_a_different_pin_counts_as_not_installed(monkeypatch, tmp_path):
    installer = _tool_folders(monkeypatch, tmp_path)
    paths = installer.paths()
    paths["bin_dir"].mkdir(parents=True)
    paths["tool_dir"].mkdir(parents=True)
    paths["cli"].write_text("treg")
    paths["stamp"].write_text(json.dumps({"spec": "tools-registry==0.29.0"}), encoding="utf-8")
    assert installer.installed() is None
    paths["stamp"].write_text(json.dumps({"spec": treg_install.SPEC}), encoding="utf-8")
    assert installer.installed() == paths["cli"]


async def test_install_without_uv_needs_the_owner(monkeypatch, tmp_path):
    installer = _tool_folders(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENCOMPANY_UV_BIN")
    monkeypatch.setattr(treg_install.shutil, "which", lambda name: None)
    with pytest.raises(NodeUserError) as raised:
        await installer.ensure(wait=5)
    assert "uv" in str(raised.value)
    assert raised.value.requires_user_action is True


async def test_concurrent_callers_share_one_install(monkeypatch, tmp_path):
    installer = _tool_folders(monkeypatch, tmp_path)
    cli = installer.paths()["cli"]
    release = asyncio.Event()
    runs = []

    class _SlowUv(_FakeUv):
        async def communicate(self):
            await release.wait()
            cli.write_text("treg")
            return b"", b""

    async def fake_exec(*argv, **kwargs):
        runs.append(argv)
        return _SlowUv()

    monkeypatch.setattr(treg_install.asyncio, "create_subprocess_exec", fake_exec)
    first = asyncio.create_task(installer.ensure(wait=5))
    second = asyncio.create_task(installer.ensure(wait=5))
    await _until(lambda: runs)
    release.set()
    assert await first == cli and await second == cli
    assert len(runs) == 1


async def test_a_failed_install_reports_uv_s_words(monkeypatch, tmp_path):
    installer = _tool_folders(monkeypatch, tmp_path)

    async def fake_exec(*argv, **kwargs):
        return _FakeUv(returncode=2, stderr=b"error: no network")

    monkeypatch.setattr(treg_install.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(NodeUserError, match="no network"):
        await installer.ensure(wait=5)
    # The cooldown answers at once instead of starting another install.
    with pytest.raises(NodeUserError, match="Retrying in"):
        await installer.ensure(wait=5)


# --- sign-in relay -------------------------------------------------------------


class _LoginProc:
    """Just enough of asyncio.subprocess.Process for ``treg login``."""

    def __init__(self, output: str = ""):
        self.pid = 4242
        self.returncode = None
        self.stdout = asyncio.StreamReader()
        self._exited = asyncio.Event()
        if output:
            self.stdout.feed_data(output.encode())

    def finish(self, code: int = 0, output: str = "") -> None:
        if output:
            self.stdout.feed_data(output.encode())
        self.stdout.feed_eof()
        self.returncode = code
        self._exited.set()

    async def wait(self) -> int:
        await self._exited.wait()
        return self.returncode


class _Installer:
    def __init__(self, cli: Path, *, installed: bool = True, install=None):
        self.cli = cli
        self._installed = installed
        self._install = install

    def installed(self):
        return self.cli if self._installed else None

    def paths(self):
        return {"cli": self.cli}

    async def ensure(self, *, wait):
        if not self._installed and self._install is not None:
            await self._install()
            self._installed = True
        return self.cli


@pytest.fixture
def relay(monkeypatch, tmp_path):
    """Handlers wired to fakes: an installed CLI, no accepted key, and recorded marker changes."""
    cli = tmp_path / "treg.exe"
    cli.write_text("treg")
    monkeypatch.setattr(treg_handlers, "_login_task", None)
    monkeypatch.setattr(treg_handlers, "_login_state", {})
    monkeypatch.setattr(treg_handlers, "get_installer", lambda: _Installer(cli))
    events = []

    async def connect(account):
        events.append(("connected", account))

    async def disconnect():
        events.append(("disconnected",))

    async def no_key(binary):
        return None

    monkeypatch.setattr(treg_handlers, "_connect", connect)
    monkeypatch.setattr(treg_handlers, "_disconnect", disconnect)
    monkeypatch.setattr(treg_handlers, "verified_account", no_key)
    killed = []
    monkeypatch.setattr(treg_handlers, "kill_tree", killed.append)
    return {"cli": cli, "events": events, "killed": killed, "monkeypatch": monkeypatch}


def _spawning(relay, proc):
    """``treg login`` spawns ``proc``; killing it ends it, as a real kill would."""
    spawned = []

    async def fake_spawn(binary, argv):
        spawned.append(argv)
        return proc

    def fake_kill(pid):
        relay["killed"].append(pid)
        if proc.returncode is None:
            proc.finish(-9)

    relay["monkeypatch"].setattr(treg_handlers, "spawn_treg", fake_spawn)
    relay["monkeypatch"].setattr(treg_handlers, "kill_tree", fake_kill)
    return spawned


def _signed_in(relay, account):
    async def read_config(binary):
        return account

    relay["monkeypatch"].setattr(treg_handlers, "signed_in_account", read_config)


async def test_login_relays_the_link_and_code_then_marks_once_treg_exits(relay):
    proc = _LoginProc(SIGN_IN_OUTPUT)
    spawned = _spawning(relay, proc)
    _signed_in(relay, ACCOUNT)

    res = await treg_handlers.handle_treg_login({}, websocket=None)
    assert res["success"] is True and res["pending"] is True and res["connected"] is False
    assert res["url"] == LINK
    assert res["verification_code"] == "R658"
    assert spawned == [["login"]]

    # A repeat click while the owner signs in returns the same link.
    again = await treg_handlers.handle_treg_login({}, websocket=None)
    assert again["verification_code"] == "R658"
    assert len(spawned) == 1
    # The modal's poll sees the same state.
    polled = await treg_handlers.handle_treg_status({}, websocket=None)
    assert polled["pending"] is True and polled["url"] == LINK

    proc.finish(0, "✓ Logged in as owner@example.com. Active org: acme\n")
    await _until(lambda: not treg_handlers._in_progress())
    assert relay["events"] == [("connected", ACCOUNT)]
    done = treg_handlers._response()
    assert done["connected"] is True and done["pending"] is False
    assert "owner@example.com" in done["message"]
    assert relay["killed"] == []


async def test_login_skips_the_sign_in_when_treg_accepts_the_key(relay):
    async def accepted(binary):
        return {**ACCOUNT, "role": "owner"}

    relay["monkeypatch"].setattr(treg_handlers, "verified_account", accepted)
    spawned = _spawning(relay, _LoginProc())
    res = await treg_handlers.handle_treg_login({}, websocket=None)
    assert res["connected"] is True and res["pending"] is False
    assert spawned == []
    assert relay["events"] == [("connected", {**ACCOUNT, "role": "owner"})]


async def test_login_without_a_code_says_treg_could_not_start_one(relay):
    # treg prints a link without a code when it cannot reach treg.to.
    proc = _LoginProc("If it doesn't open, visit:\n  https://treg.to/login?cli=local123\n")
    _spawning(relay, proc)

    res = await treg_handlers.handle_treg_login({}, websocket=None)
    assert res["success"] is False
    assert "could not start a sign-in" in res["error"]
    assert relay["killed"] == [4242]
    assert relay["events"] == []


async def test_a_sign_in_nobody_finishes_is_ended(relay):
    proc = _LoginProc(SIGN_IN_OUTPUT)
    _spawning(relay, proc)
    relay["monkeypatch"].setattr(treg_handlers, "_SIGN_IN_DEADLINE_SECONDS", 0.05)

    await treg_handlers.handle_treg_login({}, websocket=None)
    await _until(lambda: not treg_handlers._in_progress())
    res = treg_handlers._response()
    assert res["success"] is False
    assert "not finished in time" in res["error"]
    assert relay["killed"] == [4242]


async def test_a_failed_sign_in_shows_treg_s_last_line(relay):
    proc = _LoginProc(SIGN_IN_OUTPUT)
    _spawning(relay, proc)
    await treg_handlers.handle_treg_login({}, websocket=None)
    proc.finish(1, "Login timed out — run `treg login` again.\n")
    await _until(lambda: not treg_handlers._in_progress())
    res = treg_handlers._response()
    assert res["success"] is False
    assert "Login timed out" in res["error"]
    assert relay["events"] == []


async def test_first_use_installs_before_any_sign_in(relay):
    release = asyncio.Event()

    async def slow_install():
        await release.wait()

    relay["monkeypatch"].setattr(treg_handlers, "get_installer", lambda: installer)
    installer = _Installer(relay["cli"], installed=False, install=slow_install)
    relay["monkeypatch"].setattr(treg_handlers, "_RESPONSE_WAIT_SECONDS", 0.05)
    proc = _LoginProc(SIGN_IN_OUTPUT)
    spawned = _spawning(relay, proc)

    res = await treg_handlers.handle_treg_login({}, websocket=None)
    assert res["pending"] is True
    assert "Installing" in res["message"]
    assert spawned == []
    release.set()
    await _until(lambda: spawned == [["login"]])
    await _until(lambda: treg_handlers._response().get("verification_code") == "R658")
    await treg_handlers.shutdown_login()


async def test_a_cli_that_ignores_the_kill_cannot_hold_up_a_cancel(relay):
    proc = _LoginProc(SIGN_IN_OUTPUT)
    _spawning(relay, proc)
    # A kill that does not take: the process never exits.
    relay["monkeypatch"].setattr(treg_handlers, "kill_tree", relay["killed"].append)
    relay["monkeypatch"].setattr(treg_handlers, "_REAP_SECONDS", 0.05)
    await treg_handlers.handle_treg_login({}, websocket=None)
    await asyncio.wait_for(treg_handlers.shutdown_login(), timeout=2)
    assert relay["killed"] == [4242]
    assert not treg_handlers._in_progress()


async def test_status_keeps_the_marker_in_step_with_the_cli(relay):
    marked = {"value": False}

    async def is_marked():
        return marked["value"]

    relay["monkeypatch"].setattr(treg_handlers, "_marked", is_marked)
    _signed_in(relay, ACCOUNT)
    res = await treg_handlers.handle_treg_status({}, websocket=None)
    assert res["connected"] is True
    assert relay["events"] == [("connected", ACCOUNT)]

    # Already marked: nothing to broadcast.
    marked["value"] = True
    await treg_handlers.handle_treg_status({}, websocket=None)
    assert len(relay["events"]) == 1

    # The CLI was signed out elsewhere: the stale marker goes.
    _signed_in(relay, None)
    res = await treg_handlers.handle_treg_status({}, websocket=None)
    assert res["connected"] is False
    assert relay["events"][-1] == ("disconnected",)


async def test_logout_cancels_a_sign_in_signs_the_cli_out_and_drops_the_marker(relay):
    proc = _LoginProc(SIGN_IN_OUTPUT)
    _spawning(relay, proc)
    calls, fake_run = _recorder(_ok(stdout="Logged out."))
    relay["monkeypatch"].setattr(treg_handlers, "run_treg", lambda binary, argv, **kw: fake_run(binary=binary, argv=argv, **kw))

    await treg_handlers.handle_treg_login({}, websocket=None)
    assert treg_handlers._in_progress()
    res = await treg_handlers.handle_treg_logout({}, websocket=None)
    assert res == {"success": True, "connected": False, "pending": False}
    assert not treg_handlers._in_progress()
    assert relay["killed"] == [4242]
    assert [call["argv"] for call in calls] == [["logout"]]
    assert relay["events"] == [("disconnected",)]


def test_the_link_is_never_logged():
    for line in inspect.getsource(treg_handlers).splitlines():
        if "logger." in line:
            assert "link" not in line and "url" not in line and "LINK" not in line, line


def test_ws_handlers_and_shutdown_hook_are_registered():
    from services.plugin.shutdown_hooks import registered_labels
    from services.ws_handler_registry import get_ws_handlers

    assert set(WS_HANDLERS) == {"treg_login", "treg_status", "treg_logout"}
    assert set(WS_HANDLERS) <= set(get_ws_handlers())
    assert "treg_login" in registered_labels()


# --- the node: registration ----------------------------------------------------


def test_node_class_attributes():
    from services.node_registry import get_node_class

    cls = get_node_class("tregAction")
    assert cls is TregActionNode
    assert cls.tool_name == "treg_action"
    assert tuple(cls.group) == ("api", "tool")
    assert cls.usable_as_tool is True
    assert TregCredential in tuple(cls.credentials)
    # usable_as_tool hides both main handles unless the class keeps them.
    assert cls.__dict__["hide_input_handle"] is False
    assert cls.__dict__["hide_output_handle"] is False


def test_operations_match_the_literal_and_default_to_a_free_read():
    operations = set(typing.get_args(TregActionParams.model_fields["operation"].annotation))
    assert operations == set(TregActionNode._operations)
    assert TregActionParams().operation == "search"


def test_tool_schema_is_flat():
    from services.plugin.tool import inline_schema_refs

    dumped = json.dumps(inline_schema_refs(TregActionParams.model_json_schema()))
    assert "$defs" not in dumped and '"$ref"' not in dumped
    # ParameterRenderer keys magic on these literal names.
    assert not {"model", "api_key", "parameters", "action"} & set(TregActionParams.model_fields)


def test_panel_blanks_fall_back_to_defaults():
    assert TregActionParams(limit="").limit == 10


# --- the node: operations ------------------------------------------------------


SEARCH_ANSWER = {
    "query": "google results",
    "count": 2,
    "total": 14,
    "engine": "agent",
    "verdict": "strong",
    "reason": "Serper returns Google results.",
    "results": [
        {
            "id": "serper.web.search",
            "provider": "serper",
            "provider_display": "Serper",
            "name": "Google web search",
            "summary": "Google organic results for a query.",
            "kind": "data",
            "cost": {"type": "per_call", "usd": 0.001, "source": "serper.dev", "checked": "2026-10-01"},
            "async": None,
            "observed": {"samples": 900},
        },
        {
            "id": "treg.google.serp.organic",
            "provider": "treg",
            "name": "Google organic results",
            "kind": "routed",
            "cost": {"varies": True},
            "async": None,
        },
    ],
}

GET_ANSWER = {
    "endpoint": {
        "id": "serper.web.search",
        "provider": "serper",
        "provider_display": "Serper",
        "name": "Google web search",
        "summary": "Google organic results for a query.",
        "method": "POST",
        "cost": {"type": "per_call", "usd": 0.001},
        "async": None,
        "docs_url": "https://serper.dev",
        "input": {
            "queryParams": {"gl": {"type": "string", "required": False, "example": "us"}},
            "body": {"properties": {"q": {"type": "string", "required": True, "example": "coffee"}}},
            "note": "One query per call.",
        },
        "test_request": {"body": {"q": "coffee"}},
    },
    "example_response": {"organic": [{"title": "a"}, {"title": "b"}, {"title": "c"}]},
    "call_template": "treg call serper.web.search --method POST --data '{\"q\":\"coffee\"}'",
}


@pytest.fixture
def node_cli(monkeypatch, tmp_path):
    """The node runs a fake installed CLI; returns ``set_replies``."""
    cli = tmp_path / "treg.exe"
    monkeypatch.setattr(treg_action_mod, "get_installer", lambda: _Installer(cli))
    state = {}

    def set_replies(*replies):
        calls, fake = _recorder(*replies)

        async def run(binary, argv, *, timeout, body=None):
            return await fake(binary=binary, argv=argv, timeout=timeout, body=body)

        monkeypatch.setattr(treg_action_mod, "run_treg", run)
        state["calls"] = calls
        return calls

    return set_replies


def _node_ctx(tmp_path):
    from services.plugin import NodeContext

    return NodeContext(node_id="treg1", node_type="tregAction", workspace_dir=str(tmp_path))


async def test_search_runs_the_catalog_search_and_trims_the_rows(node_cli, tmp_path):
    calls = node_cli(_ok(SEARCH_ANSWER))
    out = await TregActionNode().search(_node_ctx(tmp_path), TregActionParams(query=" google results ", limit=5))

    assert calls[0]["argv"] == ["catalog", "search", "google results", "--limit=5"]
    assert out["query"] == "google results" and out["count"] == 2 and out["total"] == 14
    assert out["verdict"] == "strong"
    assert out["tools"][0] == {
        "id": "serper.web.search",
        "name": "Google web search",
        "summary": "Google organic results for a query.",
        "provider": "Serper",
        "price": {"type": "per_call", "usd": 0.001},
    }
    assert out["tools"][1]["routed"] is True and out["tools"][1]["price"] == {"varies": True}


@pytest.mark.parametrize("query", ["", "   ", "--org=other-team"])
async def test_search_refuses_an_empty_or_dashed_query_before_running_the_cli(node_cli, tmp_path, query):
    calls = node_cli(_ok(SEARCH_ANSWER))
    with pytest.raises(NodeUserError):
        await TregActionNode().search(_node_ctx(tmp_path), TregActionParams(query=query))
    assert calls == []


async def test_describe_lists_parameters_with_where_each_goes(node_cli, tmp_path):
    calls = node_cli(_ok(GET_ANSWER))
    out = await TregActionNode().describe(_node_ctx(tmp_path), TregActionParams(operation="describe", tool_id="serper.web.search"))

    assert calls[0]["argv"] == ["catalog", "get", "serper.web.search"]
    assert out["method"] == "POST"
    assert out["price"] == {"type": "per_call", "usd": 0.001}
    assert out["parameters"] == [
        {"name": "gl", "in": "query", "type": "string", "required": False, "example": "us"},
        {"name": "q", "in": "body", "type": "string", "required": True, "example": "coffee"},
    ]
    assert out["parameters_note"] == "One query per call."
    assert out["example_request"] == {"body": {"q": "coffee"}}
    # The example answer is previewed: the first element of each list.
    assert out["example_response"] == {"organic": [{"title": "a"}, "... 2 more"]}


async def test_describe_of_a_job_returns_treg_s_providers(node_cli, tmp_path):
    job = {"job": "google.serp.organic", "does": "Google organic results", "providers": 6}
    node_cli(_ok(job))
    out = await TregActionNode().describe(_node_ctx(tmp_path), TregActionParams(operation="describe", tool_id="google.serp.organic"))
    assert out["job"] == job


@pytest.mark.parametrize("tool_id", ["", "../auth/me", "a/b", "x?y=1", "-h", "https://api.example.com/v1"])
async def test_describe_refuses_what_is_not_a_tool_id(node_cli, tmp_path, tool_id):
    calls = node_cli(_ok(GET_ANSWER))
    with pytest.raises(NodeUserError):
        await TregActionNode().describe(_node_ctx(tmp_path), TregActionParams(operation="describe", tool_id=tool_id))
    assert calls == []


async def test_an_unknown_tool_is_reported_in_treg_s_words(node_cli, tmp_path):
    body = {"detail": {"error": "unknown endpoint", "hint": "search first", "did_you_mean": ["serper.web.search"]}}
    node_cli(_failed(stdout=json.dumps(body), stderr=""))
    with pytest.raises(NodeUserError) as raised:
        await TregActionNode().describe(_node_ctx(tmp_path), TregActionParams(operation="describe", tool_id="serper.web.serch"))
    assert "unknown endpoint" in str(raised.value)
    assert "serper.web.search" in str(raised.value)


async def test_balance_reads_micro_usd_and_holds(node_cli, tmp_path):
    answer = {
        "org_id": 7,
        "balance_micro": 933920,
        "balance_usd": "0.93",
        "holds": [{"call_id": "c1", "endpoint_id": "piapi.video.generate", "amount_micro": 250000}],
        "entries": {"items": []},
    }
    calls = node_cli(_ok(answer))
    out = await TregActionNode().balance(_node_ctx(tmp_path), TregActionParams(operation="balance"))
    assert calls[0]["argv"] == ["balance", "--limit=1"]
    assert out["balance_usd"] == 0.93392
    assert out["holds"] == [{"tool_id": "piapi.video.generate", "reserved_usd": 0.25, "call_id": "c1"}]


async def test_balance_without_a_sign_in_points_at_credentials(node_cli, tmp_path):
    node_cli(_failed(stderr="treg: not signed in, or this token is invalid/expired.\n  Sign in:  treg login"))
    with pytest.raises(NodeUserError) as raised:
        await TregActionNode().balance(_node_ctx(tmp_path), TregActionParams(operation="balance"))
    assert "Sign in" in str(raised.value)
    assert "Credentials" in raised.value.hint


async def test_execute_dispatches_and_returns_the_envelope(node_cli, tmp_path):
    node_cli(_ok(SEARCH_ANSWER))
    result = await TregActionNode().execute("treg1", {"operation": "search", "query": "google results"}, _node_ctx(tmp_path))
    assert result["success"] is True
    assert result["result"]["tools"][0]["id"] == "serper.web.search"


# --- credential, catalogue card, icon -----------------------------------------


async def test_credential_is_a_marker_read_from_the_oauth_store():
    class _Auth:
        def __init__(self, tokens):
            self.tokens = tokens

        async def get_oauth_tokens(self, provider):
            assert provider == "treg"
            return self.tokens

    assert TregCredential.id == "treg"
    assert TregCredential.auth == "custom"
    assert await TregCredential.resolve() == {}
    assert await TregCredential.is_configured(_Auth({"access_token": "cli-managed"}), {}) is True
    assert await TregCredential.is_configured(_Auth(None), {}) is False


def test_catalogue_card_is_a_cli_sign_in():
    provider = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))["providers"]["treg"]
    assert provider["kind"] == "oauth"
    assert provider["ws"] == {"login": "treg_login", "logout": "treg_logout", "status": "treg_status"}
    # No fields: nothing is typed in, and a required one would disable Login.
    assert "fields" not in provider
    assert provider["usage_service"] == "treg"
    assert provider["consumer_category"] == "research"
    assert provider["icon_ref"] == "/api/schemas/credentials/treg/icon"


def test_credential_icon_sits_beside_the_credential():
    assert TregCredential.get_icon_path() == (PLUGIN_DIR / "treg.svg").resolve()
