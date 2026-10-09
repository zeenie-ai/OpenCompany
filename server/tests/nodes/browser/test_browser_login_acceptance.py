"""Opt-in controlled local-browser acceptance; never uses external accounts.

BROWSER_ACCEPTANCE_LIVE=1 runs installed Chrome and the already provisioned
browser-use CLI. It does not install software or read real 1Password values.
"""
from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs
from unittest.mock import AsyncMock, patch

import pytest

from nodes.browser._chrome import ChromeProcess
from nodes.browser._cli import BrowserUseCli
from nodes.browser._credentials import fill_credentials
from nodes.browser._egress import EgressProxy
from services.netpolicy import NetPolicy
from nodes.browser._runtime import ProfileRuntime
from nodes.browser._session import ProfileController
from nodes.browser._stream import ScreencastHub, Viewer
from nodes.browser._system_browser import discover_browser, browser_version, version_major
from nodes.browser._webmcp import WebMcpTracker


@pytest.mark.skipif(os.environ.get("BROWSER_ACCEPTANCE_LIVE") != "1", reason="Opt-in installed-browser acceptance")
@pytest.mark.parametrize("newline", [False, True], ids=["exact-whitespace", "reject-browser-normalization"])
async def test_controlled_login_preserves_secret_and_drains_live_capture(tmp_path, newline):
    repository = Path(__file__).resolve().parents[4]
    package = repository / ".opencompany" / "packages" / "browser-use"
    exe = "browser-use.exe" if os.name == "nt" else "browser-use"
    cli_path = Path(os.environ.get("BROWSER_ACCEPTANCE_CLI", str(package / "bin" / exe)))
    python_path = package / "tools" / "browser-use" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not cli_path.is_file() or not python_path.is_file():
        pytest.skip("Provision the pinned browser-use CLI before live acceptance")
    executable, _ = discover_browser(os.environ.get("BROWSER_CHROME_PATH", ""))
    major = version_major(await browser_version(executable))
    canary = " secret-Σ-private-login" + ("\n" if newline else "") + " "
    accepted = []
    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            document = b'<form action="/account" method="POST"><label for="u">Username</label><input id="u" name="u" autocomplete="off"><label for="p">Password</label><input id="p" name="p" type="password" autocomplete="off"><button id="s" type="submit">Sign in</button></form>'
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(document)
        def do_POST(self):
            values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode(), keep_blank_values=True)
            accepted.append(values.get("p") == [canary] and values.get("u") == [" user "])
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(b'<h1 id="account">Account</h1>')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    profile_id = "acceptance-newline" if newline else "acceptance-whitespace"
    proxy = EgressProxy(lambda: NetPolicy(), name=f"browser-acceptance-proxy-{profile_id}")
    proxy_port = await asyncio.to_thread(proxy.start)
    root = tmp_path / "profile"; root.mkdir()
    user_data = root / "user-data"; user_data.mkdir()
    chrome = ChromeProcess(profile_id=profile_id, exe=executable, profile_root=root, user_data_dir=user_data,
                           proxy_port=proxy_port, major=major, no_sandbox=False, small_shm=False)
    homes = {key: tmp_path / key for key in ("home", "runtime", "tmp", "workspace", "config")}
    for home in homes.values(): home.mkdir()
    cli = BrowserUseCli(cli_path=cli_path, python_path=python_path, profile_id=profile_id, cdp_http_url="")
    cli.dirs = lambda: homes
    hub = None
    try:
        cdp = await chrome.launch()
        cli.cdp_http_url = f"http://127.0.0.1:{chrome.port}"
        target = (await cdp.page_targets())[0]["targetId"]
        await cli.run("navigate", {"url": origin + "/login", "_target_id": target, "timeout": 25}, timeout=30)
        snapshot = await cli.run("snapshot", {"_target_id": target, "max_chars": 20000, "interactive_only": True, "timeout": 25}, timeout=30)
        assert snapshot.ok
        controller = ProfileController(profile_id, "Controlled fixture")
        controller.active_target_id = target
        controller.tabs[target] = {"target_id": target, "url": origin + "/login", "title": "Fixture"}
        controller.refs[target] = snapshot.value["refs"]
        runtime = ProfileRuntime(profile=SimpleNamespace(id=profile_id), controller=controller, chrome=chrome,
                                 proxy=proxy, cdp=cdp, cli=cli, webmcp=WebMcpTracker())
        page = await runtime.page_session(target)
        await runtime.webmcp.attach(target, page)
        document = await page.send("DOM.getDocument")
        chosen = []
        for selector in ("#u", "#p", "#s"):
            node = await page.send("DOM.querySelector", {"nodeId": document["root"]["nodeId"], "selector": selector})
            detail = await page.send("DOM.describeNode", {"nodeId": node["nodeId"]})
            backend = detail["node"]["backendNodeId"]
            chosen.append(next(ref for ref, value in snapshot.value["refs"].items() if value == backend))
        hub = ScreencastHub(runtime); runtime.hub = hub
        viewer = Viewer(SimpleNamespace(send_text=AsyncMock(), send_bytes=AsyncMock()), "owner")
        await hub.add(viewer)
        assert hub._active
        metadata = {"origin": origin, "success_origin": origin, "success_path": "/account", "success_selector": "#account"}
        async def resolve(*_args, **_kwargs):
            assert controller.sensitive_login and viewer.privacy_blocked and not hub._active
            assert not (homes["runtime"] / "bu.pid").exists()
            return {**metadata, "username": " user ", "password": canary}
        auth = SimpleNamespace(get_browser_credential_binding=AsyncMock(return_value=metadata), resolve_browser_credentials=resolve)
        ctx = SimpleNamespace(user_id="owner", execution_id="task", workflow_id="wf", raw={})
        call = SimpleNamespace(credential_binding_id="fixture", username_ref=chosen[0], password_ref=chosen[1], submit_ref=chosen[2])
        with patch("core.container.container.auth_service", return_value=auth), patch("services.browser_owners.set_sensitive", AsyncMock()):
            result = await fill_credentials(ctx, runtime, call, timeout=15)
        if newline:
            assert result["success"] is False
            assert accepted == []
            assert controller.sensitive_login and viewer.privacy_blocked
        else:
            assert result == {"success": True}
            assert accepted == [True]
        assert canary not in str(result)
        for home in homes.values():
            for file in home.rglob("*"):
                if file.is_file(): assert canary.encode() not in file.read_bytes()
    finally:
        if hub is not None:
            for viewer in list(hub.viewers.values()): await hub.remove(viewer)
        cli.stop_daemon()
        await chrome.shutdown()
        await asyncio.to_thread(proxy.stop)
        await asyncio.to_thread(server.shutdown)
        server.server_close()
