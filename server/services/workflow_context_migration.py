"""Second phase for Context workflow normalization.

The pure graph migration (``services/workflow_migrations.py``) returns
state-import receipts after canonical IDs are known. With the plain
conversation store (``key → messages JSON``) there is no journal to import
legacy markdown into: a migrated graph simply starts its next generation
with an empty conversation, and the legacy markdown remains inert on the
old node parameters (``SimpleMemoryParams`` declares ``extra="ignore"``).
The receipt-processing entry points are kept so callers need no changes,
but they are deliberate no-ops now.
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable, Mapping

from core.logging import get_logger

logger = get_logger(__name__)


async def load_node_parameters(
    database: Any,
    nodes: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Hydrate parameters without serial reads at graph boundaries."""

    node_ids = [
        str(node["id"])
        for node in nodes
        if node.get("id") is not None
    ]
    getter = getattr(database, "get_node_parameters", None)
    if not callable(getter):
        return {}
    values = await asyncio.gather(
        *(getter(node_id) for node_id in node_ids)
    )
    return {
        node_id: dict(value or {})
        for node_id, value in zip(node_ids, values)
        if value
    }


async def import_legacy_context_receipts(
    database: Any,
    receipts: Iterable[Mapping[str, Any]],
) -> int:
    """No-op under the plain conversation store.

    Legacy markdown/provider-binding receipts were journal imports; the
    plain store starts migrated conversations fresh. Logged so a migration
    that produced receipts is still visible in the operator log.
    """

    count = sum(1 for _ in receipts)
    if count:
        logger.info(
            f"[migration] {count} legacy context receipt(s) skipped — the "
            "plain conversation store starts migrated conversations fresh"
        )
    return 0


async def persist_parameter_aliases(
    database: Any,
    *,
    aliases: Mapping[str, str],
    parameters: Mapping[str, Mapping[str, Any]],
) -> None:
    """Rekey node configuration onto canonical node ids.

    This only moves rows; it never edits their contents. It previously also
    stripped a set of "legacy runtime fields" from every node, which destroyed
    real configuration: ``session_id`` is in that set and is a declared,
    load-bearing parameter on ``chatTrigger`` / ``chatSend`` / ``chatHistory``,
    so an ordinary read silently widened a chat trigger to match every session.

    Retiring them is unnecessary as well as unsafe. ``SimpleMemoryParams``
    declares ``extra="ignore"``, so a leftover ``memory_content`` on a migrated
    node is already inert — it reaches neither runtime behaviour nor the
    NodeSpec.
    """

    reverse_aliases = {new: old for old, new in aliases.items()}
    save = getattr(database, "save_node_parameters", None)
    remove = getattr(database, "delete_node_parameters", None)
    if not callable(save):
        return
    for node_id, raw_params in parameters.items():
        if await save(node_id, dict(raw_params or {})) is False:
            raise RuntimeError(f"Could not persist parameters for {node_id}")
        old_id = reverse_aliases.get(node_id)
        if old_id and old_id != node_id and callable(remove):
            await remove(old_id)


async def archive_removed_contexts(
    database: Any,
    *,
    workflow_id: str,
    previous_nodes: Iterable[Mapping[str, Any]],
    normalized_nodes: Iterable[Mapping[str, Any]],
    aliases: Mapping[str, str],
) -> int:
    """No-op under the plain conversation store.

    Conversations are keyed by agent node, not Context node; removing a
    Context node merely opts the agent out of persistence going forward.
    Stored rows stay until the workflow is deleted (or cleared from the
    panel) — harmless, and cheaper than guessing which agent they served.
    """

    return 0


__all__ = [
    "archive_removed_contexts",
    "import_legacy_context_receipts",
    "load_node_parameters",
    "persist_parameter_aliases",
]
