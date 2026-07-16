"""Cloudflare plugin contract tests.

Locks the plugin's load-bearing seams under the CLI-owns-auth (Stripe /
gh) pattern: env builders (no token handling; login env strips ambient
Cloudflare credential vars so whoami reflects the OAuth session), the
login-banner URL parser (source-verified cf 0.2.0 output), install
resolution (pinned npm spec into the shared packages tree; system cf
never consulted), the marker-token + CloudEvents broadcast contract
(``inspect.getsource`` introspection, ``test_credential_broadcasts``
style), op argv building (verified against cf 0.2.0 ``--help-full``),
NDJSON recovery, and the catalogue entry shape (fieldless — cf owns the
token).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

import nodes.cloudflare._handlers as cf_handlers
import nodes.cloudflare._install as cf_install
import nodes.cloudflare._service as cf_service
import nodes.cloudflare.cloudflare_action as cf_action_mod
from nodes.cloudflare import WS_HANDLERS
from nodes.cloudflare._credentials import CloudflareCredential
from nodes.cloudflare.cloudflare_action import CloudflareActionNode, CloudflareActionParams
from services.plugin import NodeContext
from services.plugin.base import NodeUserError

PLUGIN_DIR = Path(cf_service.__file__).parent
CONFIG_PATH = Path(cf_service.__file__).parents[2] / "config" / "credential_providers.json"


# --- _service env builders ----------------------------------------------------


def test_cf_env_keeps_ambient_token_for_ops(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf_ambient")
    env = cf_service.cf_env()
    assert env["NO_COLOR"] == "1"
    # Ambient env token is left alone for ops — cf's documented precedence
    # (and the headless path on remote deployments).
    assert env["CLOUDFLARE_API_TOKEN"] == "cf_ambient"


def test_login_env_strips_ambient_credential_vars(monkeypatch):
    """With CLOUDFLARE_API_TOKEN set, `cf auth whoami` reports the env
    token (authSource: env) instead of the stored OAuth session — the
    login/status/logout paths must consult cf's OWN store."""
    for var in cf_service._AMBIENT_CREDENTIAL_VARS:
        monkeypatch.setenv(var, "ambient")
    env = cf_service.login_env()
    for var in cf_service._AMBIENT_CREDENTIAL_VARS:
        assert var not in env
    assert env["NO_COLOR"] == "1"


# --- login banner parsing (source-verified cf 0.2.0 output) ---------------------


def test_extract_login_url_from_browser_banner():
    # Live-captured cf 0.2.0 stderr shape.
    banner = (
        "Attempting to login via OAuth...\n"
        "Opening a link in your default browser: "
        "https://dash.cloudflare.com/oauth2/auth?response_type=code&client_id=abc&redirect_uri=http%3A%2F%2Flocalhost%3A8877%2Foauth%2Fcallback&scope=openid\n"
    )
    url = cf_service.extract_login_url(banner)
    assert url is not None
    assert url.startswith("https://dash.cloudflare.com/oauth2/auth?response_type=code")
    assert "8877" in url


def test_extract_login_url_from_headless_banner():
    # The no-browser variant carries the same URL.
    banner = "Visit this link to authenticate: https://dash.cloudflare.com/oauth2/auth?response_type=code&client_id=abc\n"
    assert cf_service.extract_login_url(banner) == (
        "https://dash.cloudflare.com/oauth2/auth?response_type=code&client_id=abc"
    )


def test_extract_login_url_waits_until_url_present():
    assert cf_service.extract_login_url("Attempting to login via OAuth...\n") is None
    assert cf_service.extract_login_url("") is None


def test_extract_login_url_strips_ansi():
    banner = "\x1b[36mOpening a link in your default browser:\x1b[0m https://dash.cloudflare.com/oauth2/auth?x=1\x1b[0m\n"
    assert cf_service.extract_login_url(banner) == "https://dash.cloudflare.com/oauth2/auth?x=1"


# --- _install resolution --------------------------------------------------------


def test_npm_spec_is_pinned():
    # Preview CLI: @latest would make cold installs non-reproducible and
    # silently change the argv surface this plugin was verified against.
    assert cf_install._NPM_SPEC.startswith("cf@")
    assert "latest" not in cf_install._NPM_SPEC


async def test_ensure_cf_cli_installs_into_shared_tree(monkeypatch, tmp_path):
    """The system-global cf is never consulted — the binary comes from
    the pinned npm install in the shared packages tree."""
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / ("cf.cmd" if cf_install.sys.platform == "win32" else "cf")

    calls = {"installs": 0}

    def fake_install():
        calls["installs"] += 1
        fake_bin.write_text("", encoding="utf-8")
        return fake_bin

    monkeypatch.setattr(cf_install, "_cached_path", None)
    monkeypatch.setattr(cf_install, "packages_dir", lambda: tmp_path)
    monkeypatch.setattr(cf_install, "_npm_install", fake_install)

    resolved = await cf_install.ensure_cf_cli()
    assert resolved == fake_bin
    assert calls["installs"] == 1
    # cached for subsequent calls without re-installing
    assert await cf_install.ensure_cf_cli() == fake_bin
    assert calls["installs"] == 1


def test_cf_binary_never_resolved_from_system_path():
    # Project-local contract: PATH lookup is allowed for npm itself,
    # never for the cf binary.
    src_install = inspect.getsource(cf_install)
    src_service = inspect.getsource(cf_service)
    assert 'which("cf")' not in src_install
    assert "which(" not in src_service
    # resolution goes through the shared-tree shim only
    assert "node_modules" in inspect.getsource(cf_install._shared_tree_bin)


# --- operations (no auth pre-flight — Stripe pattern) ---------------------------


def _wire(monkeypatch, tmp_path, run_impl):
    async def fake_ensure():
        return tmp_path / "cf"

    monkeypatch.setattr(cf_install, "ensure_cf_cli", fake_ensure)
    monkeypatch.setattr(cf_action_mod, "run_cli_command", run_impl)


def _ctx(tmp_path) -> NodeContext:
    return NodeContext(node_id="cf1", node_type="cloudflareAction", workspace_dir=str(tmp_path))


async def test_whoami_argv_and_env(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"authenticated": True, "email": "o@x.dev"}, "stdout": "{}", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()
    out = await node.whoami(_ctx(tmp_path), CloudflareActionParams(operation="whoami"))

    assert captured["argv"] == ["auth", "whoami"]
    # env comes from cf_env() — no token was resolved or injected anywhere
    assert captured["env"]["NO_COLOR"] == "1"
    assert out["result"] == {"authenticated": True, "email": "o@x.dev"}


async def test_zones_list_builds_filter_argv(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": [{"name": "example.com"}], "stdout": "[]", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    await node.zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert captured["argv"] == ["zones", "list"]

    out = await node.zones_list(
        _ctx(tmp_path),
        CloudflareActionParams(operation="zones_list", name_filter="example.com", account_id="acct1"),
    )
    argv = captured["argv"]
    assert argv[:2] == ["zones", "list"]
    assert argv[argv.index("--name") + 1] == "example.com"
    assert argv[argv.index("--account-id") + 1] == "acct1"
    assert out["result"] == [{"name": "example.com"}]


async def test_dns_records_list_requires_zone(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": [{"id": "r1"}], "stdout": "[]", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="zone"):
        await node.dns_records_list(_ctx(tmp_path), CloudflareActionParams(operation="dns_records_list"))

    await node.dns_records_list(_ctx(tmp_path), CloudflareActionParams(operation="dns_records_list", zone="example.com"))
    argv = captured["argv"]
    assert argv[:3] == ["dns", "records", "list"]
    assert argv[argv.index("--zone") + 1] == "example.com"


async def test_dns_record_create_validates_body_and_builds_argv(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"id": "new1"}, "stdout": "{}", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()
    body = '{"type":"A","name":"www","content":"192.0.2.1","ttl":1}'

    with pytest.raises(NodeUserError, match="record_body"):
        await node.dns_record_create(_ctx(tmp_path), CloudflareActionParams(operation="dns_record_create", zone="example.com"))

    with pytest.raises(NodeUserError, match="not valid JSON"):
        await node.dns_record_create(
            _ctx(tmp_path),
            CloudflareActionParams(operation="dns_record_create", zone="example.com", record_body="{oops"),
        )

    await node.dns_record_create(
        _ctx(tmp_path),
        CloudflareActionParams(operation="dns_record_create", zone="example.com", record_body=body),
    )
    argv = captured["argv"]
    assert argv[:3] == ["dns", "records", "create"]
    assert argv[argv.index("--zone") + 1] == "example.com"
    assert argv[argv.index("--body") + 1] == body


def test_record_body_coerces_llm_dict_args():
    # LLM tool calls may pass the record as a real JSON object.
    params = CloudflareActionParams(
        operation="dns_record_create",
        zone="example.com",
        record_body={"type": "A", "name": "www"},
    )
    assert json.loads(params.record_body) == {"type": "A", "name": "www"}


async def test_dns_record_delete_uses_positional_id(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"id": "r1"}, "stdout": "{}", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="record_id"):
        await node.dns_record_delete(_ctx(tmp_path), CloudflareActionParams(operation="dns_record_delete", zone="example.com"))

    await node.dns_record_delete(
        _ctx(tmp_path),
        CloudflareActionParams(operation="dns_record_delete", zone="example.com", record_id="r1"),
    )
    # cf 0.2.0: `cf dns records delete <dns-record-id>` — the id is positional.
    assert captured["argv"][:4] == ["dns", "records", "delete", "r1"]
    assert captured["argv"][captured["argv"].index("--zone") + 1] == "example.com"


async def test_cf_auth_error_surfaces_verbatim_as_node_user_error(monkeypatch, tmp_path):
    """No pre-flight: cf's own 'Not logged in' error IS the auth error."""

    async def fake_run(**kwargs):
        return {"success": False, "stdout": "", "stderr": "Not logged in. Run 'cf auth login' to authenticate.", "error": "exit 1"}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()
    with pytest.raises(NodeUserError, match="cf auth login"):
        await node.zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))


async def test_custom_requires_command_and_shlex_splits(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"ok": True}, "stdout": "{}", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError):
        await node.custom(_ctx(tmp_path), CloudflareActionParams(operation="custom", command="  "))

    await node.custom(
        _ctx(tmp_path),
        CloudflareActionParams(operation="custom", command="dns records update r1 --zone example.com --body '{\"content\":\"192.0.2.2\"}'"),
    )
    assert captured["argv"] == ["dns", "records", "update", "r1", "--zone", "example.com", "--body", '{"content":"192.0.2.2"}']
    assert captured["timeout"] == cf_action_mod._CUSTOM_TIMEOUT


def test_parse_ndjson_recovers_multi_object_stdout():
    ndjson = '{"id": 1}\n{"id": 2}\n'
    assert CloudflareActionNode._parse_ndjson(ndjson) == [{"id": 1}, {"id": 2}]
    # Single document and non-JSON text are not NDJSON.
    assert CloudflareActionNode._parse_ndjson('{"id": 1}') is None
    assert CloudflareActionNode._parse_ndjson("plain\ntext") is None


def test_shape_prefers_parsed_result_then_ndjson_then_stdout():
    shaped = CloudflareActionNode._shape("op", {"result": {"a": 1}, "stdout": '{"a": 1}', "stderr": ""})
    assert shaped["result"] == {"a": 1}
    assert "stdout" not in shaped

    shaped = CloudflareActionNode._shape("op", {"result": None, "stdout": '{"id": 1}\n{"id": 2}', "stderr": ""})
    assert shaped["result"] == [{"id": 1}, {"id": 2}]
    assert "stdout" not in shaped

    shaped = CloudflareActionNode._shape("op", {"result": None, "stdout": "plain text", "stderr": "warn"})
    assert shaped["stdout"] == "plain text"
    assert "result" not in shaped
    assert shaped["stderr_tail"] == "warn"


def test_node_has_no_auth_preflight():
    # Stripe-strict: the CLI owns auth; the node must not pre-check it.
    assert not hasattr(CloudflareActionNode, "_preflight")
    src = inspect.getsource(cf_action_mod)
    assert "PermissionError" not in src


# --- login handler --------------------------------------------------------------


async def test_login_answers_within_budget_when_flow_stalls(monkeypatch):
    async def stalled_flow():
        await asyncio.sleep(30)
        return {"success": True, "url": "https://dash.cloudflare.com/oauth2/auth?x=1"}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", stalled_flow)
    monkeypatch.setattr(cf_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert res.get("pending") is True


async def test_login_fast_path_returns_authorize_url(monkeypatch):
    async def instant_flow():
        return {"success": True, "url": "https://dash.cloudflare.com/oauth2/auth?x=1"}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", instant_flow)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert res["url"] == "https://dash.cloudflare.com/oauth2/auth?x=1"
    # cf's loopback flow has no device code — nothing to render in the modal.
    assert "verification_code" not in res


def test_login_flow_handles_already_logged_in_exit():
    # cf delta vs gh: with a live session, `cf auth login` exits without
    # printing an authorize URL — the flow must confirm via whoami and
    # succeed instead of reporting a failure.
    src = inspect.getsource(cf_handlers._start_login_flow)
    assert "whoami_snapshot" in src
    assert "Already logged in" in src


# --- marker-token + broadcast contract (source introspection) -------------------


def test_login_success_gates_on_whoami_then_marks_and_broadcasts():
    # Marker + broadcast plumbing is the SHARED module (claude/codex/github/cloudflare).
    assert cf_handlers.mark_logged_in.__module__ == "services.cli_agent._cli_auth"
    complete = inspect.getsource(cf_handlers._complete_login)
    # exit code alone is never trusted — cf exits 0 logged-in AND logged-out;
    # `cf auth whoami` JSON is the gate (and the account-label email source).
    assert "whoami_snapshot" in complete
    assert 'mark_logged_in("cloudflare"' in complete
    assert "credential.oauth.connected" in complete
    snapshot = inspect.getsource(cf_service.whoami_snapshot)
    assert '"auth", "whoami"' in snapshot
    assert "authenticated" in snapshot


def test_logout_removes_marker_and_broadcasts():
    assert cf_handlers.mark_logged_out.__module__ == "services.cli_agent._cli_auth"
    src = inspect.getsource(cf_handlers.handle_cloudflare_logout)
    assert 'mark_logged_out("cloudflare")' in src
    assert "credential.oauth.disconnected" in src


def test_login_uses_stripped_env():
    # login/status/logout must consult cf's OWN store, never env tokens.
    for fn in (cf_handlers._start_login_flow, cf_handlers.handle_cloudflare_logout):
        assert "login_env" in inspect.getsource(fn)
    assert "login_env" in inspect.getsource(cf_service.whoami_snapshot)


def test_ws_handlers_registered():
    assert set(WS_HANDLERS) == {"cloudflare_login", "cloudflare_logout", "cloudflare_status"}


# --- catalogue + assets ----------------------------------------------------------


def test_catalogue_entry_is_fieldless_stripe_shape():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "deployment" in config["categories"]
    cloudflare = config["providers"]["cloudflare"]
    assert cloudflare["kind"] == "oauth"
    assert cloudflare["category"] == "deployment"
    assert cloudflare["icon_ref"] == "lobehub:Cloudflare"
    assert cloudflare["ws"] == {
        "login": "cloudflare_login",
        "logout": "cloudflare_logout",
        "status": "cloudflare_status",
    }
    # cf owns the token — the modal stores nothing (Stripe shape).
    assert "fields" not in cloudflare


def test_credential_class_shape():
    assert CloudflareCredential.id == "cloudflare"
    assert CloudflareCredential.auth == "custom"


def test_plugin_folder_assets():
    # Icon is the official lobehub brand glyph via visuals.json — a
    # co-located icon.svg would silently override it.
    assert not (PLUGIN_DIR / "icon.svg").exists()
    meta = json.loads((PLUGIN_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["color"] == "#F38020"

    visuals = json.loads((PLUGIN_DIR.parent / "visuals.json").read_text(encoding="utf-8"))
    entry = visuals["cloudflareAction"]
    assert entry["icon"] == "lobehub:Cloudflare"
    assert entry["skill"] == "cloudflare-skill"
    # tool_name ("cloudflare") != snake_case(node type) — the lowercase
    # alias carries icon + color for the Master Skill row.
    alias = visuals["cloudflare"]
    assert alias["icon"] == "lobehub:Cloudflare"
    assert alias["color"].startswith("#")

    skill_md = PLUGIN_DIR.parents[1] / "skills" / "cloudflare" / "cloudflare-skill" / "SKILL.md"
    assert skill_md.exists()
