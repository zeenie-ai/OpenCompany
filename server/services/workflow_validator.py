"""Pre-execution workflow validation.

Returns plain dicts so callers can JSON-serialize directly; no dataclasses,
no enums, no new exception types. Reuses:

- ``services.node_registry.get_node_class`` to look up the plugin class for
  parameter validation and credential introspection.
- The plugin's ``Params`` Pydantic model for INVALID_PARAM detection.
- ``auth_service.has_valid_key`` for MISSING_CREDENTIAL detection.
- Kahn's algorithm pattern from ``services/execution/executor.py`` for CYCLE
  detection — duplicated here as a small local helper (~15 lines) to keep
  this module dependency-free.

Issue shape (flat dict):

    {
      "code": "CYCLE" | "DANGLING_EDGE" | "UNKNOWN_NODE_TYPE"
            | "INVALID_PARAM" | "MISSING_CREDENTIAL",
      "node_id": str | None,    # node where the issue surfaces, if applicable
      "message": str,           # user-facing description
      # plus code-specific extras:
      "node_type": str?,        # INVALID_PARAM / UNKNOWN_NODE_TYPE
      "provider_id": str?,      # MISSING_CREDENTIAL
      "remediation": str?,      # MISSING_CREDENTIAL ("add_key" / "reconnect")
    }

The validator is called from three places:

1. ``handle_validate_workflow`` WS handler — editor live-lint.
2. ``handle_execute_workflow`` / ``handle_deploy_workflow`` — pre-flight gate.
3. ``example_loader.import_examples_for_user`` — log-only warning on first launch.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logging import get_logger
from constants import AI_AGENT_TYPES
from services.node_registry import get_node_class
from services.plugin.edge_walker import (
    TEAM_LEAD_TYPES,
    TEAMMATE_HANDLE,
    build_teammate_descriptors,
    edge_target_handle,
)

logger = get_logger(__name__)


async def validate_workflow(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    *,
    parameters_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Validate a workflow graph + per-node params + credential presence.

    Args:
        nodes: ReactFlow-shaped nodes ``[{"id", "type", "data": {...}}, ...]``.
        edges: ReactFlow-shaped edges ``[{"source", "target", ...}, ...]``.
        parameters_by_id: Optional override map of node_id -> parameters dict.
            When omitted, the validator reads ``node["data"]["parameters"]``
            (which is usually empty since parameters live in the DB — callers
            who want INVALID_PARAM warnings must hydrate from DB and pass them).

    Returns:
        ``{"errors": [...], "warnings": [...]}``. ``errors`` block execution
        when ``force=False``; ``warnings`` are surfaced to the user but don't
        block.
    """
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    nodes = nodes or []
    edges = edges or []
    parameters_by_id = parameters_by_id or {}

    node_by_id: Dict[str, Dict[str, Any]] = {n["id"]: n for n in nodes if n.get("id")}

    # 1. Dangling edges — references to non-existent nodes. Errors.
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        if src and src not in node_by_id:
            errors.append(
                {
                    "code": "DANGLING_EDGE",
                    "node_id": None,
                    "message": f"Edge source {src!r} does not match any node",
                }
            )
        if tgt and tgt not in node_by_id:
            errors.append(
                {
                    "code": "DANGLING_EDGE",
                    "node_id": None,
                    "message": f"Edge target {tgt!r} does not match any node",
                }
            )

    # Team topology is validated independently from ordinary execution edges.
    # ReactFlow stores teammate edges as worker -> lead, while delegation runs
    # lead -> worker, so cycle/depth analysis below reverses them.
    team_edges: List[tuple[str, str]] = []
    for edge in edges:
        handle = edge_target_handle(edge)
        if handle != TEAMMATE_HANDLE:
            if isinstance(handle, str) and handle.startswith("input-team"):
                errors.append({
                    "code": "INVALID_TEAM_EDGE",
                    "node_id": edge.get("target"),
                    "message": f"Unsupported teammate handle {handle!r}; use {TEAMMATE_HANDLE!r}",
                })
            continue
        source = node_by_id.get(edge.get("source"))
        target = node_by_id.get(edge.get("target"))
        if not source or not target:
            continue  # already reported as DANGLING_EDGE
        if target.get("type") not in TEAM_LEAD_TYPES:
            errors.append({
                "code": "INVALID_TEAM_EDGE",
                "node_id": target.get("id"),
                "message": "The input-teammates handle is only valid on orchestrator_agent or ai_employee",
            })
            continue
        if source.get("type") not in AI_AGENT_TYPES:
            errors.append({
                "code": "INVALID_TEAM_EDGE",
                "node_id": source.get("id"),
                "message": f"Node type {source.get('type')!r} cannot be connected as a teammate",
            })
            continue
        team_edges.append((target["id"], source["id"]))

    for lead_id in sorted({lead for lead, _ in team_edges}):
        descriptors = build_teammate_descriptors(lead_id, {"nodes": nodes, "edges": edges})
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        by_name: Dict[str, List[Dict[str, Any]]] = {}
        for descriptor in descriptors:
            by_type.setdefault(descriptor["node_type"], []).append(descriptor)
            by_name.setdefault(descriptor["delegate_tool_name"], []).append(descriptor)
        for teammate_type, duplicates in sorted(by_type.items()):
            if teammate_type != "aiAgent" and len(duplicates) > 1:
                errors.append({
                    "code": "DUPLICATE_TEAMMATE_TYPE",
                    "node_id": lead_id,
                    "node_type": teammate_type,
                    "nodes": sorted(item["node_id"] for item in duplicates),
                    "message": f"Team lead {lead_id!r} may connect only one {teammate_type!r} teammate",
                })
        for name, duplicates in sorted(by_name.items()):
            if len(duplicates) > 1:
                errors.append({
                    "code": "DUPLICATE_DELEGATE_NAME",
                    "node_id": lead_id,
                    "tool_name": name,
                    "nodes": sorted(item["node_id"] for item in duplicates),
                    "message": f"Delegation tool name {name!r} is ambiguous on team lead {lead_id!r}",
                })

    delegation_graph: Dict[str, List[str]] = {}
    for lead, teammate in team_edges:
        delegation_graph.setdefault(lead, []).append(teammate)

    cycle_nodes: set[str] = set()
    depth_nodes: set[str] = set()

    def walk_team(node_id: str, path: List[str]) -> None:
        if node_id in path:
            cycle_nodes.update(path[path.index(node_id):] + [node_id])
            return
        next_path = [*path, node_id]
        # At most two delegated child layers beneath a root lead.
        if len(next_path) - 1 > 2:
            depth_nodes.update(next_path)
            return
        for child_id in delegation_graph.get(node_id, []):
            walk_team(child_id, next_path)

    for lead_id in sorted(delegation_graph):
        walk_team(lead_id, [])
    if cycle_nodes:
        errors.append({
            "code": "TEAM_DELEGATION_CYCLE",
            "node_id": None,
            "nodes": sorted(cycle_nodes),
            "message": f"Team delegation cycle detected involving nodes: {sorted(cycle_nodes)}",
        })
    if depth_nodes:
        errors.append({
            "code": "TEAM_DEPTH_EXCEEDED",
            "node_id": None,
            "nodes": sorted(depth_nodes),
            "message": "Team delegation exceeds the maximum of two child layers",
        })
    # 2. Unknown node types — plugin not installed on this instance. Errors.
    # 3. Per-node Pydantic param validation. Warnings (matches runtime
    #    soft-fail at node_executor._prepare_parameters).
    # 4. Missing credentials. Warnings (a workflow can be saved with missing
    #    credentials and configured later — only at force=True execute does
    #    it become a runtime error).
    auth_service = None
    for n in nodes:
        node_id = n.get("id")
        node_type = n.get("type", "")
        if not node_id:
            continue

        cls = get_node_class(node_type)
        if cls is None:
            errors.append(
                {
                    "code": "UNKNOWN_NODE_TYPE",
                    "node_id": node_id,
                    "node_type": node_type,
                    "message": f"Plugin type {node_type!r} is not installed",
                }
            )
            continue

        # Param validation — prefer caller-supplied params, fall back to
        # node.data.parameters (rarely populated since params live in DB).
        params = parameters_by_id.get(node_id)
        if params is None:
            params = (n.get("data") or {}).get("parameters") or {}
        try:
            cls.Params.model_validate(params)
        except Exception as exc:  # Pydantic ValidationError or anything Params raises
            # Surface the first error's message — matches the runtime
            # envelope produced by BaseNode.execute on ValidationError.
            first_err = None
            try:
                errs = exc.errors()  # type: ignore[attr-defined]
                if errs:
                    first_err = errs[0]
            except Exception:
                pass
            msg = first_err.get("msg") if isinstance(first_err, dict) and first_err.get("msg") else str(exc)
            warnings.append(
                {
                    "code": "INVALID_PARAM",
                    "node_id": node_id,
                    "node_type": node_type,
                    "message": msg,
                    "path": list(first_err.get("loc", ())) if isinstance(first_err, dict) else [],
                }
            )

        # Credential presence — for each Credential subclass declared on
        # the plugin, ask AuthService whether a key/token is stored.
        for cred_cls in getattr(cls, "credentials", ()) or ():
            if auth_service is None:
                # Lazy import to avoid a hard dependency on the container at
                # module import time (lets validator be unit-tested with a
                # fake auth_service injected via the parameter — see
                # tests/test_workflow_validator.py).
                from core.container import container

                auth_service = container.auth_service()
            try:
                stored = await auth_service.has_valid_key(cred_cls.id)
            except Exception:
                logger.debug(
                    "[workflow_validator] has_valid_key failed for %s",
                    cred_cls.id,
                    exc_info=True,
                )
                stored = False
            if not stored:
                warnings.append(
                    {
                        "code": "MISSING_CREDENTIAL",
                        "node_id": node_id,
                        "node_type": node_type,
                        "provider_id": cred_cls.id,
                        "message": f"Credential {cred_cls.id!r} is not configured",
                        "remediation": "add_key",
                    }
                )

    # 5. Cycle detection via Kahn's algorithm. Errors when unresolved nodes
    #    remain after the topological pass. Mirrors the warn-and-continue
    #    pattern in execution/executor.py._compute_execution_layers but
    #    promotes the warning to an error (validator's job is to block).
    in_degree: Dict[str, int] = {n["id"]: 0 for n in nodes if n.get("id")}
    for e in edges:
        tgt = e.get("target")
        if tgt in in_degree:
            in_degree[tgt] += 1
    queue: List[str] = [nid for nid, d in in_degree.items() if d == 0]
    seen = 0
    while queue:
        nid = queue.pop()
        seen += 1
        for e in edges:
            if e.get("source") == nid and e.get("target") in in_degree:
                in_degree[e["target"]] -= 1
                if in_degree[e["target"]] == 0:
                    queue.append(e["target"])
    if seen < len(in_degree):
        unresolved = sorted(nid for nid, d in in_degree.items() if d > 0)
        errors.append(
            {
                "code": "CYCLE",
                "node_id": None,
                "message": f"Cycle detected involving nodes: {unresolved}",
                "nodes": unresolved,
            }
        )

    return {"errors": errors, "warnings": warnings}
