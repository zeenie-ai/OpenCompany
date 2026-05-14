"""Unit tests for `AnthropicClaudeProvider` and `OpenAICodexProvider`.

Covers:
  - `interactive_argv` shape (every flag in the right place, defaults
    applied, optional fields omitted when unset). Claude's argv no
    longer carries ``-p``/``--output-format``/``--max-turns``/
    ``--max-budget-usd``/``--session-id``/``--include-hook-events`` —
    interactive cutover ($docs-internal/claude_code_interactive_mode.md).
  - `parse_event` round-trips JSON correctly, returns None for garbage
  - `is_final_event` matches the right event types
  - `event_to_session_result` reconstructs cost / session_id /
    canonical_usage / response from vendored NDJSON
  - `detect_auth_error` matches "not logged in" stderr patterns
  - `supports()` flags align with `ai_cli_providers.json`
  - Factory raises NotImplementedError for gemini
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.cli_agent import (
    ClaudeTaskSpec,
    CodexTaskSpec,
    create_cli_provider,
)
from services.cli_agent.factory import is_supported
from services.cli_agent.protocol import AICliProvider, CanonicalUsage


# ---------------------------------------------------------------------------
# Factory contract
# ---------------------------------------------------------------------------

class TestFactory:
    def test_claude_creates_provider(self):
        p = create_cli_provider("claude")
        assert p.name == "claude"
        assert isinstance(p, AICliProvider)

    def test_codex_creates_provider(self):
        p = create_cli_provider("codex")
        assert p.name == "codex"
        assert isinstance(p, AICliProvider)

    def test_gemini_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="deferred to v2"):
            create_cli_provider("gemini")

    def test_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown CLI provider"):
            create_cli_provider("openai")

    def test_is_supported(self):
        assert is_supported("claude") is True
        assert is_supported("codex") is True
        assert is_supported("gemini") is False
        assert is_supported("nope") is False


# ---------------------------------------------------------------------------
# Claude provider
# ---------------------------------------------------------------------------

@pytest.fixture
def claude_provider():
    return create_cli_provider("claude")


class TestClaudeArgv:
    def test_minimum_required_flags(self, claude_provider):
        task = ClaudeTaskSpec(prompt="hello world")
        argv = claude_provider.interactive_argv(task, defaults={})
        # VSCode-extension pattern (stream-json over stdio pipes, no PTY).
        # ``-p`` / ``--print`` are NEVER emitted — we stay in interactive
        # billing (entrypoint ``claude-vscode``, not ``sdk-cli``).
        assert "-p" not in argv
        assert "--print" not in argv
        # Stream-json I/O flags ARE emitted now (replace the headless
        # PTY pattern):
        assert "--output-format" in argv
        out_idx = argv.index("--output-format")
        assert argv[out_idx + 1] == "stream-json"
        assert "--input-format" in argv
        in_idx = argv.index("--input-format")
        assert argv[in_idx + 1] == "stream-json"
        assert "--verbose" in argv  # required for stream-json detail
        assert "--ide" in argv  # lockfile auto-discovery
        # The prompt is NEVER a positional in stream-json mode — it goes
        # over ``proc.stdin``. ``--`` separator is gone.
        assert "--" not in argv
        assert "hello world" not in argv
        # Defaults applied
        assert "--model" in argv
        assert "claude-sonnet-4-6" in argv  # default_model from JSON
        # ``-p``-only flags still not emitted (kept on the spec for back-compat)
        assert "--max-turns" not in argv
        assert "--max-budget-usd" not in argv
        assert "--fallback-model" not in argv
        # Tool + permission machinery preserved
        assert "--allowedTools" in argv
        assert "--permission-mode" in argv
        # Default permission mode is read from ``config/ai_cli_providers.json``
        # (currently ``acceptEdits``). Callers wanting ``bypassPermissions``
        # set it explicitly on ``task.permission_mode`` or via the per-call
        # ``defaults`` arg.
        perm_idx = argv.index("--permission-mode")
        assert argv[perm_idx + 1] == "acceptEdits"

    def test_no_session_id_flag_in_interactive(self, claude_provider):
        """Claude assigns its own session UUID in interactive mode; we
        no longer pre-mint a UUID and pass it via `--session-id`. The
        `session_id` field on ClaudeTaskSpec is kept for back-compat
        but silently dropped from argv."""
        task = ClaudeTaskSpec(prompt="x", session_id="sess-abc-123")
        argv = claude_provider.interactive_argv(task, defaults={})
        assert "--session-id" not in argv

    def test_resume_flag_emitted(self, claude_provider):
        task = ClaudeTaskSpec(
            prompt="x",
            session_id="new-sess",  # silently dropped in interactive
            resume_session_id="prior-sess",
        )
        argv = claude_provider.interactive_argv(task, defaults={})
        assert "--resume" in argv
        assert "prior-sess" in argv
        assert "--session-id" not in argv
        # `--continue` is mutually exclusive with `--resume`; explicit
        # UUID wins.
        assert "--continue" not in argv

    def test_continue_flag_emitted(self, claude_provider):
        """``continue_session=True`` produces ``--continue`` — the
        cleaner default for memory-bound runs (claude auto-finds the
        latest conversation under cwd, no UUID round-trip)."""
        task = ClaudeTaskSpec(prompt="x", continue_session=True)
        argv = claude_provider.interactive_argv(task, defaults={})
        assert "--continue" in argv
        assert "--resume" not in argv
        assert "--session-id" not in argv

    def test_resume_wins_over_continue(self, claude_provider):
        """If both are set (caller bug), explicit ``--resume`` wins —
        matches the argv-builder's mutually-exclusive guard."""
        task = ClaudeTaskSpec(
            prompt="x",
            resume_session_id="prior",
            continue_session=True,
        )
        argv = claude_provider.interactive_argv(task, defaults={})
        assert "--resume" in argv
        assert "--continue" not in argv

    def test_no_continuity_flag_by_default(self, claude_provider):
        """Fresh task with no continuity hint → no flag. Claude assigns
        a new session UUID."""
        task = ClaudeTaskSpec(prompt="x")
        argv = claude_provider.interactive_argv(task, defaults={})
        assert "--resume" not in argv
        assert "--continue" not in argv
        assert "--session-id" not in argv

    def test_system_prompt_propagates(self, claude_provider):
        task = ClaudeTaskSpec(prompt="x", system_prompt="be concise")
        argv = claude_provider.interactive_argv(task, defaults={})
        assert "--append-system-prompt" in argv
        assert "be concise" in argv

    def test_include_prompt_is_ignored_in_stream_json_mode(self, claude_provider):
        """``include_prompt`` is kept on the signature for back-compat
        but is intentionally ignored in stream-json input mode — passing
        ``-- "<prompt>"`` would double-send the first turn (once via
        argv, once via stdin). Both ``True`` and ``False`` produce the
        same argv shape: no ``--`` separator, no positional prompt."""
        task = ClaudeTaskSpec(prompt="will-be-piped-via-stdin")
        argv_true = claude_provider.interactive_argv(
            task, defaults={}, include_prompt=True,
        )
        argv_false = claude_provider.interactive_argv(
            task, defaults={}, include_prompt=False,
        )
        for argv in (argv_true, argv_false):
            assert "--" not in argv
            assert "will-be-piped-via-stdin" not in argv
        # Same shape regardless of the back-compat flag.
        assert argv_true == argv_false

    def test_wrong_task_type_raises(self, claude_provider):
        codex_task = CodexTaskSpec(prompt="x")
        with pytest.raises(TypeError, match="ClaudeTaskSpec"):
            claude_provider.interactive_argv(codex_task, defaults={})


class TestClaudeParseEvent:
    def test_valid_json_round_trips(self, claude_provider):
        line = json.dumps({"type": "result", "result": "42", "session_id": "abc"})
        event = claude_provider.parse_event(line)
        assert event is not None
        assert event["type"] == "result"
        assert event["result"] == "42"

    def test_garbage_returns_none(self, claude_provider):
        assert claude_provider.parse_event("not json") is None
        assert claude_provider.parse_event("") is None
        assert claude_provider.parse_event("   ") is None

    def test_is_final_event(self, claude_provider):
        assert claude_provider.is_final_event({"type": "result"}) is True
        assert claude_provider.is_final_event({"type": "assistant"}) is False
        assert claude_provider.is_final_event({"type": "system"}) is False


class TestClaudeEventToSessionResult:
    """Vendored Claude stream-json fixture verified end-to-end."""

    @pytest.fixture
    def fixture_events(self):
        return [
            {"type": "system", "subtype": "init", "session_id": "sess-1"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Sure, here's..."},
                        {"type": "tool_use", "id": "t1", "name": "Read", "input": {}},
                    ],
                },
                "session_id": "sess-1",
            },
            {
                "type": "tool_use",
                "tool_name": "Edit",
                "tool_input": {"file_path": "x.py"},
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "Done — refactored to async.",
                "total_cost_usd": 0.4231,
                "duration_ms": 18234,
                "num_turns": 7,
                "session_id": "sess-1",
                "usage": {
                    "input_tokens": 12000,
                    "output_tokens": 3500,
                    "cache_creation_input_tokens": 500,
                    "cache_read_input_tokens": 8000,
                },
            },
        ]

    def test_reconstructs_response(self, claude_provider, fixture_events):
        result = claude_provider.event_to_session_result(fixture_events, "", 0)
        assert result["response"] == "Done — refactored to async."

    def test_reconstructs_cost(self, claude_provider, fixture_events):
        result = claude_provider.event_to_session_result(fixture_events, "", 0)
        assert result["cost_usd"] == pytest.approx(0.4231)

    def test_reconstructs_session_id(self, claude_provider, fixture_events):
        result = claude_provider.event_to_session_result(fixture_events, "", 0)
        assert result["session_id"] == "sess-1"

    def test_reconstructs_duration_and_turns(self, claude_provider, fixture_events):
        result = claude_provider.event_to_session_result(fixture_events, "", 0)
        assert result["duration_ms"] == 18234
        assert result["num_turns"] == 7

    def test_canonical_usage_normalises(self, claude_provider, fixture_events):
        cu: CanonicalUsage = claude_provider.canonical_usage(fixture_events)
        assert cu.input_tokens == 12000
        assert cu.output_tokens == 3500
        assert cu.cache_read == 8000  # remapped from cache_read_input_tokens
        assert cu.cache_write == 500  # remapped from cache_creation_input_tokens
        assert cu.request_count == 7  # from num_turns

    def test_counts_tool_calls(self, claude_provider, fixture_events):
        # Two tool_use events: one inside an assistant message, one standalone
        result = claude_provider.event_to_session_result(fixture_events, "", 0)
        assert result["tool_calls"] >= 2

    def test_success_on_zero_exit_with_result_event(self, claude_provider, fixture_events):
        result = claude_provider.event_to_session_result(fixture_events, "", 0)
        assert result["success"] is True
        assert result["error"] is None

    def test_failure_on_non_zero_exit(self, claude_provider, fixture_events):
        result = claude_provider.event_to_session_result(
            fixture_events, "exploded", 1,
        )
        assert result["success"] is False
        assert "exploded" in (result["error"] or "")

    def test_failure_on_missing_result_event(self, claude_provider):
        events = [
            {"type": "system", "subtype": "init", "session_id": "s"},
            {"type": "assistant", "message": {}, "session_id": "s"},
        ]
        result = claude_provider.event_to_session_result(events, "", 0)
        assert result["success"] is False
        assert "no result event" in (result["error"] or "")


class TestClaudeAuthDetection:
    def test_logged_out_marker(self, claude_provider):
        assert claude_provider.detect_auth_error(
            "Please run 'claude login' first.", 1,
        ) is True

    def test_clean_run_not_auth_error(self, claude_provider):
        assert claude_provider.detect_auth_error("", 0) is False

    def test_unrelated_stderr_not_auth_error(self, claude_provider):
        assert claude_provider.detect_auth_error(
            "git: pathspec 'x' did not match any files\n", 1,
        ) is False


class TestClaudeSupports:
    def test_supports_full_feature_set(self, claude_provider):
        for feature in (
            "max_budget", "max_turns", "session_id", "resume",
            "mcp_runtime", "json_cost", "ide_lockfile",
        ):
            assert claude_provider.supports(feature), feature

    def test_does_not_support_sandbox(self, claude_provider):
        assert claude_provider.supports("sandbox") is False


# ---------------------------------------------------------------------------
# Codex provider
# ---------------------------------------------------------------------------

@pytest.fixture
def codex_provider():
    return create_cli_provider("codex")


class TestCodexArgv:
    def test_no_session_no_budget_no_turns(self, codex_provider):
        task = CodexTaskSpec(prompt="hello")
        argv = codex_provider.interactive_argv(task, defaults={})
        assert "--max-turns" not in argv
        assert "--max-budget-usd" not in argv
        assert "--session-id" not in argv
        assert "--resume" not in argv
        assert "--allowedTools" not in argv

    def test_sandbox_flag(self, codex_provider):
        task = CodexTaskSpec(prompt="x", sandbox="read-only")
        argv = codex_provider.interactive_argv(task, defaults={})
        assert "--sandbox" in argv
        assert "read-only" in argv

    def test_ask_for_approval_flag(self, codex_provider):
        task = CodexTaskSpec(prompt="x", ask_for_approval="on-request")
        argv = codex_provider.interactive_argv(task, defaults={})
        assert "--ask-for-approval" in argv
        assert "on-request" in argv

    def test_default_sandbox_workspace_write(self, codex_provider):
        task = CodexTaskSpec(prompt="x")
        argv = codex_provider.interactive_argv(task, defaults={})
        idx = argv.index("--sandbox")
        assert argv[idx + 1] == "workspace-write"

    def test_system_prompt_prepended(self, codex_provider):
        task = CodexTaskSpec(prompt="user thing", system_prompt="be careful")
        argv = codex_provider.interactive_argv(task, defaults={})
        # Codex has no --system-prompt flag; we prepend with <system> tags
        prompt_arg = argv[-1]  # last arg is the prompt
        assert "<system>" in prompt_arg
        assert "be careful" in prompt_arg
        assert "user thing" in prompt_arg

    def test_wrong_task_type_raises(self, codex_provider):
        claude_task = ClaudeTaskSpec(prompt="x")
        with pytest.raises(TypeError, match="CodexTaskSpec"):
            codex_provider.interactive_argv(claude_task, defaults={})


class TestCodexEventReconstruction:
    @pytest.fixture
    def fixture_events(self):
        # Codex has no public stream-json schema; this is a plausible
        # synthetic stream that exercises our best-effort matchers.
        return [
            {"type": "message", "text": "Working on it..."},
            {"type": "assistant", "text": "Final answer is X."},
            {"type": "complete", "duration_ms": 5000, "stats": {"turns": 3}},
        ]

    def test_extracts_response_from_final_event(self, codex_provider, fixture_events):
        result = codex_provider.event_to_session_result(fixture_events, "", 0)
        # Response comes from final event's `text`/`response`/`result`/`content`
        # if present; otherwise the last assistant message.
        assert "Final answer is X." in result["response"]

    def test_cost_always_none_for_codex(self, codex_provider, fixture_events):
        result = codex_provider.event_to_session_result(fixture_events, "", 0)
        assert result["cost_usd"] is None

    def test_canonical_usage_zeros(self, codex_provider, fixture_events):
        cu = codex_provider.canonical_usage(fixture_events)
        assert cu.input_tokens == 0
        assert cu.output_tokens == 0


class TestCodexAuthDetection:
    def test_openai_api_key_missing(self, codex_provider):
        assert codex_provider.detect_auth_error(
            "Error: OPENAI_API_KEY not set.\n", 1,
        ) is True

    def test_401_marker(self, codex_provider):
        assert codex_provider.detect_auth_error("HTTP 401 Unauthorized\n", 1) is True


class TestCodexSupports:
    def test_supports_only_sandbox(self, codex_provider):
        assert codex_provider.supports("sandbox") is True
        for feature in (
            "max_budget", "max_turns", "session_id", "resume",
            "json_cost", "ide_lockfile",
        ):
            assert codex_provider.supports(feature) is False, feature
