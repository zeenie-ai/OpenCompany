"""Proxy Status — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class ProxyStatusParams(BaseModel):
    provider_name: str = Field(default="")

    model_config = ConfigDict(extra="ignore")


class ProxyStatusOutput(BaseModel):
    providers: Optional[list] = None
    stats: Optional[dict] = None

    model_config = ConfigDict(extra="allow")


class ProxyStatusNode(ActionNode):
    type = "proxyStatus"
    display_name = "Proxy Status"
    subtitle = "Health Stats"
    group = ("proxy", "tool")
    description = "View proxy provider health, scores, and usage statistics"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = ProxyStatusParams
    Output = ProxyStatusOutput

    @Operation("status")
    async def status(self, ctx: NodeContext, params: ProxyStatusParams) -> Any:
        """Inlined from handlers/proxy.py (Wave 11.D.3)."""
        from services.proxy.service import get_proxy_service

        svc = get_proxy_service()
        if not svc or not svc.is_enabled():
            return {"enabled": False, "providers": [], "stats": {}}
        return {
            "enabled": True,
            "providers": [p.model_dump() for p in svc.get_providers()],
            "stats": svc.get_stats(),
        }
