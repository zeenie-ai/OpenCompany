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


async def _broadcast_renamed(workflow_id: str, name: str, slug: str, old_slug: str) -> None:
    """CloudEvents ``workflow.renamed`` — sidebar + open workflow refresh."""
    try:
        from services.status_broadcaster import get_status_broadcaster

        await get_status_broadcaster().broadcast_workflow_lifecycle(
            "renamed",
            workflow_id=workflow_id,
            name=name,
            slug=slug,
            old_slug=old_slug,
        )
    except Exception:
        logger.debug("[workflow] rename broadcast failed", exc_info=True)


async def handle_save_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save workflow. Re-slugs + renames workspace dir when name changes.

    The frontend's auto-save chain (TopToolbar inline rename ->
    ``updateWorkflow({name})`` -> debounced save) flows through this
    handler, so renaming happens here — no dedicated rename endpoint.
    """
    from services.workflow_naming import canonicalize_node_ids, next_available_slug

    database = container.database()
    requested_id = str(data.get("workflow_id") or "").strip()
    is_new_marker = requested_id.lower() in {"", "new"}
    existing = await database.get_workflow(requested_id) if requested_id and not is_new_marker else None
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
    from services.workflow_migrations import normalize_legacy_android_toolkit

    nodes, edges, _, migration_warnings = normalize_legacy_android_toolkit(
        workflow_data.get("nodes") or [], workflow_data.get("edges") or []
    )
    # New workflows use canonical IDs from their first persistence boundary.
    # Existing legacy workflows are deliberately left byte-for-byte compatible;
    # there is no startup or save-time migration.
    if is_new_marker:
        nodes, edges, node_id_aliases = canonicalize_node_ids(workflow_id, nodes, edges)
    else:
        node_id_aliases = {}
    normalized_data = {**workflow_data, "nodes": nodes, "edges": edges}
    success = await database.save_workflow(
        workflow_id=storage_id,
        name=name,
        slug=slug,
        data=normalized_data,
    )

    if existing and existing.slug and existing.slug != slug:
        _move_workspace(existing.slug, slug)
        await _broadcast_renamed(workflow_id, name, slug, existing.slug)

    return {
        "success": success,
        "workflow_id": workflow_id,
        "name": name,
        "slug": slug,
        "migration_warnings": migration_warnings,
        "node_id_aliases": node_id_aliases,
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
    workflow = await database.get_workflow(data["workflow_id"])
    if workflow:
        from services.workflow_migrations import normalize_legacy_android_toolkit

        workflow_data = workflow.data or {}
        nodes, edges, _, migration_warnings = normalize_legacy_android_toolkit(
            workflow_data.get("nodes") or [], workflow_data.get("edges") or []
        )
        if nodes != (workflow_data.get("nodes") or []) or edges != (workflow_data.get("edges") or []):
            normalized_data = {**workflow_data, "nodes": nodes, "edges": edges}
            await database.save_workflow(
                workflow_id=workflow.id,
                name=workflow.name,
                slug=workflow.slug,
                description=getattr(workflow, "description", None),
                data=normalized_data,
            )
            legacy_ids = {
                node.get("id")
                for node in workflow_data.get("nodes", [])
                if node.get("type") == "androidTool" and node.get("id")
            }
            for legacy_id in legacy_ids:
                await database.delete_node_parameters(legacy_id)
        return {
            "success": True,
            "workflow": {
                "id": workflow.id,
                "name": workflow.name,
                "slug": workflow.slug,
                "data": {**workflow_data, "nodes": nodes, "edges": edges},
                "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
            },
            "migration_warnings": migration_warnings,
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


async def handle_delete_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete workflow."""
    database = container.database()
    success = await database.delete_workflow(data["workflow_id"])
    return {"success": success, "workflow_id": data["workflow_id"]}


WS_HANDLERS: Dict[str, Any] = {
    "save_workflow": handle_save_workflow,
    "import_workflow": handle_import_workflow,
    "get_workflow": handle_get_workflow,
    "get_all_workflows": handle_get_all_workflows,
    "delete_workflow": handle_delete_workflow,
}


__all__ = [
    "WS_HANDLERS",
    "handle_delete_workflow",
    "handle_get_all_workflows",
    "handle_get_workflow",
    "handle_import_workflow",
    "handle_save_workflow",
]
