"""Dynamic cloud-tool canvas nodes for the Vertex managed agent.

agentBuilder pattern: mint ``vertexCloudTool`` display nodes for each
distinct cloud-side tool the managed agent used, wire them to the
agent's ``input-tools`` handle, persist into ``workflow.data`` FIRST
(DB is source of truth on the next run) and then broadcast the ops
batch on the ``workflow_ops_apply`` wire key so an open canvas applies
them live. Finally each used node gets a one-shot executing->success
pulse (the frontend's 500ms minimum glow makes it visible).
"""

from __future__ import annotations

import secrets
import time
from typing import Dict, List

from core.logging import get_logger
from services import workflow_ops as wf_ops
from services.plugin.deps import get_database
from services.status_broadcaster import get_status_broadcaster

logger = get_logger(__name__)

_DISPLAY_NODE_TYPE = "vertexCloudTool"
_WIRE_ROUTING_KEY = "workflow_ops_apply"


def _mint_node_id(prefix: str) -> str:
    """Same id shape the frontend mints: <type>-<epoch_ms>-<hex6>."""
    return f"{prefix}-{int(time.time() * 1000)}-{secrets.token_hex(3)}"


async def ensure_cloud_tool_nodes(
    *,
    workflow_id: str,
    agent_node_id: str,
    used: Dict[str, str],
) -> List[str]:
    """Mint/dedupe display nodes for ``{cloud_tool_key: label}`` usage.

    Returns the canvas node ids that were pulsed (existing + minted).
    """
    if not used:
        return []

    database = get_database()
    workflow = await database.get_workflow(workflow_id)
    if workflow is None:
        return []

    data = dict(workflow.data or {})
    nodes = list(data.get("nodes") or [])
    edges = list(data.get("edges") or [])

    # Dedupe against the LIVE canvas: display nodes already wired to
    # this agent's input-tools, keyed by label (labels are tool names).
    wired_sources = {
        edge.get("source")
        for edge in edges
        if edge.get("target") == agent_node_id
        and edge.get("targetHandle") == "input-tools"
    }
    existing: Dict[str, str] = {}
    for node in nodes:
        if node.get("type") == _DISPLAY_NODE_TYPE and node.get("id") in wired_sources:
            label = str((node.get("data") or {}).get("label") or "")
            if label:
                existing[label] = node.get("id")

    operations: List[dict] = []
    pulse_ids: List[str] = [
        existing[label] for label in used.values() if label in existing
    ]

    fan_index = 0
    for key, label in used.items():
        if label in existing:
            continue
        minted_id = _mint_node_id(_DISPLAY_NODE_TYPE)
        client_ref = f"new_{key}"
        add_node_op = wf_ops.add_node(
            client_ref,
            _DISPLAY_NODE_TYPE,
            {"cloud_tool_key": key},
            label=label,
            position=wf_ops.anchored(
                agent_node_id,
                offset_x=-260,
                offset_y=140 + 90 * fan_index,
            ),
        )
        # Shared id so backend status broadcasts glow the exact node the
        # frontend applier creates.
        add_node_op["minted_id"] = minted_id
        operations.append(add_node_op)
        operations.append(
            wf_ops.add_edge(
                {"client_ref": client_ref},
                agent_node_id,
                source_handle="output-main",
                target_handle="input-tools",
            )
        )

        # Server-side persistence (position resolves on the frontend;
        # {0,0} placeholder matches the agentBuilder asymmetry).
        nodes.append(
            {
                "id": minted_id,
                "type": _DISPLAY_NODE_TYPE,
                "position": {"x": 0, "y": 0},
                "data": {
                    "label": label,
                    "parameters": {"cloud_tool_key": key, "label": label},
                },
            }
        )
        edges.append(
            {
                "id": f"e-{minted_id}-{agent_node_id}",
                "source": minted_id,
                "target": agent_node_id,
                "sourceHandle": "output-main",
                "targetHandle": "input-tools",
            }
        )
        await database.save_node_parameters(
            minted_id, {"label": label, "cloud_tool_key": key}
        )
        pulse_ids.append(minted_id)
        fan_index += 1

    broadcaster = get_status_broadcaster()

    if operations:
        data["nodes"] = nodes
        data["edges"] = edges
        # Persist-then-broadcast: the DB write must land before any
        # consumer re-reads the canvas (agentBuilder invariant).
        await database.save_workflow(
            workflow_id=workflow_id,
            name=workflow.name,
            slug=workflow.slug,
            data=data,
            description=workflow.description,
        )
        await broadcaster.broadcast(
            {
                "type": _WIRE_ROUTING_KEY,
                "data": {
                    "workflow_id": workflow_id,
                    "caller_node_id": agent_node_id,
                    "operations": operations,
                },
            }
        )
        logger.info(
            "[Vertex Agent] minted %d cloud-tool node(s) for %s",
            len(operations) // 2,
            agent_node_id,
        )

    for node_id in pulse_ids:
        await broadcaster.update_node_status(
            node_id,
            "executing",
            {"message": "Used by Vertex agent"},
            workflow_id=workflow_id,
        )
        await broadcaster.update_node_status(
            node_id,
            "success",
            {"message": "Used by Vertex agent"},
            workflow_id=workflow_id,
        )

    return pulse_ids
