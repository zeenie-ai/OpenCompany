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


def test_cf_env_stored_token_overrides_ambient(monkeypatch):
    # Explicit user config (the credentials-modal token) beats the
    # server environment.
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf_ambient")
    env = cf_service.cf_env("cf_stored")
    assert env["CLOUDFLARE_API_TOKEN"] == "cf_stored"


def test_cf_env_global_key_injects_documented_pair(monkeypatch):
    """A stored cfk_ Global API Key rides cf's documented legacy pair
    (CLOUDFLARE_API_KEY + CLOUDFLARE_EMAIL). Ambient token vars are
    dropped — cf ranks tokens above the key pair and would silently
    override the user's explicit choice."""
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf_ambient")
    monkeypatch.setenv("CF_API_TOKEN", "cf_ambient2")
    key = "cfk_" + "y" * 48
    env = cf_service.cf_env(key, "o@x.dev")
    assert env["CLOUDFLARE_API_KEY"] == key
    assert env["CLOUDFLARE_EMAIL"] == "o@x.dev"
    assert "CLOUDFLARE_API_TOKEN" not in env
    assert "CF_API_TOKEN" not in env

    # cfk_ without an email is unusable — nothing injected, cf falls
    # back to its OAuth session.
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    env = cf_service.cf_env(key, None)
    assert "CLOUDFLARE_API_KEY" not in env
    assert "CLOUDFLARE_API_TOKEN" not in env


def test_api_auth_headers_route_by_prefix():
    key = "cfk_" + "y" * 48
    assert cf_service.api_auth_headers(key, "o@x.dev") == {"X-Auth-Email": "o@x.dev", "X-Auth-Key": key}
    assert cf_service.api_auth_headers(key, None) is None
    assert cf_service.api_auth_headers("cfut_tok", None) == {"Authorization": "Bearer cfut_tok"}
    assert cf_service.api_auth_headers(None, "o@x.dev") is None


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

    async def nothing_stored():
        return None

    monkeypatch.setattr(cf_install, "ensure_cf_cli", fake_ensure)
    monkeypatch.setattr(cf_action_mod, "run_cli_command", run_impl)
    # ops resolve the optional modal credential from the credentials DB
    # — absent in unit tests, so stub the lookups to "nothing stored".
    monkeypatch.setattr(cf_service, "stored_token", nothing_stored)
    monkeypatch.setattr(cf_service, "stored_email", nothing_stored)


def _ctx(tmp_path) -> NodeContext:
    return NodeContext(node_id="cf1", node_type="cloudflareAction", workspace_dir=str(tmp_path))


async def test_whoami_argv_and_env(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"authenticated": True, "email": "o@x.dev"}, "stdout": "{}", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    node = CloudflareActionNode()
    out = await node.whoami(_ctx(tmp_path), CloudflareActionParams(operation="whoami"))

    assert captured["argv"] == ["auth", "whoami"]
    # env comes from cf_env() — no stored token, so nothing injected
    assert captured["env"]["NO_COLOR"] == "1"
    assert "CLOUDFLARE_API_TOKEN" not in captured["env"]
    assert out["result"] == {"authenticated": True, "email": "o@x.dev"}


async def test_ops_inject_stored_api_token(monkeypatch, tmp_path):
    """The optional credentials-modal token rides every op as
    CLOUDFLARE_API_TOKEN (cf's first-priority credential source) — the
    only path past the OAuth grant's fixed scope set."""
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": [], "stdout": "[]", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)

    async def fake_token():
        return "cf_tok_123"

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    node = CloudflareActionNode()
    await node.zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert captured["env"]["CLOUDFLARE_API_TOKEN"] == "cf_tok_123"


async def test_ops_inject_stored_global_key_pair(monkeypatch, tmp_path):
    """A stored cfk_ Global API Key + email ride every op as cf's
    documented CLOUDFLARE_API_KEY + CLOUDFLARE_EMAIL pair."""
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": [], "stdout": "[]", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    key = "cfk_" + "y" * 48

    async def fake_token():
        return key

    async def fake_email():
        return "o@x.dev"

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    monkeypatch.setattr(cf_service, "stored_email", fake_email)
    node = CloudflareActionNode()
    await node.zones_list(_ctx(tmp_path), CloudflareActionParams(operation="zones_list"))
    assert captured["env"]["CLOUDFLARE_API_KEY"] == key
    assert captured["env"]["CLOUDFLARE_EMAIL"] == "o@x.dev"
    assert "CLOUDFLARE_API_TOKEN" not in captured["env"]


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


async def test_graphql_requires_a_credential(monkeypatch, tmp_path):
    """The cf OAuth grant has no analytics scopes — graphql_query must
    fail with the credential guidance (token OR global key + email),
    not a bare 403."""
    _wire(monkeypatch, tmp_path, None)
    for var in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY", "CLOUDFLARE_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    node = CloudflareActionNode()
    with pytest.raises(NodeUserError, match="Account Analytics"):
        await node.graphql_query(
            _ctx(tmp_path),
            CloudflareActionParams(operation="graphql_query", graphql_query="query { viewer { zones { __typename } } }"),
        )


async def test_graphql_posts_official_body_shape(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, None)

    async def fake_token():
        return "cf_tok"

    captured = {}

    async def fake_post(headers, payload):
        captured["headers"] = headers
        captured["payload"] = payload
        return {"data": {"viewer": {"zones": []}}}

    monkeypatch.setattr(cf_service, "stored_token", fake_token)
    monkeypatch.setattr(CloudflareActionNode, "_graphql_post", staticmethod(fake_post))
    node = CloudflareActionNode()

    with pytest.raises(NodeUserError, match="graphql_query is required"):
        await node.graphql_query(_ctx(tmp_path), CloudflareActionParams(operation="graphql_query"))

    with pytest.raises(NodeUserError, match="not valid JSON"):
        await node.graphql_query(
            _ctx(tmp_path),
            CloudflareActionParams(operation="graphql_query", graphql_query="query { viewer }", graphql_variables="{oops"),
        )

    out = await node.graphql_query(
        _ctx(tmp_path),
        CloudflareActionParams(
            operation="graphql_query",
            graphql_query="query Q($zoneTag: string) { viewer }",
            graphql_variables='{"zoneTag": "z1"}',
        ),
    )
    # The official request body shape: {query, variables}, Bearer token.
    assert captured["headers"] == {"Authorization": "Bearer cf_tok"}
    assert captured["payload"] == {
        "query": "query Q($zoneTag: string) { viewer }",
        "variables": {"zoneTag": "z1"},
    }
    assert out["result"] == {"data": {"viewer": {"zones": []}}}


async def test_graphql_post_maps_403_to_permission_guidance(monkeypatch):
    class _Resp:
        status_code = 403
        text = "forbidden"

        @staticmethod
        def json():
            return {"data": None, "errors": [{"message": "not authorized for that account"}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            assert url == cf_action_mod._GRAPHQL_ENDPOINT
            assert kwargs["headers"]["Authorization"] == "Bearer tok"
            return _Resp()

    monkeypatch.setattr(cf_action_mod.httpx, "AsyncClient", _Client)
    with pytest.raises(NodeUserError, match="Account Analytics"):
        await CloudflareActionNode._graphql_post({"Authorization": "Bearer tok"}, {"query": "q", "variables": {}})


def test_graphql_endpoint_is_the_official_one():
    assert cf_action_mod._GRAPHQL_ENDPOINT == "https://api.cloudflare.com/client/v4/graphql"


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


def _reset_login_state(monkeypatch):
    monkeypatch.setattr(cf_handlers, "_active_login", {"task": None, "proc": None})


async def test_login_answers_within_budget_when_flow_stalls(monkeypatch):
    _reset_login_state(monkeypatch)

    async def stalled_flow():
        await asyncio.sleep(30)
        return {"success": True, "message": "done"}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", stalled_flow)
    monkeypatch.setattr(cf_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert res.get("pending") is True


async def test_login_response_never_proxies_a_url(monkeypatch):
    # The CLI owns the whole interaction — it opens the browser itself.
    # The handler must not return url/verification_code for the modal
    # to act on (no custom login UI).
    _reset_login_state(monkeypatch)

    async def instant_flow():
        return {"success": True, "message": "cf is opening your default browser."}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", instant_flow)
    res = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert res["success"] is True
    assert "url" not in res
    assert "verification_code" not in res
    src = inspect.getsource(cf_handlers._start_login_flow)
    assert '"url"' not in src


async def test_login_is_single_flight(monkeypatch):
    # cf's callback server binds the FIXED port 8877 — a second
    # concurrent `cf auth login` exits instantly with a port conflict.
    # A repeat click while a flow is live must not spawn again.
    _reset_login_state(monkeypatch)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_flow():
        started.set()
        await release.wait()
        return {"success": True, "message": "done"}

    monkeypatch.setattr(cf_handlers, "_start_login_flow", slow_flow)
    monkeypatch.setattr(cf_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)

    first = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert first.get("pending") is True
    await started.wait()

    def boom():
        raise AssertionError("second click must not spawn a second flow")

    monkeypatch.setattr(cf_handlers, "_start_login_flow", boom)
    second = await cf_handlers.handle_cloudflare_login({}, websocket=None)
    assert second["success"] is True
    assert second.get("pending") is True
    assert "already in progress" in second["message"].lower()

    release.set()


def test_login_flow_short_circuits_when_already_logged_in():
    # A live session (terminal login, or a flow that completed after the
    # modal closed) marks + returns immediately instead of spawning.
    src = inspect.getsource(cf_handlers._start_login_flow)
    assert "whoami_snapshot" in src
    assert "Already logged in" in src
    # ...and the spawn (only reached without a session) forces past a
    # stale/invalid stored token instead of short-circuiting on it.
    assert "--force" in cf_handlers._LOGIN_ARGS


def test_completion_never_kills_the_login_process():
    # The installed binary is an npm .cmd shim on Windows: killing it
    # orphans the node child, which keeps holding callback port 8877
    # and breaks every later login. cf enforces its own login timeout.
    src = inspect.getsource(cf_handlers._complete_login)
    assert ".kill(" not in src
    assert ".terminate(" not in src


# --- marker-token + broadcast contract (source introspection) -------------------


def test_login_success_gates_on_whoami_then_marks_and_broadcasts():
    # Marker + broadcast plumbing is the SHARED module (claude/codex/github/cloudflare).
    assert cf_handlers.mark_logged_in.__module__ == "services.cli_agent._cli_auth"
    complete = inspect.getsource(cf_handlers._complete_login)
    # exit code alone is never trusted — cf exits 0 logged-in AND logged-out;
    # `cf auth whoami` JSON is the gate (and the account-label email source).
    assert "whoami_snapshot" in complete
    assert "_mark_connected" in complete
    marked = inspect.getsource(cf_handlers._mark_connected)
    assert 'mark_logged_in("cloudflare"' in marked
    assert "credential.oauth.connected" in marked
    snapshot = inspect.getsource(cf_service.whoami_snapshot)
    assert '"auth", "whoami"' in snapshot
    assert "authenticated" in snapshot


def test_logout_removes_marker_and_broadcasts():
    assert cf_handlers.mark_logged_out.__module__ == "services.cli_agent._cli_auth"
    src = inspect.getsource(cf_handlers.handle_cloudflare_logout)
    assert 'mark_logged_out("cloudflare")' in src
    assert "credential.oauth.disconnected" in src


def test_login_uses_stripped_env():
    # login/status/logout must consult cf's OWN store, never env tokens
    # — and the stored API token must never be injected there either.
    for fn in (cf_handlers._start_login_flow, cf_handlers.handle_cloudflare_logout):
        src = inspect.getsource(fn)
        assert "login_env" in src
        assert "stored_token" not in src
    assert "login_env" in inspect.getsource(cf_service.whoami_snapshot)


def test_ws_handlers_registered():
    assert set(WS_HANDLERS) == {"cloudflare_login", "cloudflare_logout", "cloudflare_status"}


# --- catalogue + assets ----------------------------------------------------------


def test_catalogue_entry_is_vercel_dual_path_shape():
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
    # Dual-path (vercel shape): OPTIONAL fields only — required would
    # gate the Login button (OAuthConnect counts required fields only),
    # and the OAuth login must keep working without a credential.
    # The primary key is the canonical `apiKey` (accepts an API token
    # OR a cfk_ Global API Key): the panel maps it to the provider id
    # for storage, so the base Credential.validate scaffold (which
    # stores under cls.id) needs no override, and the shared
    # ApiKeyInput validate flow lights up automatically. The secondary
    # field carries the account email Global API Keys authenticate with.
    fields = cloudflare["fields"]
    assert [f["key"] for f in fields] == ["apiKey", "cloudflare_email"]
    token_field = fields[0]
    assert token_field["required"] is False
    assert token_field["secret"] is True
    assert token_field["type"] == "password"
    email_field = fields[1]
    assert email_field["required"] is False


def test_credential_class_shape():
    assert CloudflareCredential.id == "cloudflare"
    assert CloudflareCredential.auth == "custom"


async def test_credential_resolve_returns_optional_credential_rows(monkeypatch):
    from services.plugin import deps as plugin_deps

    class _Auth:
        def __init__(self, rows):
            self._rows = rows

        async def get_api_key(self, key):
            # Token stored under the provider id (canonical `apiKey`
            # field, mapped to config.id by the panel); the email
            # companion under its own field key.
            assert key in ("cloudflare", "cloudflare_email")
            return self._rows.get(key)

    monkeypatch.setattr(plugin_deps, "get_auth_service", lambda: _Auth({"cloudflare": "tok"}))
    assert await CloudflareCredential.resolve() == {"cloudflare_api_token": "tok"}

    monkeypatch.setattr(
        plugin_deps,
        "get_auth_service",
        lambda: _Auth({"cloudflare": "cfk_" + "y" * 48, "cloudflare_email": "o@x.dev"}),
    )
    assert await CloudflareCredential.resolve() == {
        "cloudflare_api_token": "cfk_" + "y" * 48,
        "cloudflare_email": "o@x.dev",
    }

    monkeypatch.setattr(plugin_deps, "get_auth_service", lambda: _Auth({}))
    assert await CloudflareCredential.resolve() == {}


async def test_probe_hits_official_verify_endpoint(monkeypatch):
    """The Validate button flows through the base Credential.validate
    scaffold to this probe — one GET to Cloudflare's documented
    token-verify endpoint, nothing else."""
    import nodes.cloudflare._credentials as cf_credentials

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"success": True, "result": {"id": "t1", "status": captured["status"]}}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["auth"] = kwargs["headers"]["Authorization"]
            return _Resp()

    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Client)

    captured["status"] = "active"
    result = await CloudflareCredential._probe("tok123")
    assert result.valid is True
    assert captured["url"] == "https://api.cloudflare.com/client/v4/user/tokens/verify"
    assert captured["auth"] == "Bearer tok123"

    captured["status"] = "disabled"
    result = await CloudflareCredential._probe("tok123")
    assert result.valid is False
    assert "disabled" in result.message


async def test_probe_global_key_without_email_gives_guidance(monkeypatch):
    """cfk_ is Cloudflare's documented Global API Key prefix — legacy
    X-Auth-Email/X-Auth-Key auth that is NEVER valid as a Bearer token.
    Without the stored email companion the probe must explain the fix
    up front (no network call) instead of relaying the API's generic
    1000/9109 rejection."""
    import nodes.cloudflare._credentials as cf_credentials

    async def no_email():
        return None

    class _Boom:
        def __init__(self, **kwargs):
            raise AssertionError("cfk_ without email must not hit the network")

    monkeypatch.setattr(cf_credentials, "stored_email", no_email)
    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Boom)
    result = await CloudflareCredential._probe("cfk_" + "y" * 48)
    assert result.valid is False
    assert "Account Email" in result.message


async def test_probe_validates_global_key_with_email(monkeypatch):
    """cfk_ + stored email validate via the documented legacy header
    pair against GET /user."""
    import nodes.cloudflare._credentials as cf_credentials

    async def has_email():
        return "o@x.dev"

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"success": True, "result": {"id": "u1", "email": "o@x.dev"}}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["headers"] = kwargs["headers"]
            return _Resp()

    monkeypatch.setattr(cf_credentials, "stored_email", has_email)
    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Client)
    key = "cfk_" + "y" * 48
    result = await CloudflareCredential._probe(key)
    assert result.valid is True
    assert "o@x.dev" in result.message
    assert captured["url"] == "https://api.cloudflare.com/client/v4/user"
    assert captured["headers"] == {"X-Auth-Email": "o@x.dev", "X-Auth-Key": key}


async def test_probe_verifies_account_tokens_via_accounts_read(monkeypatch):
    """cfat_ account-owned tokens cannot verify at /user/tokens/verify
    (that endpoint is user-token-only) — validity is proven with a
    lightweight authenticated read instead."""
    import nodes.cloudflare._credentials as cf_credentials

    captured = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"success": True, "result": [{"id": "acct1"}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["auth"] = kwargs["headers"]["Authorization"]
            return _Resp()

    monkeypatch.setattr(cf_credentials.httpx, "AsyncClient", _Client)
    token = "cfat_" + "y" * 48
    result = await CloudflareCredential._probe(token)
    assert result.valid is True
    assert captured["url"] == "https://api.cloudflare.com/client/v4/accounts"
    assert captured["auth"] == f"Bearer {token}"


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
