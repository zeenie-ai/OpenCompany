"""Google Maps Nearby Places — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

from ._credentials import GoogleMapsCredential


class GmapsNearbyPlacesParams(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    radius: int = Field(default=1000, ge=1, le=50000)
    place_type: str = Field(default="")
    keyword: str = Field(default="")

    model_config = ConfigDict(extra="ignore")


class GmapsNearbyPlacesOutput(BaseModel):
    places: Optional[list] = None
    count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class GmapsNearbyPlacesNode(ActionNode):
    type = "gmaps_nearby_places"
    display_name = "Nearby Places"
    subtitle = "Places API"
    group = ("location", "service", "tool")
    description = "Google Places API nearbySearch"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    credentials = (GoogleMapsCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = GmapsNearbyPlacesParams
    Output = GmapsNearbyPlacesOutput

    @Operation("nearby", cost={"service": "google_maps", "action": "places_nearby", "count": 1})
    async def nearby(self, ctx: NodeContext, params: GmapsNearbyPlacesParams) -> Any:
        from services.plugin.deps import get_maps_service

        maps_service = get_maps_service()
        response = await maps_service.find_nearby_places(
            ctx.node_id, params.model_dump(), ctx.raw,
        )
        if response.get("success"):
            return response.get("result") or response
        raise NodeUserError(response.get("error") or "Nearby places failed")
