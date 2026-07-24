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
also reset through the atomic parameter mutation path so the
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

    Modern ``TodoService`` entries are keyed by workflow + writeTodos node.
    Clearing a workflow removes every v2 node-scoped key plus the historical
    workflow/session/default fallbacks used by legacy callers.

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
    from services.memory.vector_store import clear_memory_vector_stores
    from services.todo_service import UNSAVED_WORKFLOW_ID, get_todo_service

    cleared_vector_store = False
    if clear_long_term:
        cleared_vector_store = await clear_memory_vector_stores(session_id)
    if cleared_vector_store:
        logger.info(f"[Memory] Cleared vector store for session '{session_id}'")

    todo_service = get_todo_service()
    cleared_todo_keys: List[str] = []

    cleared_todo_keys.extend(
        todo_service.clear_workflow(workflow_id or UNSAVED_WORKFLOW_ID)
    )

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
        from services.memory.runtime import update_memory_parameters_atomic

        # This shares the same BEGIN IMMEDIATE transaction path as runtime
        # appends, preventing a clear from restoring a stale parameter
        # snapshot over unrelated concurrent updates.
        await update_memory_parameters_atomic(
            db,
            memory_node_id,
            parameter_updates={
                "memory_content": DEFAULT_MEMORY_CONTENT,
                "last_session_id": None,
            },
            remove_parameters=(
                "memory_jsonl",
                "vertex_interaction_id",
                "vertex_environment_id",
            ),
        )
        cleared_memory_node = True
        logger.info(
            "[Memory] Cleared simpleMemory node fields memory_node=%s " "(memory_content reset + last_session_id wiped)",
            memory_node_id,
        )

    logger.info(
        "[Memory] Cleared agent session state session=%s workflow_id=%s " "vector_store=%s todo_keys=%s memory_node=%s",
        session_id,
        workflow_id,
        cleared_vector_store,
        cleared_todo_keys,
        cleared_memory_node,
    )

    return {
        "cleared_vector_store": cleared_vector_store,
        "cleared_todo_keys": cleared_todo_keys,
        "cleared_memory_node": cleared_memory_node,
    }
