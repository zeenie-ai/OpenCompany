"""Atomic persistence helpers for runtime conversation memory.

All agent bridges share the same ``simpleMemory`` JSON row.  Appending by
reading the row and later replacing it loses turns whenever two agents finish
at the same time.  This module keeps the markdown transformation inside the
database's reserved write transaction and optionally records a durable
idempotency key for workflow/activity retries.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from services.memory.markdown import (
    append_to_memory_markdown,
    trim_markdown_window,
)

MemoryTurn = Tuple[str, str]


async def append_memory_turns_atomic(
    database: Any,
    memory_node_id: str,
    turns: Iterable[MemoryTurn],
    *,
    window_size: int,
    mutation_id: Optional[str] = None,
    parameter_updates: Optional[Dict[str, Any]] = None,
    remove_parameters: Sequence[str] = (),
    replacement_content: Optional[str] = None,
    expected_content: Optional[str] = None,
) -> Tuple[Dict[str, Any], list[str], bool]:
    """Append one or more role/message pairs without stale overwrites.

    ``replacement_content`` is used for a compaction summary only when the
    stored content still equals ``expected_content``.  If another writer has
    appended since compaction started, its newer content wins and this turn is
    appended to it instead of erasing it.

    The fallback path preserves compatibility with lightweight database mocks
    used by plugin tests; production :class:`core.database.Database` always
    provides ``mutate_node_parameters_atomic``.
    """
    materialized_turns = [(str(role), str(message)) for role, message in turns]
    updates = dict(parameter_updates or {})
    # Removed transcript blocks are needed only by the process that actually
    # applies this mutation (for optional long-term archival). Keep them out
    # of RuntimeMutation.result so trimming memory does not silently retain
    # the deleted conversation text in the idempotency ledger.
    removed_for_applied: list[str] = []

    def _transform(params: Dict[str, Any]):
        current_content = params.get("memory_content") or (
            "# Conversation History\n\n*No messages yet.*\n"
        )
        if (
            replacement_content is not None
            and (expected_content is None or current_content == expected_content)
        ):
            content = replacement_content
        else:
            content = current_content

        for role, message in materialized_turns:
            content = append_to_memory_markdown(content, role, message)
        content, removed_texts = trim_markdown_window(
            content,
            max(1, int(window_size)),
        )
        removed_for_applied[:] = removed_texts

        params.update(updates)
        for key in remove_parameters:
            params.pop(key, None)
        params["memory_content"] = content
        params.pop("memoryContent", None)
        return params, {
            "appended_turns": len(materialized_turns),
            "trimmed_count": len(removed_texts),
        }

    atomic_mutate = getattr(database, "mutate_node_parameters_atomic", None)
    if atomic_mutate is not None and inspect.iscoroutinefunction(atomic_mutate):
        params, _metadata, applied = await atomic_mutate(
            memory_node_id,
            _transform,
            mutation_id=mutation_id,
            operation="append_memory",
        )
        # The mutator closure is invoked only for a newly applied write. A
        # ledger retry skips it, naturally returning no blocks for duplicate
        # archival while the durable metadata remains content-free.
        removed_texts = list(removed_for_applied) if applied else []
        return params, removed_texts, applied

    # Compatibility for existing isolated unit-test doubles.  This remains a
    # read/modify/write only when no production atomic API is available.
    params = await database.get_node_parameters(memory_node_id) or {}
    params, metadata = _transform(dict(params))
    await database.save_node_parameters(memory_node_id, params)
    return params, list(removed_for_applied), True


async def update_memory_parameters_atomic(
    database: Any,
    memory_node_id: str,
    *,
    parameter_updates: Optional[Dict[str, Any]] = None,
    remove_parameters: Sequence[str] = (),
    mutation_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Atomically update non-transcript fields on a memory node."""
    updates = dict(parameter_updates or {})

    def _transform(params: Dict[str, Any]):
        params.update(updates)
        for key in remove_parameters:
            params.pop(key, None)
        return params

    atomic_mutate = getattr(database, "mutate_node_parameters_atomic", None)
    if atomic_mutate is not None and inspect.iscoroutinefunction(atomic_mutate):
        params, _metadata, applied = await atomic_mutate(
            memory_node_id,
            _transform,
            mutation_id=mutation_id,
            operation="update_memory_state",
        )
        return params, applied

    params = await database.get_node_parameters(memory_node_id) or {}
    params = _transform(dict(params))
    await database.save_node_parameters(memory_node_id, params)
    return params, True
