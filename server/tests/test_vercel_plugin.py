"""Vercel plugin contract tests.

Locks the plugin's load-bearing seams: argv building (config-dir
pinning, token-in-env-never-argv), device-flow banner parsing, the
marker-token + CloudEvents broadcast contract (``inspect.getsource``
introspection, ``test_credential_broadcasts`` style), install
resolution order, the annotated PermissionError pre-flight, and the
catalogue entry shape.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

import nodes.vercel._handlers as vercel_handlers
import nodes.vercel._install as vercel_install
import nodes.vercel._service as vercel_service
import nodes.vercel.vercel_action as vercel_action_mod
from nodes.vercel import WS_HANDLERS
from nodes.vercel._credentials import VercelCredential
from nodes.vercel.vercel_action import VercelActionNode, VercelActionParams
from services.plugin import NodeContext
from services.plugin.base import NodeUserError

PLUGIN_DIR = Path(vercel_service.__file__).parent
CONFIG_PATH = Path(vercel_service.__file__).parents[2] / "config" / "credential_providers.json"


# --- _service helpers ---------------------------------------------------------


def test_global_argv_pins_config_dir_and_disables_color(monkeypatch, tmp_path):
    monkeypatch.setattr(vercel_service, "data_path", lambda name: tmp_path / name)
    argv = vercel_service.global_argv(["deploy", "--yes"])
    assert argv[:2] == ["deploy", "--yes"]
    assert "--global-config" in argv
    cfg = argv[argv.index("--global-config") + 1]
    assert cfg == str(tmp_path / "vercel")
    assert argv[-1] == "--no-color"


def test_vercel_env_puts_token_in_env_never_argv(monkeypatch, tmp_path):
    monkeypatch.setattr(vercel_service, "data_path", lambda name: tmp_path / name)
    env = vercel_service.vercel_env("vcp_secret123")
    assert env["VERCEL_TOKEN"] == "vcp_secret123"
    assert env["NO_COLOR"] == "1"
    # The token must never leak into argv (process lists are world-readable).
    assert all("vcp_secret123" not in part for part in vercel_service.global_argv(["whoami"]))
    assert "VERCEL_TOKEN" not in vercel_service.vercel_env(None)


def test_is_logged_in_sniffs_pinned_auth_json(monkeypatch, tmp_path):
    monkeypatch.setattr(vercel_service, "data_path", lambda name: tmp_path / name)
    assert vercel_service.is_logged_in() is False
    vercel_service.vercel_auth_path().write_text('{"token": "vcp_abc"}', encoding="utf-8")
    assert vercel_service.is_logged_in() is True


def test_extract_login_url_prefers_code_embedding_link():
    banner = (
        "\x1b[36m> Visit \x1b[1mhttps://vercel.com/oauth/device?user_code=ABCD-EFGH\x1b[0m"
        " to authenticate.\nDocs: https://vercel.com/docs/cli/login\n"
        "> Your code is \x1b[1mABCD-EFGH\x1b[0m"
    )
    assert vercel_service.extract_login_url(banner) == "https://vercel.com/oauth/device?user_code=ABCD-EFGH"
    assert vercel_service.extract_verification_code(banner) == "ABCD-EFGH"


def test_extract_login_url_falls_back_to_first_url():
    banner = "Visit https://vercel.com/device to log in."
    assert vercel_service.extract_login_url(banner) == "https://vercel.com/device"
    assert vercel_service.extract_login_url("no links here") is None


# --- _install resolution ------------------------------------------------------


async def test_ensure_vercel_cli_prefers_system_path(monkeypatch, tmp_path):
    fake = tmp_path / "vercel"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(vercel_install, "_cached_path", None)
    monkeypatch.setattr(vercel_install.shutil, "which", lambda name: str(fake))
    resolved = await vercel_install.ensure_vercel_cli()
    assert resolved == fake
    # cached for subsequent calls without re-resolving
    monkeypatch.setattr(vercel_install.shutil, "which", lambda name: None)
    assert await vercel_install.ensure_vercel_cli() == fake


def test_npm_spec_is_pinned():
    # `@latest` makes cold installs non-reproducible (whatsapp precedent).
    assert "@" in vercel_install._NPM_SPEC
    version = vercel_install._NPM_SPEC.rsplit("@", 1)[1]
    assert version and version != "latest"


# --- pre-flight / operations --------------------------------------------------


async def test_preflight_raises_annotated_permission_error(monkeypatch):
    async def no_token():
        return None

    monkeypatch.setattr(vercel_service, "stored_token", no_token)
    monkeypatch.setattr(vercel_service, "is_logged_in", lambda: False)
    node = VercelActionNode()
    with pytest.raises(PermissionError) as exc:
        await node._preflight()
    err = exc.value
    assert err.provider == "vercel"
    assert err.reason == "missing"
    assert err.auth == "oauth2"


async def test_deploy_builds_argv_env_and_cwd(monkeypatch, tmp_path):
    async def token():
        return "vcp_tok123"

    async def fake_ensure():
        return tmp_path / "vercel"

    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": None, "stdout": "https://my-app-abc123.vercel.app", "stderr": "Build ok", "error": None}

    monkeypatch.setattr(vercel_service, "stored_token", token)
    monkeypatch.setattr(vercel_service, "data_path", lambda name: tmp_path / name)
    monkeypatch.setattr(vercel_install, "ensure_vercel_cli", fake_ensure)
    monkeypatch.setattr(vercel_action_mod, "run_cli_command", fake_run)

    node = VercelActionNode()
    ctx = NodeContext(node_id="v1", node_type="vercelAction", workspace_dir=str(tmp_path))
    params = VercelActionParams(operation="deploy", prod=True, project="my-app", extra_args="--no-wait")
    out = await node.deploy(ctx, params)

    argv = captured["argv"]
    assert argv[:2] == ["deploy", "--yes"]
    assert "--prod" in argv
    assert argv[argv.index("--project") + 1] == "my-app"
    assert "--no-wait" in argv
    assert "--global-config" in argv and "--no-color" in argv
    assert captured["env"]["VERCEL_TOKEN"] == "vcp_tok123"
    assert captured["cwd"] == str(tmp_path)
    assert out["url"] == "https://my-app-abc123.vercel.app"
    assert out["operation"] == "deploy"
    assert out["success"] is True


async def test_deploy_missing_path_and_workspace_is_user_error(monkeypatch, tmp_path):
    async def token():
        return "vcp_tok123"

    monkeypatch.setattr(vercel_service, "stored_token", token)
    node = VercelActionNode()
    ctx = NodeContext(node_id="v1", node_type="vercelAction", workspace_dir=None)
    with pytest.raises(NodeUserError):
        await node.deploy(ctx, VercelActionParams(operation="deploy"))


async def test_cli_failure_surfaces_as_node_user_error(monkeypatch, tmp_path):
    async def token():
        return "vcp_tok123"

    async def fake_ensure():
        return tmp_path / "vercel"

    async def fake_run(**kwargs):
        return {"success": False, "stdout": "", "stderr": "Error: project not found", "error": "exit 1"}

    monkeypatch.setattr(vercel_service, "stored_token", token)
    monkeypatch.setattr(vercel_service, "data_path", lambda name: tmp_path / name)
    monkeypatch.setattr(vercel_install, "ensure_vercel_cli", fake_ensure)
    monkeypatch.setattr(vercel_action_mod, "run_cli_command", fake_run)

    node = VercelActionNode()
    ctx = NodeContext(node_id="v1", node_type="vercelAction", workspace_dir=str(tmp_path))
    with pytest.raises(NodeUserError, match="project not found"):
        await node.list_deployments(ctx, VercelActionParams(operation="list"))


async def test_custom_requires_command(monkeypatch, tmp_path):
    async def token():
        return "vcp_tok123"

    monkeypatch.setattr(vercel_service, "stored_token", token)
    node = VercelActionNode()
    ctx = NodeContext(node_id="v1", node_type="vercelAction", workspace_dir=str(tmp_path))
    with pytest.raises(NodeUserError):
        await node.custom(ctx, VercelActionParams(operation="custom", command="   "))


# --- login handler ------------------------------------------------------------


async def test_login_answers_within_budget_when_flow_stalls(monkeypatch):
    """Regression: the first-ever login pays a cold `npm install vercel`
    inside the handler, which blew past the frontend's 30s WS request
    timeout ("Request timeout: vercel_login") even though the login
    itself succeeded. The handler must answer within its response
    budget with a pending success and keep the flow going."""

    async def stalled_flow():
        await asyncio.sleep(30)
        return {"success": True, "url": "https://vercel.com/late"}

    monkeypatch.setattr(vercel_handlers, "_start_login_flow", stalled_flow)
    monkeypatch.setattr(vercel_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)
    res = await vercel_handlers.handle_vercel_login({}, websocket=None)
    assert res["success"] is True
    assert res.get("pending") is True


async def test_login_fast_path_returns_url(monkeypatch):
    async def instant_flow():
        return {"success": True, "url": "https://vercel.com/oauth/device?user_code=ABCD-EFGH", "verification_code": "ABCD-EFGH"}

    monkeypatch.setattr(vercel_handlers, "_start_login_flow", instant_flow)
    res = await vercel_handlers.handle_vercel_login({}, websocket=None)
    assert res["success"] is True
    assert "user_code=" in res["url"]


# --- marker-token + broadcast contract (source introspection) -----------------


def test_login_success_persists_marker_and_broadcasts():
    src = inspect.getsource(vercel_handlers._mark_logged_in)
    assert "store_oauth_tokens" in src
    assert 'provider="vercel"' in src
    complete = inspect.getsource(vercel_handlers._complete_login)
    assert "_mark_logged_in" in complete
    assert "credential.oauth.connected" in complete
    # exit code alone is never trusted — mtime advance gates success
    assert "pre_mtime" in complete and "is_logged_in" in complete


def test_logout_removes_marker_and_broadcasts():
    src = inspect.getsource(vercel_handlers.handle_vercel_logout)
    assert "_mark_logged_out" in src
    assert "credential.oauth.disconnected" in src
    marker = inspect.getsource(vercel_handlers._mark_logged_out)
    assert 'remove_oauth_tokens("vercel")' in marker


def test_ws_handlers_registered():
    assert set(WS_HANDLERS) == {"vercel_login", "vercel_logout", "vercel_status"}


# --- catalogue + assets -------------------------------------------------------


def test_catalogue_entry_shape():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "deployment" in config["categories"]
    vercel = config["providers"]["vercel"]
    assert vercel["kind"] == "oauth"
    assert vercel["category"] == "deployment"
    assert vercel["ws"] == {"login": "vercel_login", "logout": "vercel_logout", "status": "vercel_status"}
    (token_field,) = vercel["fields"]
    assert token_field["key"] == "vercel_token"
    # optional — must NOT gate the Login button (OAuthConnect gates on required fields only)
    assert token_field["required"] is False


def test_credential_class_shape():
    assert VercelCredential.id == "vercel"
    assert VercelCredential.auth == "custom"


def test_plugin_folder_assets():
    # Icon comes from the official lobehub brand set via visuals.json —
    # a co-located icon.svg would silently override it (first hit in
    # get_plugin_icon_path), so its absence is part of the contract.
    assert not (PLUGIN_DIR / "icon.svg").exists()
    assert not (PLUGIN_DIR / "icon.dark.svg").exists()
    meta = json.loads((PLUGIN_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["color"].startswith("#")

    visuals = json.loads((PLUGIN_DIR.parent / "visuals.json").read_text(encoding="utf-8"))
    entry = visuals["vercelAction"]
    assert entry["icon"] == "lobehub:Vercel"
    assert entry["skill"] == "vercel-skill"

    skill_md = PLUGIN_DIR.parents[1] / "skills" / "vercel" / "vercel-skill" / "SKILL.md"
    assert skill_md.exists()
