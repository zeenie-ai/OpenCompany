"""Cross-store agent-session state-clear orchestration.

"Memory" from the user's perspective is not just the markdown
transcript — it's every piece of state an agent reuses across iterations
of a conversation. ``simpleMemory.memory_content`` is the visible part;
the long-term vector store and ``TodoService`` plan-work-update lists
are the invisible parts that quietly bloat subsequent runs (notably
``task_agent``, whose default skill bundle instructs the LLM to read
accumulated todos every run).

When a ``memory_node_id`` is provided, the simpleMemory node's own
fields (``memory_content``, ``memory_jsonl``, ``last_session_id``) are
also reset to defaults via ``database.save_node_parameters`` so the
claude_code_agent JSONL bridge surface clears alongside.
"""

from typing import Any, Dict, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


DEFAULT_MEMORY_CONTENT = "# Conversation History\n\n*No messages yet.*\n"


async def clear_agent_session_state(
    session_id: str,
    workflow_id: str = None,
    clear_long_term: bool = False,
    memory_node_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Clear every store keyed by an agent's conversational scope.

    ``TodoService`` is keyed by ``ctx.workflow_id or ctx.node_id or
    "default"`` (see ``server/nodes/tool/write_todos.py``). We clear all
    three candidate keys to match whichever fallback the agent actually
    used at write time.

    Args:
        session_id: ``simpleMemory`` node's ``session_id`` parameter.
        workflow_id: Active workflow id (passed by the frontend so we
            can clear ``TodoService`` entries written under it).
        clear_long_term: When ``True``, drop the per-session vector
            store too.
        memory_node_id: When provided, reset the simpleMemory node's
            ``memory_content`` to the default placeholder and wipe
            ``memory_jsonl`` + ``last_session_id``. Without this the
            JSONL bridge fields stay populated and ``--resume`` retries
            stale UUIDs. Frontend-driven legacy clears omit this and
            handle markdown reset client-side.

    Returns:
        Dict with ``cleared_vector_store`` (bool), ``cleared_todo_keys``
        (list[str]), and ``cleared_memory_node`` (bool) for
        caller-visible diagnostics.
    """
    # The live vector-store cache lives in ``services.ai`` (the dict in
    # ``services.memory.vector_store`` is dormant — future cleanup will
    # consolidate). Lazy import keeps ``services.ai``'s heavy LangChain
    # deps off the hot path.
    from services.ai import _memory_vector_stores as _live_vector_stores
    from services.todo_service import get_todo_service

    cleared_vector_store = False
    if clear_long_term and session_id in _live_vector_stores:
        del _live_vector_stores[session_id]
        cleared_vector_store = True
        logger.info(f"[Memory] Cleared vector store for session '{session_id}'")

    todo_service = get_todo_service()
    cleared_todo_keys: List[str] = []
    seen = set()
    for key in (workflow_id, session_id, "default"):
        if key and key not in seen:
            seen.add(key)
            todo_service.clear(key)
            cleared_todo_keys.append(key)

    cleared_memory_node = False
    if memory_node_id:
        from services.plugin.deps import get_database

        db = get_database()
        params = await db.get_node_parameters(memory_node_id) or {}
        params["memory_content"] = DEFAULT_MEMORY_CONTENT
        params["memory_jsonl"] = None
        params["last_session_id"] = None
        await db.save_node_parameters(memory_node_id, params)
        cleared_memory_node = True
        logger.info(
            "[Memory] Cleared simpleMemory node fields memory_node=%s "
            "(memory_content reset, memory_jsonl + last_session_id wiped)",
            memory_node_id,
        )

    logger.info(
        "[Memory] Cleared agent session state session=%s workflow_id=%s "
        "vector_store=%s todo_keys=%s memory_node=%s",
        session_id, workflow_id, cleared_vector_store, cleared_todo_keys,
        cleared_memory_node,
    )

    return {
        "cleared_vector_store": cleared_vector_store,
        "cleared_todo_keys": cleared_todo_keys,
        "cleared_memory_node": cleared_memory_node,
    }
