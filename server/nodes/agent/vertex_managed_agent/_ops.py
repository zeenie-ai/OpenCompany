"""Dynamic cloud-tool canvas nodes for the Vertex managed agent.

agentBuilder pattern: mint ``vertexCloudTool`` display nodes for each
distinct cloud-side tool the managed agent used, wire them to the
agent's ``input-tools`` handle, persist into ``workflow.data`` FIRST
(DB is source of truth on the next run) and then broadcast the ops
batch on the ``workflow_ops_apply`` wire key so an open canvas applies
them live. Pulsing (executing/success glow via ``pulse_node``) is the
caller's concern: the live SSE handler pulses per call/result step,
the post-turn sweep pulses once per missed key.
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


async def pulse_node(
    node_id: str,
    status: str,
    *,
    workflow_id: str,
    message: str = "Used by Vertex agent",
) -> None:
    """One status broadcast on a display node (executing / success)."""
    await get_status_broadcaster().update_node_status(
        node_id,
        status,
        {"message": message},
        workflow_id=workflow_id,
    )


async def ensure_cloud_tool_nodes(
    *,
    workflow_id: str,
    agent_node_id: str,
    used: Dict[str, str],
) -> Dict[str, str]:
    """Mint/dedupe display nodes for ``{cloud_tool_key: label}`` usage.

    Mint + persist + broadcast only — pulsing is the caller's concern
    (live streaming pulses per call/result; the post-turn sweep pulses
    once). Returns ``{cloud_tool_key: canvas_node_id}`` for every
    requested key (existing + minted).
    """
    if not used:
        return {}

    database = get_database()
    workflow = await database.get_workflow(workflow_id)
    if workflow is None:
        return {}

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
    resolved: Dict[str, str] = {
        key: existing[label] for key, label in used.items() if label in existing
    }

    fan_index = 0
    for key, label in used.items():
        if label in existing:
            continue
        minted_id = _mint_node_id(_DISPLAY_NODE_TYPE)
        client_ref = f"new_{key}"
        # Below-left of the agent so the top output-tool handle connects
        # upward into the agent's bottom input-tools handle (tool-node
        # convention; agentBuilder-spawned tools fan out below-right).
        add_node_op = wf_ops.add_node(
            client_ref,
            _DISPLAY_NODE_TYPE,
            {"cloud_tool_key": key},
            label=label,
            position=wf_ops.anchored(
                agent_node_id,
                offset_x=-60,
                offset_y=240 + 90 * fan_index,
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
                source_handle="output-tool",
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
                "sourceHandle": "output-tool",
                "targetHandle": "input-tools",
            }
        )
        await database.save_node_parameters(
            minted_id, {"label": label, "cloud_tool_key": key}
        )
        resolved[key] = minted_id
        fan_index += 1

    if operations:
        broadcaster = get_status_broadcaster()
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

    return resolved
