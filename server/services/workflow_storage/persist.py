"""Saving a brand-new workflow built on the server.

One path for every server-side creation (an import, a hired employee):
normalize the graph (canonical ids, current graph version), import any
legacy context receipts, pick a slug from the name, save the sanitized
graph with its owner, save each node's parameters, and announce it with a
``workflow_lifecycle`` broadcast so open editors refresh their lists.
The caller has already validated the graph and allocated the id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PersistedWorkflow:
    workflow_id: str
    slug: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    parameters: Dict[str, Dict[str, Any]]
    aliases: Dict[str, str] = field(default_factory=dict)


class PersistError(RuntimeError):
    """The workflow could not be saved. ``code`` is the error to report."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def persist_new_workflow(
    database: Any,
    *,
    workflow_id: str,
    name: str,
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    parameters: Dict[str, Dict[str, Any]],
    description: Optional[str] = None,
    owner_id: Optional[str] = None,
    lifecycle_stage: str = "created",
) -> PersistedWorkflow:
    from services.status_broadcaster import get_status_broadcaster
    from services.workflow_context_migration import import_legacy_context_receipts, persist_parameter_aliases
    from services.workflow_migrations import normalize_workflow_graph
    from services.workflow_naming import next_available_slug
    from services.workflow_sanitizer import sanitize_workflow_graph

    normalization = normalize_workflow_graph(workflow_id, nodes, edges, parameters)
    imported = await import_legacy_context_receipts(database, normalization.state_imports)
    if imported != len(normalization.state_imports):
        raise PersistError("context_state_import_failed")
    slug = await next_available_slug(name, database)
    graph: Dict[str, Any] = {
        "graphVersion": normalization.graph_version,
        "nodes": normalization.nodes,
        "edges": normalization.edges,
    }
    if owner_id:
        graph["owner_id"] = owner_id
    data = sanitize_workflow_graph(graph)
    saved = await database.save_workflow(workflow_id=workflow_id, name=name, slug=slug, description=description, data=data)
    if not saved:
        raise PersistError("save_failed")
    await persist_parameter_aliases(database, aliases=normalization.aliases, parameters=normalization.node_parameters)

    try:
        await get_status_broadcaster().broadcast_workflow_lifecycle(
            lifecycle_stage,
            workflow_id=workflow_id,
            name=name,
            node_count=len(normalization.nodes),
            edge_count=len(normalization.edges),
        )
    except Exception:
        # The workflow is saved either way; other tabs refresh on their own.
        logger.debug("workflow_lifecycle broadcast failed", workflow_id=workflow_id, exc_info=True)

    return PersistedWorkflow(
        workflow_id=workflow_id,
        slug=slug,
        nodes=list(data["nodes"]),
        edges=list(data["edges"]),
        parameters=dict(normalization.node_parameters),
        aliases=dict(normalization.aliases),
    )


__all__ = ["PersistError", "PersistedWorkflow", "persist_new_workflow"]
