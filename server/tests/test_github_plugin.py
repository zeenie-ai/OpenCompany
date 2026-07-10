"""GitHub plugin contract tests.

Locks the plugin's load-bearing seams under the CLI-owns-auth (Stripe)
pattern: env builders (no token handling; login env strips ambient
tokens — source-verified gh abort), the login-banner parser (gh's
``authflow/flow.go`` format strings), install resolution (pinned
release, per-platform member paths), the marker-token + CloudEvents
broadcast contract (``inspect.getsource`` introspection,
``test_credential_broadcasts`` style), op argv building, and the
catalogue entry shape (fieldless — gh owns the token).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

import nodes.github._handlers as gh_handlers
import nodes.github._install as gh_install
import nodes.github._service as gh_service
import nodes.github.github_action as gh_action_mod
from nodes.github import WS_HANDLERS
from nodes.github._credentials import GitHubCredential
from nodes.github.github_action import GitHubActionNode, GitHubActionParams
from services.plugin import NodeContext
from services.plugin.base import NodeUserError

PLUGIN_DIR = Path(gh_service.__file__).parent
CONFIG_PATH = Path(gh_service.__file__).parents[2] / "config" / "credential_providers.json"


# --- _service env builders ----------------------------------------------------


def test_gh_env_is_the_documented_automation_baseline(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_ambient")
    env = gh_service.gh_env()
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["NO_COLOR"] == "1"
    assert env["GH_NO_UPDATE_NOTIFIER"] == "1"
    assert env["GH_PAGER"] == "cat"
    # Ambient env token is left alone for ops — gh's documented precedence.
    assert env["GH_TOKEN"] == "ghp_ambient"


def test_login_env_strips_ambient_tokens_and_allows_prompts(monkeypatch):
    """`gh auth login` aborts when GH_TOKEN/GITHUB_TOKEN is set
    (login.go: 'The value of the %s environment variable is being used
    for authentication'), and `gh auth status` would report the env
    token instead of the stored session."""
    monkeypatch.setenv("GH_TOKEN", "ghp_ambient")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ambient2")
    env = gh_service.login_env()
    assert "GH_TOKEN" not in env
    assert "GITHUB_TOKEN" not in env
    assert "GH_PROMPT_DISABLED" not in env


# --- login banner parsing (source-verified authflow/flow.go strings) -----------


def test_parse_login_banner_extracts_code_and_device_url():
    banner = (
        "! First copy your one-time code: ABCD-1234\n"
        "Open this URL to continue in your web browser: https://github.com/login/device\n"
    )
    parsed = gh_handlers.parse_login_banner(banner)
    assert parsed == ("https://github.com/login/device", "ABCD-1234")


def test_parse_login_banner_waits_until_url_present():
    # Code line alone (printed first) is not enough — the URL is the gate.
    assert gh_handlers.parse_login_banner("! First copy your one-time code: ABCD-1234\n") is None
    assert gh_handlers.parse_login_banner("") is None


def test_parse_login_banner_tolerates_missing_code():
    # Interactive-branch output variants may phrase the code line
    # differently; the URL alone must still unblock the flow.
    parsed = gh_handlers.parse_login_banner("Open this URL to continue in your web browser: https://github.com/login/device\n")
    assert parsed == ("https://github.com/login/device", None)


# --- _install resolution --------------------------------------------------------


async def test_ensure_gh_cli_is_project_local_and_pooch_driven(monkeypatch, tmp_path):
    """The system-global gh is never consulted — the binary comes from
    the pooch-extracted copy under package_dir("gh") (temporal idiom)."""
    fake_bin = tmp_path / "extracted" / "bin" / ("gh.exe" if gh_install.platform.system() == "Windows" else "gh")
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("", encoding="utf-8")

    calls = {}

    def fake_retrieve(**kwargs):
        calls.update(kwargs)
        return [str(fake_bin)]

    monkeypatch.setattr(gh_install, "_cached_path", None)
    monkeypatch.setattr(gh_install, "_package_root", lambda: tmp_path)
    monkeypatch.setattr(gh_install.pooch, "retrieve", fake_retrieve)

    resolved = await gh_install.ensure_gh_cli()
    assert resolved == fake_bin
    assert calls["url"].startswith(gh_install._RELEASE_BASE)
    assert calls["path"] == tmp_path
    # cached for subsequent calls without re-fetching
    def boom(**kwargs):
        raise AssertionError("must not re-download")

    monkeypatch.setattr(gh_install.pooch, "retrieve", boom)
    assert await gh_install.ensure_gh_cli() == fake_bin


def test_release_assets_pinned_per_platform():
    version = gh_install._VERSION
    assert version and version != "latest"
    for (system, _machine), (asset, binary_name) in gh_install._ASSETS.items():
        assert version in asset
        assert binary_name == ("gh.exe" if system == "Windows" else "gh")
        assert asset.endswith(".zip" if system in ("Windows", "Darwin") else ".tar.gz")
    # gh's release naming quirk: macOS assets are zips with capital-S "macOS".
    assert "macOS" in gh_install._ASSETS[("Darwin", "arm64")][0]


def test_gh_cli_path_never_touches_system_path():
    # Project-local contract: no shutil.which anywhere in the plugin.
    src_install = inspect.getsource(gh_install)
    src_service = inspect.getsource(gh_service)
    assert "which(" not in src_install
    assert "which(" not in src_service


# --- operations (no auth pre-flight — Stripe pattern) ---------------------------


def _wire(monkeypatch, tmp_path, run_impl):
    async def fake_ensure():
        return tmp_path / "gh"

    monkeypatch.setattr(gh_install, "ensure_gh_cli", fake_ensure)
    monkeypatch.setattr(gh_action_mod, "run_cli_command", run_impl)


async def test_pr_list_builds_json_argv(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": [{"number": 1}], "stdout": "[{}]", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = GitHubActionNode()
    ctx = NodeContext(node_id="g1", node_type="githubAction", workspace_dir=str(tmp_path))
    out = await node.pr_list(ctx, GitHubActionParams(operation="pr_list", repo="octocat/hello", state="open", limit=5))

    argv = captured["argv"]
    assert argv[:2] == ["pr", "list"]
    assert argv[argv.index("--repo") + 1] == "octocat/hello"
    assert argv[argv.index("--limit") + 1] == "5"
    assert argv[argv.index("--json") + 1] == gh_action_mod._PR_JSON_FIELDS
    # env comes from gh_env() — no token was resolved or injected anywhere
    assert captured["env"]["GH_PAGER"] == "cat"
    assert out["result"] == [{"number": 1}]


async def test_repo_clone_requires_workspace_and_uses_clone_timeout(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": None, "stdout": "", "stderr": "Cloning...", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = GitHubActionNode()

    ctx = NodeContext(node_id="g1", node_type="githubAction", workspace_dir=str(tmp_path))
    await node.repo_clone(ctx, GitHubActionParams(operation="repo_clone", clone_repo="octocat/hello"))
    assert captured["argv"] == ["repo", "clone", "octocat/hello"]
    assert captured["cwd"] == str(tmp_path)
    assert captured["timeout"] == gh_action_mod._CLONE_TIMEOUT

    with pytest.raises(NodeUserError):
        await node.repo_clone(
            NodeContext(node_id="g1", node_type="githubAction", workspace_dir=None),
            GitHubActionParams(operation="repo_clone", clone_repo="octocat/hello"),
        )


async def test_gh_auth_error_surfaces_verbatim_as_node_user_error(monkeypatch, tmp_path):
    """No pre-flight: gh's own 'not logged in' error IS the auth error."""

    async def fake_run(**kwargs):
        return {"success": False, "stdout": "", "stderr": "To get started with GitHub CLI, please run:  gh auth login", "error": "exit 4"}

    _wire(monkeypatch, tmp_path, fake_run)
    node = GitHubActionNode()
    ctx = NodeContext(node_id="g1", node_type="githubAction", workspace_dir=str(tmp_path))
    with pytest.raises(NodeUserError, match="gh auth login"):
        await node.issue_list(ctx, GitHubActionParams(operation="issue_list"))


async def test_custom_requires_command_and_shlex_splits(monkeypatch, tmp_path):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return {"success": True, "result": {"login": "octocat"}, "stdout": "{}", "stderr": "", "error": None}

    _wire(monkeypatch, tmp_path, fake_run)
    node = GitHubActionNode()
    ctx = NodeContext(node_id="g1", node_type="githubAction", workspace_dir=str(tmp_path))

    with pytest.raises(NodeUserError):
        await node.custom(ctx, GitHubActionParams(operation="custom", command="  "))

    await node.custom(ctx, GitHubActionParams(operation="custom", command="release create v1.0.0 --notes 'First release'"))
    assert captured["argv"] == ["release", "create", "v1.0.0", "--notes", "First release"]


def test_node_has_no_auth_preflight():
    # Stripe-strict: the CLI owns auth; the node must not pre-check it.
    assert not hasattr(GitHubActionNode, "_preflight")
    src = inspect.getsource(gh_action_mod)
    assert "PermissionError" not in src


# --- login handler --------------------------------------------------------------


async def test_login_answers_within_budget_when_flow_stalls(monkeypatch):
    async def stalled_flow():
        await asyncio.sleep(30)
        return {"success": True, "url": "https://github.com/login/device"}

    monkeypatch.setattr(gh_handlers, "_start_login_flow", stalled_flow)
    monkeypatch.setattr(gh_handlers, "_RESPONSE_BUDGET_SECONDS", 0.05)
    res = await gh_handlers.handle_github_login({}, websocket=None)
    assert res["success"] is True
    assert res.get("pending") is True


async def test_login_fast_path_returns_device_url_and_code(monkeypatch):
    async def instant_flow():
        return {"success": True, "url": "https://github.com/login/device", "verification_code": "ABCD-1234"}

    monkeypatch.setattr(gh_handlers, "_start_login_flow", instant_flow)
    res = await gh_handlers.handle_github_login({}, websocket=None)
    assert res["success"] is True
    assert res["url"] == "https://github.com/login/device"
    assert res["verification_code"] == "ABCD-1234"


# --- marker-token + broadcast contract (source introspection) -------------------


def test_login_success_gates_on_auth_status_then_marks_and_broadcasts():
    # Marker + broadcast plumbing is the SHARED module (claude/codex/github).
    assert gh_handlers.mark_logged_in.__module__ == "services.cli_agent._cli_auth"
    complete = inspect.getsource(gh_handlers._complete_login)
    # exit code alone is never trusted — `gh auth status` is the gate
    assert "cli_logged_in" in complete
    assert 'mark_logged_in("github"' in complete
    assert "credential.oauth.connected" in complete
    # the official git bridge runs on success
    assert "setup-git" in complete
    # account identity feeds the catalogue's account_label ("Connected as …")
    assert "_fetch_account" in complete
    fetch = inspect.getsource(gh_handlers._fetch_account)
    assert '"api", "user"' in fetch


def test_logout_removes_marker_and_broadcasts():
    assert gh_handlers.mark_logged_out.__module__ == "services.cli_agent._cli_auth"
    src = inspect.getsource(gh_handlers.handle_github_logout)
    assert 'mark_logged_out("github")' in src
    assert "credential.oauth.disconnected" in src


def test_login_uses_stripped_env():
    # login/status/logout must consult gh's OWN store, never env tokens.
    for fn in (gh_handlers._start_login_flow, gh_handlers.handle_github_logout):
        assert "login_env" in inspect.getsource(fn)
    assert "login_env" in inspect.getsource(gh_service.cli_logged_in)


def test_ws_handlers_registered():
    assert set(WS_HANDLERS) == {"github_login", "github_logout", "github_status"}


# --- catalogue + assets ----------------------------------------------------------


def test_catalogue_entry_is_fieldless_stripe_shape():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "developer" in config["categories"]
    github = config["providers"]["github"]
    assert github["kind"] == "oauth"
    assert github["category"] == "developer"
    assert github["icon_ref"] == "lobehub:Github"
    assert github["ws"] == {"login": "github_login", "logout": "github_logout", "status": "github_status"}
    # gh owns the token — the modal stores nothing (Stripe shape).
    assert "fields" not in github


def test_credential_class_shape():
    assert GitHubCredential.id == "github"
    assert GitHubCredential.auth == "custom"


def test_plugin_folder_assets():
    # Icon is the official lobehub brand glyph via visuals.json — a
    # co-located icon.svg would silently override it.
    assert not (PLUGIN_DIR / "icon.svg").exists()
    meta = json.loads((PLUGIN_DIR / "meta.json").read_text(encoding="utf-8"))
    assert meta["color"].startswith("#")

    visuals = json.loads((PLUGIN_DIR.parent / "visuals.json").read_text(encoding="utf-8"))
    entry = visuals["githubAction"]
    assert entry["icon"] == "lobehub:Github"
    assert entry["skill"] == "github-skill"

    skill_md = PLUGIN_DIR.parents[1] / "skills" / "github" / "github-skill" / "SKILL.md"
    assert skill_md.exists()
