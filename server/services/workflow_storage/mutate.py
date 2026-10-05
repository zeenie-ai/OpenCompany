"""Adding to a saved workflow from the server, in one transaction.

``apply_graph_additions`` is how the server grows a workflow that someone
may have open in the editor (Turn on Talk, the Agent Builder, an employee's
upgrade, which may also take out the edge a new node takes the place of). One write
transaction (``database.run_runtime_mutation``) reads ``workflow.data``,
places the batch against it (``services.graph_build.add_to_graph``: ids and
labels allocated against the graph as it is at that moment), appends the
nodes and edges, writes a fresh parameter row for each new node and merges
into existing rows where asked (a Skills node's ``skills_config``). The
mutation id keys a ledger row committed in the same transaction, so a retry
returns the first result instead of adding twice.

After the commit the batch goes to every editor as ``workflow_ops_apply``
with ``persisted: true``: the ops carry the server's ids, positions and
parameters, and an editor adopts them without saving anything. Then the
graph-changed listeners run. A retry announces the batch again, since the
first announcement may never have gone out.

Unlike ``save_workflow`` this never replaces the graph, so it cannot drop
what an editor saved meanwhile.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from sqlmodel import select

from core.logging import get_logger
from models.database import NodeParameter, Workflow
from services import workflow_ops
from services.graph_build import GraphAdditions, add_to_graph, label_key, merge_params
from services.workflow_storage.listeners import notify_graph_changed

logger = get_logger(__name__)


@dataclass(frozen=True)
class GraphAdditionsResult:
    #: ref -> node id, label and label key it got.
    node_ids: Mapping[str, str]
    labels: Mapping[str, str]
    label_keys: Mapping[str, str]
    #: The batch as ``workflow_ops`` operations (what was announced).
    operations: Sequence[Mapping[str, Any]]
    #: False when the ledger already had this mutation (a retry).
    applied: bool
    saved_revision: Optional[str] = None


class _WorkflowMissing(Exception):
    pass


async def _row(session: Any, node_id: str) -> Optional[NodeParameter]:
    return (await session.execute(select(NodeParameter).where(NodeParameter.node_id == node_id))).scalar_one_or_none()


def _write(session: Any, row: Optional[NodeParameter], node_id: str, parameters: Dict[str, Any]) -> None:
    if row is None:
        session.add(NodeParameter(node_id=node_id, parameters=deepcopy(parameters)))
    else:
        row.parameters = deepcopy(parameters)
        row.updated_at = datetime.now(timezone.utc)


async def apply_graph_additions(
    database: Any,
    workflow_id: str,
    additions: GraphAdditions,
    *,
    mutation_id: str,
    caller_node_id: Optional[str] = None,
    prepare: Optional[Callable[[Mapping[str, Any]], Tuple[GraphAdditions, Mapping[str, str]]]] = None,
    authorize: Optional[Callable[[Any], Awaitable[None]]] = None,
    authorization_grant_ids: Sequence[str] = (),
) -> Optional[GraphAdditionsResult]:
    """Add ``additions`` to the saved workflow in one transaction, then
    announce them. None when the workflow does not exist (nothing is
    written). ``caller_node_id`` names the agent that asked, if one did.
    Raises ValueError, with nothing written, for a batch that does not fit
    the graph (see ``add_to_graph``)."""

    async def mutate(session: Any) -> Dict[str, Any]:
        workflow = (await session.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
        if workflow is None:
            raise _WorkflowMissing()
        if authorize:
            await authorize(session)
        # Reuse decisions must observe the graph under the mutation lock.
        # A preflight read alone permits concurrent calls to bind duplicate
        # callable identities to the same agent.
        batch, reused = prepare(workflow.data or {}) if prepare else (additions, {})
        placed = add_to_graph(workflow_id, workflow.data or {}, batch)
        operations: List[Dict[str, Any]] = []
        for edge in placed.removed_edges:
            operations.append(workflow_ops.delete_edge(str(edge.get("id"))))
        refs = {node_id: ref for ref, node_id in placed.node_ids.items()}
        for node in placed.nodes:
            data = {key: value for key, value in node["data"].items() if key != "label"}
            operations.append(
                workflow_ops.add_node(
                    refs[node["id"]],
                    node["type"],
                    placed.parameters[node["id"]],
                    label=node["data"]["label"],
                    position=node["position"],
                    minted_id=node["id"],
                    data=data,
                )
            )
        for edge in placed.edges:
            operations.append(
                workflow_ops.add_edge(
                    edge["source"],
                    edge["target"],
                    source_handle=edge["sourceHandle"],
                    target_handle=edge["targetHandle"],
                    edge_id=edge["id"],
                    condition=(edge.get("data") or {}).get("condition"),
                )
            )
        if placed.nodes or placed.edges or placed.removed_edges:
            workflow.data = deepcopy(placed.graph)
            workflow.updated_at = datetime.now(timezone.utc)
        for node_id, parameters in placed.parameters.items():
            # A fresh row: one left behind under a reused id is replaced.
            _write(session, await _row(session, node_id), node_id, parameters)
        for node_id, patch in placed.merges.items():
            row = await _row(session, node_id)
            current = dict(row.parameters or {}) if row is not None else {}
            merged = merge_params(current, patch)
            if merged != current:
                _write(session, row, node_id, merged)
                operations.append(workflow_ops.set_node_parameters(node_id, merged))
        # Team membership is part of the same saved change as the graph and
        # parameter rows. Summaries/model/permission paths must see every
        # specialist immediately after commit, including Builder additions.
        from models.employees import Employee, EmployeeGrant

        employee = (await session.execute(select(Employee).where(Employee.workflow_id == workflow_id))).scalar_one_or_none()
        if employee is not None and employee.team_plan and (placed.nodes or placed.edges):
            from services.node_registry import get_node_class

            team = deepcopy(employee.team_plan)
            roles = dict(employee.node_roles or {})
            members = list(team.get("members") or [])
            graph_nodes = {node["id"]: node for node in placed.graph.get("nodes", [])}
            graph_edges = placed.graph.get("edges", [])
            lead = roles.get("agent")
            next_index = max((int(role.split("_")[1]) for role in roles if role.startswith("specialist_") and role.split("_")[1].isdigit()), default=0)
            for node in placed.nodes:
                cls = get_node_class(node["type"])
                if not cls or getattr(cls, "component_kind", "") != "agent" or not any(edge.get("source") == node["id"] and edge.get("target") == lead and edge.get("targetHandle") == "input-teammates" for edge in graph_edges):
                    continue
                next_index += 1
                context = next((candidate["id"] for candidate in graph_nodes.values() if candidate["type"] == "context" and (candidate.get("data") or {}).get("agentNodeId") == node["id"]), None)
                roles[f"specialist_{next_index}"] = node["id"]
                if context:
                    roles[f"specialist_{next_index}_context"] = context
                config = placed.parameters.get(node["id"], {})
                members.append({"node_id": node["id"], "node_type": node["type"], "responsibility": config.get("system_message") or node["data"]["label"], "role": "custom", "context_node_id": context, "tools": [], "skills": []})
                # Child execution and tool access remain linked to the exact
                # owner-approved specialist bundle. Revoking that parent
                # grant disables the entire expansion, including nested apps.
                parent = None
                for grant_id in authorization_grant_ids:
                    candidate = await session.get(EmployeeGrant, grant_id)
                    if candidate and candidate.capability == node["type"] and candidate.member_id == lead:
                        parent = candidate
                        break
                if parent:
                    capabilities = [node["id"], *(edge["source"] for edge in graph_edges if edge.get("target") == node["id"] and edge.get("targetHandle") == "input-tools")]
                    for capability_id in capabilities:
                        capability_node = graph_nodes[capability_id]
                        params = placed.parameters.get(capability_id)
                        if params is None:
                            parameter_row = await _row(session, capability_id)
                            params = dict(parameter_row.parameters or {}) if parameter_row else {}
                        identity = hashlib.sha256(f"builder-child:{parent.id}:{node['id']}:{capability_id}".encode()).hexdigest()
                        session.add(EmployeeGrant(id=identity, workflow_id=workflow_id, owner_id=employee.owner_id,
                            capability=capability_node["type"], member_id=node["id"], account_id=str((params or {}).get("account_id") or ""),
                            limits={"approved": True, "parent_grant_id": parent.id, "tool_node_id": capability_id, "parameters": params or {}}))
            for member in members:
                member["tools"] = [edge["source"] for edge in graph_edges if edge.get("target") == member["node_id"] and edge.get("targetHandle") == "input-tools"]
                holder = next((edge["source"] for edge in graph_edges if edge.get("target") == member["node_id"] and edge.get("targetHandle") == "input-skill"), None)
                if holder:
                    config = placed.parameters.get(holder)
                    if config is None:
                        row = await _row(session, holder)
                        config = dict(row.parameters or {}) if row else {}
                    member["skills"] = list((config or {}).get("skills_config") or {})
                    role = next((key for key, value in roles.items() if value == member["node_id"] and key.startswith("specialist_")), None)
                    if role:
                        roles[role + "_skills"] = holder
            team["members"] = members
            employee.team_plan = team
            employee.node_roles = roles
            employee.updated_at = datetime.now(timezone.utc)
        reused_labels = {
            ref: str((node.get("data") or {}).get("label") or node.get("type"))
            for ref, node_id in reused.items()
            for node in (workflow.data or {}).get("nodes", [])
            if node.get("id") == node_id
        }
        labels = {**reused_labels, **placed.labels}
        saved_parameters = {}
        for node in placed.graph.get("nodes", []):
            row = await _row(session, node["id"])
            saved_parameters[node["id"]] = dict(row.parameters or {}) if row is not None else {}
        revision_value = json.dumps({"graph": placed.graph, "parameters": saved_parameters}, sort_keys=True, separators=(",", ":"), default=str)
        return {
            "node_ids": {**reused, **placed.node_ids},
            "labels": labels,
            "label_keys": {ref: label_key(label) for ref, label in labels.items()},
            "operations": operations,
            "saved_revision": hashlib.sha256(revision_value.encode()).hexdigest(),
        }

    try:
        stored, applied = await database.run_runtime_mutation(
            resource_type="workflow",
            resource_id=workflow_id,
            operation="graph_additions",
            mutation_id=mutation_id,
            mutate=mutate,
        )
    except _WorkflowMissing:
        return None
    result = GraphAdditionsResult(
        node_ids=stored["node_ids"],
        labels=stored["labels"],
        label_keys=stored["label_keys"],
        operations=stored["operations"],
        applied=applied,
        saved_revision=stored.get("saved_revision"),
    )
    if result.operations:
        await workflow_ops.broadcast_workflow_ops(
            workflow_id=workflow_id, caller_node_id=caller_node_id, operations=result.operations, persisted=True
        )
        notify_graph_changed(workflow_id)
    logger.info(
        "Graph additions saved",
        workflow_id=workflow_id,
        nodes=len(result.node_ids),
        operations=len(result.operations),
        applied=applied,
    )
    return result


__all__ = ["GraphAdditionsResult", "apply_graph_additions"]
