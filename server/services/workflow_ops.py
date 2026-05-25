"""Workflow Operations protocol.

The standard wire format any backend service uses to mutate the
React Flow canvas. A service returns ``{"operations": [...]}`` and the
frontend's single applier (``client/src/lib/workflowOps.ts``) walks
the list, generating real React Flow node ids, resolving cross-op
references, and persisting parameter changes through
``saveNodeParameters``.

See ``docs-internal/workflow_ops_protocol.md`` for the full spec.

Protocol notes:

* Operations apply **in order**. Earlier ops can produce ids that
  later ops reference via ``client_ref`` placeholders.
* Failures are logged + reported per op; subsequent ops still apply.
  v1 has no rollback -- write op sequences that are robust to partial
  application.
* The protocol is a declarative wire format, not a diff reconciler.
  Re-applying a batch is not assumed to be idempotent.

Adding a new operation type:

1. Add the TypedDict below + extend ``WorkflowOperation`` union.
2. Add a builder helper below.
3. Mirror the type in ``client/src/lib/workflowOps.ts``.
4. Add the apply branch to ``applyOperations`` in that same TS file.
5. Document the new op in ``docs-internal/workflow_ops_protocol.md``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union


# ---------------------------------------------------------------------------
# Position spec
# ---------------------------------------------------------------------------


class AbsolutePosition(TypedDict):
    x: float
    y: float


class _AnchorOffset(TypedDict, total=False):
    x: float
    y: float


class AnchoredPosition(TypedDict, total=False):
    anchor_node_id: str
    offset: _AnchorOffset
    fallback: AbsolutePosition


# Either {x,y} or {anchor_node_id, offset?, fallback?}. The frontend
# resolves the anchor against current React Flow node positions.
PositionSpec = Union[AbsolutePosition, AnchoredPosition]


# ---------------------------------------------------------------------------
# Node references
# ---------------------------------------------------------------------------


class _ClientRef(TypedDict):
    client_ref: str


# Existing-id strings mix freely with batch-local ``{client_ref}``
# placeholders so an add_edge op can target a node added earlier in
# the same batch before its real id exists.
NodeRef = Union[str, _ClientRef]


# ---------------------------------------------------------------------------
# Operation TypedDicts
# ---------------------------------------------------------------------------


class AddNodeOp(TypedDict, total=False):
    type: Literal["add_node"]
    client_ref: str
    node_type: str
    parameters: Dict[str, Any]
    label: str
    position: PositionSpec


class AddEdgeOp(TypedDict, total=False):
    type: Literal["add_edge"]
    source: NodeRef
    target: NodeRef
    source_handle: str
    target_handle: str


class SetNodeParametersOp(TypedDict, total=False):
    type: Literal["set_node_parameters"]
    node_id: str
    parameters: Dict[str, Any]


class DeleteNodeOp(TypedDict):
    type: Literal["delete_node"]
    node_id: str


class DeleteEdgeOp(TypedDict):
    type: Literal["delete_edge"]
    edge_id: str


class MoveNodeOp(TypedDict):
    type: Literal["move_node"]
    node_id: str
    position: PositionSpec


class ReplaceNodeOp(TypedDict, total=False):
    type: Literal["replace_node"]
    node_id: str
    node_type: str
    parameters: Dict[str, Any]
    label: str
    preserve_edges: bool


WorkflowOperation = Union[
    AddNodeOp,
    AddEdgeOp,
    SetNodeParametersOp,
    DeleteNodeOp,
    DeleteEdgeOp,
    MoveNodeOp,
    ReplaceNodeOp,
]


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def add_node(
    client_ref: str,
    node_type: str,
    parameters: Optional[Dict[str, Any]] = None,
    *,
    label: Optional[str] = None,
    position: Optional[PositionSpec] = None,
) -> AddNodeOp:
    op: AddNodeOp = {
        "type": "add_node",
        "client_ref": client_ref,
        "node_type": node_type,
        "parameters": parameters or {},
    }
    if label is not None:
        op["label"] = label
    if position is not None:
        op["position"] = position
    return op


def add_edge(
    source: NodeRef,
    target: NodeRef,
    *,
    source_handle: Optional[str] = None,
    target_handle: Optional[str] = None,
) -> AddEdgeOp:
    op: AddEdgeOp = {
        "type": "add_edge",
        "source": source,
        "target": target,
    }
    if source_handle is not None:
        op["source_handle"] = source_handle
    if target_handle is not None:
        op["target_handle"] = target_handle
    return op


def set_node_parameters(node_id: str, parameters: Dict[str, Any]) -> SetNodeParametersOp:
    return {
        "type": "set_node_parameters",
        "node_id": node_id,
        "parameters": parameters,
    }


def delete_node(node_id: str) -> DeleteNodeOp:
    return {"type": "delete_node", "node_id": node_id}


def delete_edge(edge_id: str) -> DeleteEdgeOp:
    return {"type": "delete_edge", "edge_id": edge_id}


def move_node(node_id: str, position: PositionSpec) -> MoveNodeOp:
    return {"type": "move_node", "node_id": node_id, "position": position}


def replace_node(
    node_id: str,
    node_type: str,
    parameters: Optional[Dict[str, Any]] = None,
    *,
    label: Optional[str] = None,
    preserve_edges: bool = True,
) -> ReplaceNodeOp:
    op: ReplaceNodeOp = {
        "type": "replace_node",
        "node_id": node_id,
        "node_type": node_type,
        "parameters": parameters or {},
        "preserve_edges": preserve_edges,
    }
    if label is not None:
        op["label"] = label
    return op


def anchored(
    anchor_node_id: str,
    *,
    offset_x: float = 0,
    offset_y: float = 0,
    fallback: Optional[AbsolutePosition] = None,
) -> AnchoredPosition:
    """Convenience builder for ``{anchor_node_id, offset, fallback}``."""
    pos: AnchoredPosition = {
        "anchor_node_id": anchor_node_id,
        "offset": {"x": offset_x, "y": offset_y},
    }
    if fallback is not None:
        pos["fallback"] = fallback
    return pos


def empty() -> Dict[str, List[WorkflowOperation]]:
    """Standard empty response (``{"operations": []}``)."""
    return {"operations": []}
