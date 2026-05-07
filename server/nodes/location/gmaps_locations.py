"""Google Maps Geocoding — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from ._credentials import GoogleMapsCredential


class GmapsLocationsParams(BaseModel):
    service_type: Literal["geocode", "reverse_geocode"] = Field(
        default="geocode",
        description="geocode: address -> lat/lng. reverse_geocode: lat/lng -> address.",
    )
    address: str = Field(
        default="",
        description="Street address or place name.",
        json_schema_extra={"displayOptions": {"show": {"service_type": ["geocode"]}}},
    )
    region: str = Field(
        default="",
        description="ISO country code bias (e.g. US, GB).",
        json_schema_extra={"displayOptions": {"show": {"service_type": ["geocode"]}}},
    )
    lat: float = Field(
        default=0.0,
        description="Latitude (-90 to 90).",
        json_schema_extra={"displayOptions": {"show": {"service_type": ["reverse_geocode"]}}},
    )
    lng: float = Field(
        default=0.0,
        description="Longitude (-180 to 180).",
        json_schema_extra={"displayOptions": {"show": {"service_type": ["reverse_geocode"]}}},
    )

    model_config = ConfigDict(extra="ignore")


class GmapsLocationsOutput(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    formatted_address: Optional[str] = None
    place_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class GmapsLocationsNode(ActionNode):
    type = "gmaps_locations"
    display_name = "Geocoding"
    subtitle = "Address \u2192 LatLng"
    group = ("location", "service", "tool")
    description = "Google Maps Geocoding service"
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

    Params = GmapsLocationsParams
    Output = GmapsLocationsOutput

    @Operation("geocode", cost={"service": "google_maps", "action": "geocode", "count": 1})
    async def geocode(self, ctx: NodeContext, params: GmapsLocationsParams) -> Any:
        from services.plugin.deps import get_maps_service

        maps_service = get_maps_service()
        response = await maps_service.geocode_location(
            ctx.node_id, params.model_dump(), ctx.raw,
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "Geocoding failed")
