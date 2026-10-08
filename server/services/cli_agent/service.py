"""`AICliService.run_batch()` — top-level entry for `claude_code_agent` /
`codex_agent` plugins.

Runs N parallel `AICliSession`s under an `asyncio.Semaphore`, mirroring
the semaphore-+-gather pattern already used in
`nodes/document/file_downloader.py`. No separate pool class — the
machinery is small enough to live inline.

Per-batch lifecycle:

  1. Verify `working_directory` is a git repo (uses `git rev-parse --show-toplevel`).
  2. Allocate a bearer token, register a `BatchContext` in the MCP server.
  3. `asyncio.gather` N sessions, each wrapped in `_run_session` with
     try/finally cleanup.
  4. Aggregate per-task `SessionResult`s into a `BatchResult`.
  5. Deregister the bearer token in the `finally` so 401s flip on the
     next MCP request after the batch settles.

Active sessions are tracked in `_active_sessions[(workflow_id, node_id)]`
so workflow cancel can target them.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import anyio

from core.logging import get_logger

from services.cli_agent.config import get_provider_config
from services.cli_agent.context_bridge import (
    SpecializedAgentContextBridge,
    is_context,
)
from services.cli_agent.factory import create_cli_provider, get_session_pool
from services.cli_agent.mcp_server import (
    BatchContext,
    issue_token,
    register_batch,
    unregister_batch,
)
from services.cli_agent.protocol import BatchResult, SessionResult
from services.cli_agent.session import AICliSession
from services.cli_agent.types import BaseAICliTaskSpec, ClaudeTaskSpec
from services.process_environment import without_onepassword_environment

logger = get_logger(__name__)


DEFAULT_MAX_PARALLEL = 5

BatchKey = Tuple[str, str]  # (workflow_id, node_id)


class AICliService:
    """Singleton service. Use `get_ai_cli_service()` to access."""

    def __init__(self) -> None:
        # workflow_id+node_id -> live session list (for cancel targeting).
        self._active_sessions: Dict[BatchKey, List[AICliSession]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_batch(
        self,
        provider_name: str,
        *,
        tasks: Iterable[BaseAICliTaskSpec],
        node_id: str,
        workflow_id: str,
        workspace_dir: Path,
        broadcaster: Any,
        repo_root: Optional[Path] = None,
        connected_skill_names: Optional[List[str]] = None,
        connected_skill_descriptors: Optional[List[Dict[str, Any]]] = None,
        connected_tools: Optional[List[Dict[str, Any]]] = None,
        connected_memory: Optional[Dict[str, Any]] = None,
        connected_context: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None,
        allowed_credentials: Optional[List[str]] = None,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        mcp_port: Optional[int] = None,
        ai_service: Any = None,
    ) -> BatchResult:
        """Run a list of CLI tasks under one batch.

        Returns:
            `BatchResult` aggregating per-task `SessionResult`s.

        Raises:
            ValueError / NotImplementedError: provider unknown / v2-deferred.
        """
        provider = create_cli_provider(provider_name)
        task_list: List[BaseAICliTaskSpec] = list(tasks)
        connected_tools = list(connected_tools or [])
        context_bridge: Optional[SpecializedAgentContextBridge] = None
        context_original_prompts: List[str] = []
        if is_context(connected_context):
            from services.plugin.deps import get_database

            provider_id = (
                "claude_code" if provider_name == "claude" else provider_name
            )
            context_bridge = await SpecializedAgentContextBridge.resolve(
                get_database(),
                connected_context,
                provider=provider_id,
                agent_node_id=node_id,
            )
            # The stored conversation is the continuity mechanism; provider
            # native resume only survives within a warm pool subprocess.
            # Keep the original prompts so the recorded turn never nests a
            # rendered transcript inside itself.
            context_original_prompts = [task.prompt for task in task_list]
            task_list = [
                task.model_copy(
                    update={
                        "prompt": context_bridge.augment_prompt(
                            task.prompt
                        )
                    }
                )
                for task in task_list
            ]

            # One conversation maps to one provider session.
            if provider_name == "claude" and len(task_list) != 1:
                raise ValueError(
                    "Context-bound Claude batches require exactly one "
                    "task so one conversation maps to one provider session."
                )

        # Pass the per-workflow workspace dir
        # (``data/workspaces/<workflow_id>/`` — injected into ctx by
        # ``workflow.py:_get_workspace_dir``) to the spawned claude via
        # ``--add-dir`` so claude's built-in filesystem tools (``Read``,
        # ``Edit``, ``Glob``, ``Grep``, ``Write``, ``Bash``) can access
        # files produced by upstream nodes (``fileDownloader``,
        # ``documentParser``, ``code`` executors, etc.). Without this
        # the workspace is invisible to claude: every session spawns in a
        # git worktree under ``<workspace>/<node>/``, which does not see
        # the workflow's workspace files.
        #
        # Mirrors the ai_agent pattern (``services/ai.py:1899`` —
        # ``config['workspace_dir'] = context.get('workspace_dir', '')``),
        # but uses claude's native ``--add-dir`` mechanism instead of
        # tool-config injection because claude has its own filesystem
        # tools rather than MCP-injected ones.
        workspace_str = str(Path(workspace_dir).resolve())
        for t in task_list:
            existing_add_dir = list(getattr(t, "add_dir", []) or [])
            if workspace_str not in existing_add_dir:
                existing_add_dir.append(workspace_str)
                # ``ClaudeTaskSpec.add_dir`` is ``List[str]`` per
                # ``types.py``; ``model_copy(update=...)`` is the
                # Pydantic-clean way to splice it without mutating.
                if hasattr(t, "model_copy"):
                    task_list[task_list.index(t)] = t.model_copy(
                        update={"add_dir": existing_add_dir},
                    )

        tool_names = [t.get("node_type") for t in (connected_tools or [])]
        memory_node = connected_memory.get("node_id") if connected_memory else None
        logger.info(
            "[CC-Agent run_batch] enter provider=%s node=%s wf=%s tasks=%d " "skills=%s tools=%s creds=%s memory=%s workspace=%s",
            provider_name,
            node_id,
            workflow_id,
            len(task_list),
            connected_skill_names or [],
            tool_names,
            allowed_credentials or [],
            memory_node,
            workspace_dir,
        )

        # Verify the working directory is under a git repo.
        resolved_repo_root = await self._resolve_repo_root(
            workspace_dir=workspace_dir,
            override=repo_root,
        )
        if resolved_repo_root is None:
            logger.warning(
                "[CC-Agent run_batch] aborting: workspace=%s is not inside a git "
                "repo (run `git init` there or set `working_directory` to "
                "an existing repo).",
                workspace_dir,
            )
            return self._abort_not_git_repo(
                provider_name=provider_name,
                tasks=task_list,
            )
        logger.info(
            "[CC-Agent run_batch] resolved repo_root=%s for workspace=%s",
            resolved_repo_root,
            workspace_dir,
        )

        # Only a runnable batch needs an MCP surface. Build it through the
        # same backend pipeline as native agents after inexpensive validation
        # has succeeded, so abort paths do not require AIService/DB access.
        connected_tools = await self._canonical_tool_surface(
            connected_tools,
            ai_service=ai_service,
        )
        from services.cli_agent.workflow_tools import _connected_tool_name

        connected_tool_names = [
            name
            for tool in connected_tools
            if (name := _connected_tool_name(tool))
        ]

        # Per-batch bearer token + MCP context
        from core.env_defaults import env_value

        token = issue_token()
        # Single source for the skill-turn scope: registration (via
        # BatchContext.execution_id) and the teardown clear must key on
        # the SAME value or badges never clear.
        turn_execution_id = execution_id or token
        port = mcp_port or int(
            os.environ.get("OPENCOMPANY_BACKEND_PORT")
            or os.environ.get("MACHINA_BACKEND_PORT")
            or env_value("PYTHON_BACKEND_PORT")
        )
        ctx = BatchContext(
            workflow_id=workflow_id,
            node_id=node_id,
            workspace_dir=Path(workspace_dir).resolve(),
            # A batch token is a safe ephemeral conversation scope when the
            # caller has no durable execution id; never share loaded-skill
            # state across later CLI runs of the same workflow/node.
            # NOTE: skill-turn state is registered under this exact value,
            # so the teardown clear MUST use it too — keying the clear on
            # the bare ``execution_id`` leaves badges lit forever on manual
            # canvas runs, where ``execution_id`` is None.
            execution_id=turn_execution_id,
            user_id=str((connected_context or {}).get("user_id") or "owner"),
            connected_skill_names=set(connected_skill_names or []),
            connected_skill_descriptors=list(connected_skill_descriptors or []),
            allowed_credentials=set(allowed_credentials or []),
            connected_tools=list(connected_tools or []),
            broadcaster=broadcaster,
        )
        register_batch(token, ctx)

        cfg = get_provider_config(provider_name)
        defaults = dict(cfg.defaults) if cfg else {}

        key: BatchKey = (workflow_id, node_id)
        async with self._lock:
            if key in self._active_sessions:
                logger.warning(
                    "[CC-Agent service] replacing stale session list for %s",
                    key,
                )
                # Cancel anything previously left dangling.
                for sess in self._active_sessions[key]:
                    try:
                        await sess.cleanup()
                    except Exception:
                        pass
            self._active_sessions[key] = []

        start = time.monotonic()
        await self._broadcast_phase(
            broadcaster,
            node_id,
            workflow_id,
            "batch_started",
            {
                "provider": provider_name,
                "n_tasks": len(task_list),
                "max_parallel": max_parallel,
                "isolation": "worktree",
            },
        )

        sem = asyncio.Semaphore(max(1, int(max_parallel)))

        async def run_one(task: BaseAICliTaskSpec) -> SessionResult:
            async with sem:
                session = AICliSession(
                    provider=provider,
                    task=task,
                    repo_root=resolved_repo_root,
                    workspace_dir=workspace_dir,
                    node_id=node_id,
                    workflow_id=workflow_id,
                    broadcaster=broadcaster,
                    defaults=defaults,
                    mcp_port=port,
                    batch_token=token,
                    connected_tool_names=connected_tool_names,
                    connected_skill_names=list(connected_skill_names or []),
                    memory_bound=bool(connected_memory) or (
                        context_bridge is not None and provider_name == "claude"
                    ),
                )
                async with self._lock:
                    self._active_sessions[key].append(session)
                try:
                    try:
                        await session.start()
                    except FileNotFoundError as exc:
                        return self._fail_result(provider_name, task, session.task_id, f"cli_not_installed: {exc}")
                    except RuntimeError as exc:
                        # `_pre_spawn` raises on git-worktree failure.
                        return self._fail_result(provider_name, task, session.task_id, f"worktree_setup_failed: {exc}")
                    except Exception as exc:
                        logger.exception("[CC-Agent service] start failed")
                        return self._fail_result(provider_name, task, session.task_id, f"start_failed: {exc}")
                    return await session.wait_for_completion(task.timeout_seconds)
                finally:
                    try:
                        await session.cleanup()
                    except Exception as exc:
                        logger.debug("[CC-Agent service] cleanup: %s", exc)
                    async with self._lock:
                        try:
                            self._active_sessions[key].remove(session)
                        except (KeyError, ValueError):
                            pass

        # Pool branch: every claude task runs through ``ClaudeSessionPool``
        # — a plain subprocess on stdio pipes speaking stream-json, the
        # same invocation the Agent SDK uses. ``AICliSession`` (PTY +
        # on-disk JSONL) never delivered the prompt on this provider and
        # a PTY stdin makes the CLI reject stream-json input (GitHub
        # issues #133 / #134), so it is no longer used for claude.
        #
        #   - Memory- or Context-bound: one warm session keyed by the
        #     conversation, released after the turn so later turns reuse
        #     it. The plugin enforces ``len(tasks)==1`` here. The pool
        #     owns the bearer token across batches (see the ``finally``).
        #   - Unbound: one ephemeral session per task keyed by task id,
        #     terminated once the whole batch is done so the shared
        #     token is unregistered exactly once (``unregister_batch``
        #     is idempotent).
        # A provider that registered a session pool from its plugin folder
        # (``register_session_pool``) runs pooled; the service never names
        # providers. Every pooled session runs in a git worktree under the
        # workflow workspace, branched off ``resolved_repo_root`` — the same
        # isolation the per-task ``AICliSession`` path uses — so the repo
        # root itself is never the CLI's cwd. Bound sessions keep one
        # stable worktree per agent node; ephemeral tasks get their own and
        # drop it afterwards.
        session_pool = get_session_pool(provider_name)
        use_pool = session_pool is not None
        bound_key = (
            context_bridge.pool_key
            if context_bridge is not None
            else connected_memory["node_id"] if connected_memory else None
        )
        from core.paths import safe_path_component
        from services.cli_agent.worktree import add_worktree, remove_worktree

        worktree_parent = Path(workspace_dir).resolve() / safe_path_component(node_id, "node")

        async def run_pooled(task: BaseAICliTaskSpec, session_key: Any, worktree_name: str) -> SessionResult:
            async with sem:
                cwd = await add_worktree(
                    resolved_repo_root,
                    worktree_parent / f"wt_{worktree_name}",
                    f"opencompany/{safe_path_component(node_id, 'node')}-{worktree_name}",
                )
                return await self._run_pooled_turn(
                    task=task,
                    session_key=session_key,
                    cwd=cwd,
                    workspace_dir=Path(workspace_dir).resolve(),
                    defaults=defaults,
                    mcp_port=port,
                    mcp_bearer_token=token,
                    connected_tools=connected_tools or [],
                    connected_skill_names=list(connected_skill_names or []),
                    workflow_id=workflow_id,
                )

        results: List[SessionResult]
        try:
            if use_pool and bound_key is not None:
                results = [await run_pooled(task_list[0], bound_key, "session")]
            elif use_pool:
                run_tag = str(turn_execution_id)[:8]
                ephemeral = [(f"{node_id}:{turn_execution_id}:{i}", f"{run_tag}_{i}") for i in range(len(task_list))]
                try:
                    results = await asyncio.gather(
                        *(run_pooled(t, key, name) for t, (key, name) in zip(task_list, ephemeral)),
                        return_exceptions=False,
                    )
                finally:
                    for key, name in ephemeral:
                        await session_pool.terminate(key)
                        try:
                            await remove_worktree(resolved_repo_root, worktree_parent / f"wt_{name}")
                        except Exception as exc:
                            logger.debug("[CC-Agent run_batch] worktree remove: %s", exc)
            else:
                results = await asyncio.gather(
                    *(run_one(t) for t in task_list),
                    return_exceptions=False,
                )
        finally:
            async with self._lock:
                self._active_sessions.pop(key, None)
            from services.skill_runtime import clear_skill_turn

            await clear_skill_turn(workflow_id, str(turn_execution_id), node_id)
            # When ``use_pool`` is True, the bearer token for THIS batch
            # is consumed by ``ClaudeSessionPool.acquire`` directly:
            #   - Cold spawn: pool stores ``token`` on
            #     ``PooledClaudeSession.batch_token`` so it survives
            #     across subsequent batches; ``_terminate_locked``
            #     unregisters it when the subprocess dies (drains
            #     FastMCP tool refcounts cleanly on eviction / pool
            #     shutdown).
            #   - Warm reuse: pool rebinds the surviving warm
            #     subprocess's persistent BatchContext to this batch's
            #     surface in place and unregisters ``token`` (which
            #     was redundant the moment we picked the warm path).
            # Either way the pool owns the token's fate, so we must
            # NOT unregister here for the pool path — doing so on
            # cold spawn would 401 every subsequent warm-reuse turn.
            if not use_pool:
                unregister_batch(token)

        # Memory bridge: persist claude's session_id + append the
        # rendered exchange to simpleMemory's markdown transcript so the
        # next run can `--resume <UUID>` and the UI sees the
        # conversation refresh live. Fire-and-forget — failure here
        # doesn't fail the batch.
        if connected_memory:
            try:
                await self._persist_memory(
                    connected_memory,
                    results,
                    broadcaster=broadcaster,
                    mutation_id=(
                        f"cli-memory:{execution_id}:{node_id}:"
                        f"{connected_memory.get('node_id', '')}"
                        if execution_id
                        else None
                    ),
                )
            except Exception as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "[CC-Agent run_batch] memory persistence failed: %s",
                    exc,
                )

        if context_bridge is not None:
            # Persist each completed exchange into the plain conversation
            # store. Best-effort: the provider already ran and billed, so a
            # save failure must not fail the batch.
            for original_prompt, item in zip(
                context_original_prompts, results
            ):
                if not (item.success and item.response):
                    continue
                try:
                    await context_bridge.record_turn(
                        original_prompt,
                        item.response,
                    )
                except Exception as exc:
                    logger.warning(
                        "[CC-Agent run_batch] conversation save failed; "
                        "continuing: %s",
                        exc,
                    )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        n_succeeded = sum(1 for r in results if r.success)
        n_failed = len(results) - n_succeeded

        # Cost roll-up: prefer the provider's reported cost (Claude exposes
        # `total_cost_usd` natively); for providers that don't (Codex,
        # Gemini v2), derive USD from `canonical_usage` via the existing
        # PricingService — a single source of truth for all LLM cost in
        # OpenCompany.
        for r in results:
            if r.cost_usd is None:
                derived = self._derive_cost(r, task_list)
                if derived is not None:
                    r.cost_usd = derived

        costs = [r.cost_usd for r in results]
        total_cost = None if any(c is None for c in costs) else round(sum(c or 0 for c in costs), 6)

        result = BatchResult(
            tasks=results,
            n_tasks=len(results),
            n_succeeded=n_succeeded,
            n_failed=n_failed,
            total_cost_usd=total_cost,
            wall_clock_ms=elapsed_ms,
            budget_remaining_usd=None,
            provider=provider_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        await self._broadcast_phase(
            broadcaster,
            node_id,
            workflow_id,
            "batch_complete",
            {
                "provider": provider_name,
                "n_succeeded": n_succeeded,
                "n_failed": n_failed,
                "total_cost_usd": total_cost,
                "wall_clock_ms": elapsed_ms,
            },
        )
        return result

    @staticmethod
    async def _canonical_tool_surface(
        connected_tools: List[Dict[str, Any]],
        *,
        ai_service: Any = None,
    ) -> List[Dict[str, Any]]:
        """Build the CLI/MCP surface through the normal agent tool pipeline."""

        if not connected_tools:
            return []
        from services.plugin.deps import get_ai_service
        from services.tool_identity import ensure_unique_tool_names

        ai_service = ai_service or get_ai_service()
        surface: List[Dict[str, Any]] = []
        identities: List[Dict[str, str]] = []
        for tool_info in connected_tools:
            tool, execution = await ai_service._build_tool_from_node(tool_info)
            if tool is None or execution is None:
                # Skip-and-log, matching the pre-existing contract. Raising
                # here fails the whole batch over one unbuildable node — and
                # this path serves every CLI agent, not only Context-bound
                # ones, so a single bad tool took down runs that never used
                # it. A surface that ends up entirely empty is caught below.
                logger.warning(
                    "[cli_agent] skipping unbuildable connected tool %r",
                    tool_info.get("node_type"),
                )
                continue
            entry = {
                **tool_info,
                "_agent_tool_name": tool.name,
                "_agent_tool_description": tool.description,
                "_agent_tool_schema": tool.parameters,
                "_agent_tool_input_model": tool.args_schema,
                "_agent_tool_execution": execution,
            }
            surface.append(entry)
            identities.append(
                {
                    "name": tool.name,
                    "node_id": str(tool_info.get("node_id") or ""),
                    "label": str(
                        tool_info.get("label")
                        or tool_info.get("node_type")
                        or "tool"
                    ),
                }
            )
        ensure_unique_tool_names(identities)
        return surface

    async def cancel_workflow(self, workflow_id: str) -> int:
        """Cancel every active session for a workflow. Returns count cancelled."""
        cancelled = 0
        async with self._lock:
            keys = [k for k in self._active_sessions if k[0] == workflow_id]
            sessions: List[AICliSession] = []
            for k in keys:
                sessions.extend(self._active_sessions[k])
        for sess in sessions:
            try:
                await sess.cleanup()
                cancelled += 1
            except Exception as exc:
                logger.debug("[CC-Agent service] cancel: %s", exc)
        return cancelled

    async def cancel_node(self, node_id: str) -> int:
        """Cancel every active session for a node. Returns count cancelled."""
        cancelled = 0
        async with self._lock:
            keys = [k for k in self._active_sessions if k[1] == node_id]
            sessions: List[AICliSession] = []
            for k in keys:
                sessions.extend(self._active_sessions[k])
        for sess in sessions:
            try:
                await sess.cleanup()
                cancelled += 1
            except Exception as exc:
                logger.debug("[CC-Agent service] cancel: %s", exc)
        return cancelled

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _run_pooled_turn(
        self,
        *,
        task: BaseAICliTaskSpec,
        session_key: Any,
        cwd: Path,
        workspace_dir: Path,
        defaults: Dict[str, Any],
        mcp_port: int,
        mcp_bearer_token: str,
        connected_tools: List[Dict[str, Any]],
        connected_skill_names: List[str],
        workflow_id: str,
        context_event_sink: Any = None,
    ) -> SessionResult:
        """Route one memory-bound claude turn through ``ClaudeSessionPool``.

        Cold spawn or warm reuse is hidden behind ``pool.acquire``; the
        ``/clear`` rotation and new-UUID capture happen inside the pool.
        The MCP bearer token stays with the spawned claude across batches
        — see the use-pool branch in :meth:`run_batch` for the rationale.

        The pool lives in the plugin folder (per the canonical
        plugin-folder layout); we look it up through the
        :func:`services.cli_agent.factory.get_session_pool` registry
        instead of importing directly so the framework stays free of
        any ``services → nodes`` layering violation.
        """
        if not isinstance(task, ClaudeTaskSpec):
            raise TypeError("Pooled turns require ClaudeTaskSpec, got " f"{type(task).__name__}")

        pool = get_session_pool(task.provider)
        if pool is None:
            raise RuntimeError(
                f"No session pool registered for {task.provider!r}. The "
                "provider's plugin __init__.py should call "
                "register_session_pool(<provider>, get_session_pool). "
                "Did its module fail to import?"
            )
        await pool.start_reaper()

        mcp_endpoint_url = f"http://127.0.0.1:{mcp_port}/mcp/ide/mcp"
        from services.cli_agent.workflow_tools import _connected_tool_name

        tool_names = [
            name
            for tool in (connected_tools or [])
            if (name := _connected_tool_name(tool))
        ]

        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        # Project-local claude auth dir — same as ``AICliSession.env``.
        from nodes.agent.claude_code_agent._oauth import OPENCOMPANY_CLAUDE_DIR

        env["CLAUDE_CONFIG_DIR"] = str(OPENCOMPANY_CLAUDE_DIR)
        # Composio-style parent-run-ID for MCP correlation.
        parent_run_id = f"{workflow_id}:{session_key}:{mcp_bearer_token[:8]}"
        env["OPENCOMPANY_PARENT_RUN_ID"] = parent_run_id
        env["MACHINA_PARENT_RUN_ID"] = parent_run_id  # legacy child-process contract

        try:
            pooled = await pool.acquire(
                session_key,
                spec=task,
                cwd=cwd,
                env=without_onepassword_environment(env),
                defaults=defaults,
                mcp_endpoint_url=mcp_endpoint_url,
                mcp_bearer_token=mcp_bearer_token,
                connected_tool_names=tool_names,
                connected_skill_names=connected_skill_names,
                workspace_dir=workspace_dir,
                workflow_id=workflow_id,
                context_event_sink=context_event_sink,
            )
        except BaseException:
            # A cold spawn can fail before the pool adopts the token. The
            # run_batch pool branch intentionally does not unregister in its
            # outer finally, so close this otherwise-orphaned context here.
            from services.cli_agent.mcp_server import unregister_batch

            unregister_batch(mcp_bearer_token)
            raise
        try:
            return await pool.send_turn(
                pooled,
                task.prompt,
                timeout_seconds=task.timeout_seconds,
                workflow_id=workflow_id,
            )
        finally:
            await pool.release(pooled)

    @staticmethod
    async def _clear_stale_session_id(
        connected_memory: Dict[str, Any],
    ) -> None:
        """Wipe a stale ``last_session_id`` that no longer maps to any
        JSONL under the current cwd's project dir.

        Triggered when claude reports ``No conversation found with
        session ID: <UUID>``. Preserves ``memory_content`` (the
        user-visible markdown mirror) since that's informational; only
        the resume UUID is broken.
        """
        from services.plugin.deps import get_database

        db = get_database()
        memory_node_id = connected_memory["node_id"]
        params = await db.get_node_parameters(memory_node_id) or {}
        prior = params.get("last_session_id")
        if not prior:
            return  # already cleared
        from services.memory.runtime import update_memory_parameters_atomic

        await update_memory_parameters_atomic(
            db,
            memory_node_id,
            parameter_updates={"last_session_id": None},
        )
        logger.warning(
            "[CC-Agent _persist_memory] cleared stale last_session_id=%s "
            "from memory_node=%s; next run will spawn a fresh claude "
            "session and persist its new UUID.",
            prior,
            memory_node_id,
        )

    @staticmethod
    async def _persist_memory(
        connected_memory: Dict[str, Any],
        results: List[SessionResult],
        broadcaster: Any = None,
        mutation_id: Optional[str] = None,
    ) -> None:
        """Append each successful run's user prompt + assistant response
        to ``simpleMemory.memory_content`` (markdown) and store the most
        recent ``session_id`` as ``last_session_id``, which the next run
        resumes with ``--resume``. Both go through one
        ``append_memory_turns_atomic`` call (one DB write).
        """
        successful = [r for r in results if r.success]
        logger.info(
            "[CC-Agent _persist_memory] memory_node=%s results=%d " "successful=%d session_ids=%s",
            connected_memory.get("node_id"),
            len(results),
            len(successful),
            [r.session_id for r in successful],
        )
        if not successful:
            logger.warning(
                "[CC-Agent _persist_memory] no successful runs; skipping " "save (memory_node=%s). Per-result: %s",
                connected_memory.get("node_id"),
                [{"success": r.success, "session_id": r.session_id, "error": (r.error or "")[:80]} for r in results],
            )
            # Auto-recovery: claude returns
            # ``No conversation found with session ID: <UUID>`` when the
            # `--resume <UUID>` we passed doesn't exist under the current
            # cwd's project dir (most often: a `last_session_id` saved
            # before the cwd-stability fix landed, or a session JSONL
            # that was wiped). Without this clear the same stale UUID
            # would re-fire on every retry and lock the user out
            # forever. The next run after this point will spawn a
            # fresh session and `_persist_memory` will save its UUID.
            stale = any(r.error and "No conversation found with session ID" in r.error for r in results)
            if stale:
                await AICliService._clear_stale_session_id(connected_memory)
            return

        from services.memory.runtime import append_memory_turns_atomic
        from services.plugin.deps import get_database

        db = get_database()
        memory_node_id = connected_memory["node_id"]

        # 1. Persist claude's returned session_id from the most recent
        # successful run. Drives `--resume <UUID>` on the next spawn so
        # claude finds and continues its own JSONL transcript on disk.
        last_run = next((r for r in reversed(successful) if r.session_id), None)
        window = int(connected_memory.get("window_size") or 100)
        params, removed_texts, _applied = await append_memory_turns_atomic(
            db,
            memory_node_id,
            [
                turn
                for result in successful
                for turn in (
                    ("human", result.prompt),
                    ("ai", result.response or ""),
                )
            ],
            window_size=window,
            mutation_id=mutation_id,
            parameter_updates=(
                {"last_session_id": last_run.session_id}
                if last_run is not None
                else None
            ),
        )
        content = params.get("memory_content") or ""
        logger.info(
            "[CC-Agent _persist_memory] saved memory_node=%s "
            "last_session_id=%s appended_turns=%d archived_blocks=%d "
            "content_length=%d",
            memory_node_id,
            params.get("last_session_id"),
            len(successful),
            len(removed_texts),
            len(content),
        )

        # Broadcast `node_parameters_updated` so the simpleMemory's
        # parameter panel + memory editor refetch live without a page
        # reload. CloudEvents v1.0 envelope (RFC §6.4) — type is
        # ``com.opencompany.node.parameters.updated``; ``source_hint="cli"``
        # distinguishes this Claude-CLI autonomous write from a user
        # parameter-panel save.
        if broadcaster is not None:
            try:
                await broadcaster.broadcast_node_parameters_updated(
                    memory_node_id,
                    parameters=params,
                    source_hint="cli",
                )
            except Exception as exc:
                logger.warning(
                    "[CC-Agent _persist_memory] broadcast failed: %s",
                    exc,
                )

        if connected_memory.get("long_term_enabled") and removed_texts:
            from services.memory.vector_store import get_memory_vector_store
            from services.plugin.deps import get_auth_service

            store = await get_memory_vector_store(
                connected_memory.get("session_id") or "default",
                provider=connected_memory.get(
                    "embedding_provider",
                    "huggingface",
                ),
                model=connected_memory.get("embedding_model"),
                endpoint=connected_memory.get("embedding_endpoint"),
                auth_service=get_auth_service(),
            )
            if store is not None:
                await store.add_texts(removed_texts)

    @staticmethod
    async def _resolve_repo_root(
        *,
        workspace_dir: Path,
        override: Optional[Path],
    ) -> Optional[Path]:
        """Find the git repo root via `git rev-parse --show-toplevel`.

        Contract:
          - When `override` is given, only consider that subtree.
          - When not given, try `workspace_dir` first, then `cwd`.
        """
        starts: List[Path]
        if override is not None:
            starts = [Path(override).resolve()]
        else:
            starts = [Path(workspace_dir).resolve(), Path.cwd().resolve()]

        for start in starts:
            try:
                result = await anyio.run_process(
                    ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                    check=False,
                    env=without_onepassword_environment(),
                )
            except FileNotFoundError:
                # `git` not on PATH at all — fail-fast, nothing to fall back to.
                return None
            if result.returncode == 0:
                root_text = (result.stdout or b"").decode("utf-8", errors="replace").strip()
                if root_text:
                    return Path(root_text)
        return None

    @staticmethod
    def _derive_cost(
        result: SessionResult,
        tasks: List[BaseAICliTaskSpec],
    ) -> Optional[float]:
        """Compute USD cost from `canonical_usage` via the central
        `PricingService`. Returns None when token counts are zero (the
        provider didn't surface them) — keeps the contract that
        ``cost_usd is None`` means "we genuinely don't know the cost"."""
        cu = result.canonical_usage
        total_tokens = cu.input_tokens + cu.output_tokens + cu.cache_read + cu.cache_write
        if total_tokens == 0:
            return None

        # Find the model the task requested (or the provider's default).
        model = ""
        for t in tasks:
            if (t.task_id or "") == result.task_id:
                model = t.model or ""
                break

        try:
            from services.pricing import get_pricing_service

            pricing = get_pricing_service()
            breakdown = pricing.calculate_cost(
                provider=result.provider,
                model=model,
                input_tokens=cu.input_tokens,
                output_tokens=cu.output_tokens,
                cache_read_tokens=cu.cache_read,
                cache_creation_tokens=cu.cache_write,
                reasoning_tokens=cu.reasoning_tokens,
            )
            total = breakdown.get("total_cost")
            return float(total) if total else None
        except Exception as exc:  # pragma: no cover — pricing is non-critical
            logger.debug("[CC-Agent service] pricing lookup failed: %s", exc)
            return None

    @staticmethod
    def _fail_result(
        provider_name: str,
        task: BaseAICliTaskSpec,
        task_id: str,
        error: str,
    ) -> SessionResult:
        return SessionResult(
            task_id=task_id,
            provider=provider_name,
            prompt=task.prompt,
            success=False,
            error=error,
        )

    @staticmethod
    async def _broadcast_phase(
        broadcaster: Any,
        node_id: str,
        workflow_id: str,
        phase: str,
        data: dict,
    ) -> None:
        if not broadcaster:
            return
        try:
            await broadcaster.update_node_status(
                node_id,
                "executing",
                {"phase": phase, **data},
                workflow_id=workflow_id,
            )
        except Exception:
            pass

    def _abort_not_git_repo(
        self,
        *,
        provider_name: str,
        tasks: List[BaseAICliTaskSpec],
    ) -> BatchResult:
        results: List[SessionResult] = [
            SessionResult(
                task_id=t.task_id or "t_unstarted",
                provider=provider_name,
                prompt=t.prompt,
                success=False,
                error="working_directory_not_git_repo",
            )
            for t in tasks
        ]
        return BatchResult(
            tasks=results,
            n_tasks=len(results),
            n_succeeded=0,
            n_failed=len(results),
            total_cost_usd=None,
            wall_clock_ms=0,
            budget_remaining_usd=None,
            provider=provider_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: Optional[AICliService] = None


def get_ai_cli_service() -> AICliService:
    global _instance
    if _instance is None:
        _instance = AICliService()
    return _instance
