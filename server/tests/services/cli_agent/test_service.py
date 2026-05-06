"""`AICliService` tests — fail-fast paths that don't spawn the CLI.

The full subprocess-driven path is covered by live verification (see
`docs-internal/cli_agent_framework.md` → Verification §5–7). These unit
tests cover:
  - `working_directory_not_git_repo` abort (no pool constructed)
  - resolver contract: explicit `repo_root` doesn't fall back to cwd
  - factory NotImplementedError surfaces cleanly
  - cancel_workflow / cancel_node return zero when nothing's running
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from services.cli_agent import ClaudeTaskSpec, CodexTaskSpec
from services.cli_agent.service import AICliService, get_ai_cli_service


@pytest.mark.asyncio
async def test_not_git_repo_returns_structured_failure():
    """Caller-supplied repo_root that isn't a git repo → fail-fast,
    every task surfaces `working_directory_not_git_repo`."""
    svc = AICliService()
    with tempfile.TemporaryDirectory() as tmp:
        result = await svc.run_batch(
            "claude",
            tasks=[
                ClaudeTaskSpec(prompt="task A"),
                ClaudeTaskSpec(prompt="task B"),
            ],
            node_id="n",
            workflow_id="wf",
            workspace_dir=Path(tmp),
            broadcaster=None,
            repo_root=Path(tmp),  # explicit non-git
        )

    assert result.n_tasks == 2
    assert result.n_succeeded == 0
    assert result.n_failed == 2
    assert all(t.error == "working_directory_not_git_repo" for t in result.tasks)
    assert result.total_cost_usd is None


@pytest.mark.asyncio
async def test_explicit_repo_root_does_not_fallback_to_cwd():
    """When the caller passes an explicit `repo_root`, the resolver
    must NOT walk to cwd. This is the bug we fixed during Phase 5
    smoke-testing."""
    # cwd is the framework worktree (a git repo). If the resolver fell
    # back, it would silently succeed — bad.
    svc = AICliService()
    with tempfile.TemporaryDirectory() as tmp:
        result = await svc.run_batch(
            "claude",
            tasks=[ClaudeTaskSpec(prompt="x")],
            node_id="n",
            workflow_id="wf",
            workspace_dir=Path(tmp),
            broadcaster=None,
            repo_root=Path(tmp),
        )
    assert result.n_failed == 1
    assert result.tasks[0].error == "working_directory_not_git_repo"


@pytest.mark.asyncio
async def test_codex_provider_works():
    """Codex factory must build a provider; happy-path argv was already
    covered in test_providers.py."""
    svc = AICliService()
    with tempfile.TemporaryDirectory() as tmp:
        # Same not-git-repo path with Codex — confirms the provider
        # discrim works through the service.
        result = await svc.run_batch(
            "codex",
            tasks=[CodexTaskSpec(prompt="x", sandbox="read-only")],
            node_id="n",
            workflow_id="wf",
            workspace_dir=Path(tmp),
            broadcaster=None,
            repo_root=Path(tmp),
        )
    assert result.provider == "codex"
    assert result.tasks[0].provider == "codex"


@pytest.mark.asyncio
async def test_gemini_factory_raises_not_implemented():
    svc = AICliService()
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(NotImplementedError, match="deferred to v2"):
            await svc.run_batch(
                "gemini",
                tasks=[],  # spec irrelevant — factory raises before construction
                node_id="n",
                workflow_id="wf",
                workspace_dir=Path(tmp),
                broadcaster=None,
                repo_root=Path(tmp),
            )


@pytest.mark.asyncio
async def test_cancel_when_no_active_pools():
    svc = AICliService()
    assert await svc.cancel_workflow("nothing") == 0
    assert await svc.cancel_node("nothing") == 0


def test_singleton_accessor_returns_same_instance():
    a = get_ai_cli_service()
    b = get_ai_cli_service()
    assert a is b


@pytest.mark.asyncio
async def test_resolver_walks_upward_to_find_git():
    """Without `override`, the resolver tries workspace_dir then cwd.

    The cli-agent-framework worktree is a git repo. A deep child path
    should resolve via `git rev-parse --show-toplevel` to the worktree root.
    """
    deep = Path(__file__).resolve().parent / "deep" / "deeper"
    root = await AICliService._resolve_repo_root(workspace_dir=deep, override=None)
    assert root is not None
    assert (root / ".git").exists()


@pytest.mark.asyncio
async def test_resolver_returns_none_when_override_not_git():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        root = await AICliService._resolve_repo_root(
            workspace_dir=Path(tmp),  # ignored when override is set
            override=Path(tmp),
        )
        assert root is None


# ---------------------------------------------------------------------------
# Execution-engine integration: drive the real `run_batch` (via the DI
# accessor) and assert the `[CC-Agent ...]` log lines fire at the right
# transitions. This replaces direct contextvar pokes — every assertion
# walks the same resolver / register_batch / session-start path the
# production code does.
#
# The top-level `tests/conftest.py` stubs `core.logging.get_logger` with
# a shared MagicMock, so we can't use `caplog`. Instead each module's
# module-level ``logger`` IS that shared MagicMock — we inspect its
# ``info``/``warning`` ``call_args_list`` to verify the diagnostic
# chain fires.
# ---------------------------------------------------------------------------


def _logger_messages(*module_loggers) -> str:
    """Concatenate every ``.info`` / ``.warning`` / ``.error`` call's
    rendered template+args across one or more module-level loggers."""
    out: list[str] = []
    for lg in module_loggers:
        for level in ("info", "warning", "error", "exception", "debug"):
            method = getattr(lg, level, None)
            if method is None or not hasattr(method, "call_args_list"):
                continue
            for call in method.call_args_list:
                args = call.args or ()
                if not args:
                    continue
                template = args[0]
                if not isinstance(template, str):
                    out.append(repr(template))
                    continue
                try:
                    out.append(template % args[1:] if len(args) > 1 else template)
                except (TypeError, ValueError):
                    out.append(template + " | " + repr(args[1:]))
    return "\n".join(out)


@pytest.mark.asyncio
async def test_run_batch_emits_diagnostic_logs_when_workspace_not_git(monkeypatch):
    """Abort path: `run_batch` enters, fails the resolver, returns
    structured failure. The `[CC-Agent run_batch] enter` and
    `[CC-Agent run_batch] aborting` log lines must fire so the operator
    sees WHY the batch never reached `register_batch`."""
    # Force the module-level logger to a Mock for THIS test, regardless of
    # whether conftest's `core.logging.get_logger` stub was active at the
    # moment the service module was first imported. In the full-suite
    # ordering some sibling test imports `core.logging` after the stub is
    # placed, leaving the cli_agent module with a real structlog
    # BoundLogger that has no `reset_mock`. Locally patching makes the test
    # robust to import-order pollution.
    from services.cli_agent import service as svc_mod
    svc_logger = MagicMock()
    monkeypatch.setattr(svc_mod, "logger", svc_logger)

    svc = get_ai_cli_service()  # DI singleton — same accessor production uses
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = await svc.run_batch(
            "claude",
            tasks=[ClaudeTaskSpec(prompt="hello")],
            node_id="ccode_test_abort",
            workflow_id="wf_abort",
            workspace_dir=Path(tmp),
            broadcaster=None,
            repo_root=Path(tmp),
            connected_tools=[
                {"node_id": "ddg_1", "node_type": "duckduckgoSearch",
                 "label": "DDG", "parameters": {}},
            ],
        )

    text = _logger_messages(svc_logger)
    assert "[CC-Agent run_batch] enter" in text, text
    assert "duckduckgoSearch" in text, text
    assert "[CC-Agent run_batch] aborting" in text, text
    # The abort path MUST NOT register a batch (no MCP tokens leaked):
    assert "[CC-Agent MCP register_batch]" not in text, text
    assert result.n_failed == 1


@pytest.mark.asyncio
async def test_run_batch_registers_mcp_batch_on_happy_path(monkeypatch):
    """Happy path: real resolver, real `register_batch`, but
    `AICliSession.start` is short-circuited so we don't spawn `claude`.
    Asserts the `[CC-Agent MCP register_batch]` log fires with the
    expected tool list — proving the diagnostic chain is intact
    end-to-end and the spawned CLI WOULD see the MCP server registered
    for it."""
    from services.cli_agent import session as session_mod
    from services.cli_agent import service as svc_mod
    from services.cli_agent import mcp_server as mcp_mod
    from services.cli_agent.protocol import SessionResult

    # See the abort-path test for the rationale — patch the module-level
    # logger directly so the test does not depend on conftest's
    # `core.logging.get_logger` stub being active at import time.
    svc_logger = MagicMock()
    mcp_logger = MagicMock()
    monkeypatch.setattr(svc_mod, "logger", svc_logger)
    monkeypatch.setattr(mcp_mod, "logger", mcp_logger)

    started = {"count": 0}

    async def _fake_start(self):  # noqa: ANN001
        started["count"] += 1
        self._completed = True

    async def _fake_wait(self, timeout):  # noqa: ANN001, ARG002
        return SessionResult(
            task_id=self.task_id, provider=self._provider.name,
            prompt=getattr(self._task, "prompt", ""),
            success=True, response="stub",
        )

    async def _fake_cleanup(self):  # noqa: ANN001
        pass

    monkeypatch.setattr(session_mod.AICliSession, "start", _fake_start)
    monkeypatch.setattr(session_mod.AICliSession, "wait_for_completion", _fake_wait)
    monkeypatch.setattr(session_mod.AICliSession, "cleanup", _fake_cleanup)

    svc = get_ai_cli_service()
    workspace = Path(__file__).resolve().parents[3]  # the repo root (a git repo)
    result = await svc.run_batch(
        "claude",
        tasks=[ClaudeTaskSpec(prompt="ping")],
        node_id="ccode_test_happy",
        workflow_id="wf_happy",
        workspace_dir=workspace,
        broadcaster=None,
        repo_root=None,  # let the resolver find the parent .git
        connected_tools=[
            {"node_id": "ddg_1", "node_type": "duckduckgoSearch",
             "label": "DDG", "parameters": {}},
        ],
        connected_skill_names=["duckduckgo-search-skill"],
    )

    text = _logger_messages(svc_logger, mcp_logger)
    # Engine-entry log:
    assert "[CC-Agent run_batch] enter" in text, text
    assert "duckduckgoSearch" in text, text
    # Resolver succeeded:
    assert "[CC-Agent run_batch] resolved repo_root=" in text, text
    # MCP token registered with our connected tool:
    assert "[CC-Agent MCP register_batch]" in text, text
    # Session was actually exercised (proves the engine ran the inner
    # gather, not just the abort path):
    assert started["count"] == 1
    assert result.n_succeeded == 1
