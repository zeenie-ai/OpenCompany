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
from services.cli_agent.factory import create_cli_provider
from services.cli_agent.mcp_server import (
    BatchContext,
    issue_token,
    register_batch,
    unregister_batch,
)
from services.cli_agent.protocol import BatchResult, SessionResult
from services.cli_agent.session import AICliSession
from services.cli_agent.types import BaseAICliTaskSpec

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
        connected_tools: Optional[List[Dict[str, Any]]] = None,
        connected_memory: Optional[Dict[str, Any]] = None,
        allowed_credentials: Optional[List[str]] = None,
        max_parallel: int = DEFAULT_MAX_PARALLEL,
        mcp_port: Optional[int] = None,
    ) -> BatchResult:
        """Run a list of CLI tasks under one batch.

        Returns:
            `BatchResult` aggregating per-task `SessionResult`s.

        Raises:
            ValueError / NotImplementedError: provider unknown / v2-deferred.
        """
        provider = create_cli_provider(provider_name)
        task_list: List[BaseAICliTaskSpec] = list(tasks)
        tool_names = [t.get("node_type") for t in (connected_tools or [])]
        memory_node = (
            connected_memory.get("node_id") if connected_memory else None
        )
        logger.info(
            "[CC-Agent run_batch] enter provider=%s node=%s wf=%s tasks=%d "
            "skills=%s tools=%s creds=%s memory=%s workspace=%s",
            provider_name, node_id, workflow_id, len(task_list),
            connected_skill_names or [], tool_names,
            allowed_credentials or [], memory_node, workspace_dir,
        )

        # Verify the working directory is under a git repo.
        resolved_repo_root = await self._resolve_repo_root(
            workspace_dir=workspace_dir, override=repo_root,
        )
        if resolved_repo_root is None:
            logger.warning(
                "[CC-Agent run_batch] aborting: workspace=%s is not inside a git "
                "repo (run `git init` there or set `working_directory` to "
                "an existing repo).", workspace_dir,
            )
            return self._abort_not_git_repo(
                provider_name=provider_name,
                tasks=task_list,
            )
        logger.info(
            "[CC-Agent run_batch] resolved repo_root=%s for workspace=%s",
            resolved_repo_root, workspace_dir,
        )

        # Per-batch bearer token + MCP context
        token = issue_token()
        port = mcp_port or int(os.environ.get("MACHINA_BACKEND_PORT", "3010"))
        ctx = BatchContext(
            workflow_id=workflow_id,
            node_id=node_id,
            workspace_dir=Path(workspace_dir).resolve(),
            connected_skill_names=set(connected_skill_names or []),
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
                    "[CC-Agent service] replacing stale session list for %s", key,
                )
                # Cancel anything previously left dangling.
                for sess in self._active_sessions[key]:
                    try:
                        await sess.cleanup()
                    except Exception:
                        pass
            self._active_sessions[key] = []

        start = time.monotonic()
        await self._broadcast_phase(broadcaster, node_id, workflow_id, "batch_started", {
            "provider": provider_name,
            "n_tasks": len(task_list),
            "max_parallel": max_parallel,
            "isolation": "worktree",
        })

        sem = asyncio.Semaphore(max(1, int(max_parallel)))

        async def run_one(task: BaseAICliTaskSpec) -> SessionResult:
            async with sem:
                session = AICliSession(
                    provider=provider, task=task,
                    repo_root=resolved_repo_root, workspace_dir=workspace_dir,
                    node_id=node_id, workflow_id=workflow_id,
                    broadcaster=broadcaster, defaults=defaults,
                    mcp_port=port, batch_token=token,
                    connected_tool_names=[
                        t.get("node_type") for t in (connected_tools or [])
                        if t.get("node_type")
                    ],
                    connected_skill_names=list(connected_skill_names or []),
                )
                async with self._lock:
                    self._active_sessions[key].append(session)
                try:
                    try:
                        await session.start()
                    except FileNotFoundError as exc:
                        return self._fail_result(provider_name, task, session.task_id,
                                                 f"cli_not_installed: {exc}")
                    except RuntimeError as exc:
                        # `_pre_spawn` raises on git-worktree failure.
                        return self._fail_result(provider_name, task, session.task_id,
                                                 f"worktree_setup_failed: {exc}")
                    except Exception as exc:
                        logger.exception("[CC-Agent service] start failed")
                        return self._fail_result(provider_name, task, session.task_id,
                                                 f"start_failed: {exc}")
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

        try:
            results: List[SessionResult] = await asyncio.gather(
                *(run_one(t) for t in task_list),
                return_exceptions=False,
            )
        finally:
            async with self._lock:
                self._active_sessions.pop(key, None)
            unregister_batch(token)

        # Memory bridge: persist claude's session_id + append the
        # rendered exchange to simpleMemory's JSONL transcript so the
        # next run can `--resume <UUID>` and the UI shows the
        # conversation. Fire-and-forget — failure here doesn't fail
        # the batch.
        if connected_memory:
            try:
                await self._persist_memory(connected_memory, results)
            except Exception as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "[CC-Agent run_batch] memory persistence failed: %s",
                    exc,
                )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        n_succeeded = sum(1 for r in results if r.success)
        n_failed = len(results) - n_succeeded

        # Cost roll-up: prefer the provider's reported cost (Claude exposes
        # `total_cost_usd` natively); for providers that don't (Codex,
        # Gemini v2), derive USD from `canonical_usage` via the existing
        # PricingService — a single source of truth for all LLM cost in
        # MachinaOs.
        for r in results:
            if r.cost_usd is None:
                derived = self._derive_cost(r, task_list)
                if derived is not None:
                    r.cost_usd = derived

        costs = [r.cost_usd for r in results]
        total_cost = (
            None if any(c is None for c in costs) else round(sum(c or 0 for c in costs), 6)
        )

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

        await self._broadcast_phase(broadcaster, node_id, workflow_id, "batch_complete", {
            "provider": provider_name,
            "n_succeeded": n_succeeded,
            "n_failed": n_failed,
            "total_cost_usd": total_cost,
            "wall_clock_ms": elapsed_ms,
        })
        return result

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

    @staticmethod
    async def _persist_memory(
        connected_memory: Dict[str, Any],
        results: List[SessionResult],
    ) -> None:
        """Append each successful run's user prompt + assistant response
        to ``simpleMemory.memory_content`` (markdown). Mirrors aiAgent /
        chatAgent / deep_agent / rlm_agent's persistence pattern exactly
        — same helpers (``append_to_memory_markdown``,
        ``trim_markdown_window``), same field. One DB write.
        """
        successful = [r for r in results if r.success]
        logger.info(
            "[CC-Agent _persist_memory] memory_node=%s results=%d "
            "successful=%d",
            connected_memory.get("node_id"),
            len(results),
            len(successful),
        )
        if not successful:
            logger.warning(
                "[CC-Agent _persist_memory] no successful runs; skipping "
                "save (memory_node=%s). Per-result: %s",
                connected_memory.get("node_id"),
                [
                    {"success": r.success, "error": (r.error or "")[:80]}
                    for r in results
                ],
            )
            return

        from services.memory import (
            append_to_memory_markdown,
            trim_markdown_window,
        )
        from services.plugin.deps import get_database

        db = get_database()
        memory_node_id = connected_memory["node_id"]
        params = await db.get_node_parameters(memory_node_id) or {}

        content = params.get("memory_content") or (
            "# Conversation History\n\n*No messages yet.*\n"
        )
        for r in successful:
            content = append_to_memory_markdown(content, "human", r.prompt)
            content = append_to_memory_markdown(
                content, "ai", r.response or "",
            )

        window = int(connected_memory.get("window_size") or 100)
        content, removed_texts = trim_markdown_window(content, window)
        params["memory_content"] = content

        await db.save_node_parameters(memory_node_id, params)
        logger.info(
            "[CC-Agent _persist_memory] saved memory_node=%s "
            "appended_turns=%d archived_blocks=%d content_length=%d",
            memory_node_id, len(successful),
            len(removed_texts), len(content),
        )

        if connected_memory.get("long_term_enabled") and removed_texts:
            from services.memory.vector_store import get_memory_vector_store

            store = get_memory_vector_store(
                connected_memory.get("session_id") or "default",
            )
            if store is not None:
                await asyncio.to_thread(store.add_texts, removed_texts)

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
