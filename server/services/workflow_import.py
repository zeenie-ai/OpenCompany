"""Workflow import — backend-authoritative orchestrator.

Owns every business decision the import flow needs to make. The frontend
is reduced to a thin pipeline (file picker -> WS call -> user prompts on
the returned preview -> WS call again with confirmations). All of the
following are server-side:

- **Parse + validation** — runs :func:`services.workflow_validator.validate_workflow`
  on the raw graph; errors block the import entirely.
- **Requirements extraction** — walks the plugin registry to build a
  canonical ``{credentials, nodes}`` manifest from the actual node types,
  rather than trusting the embedded ``requirements`` block in the file.
- **Credential cross-check** — diffs the manifest against
  ``auth_service.has_valid_key``.
- **Name conflict detection** — diffs the proposed name against
  ``database.get_all_workflows()`` and synthesizes a suggested alternative.
- **Node-id remap** — rewrites every node id (and edge refs +
  nodeParameters keys) so two imports of the same JSON don't collide on
  the ``node_parameters`` table's unique key.
- **Persistence** — single ``database.save_workflow`` + per-node
  ``save_node_parameters`` calls under the freshly-remapped ids.

The two-step UX: a first call returns ``preview=True`` with whatever
needs user confirmation (missing credentials, name conflict). The
frontend prompts; the second call passes the confirmed name + a
``force_credentials`` flag and the same orchestrator branches to the
save path.
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from core.logging import get_logger
from services.node_registry import get_node_class
from services.workflow_migrations import normalize_legacy_android_toolkit
from services.workflow_naming import new_workflow_id, next_available_slug
from services.workflow_validator import validate_workflow

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Node-id remap (was example_loader._remap_node_ids — moved here so both
# the example loader and the WS import handler share one implementation).
# ---------------------------------------------------------------------------


def remap_node_ids(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    node_parameters: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Rewrite every node id to a fresh unique id; remap edge refs and
    nodeParameters keys to match.

    Without this, two workflows that share node ids (today: ``AI Assistant``
    and ``Claude Assistant`` share 6 trigger/console/memory nodes) collide
    in the ``node_parameters`` table — keyed unique on ``node_id`` — and
    the second import silently overwrites the first's parameters.

    New ids follow the same convention as ``useDragAndDrop``'s on-drop
    scheme: ``{node_type}-{timestamp}{index}-{random}``. The random salt
    protects against two clients re-importing the same file within one
    millisecond.

    Returns a new ``(nodes, edges, node_parameters)`` triple — inputs
    untouched (shallow copy at the dict level).
    """
    node_parameters = node_parameters or {}
    now = int(time.time() * 1000)
    salt = secrets.token_hex(3)

    id_map: Dict[str, str] = {}
    for index, node in enumerate(nodes):
        prefix = node.get("type") or "node"
        old_id = node.get("id")
        if old_id:
            id_map[old_id] = f"{prefix}-{now}{index}-{salt}"

    new_nodes = [{**node, "id": id_map.get(node.get("id"), node.get("id"))} for node in nodes]

    new_edges: List[Dict[str, Any]] = []
    for edge in edges:
        src = id_map.get(edge.get("source"), edge.get("source"))
        tgt = id_map.get(edge.get("target"), edge.get("target"))
        # Older backend-generated workflow operations used snake_case.
        # Persist imports in the canonical ReactFlow shape.
        target_handle = edge.get("targetHandle") or edge.get("target_handle")
        normalized_edge = {key: value for key, value in edge.items() if key != "target_handle"}
        if target_handle is not None:
            normalized_edge["targetHandle"] = target_handle
        new_edges.append(
            {
                **normalized_edge,
                "source": src,
                "target": tgt,
                "id": f"e-{src}-{tgt}-{now}-{salt}",
            }
        )

    new_params: Dict[str, Dict[str, Any]] = {}
    for old_id, params in node_parameters.items():
        new_id = id_map.get(old_id)
        if new_id:
            new_params[new_id] = params
        # Orphans (param entry whose node isn't in the graph) are dropped.

    return new_nodes, new_edges, new_params


# ---------------------------------------------------------------------------
# Requirements + credential cross-check + name conflict
# ---------------------------------------------------------------------------


def extract_requirements(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a canonical ``{credentials, nodes}`` manifest from the actual
    nodes in the graph.

    Replaces the frontend's ``buildRequirements`` helper for the import
    path — the backend has authoritative ``NodeMetadata`` via the plugin
    registry, so we do NOT trust whatever ``requirements`` block was
    embedded in the imported file (a hand-edited export could lie).
    """
    cred_ids: set[str] = set()
    node_reqs: List[Dict[str, Any]] = []
    for node in nodes:
        cls = get_node_class(node.get("type", ""))
        if cls is None:
            continue
        node_reqs.append({"type": cls.type, "version": getattr(cls, "version", 1)})
        for cred_cls in getattr(cls, "credentials", ()) or ():
            cred_ids.add(cred_cls.id)
    return {
        "credentials": [{"provider_id": pid} for pid in sorted(cred_ids)],
        "nodes": node_reqs,
    }


async def cross_check_credentials(requirements: Dict[str, Any], auth_service) -> List[Dict[str, Any]]:
    """For each credential in the manifest, ask ``auth_service`` whether
    it's stored. Returns the MISSING ones with display info pulled from
    ``CREDENTIAL_REGISTRY`` so the frontend can render a friendly list.

    Display info shape: ``{provider_id, display_name, kind}``. ``kind``
    is ``"api_key"`` or ``"oauth2"`` (mirrors ``Credential.auth``).
    """
    from services.plugin.credential import CREDENTIAL_REGISTRY

    missing: List[Dict[str, Any]] = []
    for need in requirements.get("credentials", []):
        pid = need.get("provider_id")
        if not pid:
            continue
        try:
            stored = await auth_service.has_valid_key(pid)
        except Exception:
            logger.debug(
                "[workflow_import] has_valid_key failed for %s",
                pid,
                exc_info=True,
            )
            stored = False
        if stored:
            continue
        cred_cls = CREDENTIAL_REGISTRY.get(pid)
        missing.append(
            {
                "provider_id": pid,
                "display_name": getattr(cred_cls, "display_name", pid) if cred_cls else pid,
                "kind": getattr(cred_cls, "auth", "api_key") if cred_cls else "api_key",
            }
        )
    return missing


async def check_name_conflict(name: str, database) -> Dict[str, Any]:
    """Returns ``{has_conflict, suggested_name}`` for a proposed workflow
    name. Suggestion format: ``"<name> (imported)"`` with a numeric suffix
    if that itself collides — mirrors the conflict-resolution pattern in
    ``client/src/utils/workflow.ts`` callers.
    """
    existing = await database.get_all_workflows()
    existing_names = {(getattr(w, "name", "") or "").lower() for w in existing}
    has_conflict = (name or "").lower() in existing_names
    suggested: Optional[str] = None
    if has_conflict:
        base = f"{name} (imported)"
        candidate = base
        counter = 2
        while candidate.lower() in existing_names:
            candidate = f"{base} {counter}"
            counter += 1
        suggested = candidate
    return {"has_conflict": has_conflict, "suggested_name": suggested}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def import_workflow(
    workflow: Dict[str, Any],
    *,
    name: Optional[str] = None,
    force_credentials: bool = False,
    auth_service,
    database,
) -> Dict[str, Any]:
    """Orchestrate the full import: validate -> cross-check -> name check
    -> (preview if confirmations needed) -> remap -> save.

    Args:
        workflow: Raw workflow dict as deserialized from the import JSON.
            Must contain ``nodes`` + ``edges`` at minimum; ``nodeParameters``
            and ``name`` are optional.
        name: User-confirmed final name. When ``None``, the orchestrator
            uses ``workflow.name`` and reports a name conflict (if any)
            as part of the preview envelope.
        force_credentials: When ``True``, missing credentials don't block
            the save (user confirmed via UI). When ``False``, missing
            credentials cause a preview response.
        auth_service: Injected ``AuthService`` (for credential checks).
        database: Injected ``Database`` (for name conflicts + persistence).

    Returns:
        On validation failure::

            {"success": False, "error": "validation_failed", "report": {...}}

        On preview (needs user confirmation)::

            {
                "success": True, "preview": True,
                "report": {...},
                "missing_credentials": [...],
                "name_conflict": bool,
                "suggested_name": str | None,
                "requirements": {...},
            }

        On save success::

            {
                "success": True, "preview": False,
                "workflow_id": str, "name": str,
                "node_count": int, "edge_count": int,
                "report": {...},
            }
    """
    nodes = workflow.get("nodes") or []
    edges = workflow.get("edges") or []
    node_parameters = workflow.get("nodeParameters") or {}
    proposed_name = name or workflow.get("name") or "Imported Workflow"

    # Compatibility must run before validation: androidTool is no longer a
    # supported node, but historical exports remain importable.
    nodes, edges, node_parameters, migration_warnings = normalize_legacy_android_toolkit(
        nodes, edges, node_parameters
    )

    # 1. Validate. Errors block the import; warnings flow through.
    report = await validate_workflow(
        nodes=nodes,
        edges=edges,
        parameters_by_id=node_parameters,
        auth_service=auth_service,
    )
    report.setdefault("warnings", []).extend(
        {"code": "LEGACY_ANDROID_TOOLKIT_REMOVED", "message": warning}
        for warning in migration_warnings
    )
    if report["errors"]:
        return {
            "success": False,
            "error": "validation_failed",
            "report": report,
        }

    # 2. Canonical requirements (don't trust the embedded manifest).
    requirements = extract_requirements(nodes)

    # 3. Cross-check stored credentials.
    missing_credentials = await cross_check_credentials(requirements, auth_service)

    # 4. Name conflict check.
    name_check = await check_name_conflict(proposed_name, database)

    # 5. If anything needs user confirmation, return preview without saving.
    needs_preview = (bool(missing_credentials) and not force_credentials) or name_check["has_conflict"]
    if needs_preview:
        return {
            "success": True,
            "preview": True,
            "report": report,
            "missing_credentials": missing_credentials,
            "name_conflict": name_check["has_conflict"],
            "suggested_name": name_check["suggested_name"],
            "requirements": requirements,
        }

    # 6. Remap node ids — protects the node_parameters table from
    #    duplicate-id collisions across imports.
    remapped_nodes, remapped_edges, remapped_params = remap_node_ids(
        nodes,
        edges,
        node_parameters,
    )

    # 7. Save. UUID-based system identity + name-derived slug for
    #    human-visible surfaces (folder names, Temporal Web UI).
    workflow_id = new_workflow_id()
    slug = await next_available_slug(proposed_name, database)
    saved = await database.save_workflow(
        workflow_id=workflow_id,
        name=proposed_name,
        slug=slug,
        description=workflow.get("description"),
        data={"nodes": remapped_nodes, "edges": remapped_edges},
    )
    if not saved:
        return {"success": False, "error": "save_failed", "report": report}

    # 8. Per-node parameter saves under the remapped ids.
    saved_params = 0
    for node_id, params in remapped_params.items():
        if not params:
            continue
        try:
            await database.save_node_parameters(node_id, params)
            saved_params += 1
        except Exception as e:
            logger.error(
                "[workflow_import] save_node_parameters failed for %s: %s",
                node_id,
                e,
            )

    # 9. CloudEvents broadcast — ``workflow.imported`` envelope. Other
    #    connected clients (browser tabs) listen for this and invalidate
    #    their workflows query so the sidebar picks up the new entry
    #    without a manual refresh. Broadcast failure must never fail the
    #    import — the workflow IS saved either way.
    try:
        from services.status_broadcaster import get_status_broadcaster

        await get_status_broadcaster().broadcast_workflow_lifecycle(
            "imported",
            workflow_id=workflow_id,
            name=proposed_name,
            node_count=len(remapped_nodes),
            edge_count=len(remapped_edges),
        )
    except Exception:
        logger.debug(
            "[workflow_import] broadcast_workflow_lifecycle failed",
            exc_info=True,
        )

    return {
        "success": True,
        "preview": False,
        "workflow_id": workflow_id,
        "name": proposed_name,
        "node_count": len(remapped_nodes),
        "edge_count": len(remapped_edges),
        "saved_parameters": saved_params,
        "report": report,
    }
