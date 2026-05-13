"""Master Skill — Wave 11.C migration.

Skill aggregator. Passive node — the connected agent reads
``skillsConfig`` directly during ``_collect_agent_connections``;
this plugin only carries the metadata + Params shape. Run-button
hidden because there's nothing to execute standalone.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class MasterSkillParams(BaseModel):
    skill_folder: str = Field(default="assistant")
    skills_config: Dict[str, Any] = Field(
        default_factory=dict,
    )

    model_config = ConfigDict(extra="ignore")


class MasterSkillOutput(BaseModel):
    skills_active: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class MasterSkillNode(ActionNode):
    type = "masterSkill"
    display_name = "Master Skill"
    subtitle = "Skill Aggregator"
    group = ("tool",)
    description = "Aggregate multiple skills with enable/disable toggles"
    component_kind = "tool"
    handles = (
        {"name": "output-skill", "kind": "output", "position": "top",
         "label": "Skill", "role": "skill"},
    )
    ui_hints = {
        "isToolPanel": True,
        "isMasterSkillEditor": True,
        "hideRunButton": True,
        "hideInputSection": True,
        "hideOutputSection": True,
    }
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = MasterSkillParams
    Output = MasterSkillOutput

    @Operation("noop")
    async def noop(self, ctx: NodeContext, params: MasterSkillParams) -> MasterSkillOutput:
        # Passive node — agent reads skillsConfig during execution.
        active = sum(
            1 for cfg in (params.skills_config or {}).values()
            if isinstance(cfg, dict) and cfg.get("enabled")
        )
        return MasterSkillOutput(skills_active=active)
