"""Google Maps Create — Wave 11.C migration. Renders an interactive map."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from ._credentials import GoogleMapsCredential


class GmapsCreateParams(BaseModel):
    center_lat: float = Field(default=0.0, description="Map center latitude.")
    center_lng: float = Field(default=0.0, description="Map center longitude.")
    zoom: int = Field(default=10, ge=1, le=20, description="Zoom level (1=world, 20=street).")
    map_type: Literal["roadmap", "satellite", "hybrid", "terrain"] = Field(
        default="roadmap",
    )
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Map customization options. Supported keys: disable_default_ui (bool), "
            "zoom_control (bool), street_view_control (bool), map_type_control (bool), "
            "fullscreen_control (bool)."
        ),
        json_schema_extra={"rows": 4},
    )

    model_config = ConfigDict(extra="ignore")


class GmapsCreateOutput(BaseModel):
    map_id: Optional[str] = None
    config: Optional[dict] = None

    model_config = ConfigDict(extra="allow")


class GmapsCreateNode(ActionNode):
    type = "gmaps_create"
    display_name = "Map Create"
    subtitle = "Google Map"
    group = ("location", "service")
    description = "Google Maps creation with center, zoom, and map type"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    ui_hints = {"showLocationPanel": True}
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    credentials = (GoogleMapsCredential,)
    task_queue = TaskQueue.DEFAULT

    Params = GmapsCreateParams
    Output = GmapsCreateOutput

    @Operation("create")
    async def create(self, ctx: NodeContext, params: GmapsCreateParams) -> Any:
        from services.plugin.deps import get_maps_service

        maps_service = get_maps_service()
        response = await maps_service.create_map(
            ctx.node_id, params.model_dump(), ctx.raw,
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "Map create failed")
