"""Plugin-owned Workspace cleanup, acknowledged by the durable task controller."""

import asyncio
from temporalio import activity


@activity.defn(name="workspace_tasks.reset_runtime")
async def reset_workspace_task_runtime(payload: dict) -> dict:
    from services.plugin.deps import get_database
    from services.node_registry import get_node_class
    from services.node_invocations import cancel_legacy_invocations, workspace_task_nodes

    workflow_id = payload["workflow_id"]

    async def heartbeat():
        while True:
            activity.heartbeat("Waiting for Workspace task cleanup")
            await asyncio.sleep(5)

    beat = asyncio.create_task(heartbeat())
    try:
        # Upgrade compatibility: old standalone roots were not children of
        # this controller. New admissions are fenced while this activity runs.
        legacy = await cancel_legacy_invocations(workflow_id)
        database = get_database()
        control = await database.get_latest_workflow_control(workflow_id)
        graph, nodes = await workspace_task_nodes(database, workflow_id, control)
        generation = int(control.generation) if control and control.status == "resetting" else 0
        reset_nodes = []
        for node in nodes:
            if node["type"] == "browser_agent":
                # Every native Browser Agent child has already acknowledged its
                # matching-token owner cleanup before the controller reaches us.
                # Re-resolving editable bindings would target a different profile
                # or fail after a deliberate graph deletion.
                continue
            result = await get_node_class(node["type"]).reset_execution_state(
                node_id=node["id"], workflow_id=workflow_id,
                execution_id=control.execution_id if control else "",
                generation=generation, graph=graph, database=database,
            )
            if result.get("reset"):
                reset_nodes.append(node["id"])
        return {"reset_nodes": reset_nodes, "legacy_cancelled": legacy}
    finally:
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)
