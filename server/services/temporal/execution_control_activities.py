"""Client-side bridges for acknowledged execution-root enrollment.

The worker injects its existing Temporal client. Activities perform no provider
work, and retries are safe because enrollment is identified by execution chain.
"""

from typing import Any

from temporalio import activity
from temporalio.client import Client


class ExecutionControlActivities:
    def __init__(self, client: Client) -> None:
        self.client = client

    @activity.defn(name="execution_control.register")
    async def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.client.get_workflow_handle(payload["controller_workflow_id"]).execute_update(
            "register_execution", payload,
        )

    @activity.defn(name="execution_control.unregister")
    async def unregister(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.client.get_workflow_handle(payload["controller_workflow_id"]).execute_update(
            "unregister_execution", payload,
        )

    def activities(self) -> list:
        return [self.register, self.unregister]
