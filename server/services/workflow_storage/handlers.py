"""Workflow storage WS handlers extracted from ``routers/websocket.py`` (Wave 13.7).

5 handlers wrapping the workflow-record CRUD surface. ``save_workflow``
detects display-name changes and applies the slug-rename side effects
(folder move + lifecycle broadcast) inline, so the frontend's existing
auto-save chain IS the rename path — no separate rename endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import WebSocket

from core.config import Settings
from core.container import container
from core.logging import get_logger
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)

_CONTEXT_TOPOLOGY_ERROR_CODES = frozenset(
    {
        "INVALID_CONTEXT_EDGE",
        "MULTIPLE_CONTEXTS",
        "SHARED_CONTEXT",
    }
)


class _AssumeCredentialsPresent:
    """Avoid credential lookups while validating graph-only invariants."""

    async def has_valid_key(self, provider_id: str) -> bool:
        return True


async def _context_topology_errors(
    nodes: list[Dict[str, Any]],
    edges: list[Dict[str, Any]],
    parameters_by_id: Dict[str, Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Run the shared validator and retain only Context invariants."""

    from services.workflow_validator import validate_workflow

    report = await validate_workflow(
        nodes,
        edges,
        parameters_by_id=parameters_by_id,
        auth_service=_AssumeCredentialsPresent(),
        enforce_context=True,
    )
    return [issue for issue in report.get("errors", []) if issue.get("code") in _CONTEXT_TOPOLOGY_ERROR_CODES]


def _trusted_owner_id(websocket: WebSocket, existing: Any) -> str:
    """Resolve workflow ownership without trusting client graph fields."""

    state = getattr(websocket, "state", None)
    authenticated = getattr(state, "user_id", None)
    if authenticated is not None and str(authenticated).strip():
        return str(authenticated).strip()
    existing_data = getattr(existing, "data", None)
    if isinstance(existing_data, dict):
        stored = existing_data.get("owner_id")
        if stored is not None and str(stored).strip():
            return str(stored).strip()
    return "owner"


def _supports_context_archive_outbox(database: Any) -> bool:
    return callable(
        getattr(
            database,
            "list_workflow_context_archive_outbox",
            None,
        )
    ) and callable(
        getattr(
            database,
            "complete_workflow_context_archive_outbox",
            None,
        )
    )


async def _drain_context_archive_outbox(
    database: Any,
    workflow_id: str,
) -> tuple[int, int]:
    """Archive pending Context identities and acknowledge each durably."""

    if not _supports_context_archive_outbox(database):
        return 0, 0
    pending = await database.list_workflow_context_archive_outbox(workflow_id)
    if not pending:
        return 0, 0

    from services.agent_context import clear_conversation

    completed = 0
    for receipt in pending:
        outbox_id = str(receipt.get("id") or "")
        context_node_id = str(receipt.get("context_node_id") or "")
        if not outbox_id or not context_node_id:
            logger.error(
                "[workflow] invalid Context archive outbox identity for workflow %s",
                workflow_id,
            )
            continue
        try:
            # Plain store: deleting the workflow deletes its conversations.
            # Idempotent per receipt — a second pass finds nothing to clear.
            await clear_conversation(database, workflow_id=workflow_id)
            acknowledged = await database.complete_workflow_context_archive_outbox(
                outbox_id=outbox_id,
                workflow_id=workflow_id,
            )
            if acknowledged:
                completed += 1
        except Exception:
            # The graph mutation is already durable. Leave the receipt pending
            # so a later save/get/delete boundary safely retries it.
            logger.exception(
                "[workflow] Context archive outbox drain failed (workflow=%s context=%s receipt=%s)",
                workflow_id,
                context_node_id,
                outbox_id,
            )

    remaining = await database.list_workflow_context_archive_outbox(workflow_id)
    return completed, len(remaining)


def _move_workspace(old_slug: str, new_slug: str) -> None:
    """Rename ``<workspace_base>/<old_slug>/`` on disk. No-op when the
    source is missing (lazy creation) or the target already exists.
    Failure is cosmetic — DB is the source of truth.
    """
    if not old_slug or old_slug == new_slug:
        return
    base = Path(Settings().workspace_base_resolved)
    src, dst = base / old_slug, base / new_slug
    if not src.is_dir() or dst.exists():
        return
    try:
        src.rename(dst)
        logger.info("[workflow] workspace renamed %s -> %s", old_slug, new_slug)
    except OSError as exc:
        logger.warning("[workflow] workspace rename failed: %s", exc)


async def _broadcast_lifecycle(stage: str, workflow_id: str, **data: Any) -> None:
    """CloudEvents ``workflow.{stage}``: every client's workflow list (the
    editor sidebar, Normal mode's team) refreshes. Best-effort."""
    try:
        from services.status_broadcaster import get_status_broadcaster

        await get_status_broadcaster().broadcast_workflow_lifecycle(stage, workflow_id=workflow_id, **data)
    except Exception:
        logger.debug("[workflow] %s broadcast failed", stage, exc_info=True)


async def _broadcast_renamed(workflow_id: str, name: str, slug: str, old_slug: str) -> None:
    """CloudEvents ``workflow.renamed`` — sidebar + open workflow refresh."""
    await _broadcast_lifecycle("renamed", workflow_id, name=name, slug=slug, old_slug=old_slug)


async def handle_save_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save workflow. Re-slugs + renames workspace dir when name changes.

    The frontend's auto-save chain (TopToolbar inline rename ->
    ``updateWorkflow({name})`` -> debounced save) flows through this
    handler, so renaming happens here — no dedicated rename endpoint.
    """
    from services.workflow_naming import next_available_slug

    database = container.database()
    requested_id = str(data.get("workflow_id") or "").strip()
    is_new_marker = requested_id.lower() in {"", "new"}
    if requested_id and not is_new_marker:
        await _drain_context_archive_outbox(database, requested_id)
        existing = await database.get_workflow(requested_id)
    else:
        existing = None
    if is_new_marker:
        workflow_id = await database.allocate_workflow_id()
    elif existing is None:
        return {"success": False, "error": "workflow_not_found"}
    else:
        workflow_id = requested_id
    name = data["name"]
    storage_id = existing.id if existing is not None else workflow_id

    if existing and existing.name == name and existing.slug:
        slug = existing.slug
    else:
        slug = await next_available_slug(name, database, exclude_id=workflow_id)

    workflow_data = data.get("data", {})
    from services.workflow_context_migration import (
        archive_removed_contexts,
        import_legacy_context_receipts,
        load_node_parameters,
        persist_parameter_aliases,
    )
    from services.workflow_migrations import normalize_workflow_graph

    # The submitted graph is authoritative for Context nodes: one the user
    # deleted stays deleted, and one the user added is kept.
    source_nodes = [node for node in workflow_data.get("nodes") or [] if isinstance(node, dict)]
    source_edges = [edge for edge in workflow_data.get("edges") or [] if isinstance(edge, dict)]
    source_params = await load_node_parameters(database, source_nodes)
    normalization = normalize_workflow_graph(
        workflow_id,
        source_nodes,
        source_edges,
        source_params,
    )
    migration_warnings = list(normalization.warnings)
    context_errors = await _context_topology_errors(
        normalization.nodes,
        normalization.edges,
        normalization.node_parameters,
    )
    from services.workflow_sanitizer import sanitize_workflow_graph

    normalized_graph = normalization.graph_data(workflow_data)
    normalized_graph["owner_id"] = _trusted_owner_id(websocket, existing)
    normalized_data = sanitize_workflow_graph(normalized_graph)
    if context_errors:
        return {
            "success": False,
            "error": "invalid_context_topology",
            "workflow_id": workflow_id,
            "validation_errors": context_errors,
            "migration_warnings": migration_warnings,
            "node_id_aliases": normalization.aliases,
            "data": normalized_data,
        }

    # Import exact legacy artifacts before replacing their only topology
    # pointer. Receipts are idempotent, so a failed graph save is safe to retry.
    await import_legacy_context_receipts(
        database,
        normalization.state_imports,
    )
    save_kwargs: Dict[str, Any] = {
        "workflow_id": storage_id,
        "name": name,
        "slug": slug,
        # Omitting this nulled the description on every save, because
        # ``save_workflow`` writes the column unconditionally.
        "description": getattr(existing, "description", None),
        "data": normalized_data,
    }
    if _supports_context_archive_outbox(database):
        save_kwargs["context_id_aliases"] = normalization.aliases
    success = await database.save_workflow(
        **save_kwargs,
    )
    if success:
        await persist_parameter_aliases(
            database,
            aliases=normalization.aliases,
            parameters=normalization.node_parameters,
        )
        if _supports_context_archive_outbox(database):
            context_archives_completed, context_archives_pending = await _drain_context_archive_outbox(
                database,
                workflow_id,
            )
        else:
            # Compatibility for isolated test doubles and older storage
            # implementations. Production Database commits the outbox in the
            # same transaction as the graph.
            context_archives_completed = await archive_removed_contexts(
                database,
                workflow_id=workflow_id,
                previous_nodes=((existing.data or {}).get("nodes") or [] if existing is not None else []),
                normalized_nodes=normalization.nodes,
                aliases=normalization.aliases,
            )
            context_archives_pending = 0
    else:
        context_archives_completed = 0
        context_archives_pending = 0

    if existing and existing.slug and existing.slug != slug:
        _move_workspace(existing.slug, slug)
        await _broadcast_renamed(workflow_id, name, slug, existing.slug)
    elif success and existing is None:
        await _broadcast_lifecycle("created", workflow_id, name=name, slug=slug)

    return {
        "success": success,
        "workflow_id": workflow_id,
        "name": name,
        "slug": slug,
        "migration_warnings": migration_warnings,
        "node_id_aliases": normalization.aliases,
        "context_archives_completed": context_archives_completed,
        "context_archives_pending": context_archives_pending,
        # The server owns topology normalization. The editor replaces its
        # local draft with this authoritative graph instead of reimplementing
        # Context lifecycle rules.
        "data": normalized_data,
    }


@ws_handler()
async def handle_import_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Import a workflow JSON. Two-step UX:

    First call with just the workflow object returns a preview if
    confirmations are needed (name conflict, missing credentials). The
    frontend prompts the user, then re-calls with ``name`` set and
    ``force_credentials=True`` to commit.

    Body fields:
        workflow: Raw workflow dict (nodes, edges, optional nodeParameters).
        name: User-confirmed final workflow name; omit on first call to
            let the server report a name conflict.
        force_credentials: Skip the missing-credential preview gate when
            the user has acknowledged the warning.

    See ``services.workflow_import.import_workflow`` for the full
    orchestrator contract.
    """
    from services.workflow_import import import_workflow

    workflow_payload = data.get("workflow")
    if not isinstance(workflow_payload, dict):
        return {"success": False, "error": "workflow payload required"}

    return await import_workflow(
        workflow_payload,
        name=data.get("name"),
        force_credentials=bool(data.get("force_credentials")),
        auth_service=container.auth_service(),
        database=container.database(),
    )


async def handle_get_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get workflow by ID."""
    database = container.database()
    workflow_id = str(data["workflow_id"])
    recovered_archives, pending_archives = await _drain_context_archive_outbox(database, workflow_id)
    workflow = await database.get_workflow(workflow_id)
    if workflow:
        workflow_data = workflow.data or {}
        from services.workflow_context_migration import (
            archive_removed_contexts,
            import_legacy_context_receipts,
            load_node_parameters,
            persist_parameter_aliases,
        )
        from services.workflow_migrations import normalize_workflow_graph

        source_nodes = workflow_data.get("nodes") or []
        source_params = await load_node_parameters(database, source_nodes)
        normalization = normalize_workflow_graph(
            workflow.id,
            source_nodes,
            workflow_data.get("edges") or [],
            source_params,
        )
        context_errors = await _context_topology_errors(
            normalization.nodes,
            normalization.edges,
            normalization.node_parameters,
        )
        from services.workflow_sanitizer import sanitize_workflow_graph

        normalized_data = sanitize_workflow_graph(normalization.graph_data(workflow_data))
        if not context_errors:
            await import_legacy_context_receipts(
                database,
                normalization.state_imports,
            )
            normalization_persisted = True
            if normalized_data != workflow_data:
                save_kwargs = {
                    "workflow_id": workflow.id,
                    "name": workflow.name,
                    "slug": workflow.slug,
                    "description": getattr(
                        workflow,
                        "description",
                        None,
                    ),
                    "data": normalized_data,
                }
                if _supports_context_archive_outbox(database):
                    save_kwargs["context_id_aliases"] = (
                        normalization.aliases
                    )
                normalization_persisted = (
                    await database.save_workflow(**save_kwargs)
                )
                if normalization_persisted and _supports_context_archive_outbox(database):
                    drained, pending_archives = await _drain_context_archive_outbox(
                        database,
                        workflow.id,
                    )
                    recovered_archives += drained
                elif normalization_persisted:
                    recovered_archives += await archive_removed_contexts(
                        database,
                        workflow_id=workflow.id,
                        previous_nodes=source_nodes,
                        normalized_nodes=normalization.nodes,
                        aliases=normalization.aliases,
                    )
                else:
                    # Keep the response aligned with the authoritative row.
                    # A later read retries normalization and outbox creation.
                    normalized_data = sanitize_workflow_graph(workflow_data)
            if normalization_persisted:
                await persist_parameter_aliases(
                    database,
                    aliases=normalization.aliases,
                    parameters=normalization.node_parameters,
                )
        return {
            "success": True,
            "workflow": {
                "id": workflow.id,
                "name": workflow.name,
                "slug": workflow.slug,
                "data": normalized_data,
                "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
            },
            "migration_warnings": normalization.warnings,
            "node_id_aliases": normalization.aliases,
            "context_validation_errors": context_errors,
            "context_archives_completed": recovered_archives,
            "context_archives_pending": pending_archives,
        }
    return {"success": False, "error": "Workflow not found"}


async def handle_get_all_workflows(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all workflows."""
    database = container.database()
    workflows = await database.get_all_workflows()
    return {
        "success": True,
        "workflows": [
            {
                "id": w.id,
                "name": w.name,
                "slug": w.slug,
                "nodeCount": len(w.data.get("nodes", [])) if w.data else 0,
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
            for w in workflows
        ],
    }


async def _archive_workflow_contexts(database: Any, workflow_id: str) -> int:
    """Fence every persisted Context before its workflow graph is deleted."""

    workflow = await database.get_workflow(workflow_id)
    graph = getattr(workflow, "data", None)
    if not isinstance(graph, dict):
        return 0
    context_ids = sorted(
        {
            str(node.get("id"))
            for node in graph.get("nodes") or []
            if isinstance(node, dict) and node.get("type") == "context" and node.get("id")
        }
    )
    if not context_ids:
        return 0

    from services.agent_context import clear_conversation

    await clear_conversation(database, workflow_id=workflow_id)
    return len(context_ids)


async def delete_workflow_with_context_archival(
    database: Any,
    workflow_id: str,
) -> Dict[str, Any]:
    """Delete a workflow through the durable Context lifecycle boundary."""

    if _supports_context_archive_outbox(database):
        # Production Database commits graph deletion and archive identities in
        # one transaction. A failed delete therefore cannot fence a Context
        # that is still referenced by the workflow.
        success = await database.delete_workflow(workflow_id)
        completed, pending = await _drain_context_archive_outbox(
            database,
            workflow_id,
        )
    else:
        # Compatibility for isolated test doubles. Production never takes
        # this archive-before-delete path.
        completed = await _archive_workflow_contexts(
            database,
            workflow_id,
        )
        success = await database.delete_workflow(workflow_id)
        pending = 0
    if success:
        from services.workflow_storage.hooks import run_workflow_deleted_hooks

        # Employee and approval rows keyed by this workflow.
        await run_workflow_deleted_hooks(database, workflow_id)
        await _broadcast_lifecycle("deleted", workflow_id)
    return {
        "success": success,
        "workflow_id": workflow_id,
        "contexts_archived": completed,
        "context_archives_pending": pending,
    }


async def handle_delete_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Archive durable Context threads, then delete their workflow graph."""
    database = container.database()
    workflow_id = str(data["workflow_id"])
    return await delete_workflow_with_context_archival(
        database,
        workflow_id,
    )


WS_HANDLERS: Dict[str, Any] = {
    "save_workflow": handle_save_workflow,
    "import_workflow": handle_import_workflow,
    "get_workflow": handle_get_workflow,
    "get_all_workflows": handle_get_all_workflows,
    "delete_workflow": handle_delete_workflow,
}


__all__ = [
    "WS_HANDLERS",
    "delete_workflow_with_context_archival",
    "handle_delete_workflow",
    "handle_get_all_workflows",
    "handle_get_workflow",
    "handle_import_workflow",
    "handle_save_workflow",
]
