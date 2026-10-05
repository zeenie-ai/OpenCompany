"""The managed team job boundary, also exposed as Talk's submit tool."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class EmployeeJobParams(BaseModel):
    operation: Literal["submit", "intake", "deliver"] = "submit"
    mission: str = Field(default="", max_length=20000, description="The substantive job and expected result")
    lead_node_id: str = ""
    delivery_node_ids: list[str] = Field(default_factory=list)
    model_config = ConfigDict(extra="ignore")


class EmployeeJobOutput(BaseModel):
    job_id: str = ""
    mission: str = ""
    state: str = "working"
    acknowledgement: str = "I’ve given this to the team. I’ll share their checked result here."
    delivered: bool = False
    model_config = ConfigDict(extra="allow")


class EmployeeJobNode(ActionNode):
    type = "employeeJob"
    display_name = "Team work"
    description = "Give substantive work to this employee’s team; report progress and deliver only reviewed results"
    group = ("workflow",)
    component_kind = "square"
    usable_as_tool = True
    server_controlled_fields = ("operation", "lead_node_id", "delivery_node_ids")
    handles = ({"name": "input-main", "kind": "input", "position": "left", "role": "main"},
               {"name": "output-main", "kind": "output", "position": "right", "role": "main"},
               {"name": "output-top", "kind": "output", "position": "top", "role": "tool"})
    task_queue = TaskQueue.DEFAULT
    Params = EmployeeJobParams
    Output = EmployeeJobOutput

    async def _create(self, ctx, params, dispatch):
        from services.plugin.deps import get_database
        from services.employees.jobs import create_job, dispatch_job
        if not params.mission.strip():
            raise ValueError("Describe the work you want the team to do")
        job = await create_job(get_database(), ctx, mission=params.mission.strip(), lead_node_id=params.lead_node_id,
                               delivery_node_ids=params.delivery_node_ids, dispatch=dispatch)
        if dispatch:
            await dispatch_job(get_database(), job.id)
        return EmployeeJobOutput(job_id=job.id, mission=job.mission, state=job.state)

    @Operation("submit")
    async def submit(self, ctx: NodeContext, params: EmployeeJobParams) -> EmployeeJobOutput:
        return await self._create(ctx, params, True)

    @Operation("intake")
    async def intake(self, ctx: NodeContext, params: EmployeeJobParams) -> EmployeeJobOutput:
        return await self._create(ctx, params, False)

    @Operation("deliver")
    async def deliver(self, ctx: NodeContext, params: EmployeeJobParams) -> EmployeeJobOutput:
        from services.plugin.deps import get_database
        from services.employees.jobs import deliver_job
        return EmployeeJobOutput(**await deliver_job(get_database(), ctx, lead_node_id=params.lead_node_id))
