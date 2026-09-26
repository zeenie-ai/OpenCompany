"""Pure pieces of the browser runtime: scripts, CLI output, Chrome flags, env, migration."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from nodes.browser._chrome import build_chrome_argv, user_agent
from nodes.browser._cli import BrowserUnavailable, BrowserUseCli, parse_output
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
    with pytest.raises(RuntimeError):
        parse_output(f"{MARKER}:othernonce:" + json.dumps({"ok": True}), "", 0, nonce)


def test_missing_result_is_classified_by_cause():
    with pytest.raises(BrowserUnavailable):
        parse_output("", "browser-harness: fatal: BU_CDP_URL=... unreachable after 30s", 1, "n")
    with pytest.raises(RuntimeError) as err:
        parse_output("", "Traceback: some error", 1, "n")
    assert not isinstance(err.value, BrowserUnavailable)


def test_a_script_error_is_reported_not_raised():
    line = f"{MARKER}:n:" + json.dumps({"ok": False, "error": {"type": "stale_ref", "message": "gone"}})
    result = parse_output(line, "", 0, "n")
    assert (result.ok, result.error_type, result.error) == (False, "stale_ref", "gone")


def test_chrome_flags(tmp_path):
    argv = build_chrome_argv(Path("chrome"), user_data_dir=tmp_path, proxy_port=4000, major=154, no_sandbox=False, small_shm=False, platform="linux")
    assert "--headless=new" not in argv and "--remote-debugging-port=0" in argv
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


async def test_visible_browser_requires_linux_display(monkeypatch, tmp_path):
    from nodes.browser._chrome import ChromeProcess
    from services.plugin.base import NodeUserError

    monkeypatch.setattr("nodes.browser._chrome.sys.platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    chrome = ChromeProcess(profile_id="test", exe=Path("chrome"), profile_root=tmp_path, user_data_dir=tmp_path / "profile", proxy_port=1, major=154, no_sandbox=False, small_shm=False)
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
