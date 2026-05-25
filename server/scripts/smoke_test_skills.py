"""Smoke test: verify claude code agent can see and invoke materialised
skills from a per-workflow workspace.

Spawns one cold-spawn claude subprocess via the pool, with one skill
wired (``humanify-skill``). Asserts:

  1. The materialised SKILL.md exists at the expected path.
  2. ``Skill`` enters ``--allowedTools`` argv (paired with the wiring).
  3. The spawn succeeds (process opens stdin pipe, emits ``system/init``).
  4. Claude completes a turn that *uses* the skill (via slash command
     ``/humanify-skill`` so we don't have to rely on autodiscovery).
  5. The session's ``result`` event lands and the response is non-empty.

Run from ``server/``:
    .venv/Scripts/python.exe scripts/smoke_test_skills.py

Requires:
  - Claude binary at ``data/claude-machina/npm/...`` (lazy-installed
    by ``nodes.agent.claude_code_agent._oauth.claude_binary_path``).
  - Claude auth (``data/claude-machina/`` is the project-local
    CLAUDE_CONFIG_DIR; user must have logged in once).
  - No active workflow consuming pool slot ``smoke-test-memory``.

The test isolates itself: temp workspace dir, temp memory_node_id.
On exit, terminates the pool entry it spawned.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path


async def main() -> int:
    # Make sure we're running from the server dir so `services.*` imports work.
    server_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(server_dir))

    from services.cli_agent.types import ClaudeTaskSpec
    from nodes.agent.claude_code_agent._oauth import MACHINA_CLAUDE_DIR
    from nodes.agent.claude_code_agent._pool import get_session_pool
    from nodes.agent.claude_code_agent._skills import materialise_skills

    repo_root = server_dir.parent
    memory_node_id = "smoke-test-memory"
    workflow_id = "smoke-test-wf"
    skill_under_test = "humanify-skill"

    with tempfile.TemporaryDirectory(prefix="machina_skills_smoke_") as tmp:
        workspace = Path(tmp).resolve()
        print(f"[smoke] workspace = {workspace}")
        print(f"[smoke] repo_root = {repo_root}")

        # Step 1 — materialise the skill into the workspace.
        added, removed = await materialise_skills(
            workspace,
            [skill_under_test],
            previous_skill_names=None,
            log_label="smoke",
        )
        skill_md = workspace / ".claude" / "skills" / skill_under_test / "SKILL.md"
        assert added == 1 and removed == 0, f"materialise_skills returned ({added}, {removed})"
        assert skill_md.exists(), f"SKILL.md not at {skill_md}"
        print(f"[smoke] step 1 PASS — SKILL.md at {skill_md} ({skill_md.stat().st_size} bytes)")

        # Step 2 — build argv and check Skill is in --allowedTools.
        from services.cli_agent.factory import create_cli_provider

        provider = create_cli_provider("claude")
        spec = ClaudeTaskSpec(
            prompt="Use the humanify-skill to format this list nicely: apples, bananas, cherries.",
            add_dir=[str(workspace)],
            timeout_seconds=60,
        )
        argv = provider.interactive_argv(
            spec,
            defaults={},
            mcp_endpoint_url=None,
            mcp_bearer_token=None,
            connected_tool_names=[],
            connected_skill_names=[skill_under_test],
            include_prompt=False,
        )
        allowed_idx = argv.index("--allowedTools")
        allowed = argv[allowed_idx + 1].split(",")
        assert "Skill" in allowed, f"Skill missing from allowlist: {allowed}"
        assert "--add-dir" in argv and str(workspace) in argv, f"workspace not in --add-dir argv: {argv}"
        print(f"[smoke] step 2 PASS — argv has Skill in allowlist + --add-dir {workspace.name}")

        # Step 3 — spawn via the pool (cold spawn). Pool uses
        # `cwd=repo_root` for memory-bound runs.
        pool = get_session_pool()
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        env["CLAUDE_CONFIG_DIR"] = str(MACHINA_CLAUDE_DIR)
        env["MACHINA_PARENT_RUN_ID"] = f"{workflow_id}:{memory_node_id}:smoke"

        # Make sure no stale pool entry. Idempotent.
        await pool.terminate(memory_node_id)

        try:
            pooled = await pool.acquire(
                memory_node_id,
                spec=spec,
                cwd=repo_root,
                env=env,
                defaults={},
                mcp_endpoint_url=None,
                mcp_bearer_token=None,
                connected_tool_names=[],
                connected_skill_names=[skill_under_test],
                workspace_dir=workspace,
                workflow_id=workflow_id,
            )
            print(
                f"[smoke] step 3 PASS — claude pid={pooled.process.pid}, "
                f"workspace_dir bound, materialised_skills="
                f"{set(pooled.materialised_skills)}"
            )
            assert skill_under_test in pooled.materialised_skills

            # Step 4 — send a turn that should activate the skill.
            # Using the /humanify-skill slash command bypasses LLM
            # autodiscovery — the failure mode is much clearer if
            # the skill isn't loadable.
            print("[smoke] step 4 — sending turn (this calls the model)...")
            result = await pool.send_turn(
                pooled,
                "/humanify-skill Briefly describe the number 42 in plain language.",
                timeout_seconds=120,
                workflow_id=workflow_id,
            )
            assert result.success, f"turn failed: {result.error}"
            print(
                f"[smoke] step 4 PASS — session_id={result.session_id}, "
                f"cost=${result.cost_usd}, num_turns={result.num_turns}, "
                f"duration_ms={result.duration_ms}"
            )
            print("[smoke] response (first 400 chars):")
            print(f"  {result.response[:400]!r}")

            # Step 5 — inspect the events for evidence of skill use.
            # We look for: (a) the response is non-empty, (b) the
            # session emitted assistant content that follows the skill's
            # humanify guidance (plain language, no raw markdown). The
            # weaker but more reliable signal is just "response was
            # generated and the slash command didn't error."
            assert result.response.strip(), "response was empty"
            # If claude couldn't find the skill, it usually returns
            # something like "I don't have access to humanify-skill"
            # or an error event. Check for those markers.
            error_markers = [
                "skill not found",
                "no such skill",
                "i don't have a skill",
                "unable to find the skill",
                "unknown skill",
            ]
            response_lc = result.response.lower()
            for marker in error_markers:
                assert marker not in response_lc, (
                    f"response indicates skill not loaded: matched marker {marker!r}\n" f"full response: {result.response[:1000]!r}"
                )
            print("[smoke] step 5 PASS — response present, no 'skill not found' markers")

        finally:
            await pool.terminate(memory_node_id)
            print("[smoke] cleanup — pool entry terminated")

    print()
    print("[smoke] ===== ALL CHECKS PASSED =====")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
