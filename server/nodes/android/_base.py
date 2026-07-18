"""Shared base for Android service plugins.

16 Android nodes (battery, network, wifi, bluetooth, audio, camera, …)
all dispatch through ``invoke()`` with the node_type as the service ID.
Subclass + set 5 attrs to mint a new one. ``android_service`` is fetched
from the DI container at call time — Android relay client is process
singleton.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


# Maps camelCase node types to snake_case service IDs.
# Same map is mirrored in services/handlers/tools.py for the AI-tool path.
SERVICE_ID_MAP: Dict[str, str] = {
    "batteryMonitor": "battery",
    "networkMonitor": "network",
    "systemInfo": "system_info",
    "location": "location",
    "appLauncher": "app_launcher",
    "appList": "app_list",
    "wifiAutomation": "wifi_automation",
    "bluetoothAutomation": "bluetooth_automation",
    "audioAutomation": "audio_automation",
    "deviceStateAutomation": "device_state",
    "screenControlAutomation": "screen_control",
    "airplaneModeControl": "airplane_mode",
    "motionDetection": "motion_detection",
    "environmentalSensors": "environmental_sensors",
    "cameraControl": "camera_control",
    "mediaControl": "media_control",
}


class AndroidServiceParams(BaseModel):
    action: str = Field(
        default="status",
        description="Service action to invoke (populated dynamically from backend)",
        json_schema_extra={
            "dynamicOptions": True,
            "loadOptionsMethod": "getAndroidServiceActions",
        },
    )
    parameters: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

    @field_validator("parameters", mode="before")
    @classmethod
    def _coerce_parameters(cls, value: Any) -> Dict[str, Any]:
        """Accept a JSON-encoded string in the ``parameters`` slot.

        Pre-refactor handlers sometimes receive ``parameters`` as a raw
        JSON string when the node is wired from an upstream template.
        Malformed JSON silently becomes an empty dict rather than
        failing validation — matches the pre-refactor tolerance
        (see tests/nodes/test_android.py::TestParameterHandling).
        """
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                return {}
        return {}


class AndroidServiceOutput(BaseModel):
    success: Optional[bool] = None
    data: Optional[Any] = None

    model_config = ConfigDict(extra="allow")


class AndroidServiceBase(ActionNode, abstract=True):
    """Subclass and set type/display_name/icon/description.

    Visual metadata (icon + color) lives in ``server/nodes/visuals.json``
    keyed by individual plugin type. The ``_visuals.py`` resolver picks
    each entry up at NodeSpec emit time; no class-level ClassVars needed.
    """

    group = ("android", "service")
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.ANDROID
    usable_as_tool = True

    Params = AndroidServiceParams
    Output = AndroidServiceOutput

    @Operation("invoke", cost={"service": "android", "action": "service_call", "count": 1})
    async def invoke(self, ctx: NodeContext, params: AndroidServiceParams) -> Any:
        from services.plugin.deps import get_android_service

        android_service = get_android_service()
        payload = params.model_dump()

        # Derive service_id from the registered node type (battery etc.) —
        # hidden params may not be in the DB so the type is the source of truth.
        service_id = SERVICE_ID_MAP.get(self.type, payload.get("service_id", "battery"))
        action = payload.get("action", "status")
        service_params = payload.get("parameters", {})
        android_host = payload.get("android_host", "localhost")
        android_port = payload.get("android_port", 8888)

        # Parse JSON-string parameters defensively.
        if isinstance(service_params, str):
            try:
                service_params = json.loads(service_params)
            except json.JSONDecodeError:
                service_params = {}

        # Hoist root-level additional properties (e.g. package_name from
        # appLauncher's additionalProperties UI) into service_params.
        for key in ("package_name",):
            if payload.get(key):
                service_params[key] = payload[key]

        logger.debug(
            "[Android] node_type=%s -> service_id=%s action=%s host=%s:%s params=%s",
            self.type,
            service_id,
            action,
            android_host,
            android_port,
            service_params,
        )

        result = await android_service.execute_service(
            node_id=ctx.node_id,
            service_id=service_id,
            action=action,
            parameters=service_params,
            android_host=android_host,
            android_port=android_port,
        )
        if isinstance(result, dict) and result.get("success") is False:
            raise RuntimeError(result.get("error") or f"{self.type} failed")
        if isinstance(result, dict):
            return result.get("result") or result
        return result


# ============================================================================
# AI-tool-time dispatchers (Wave 11.E.3)
# ----------------------------------------------------------------------------
# When an Android service is connected to an AI agent's input-tools handle,
# the LLM emits a different shape from the workflow-node Params schema.
# ============================================================================


async def _execute_with_broadcast(
    *,
    target_node_id: Optional[str],
    workflow_id: Optional[str],
    service_id: str,
    action: str,
    parameters: Dict[str, Any],
    host: str,
    port: int,
    log_label: str,
) -> Dict[str, Any]:
    """Run an Android service call with broadcast status for the UI node."""
    from ._dispatcher import AndroidService
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    if target_node_id:
        await broadcaster.update_node_status(
            target_node_id,
            "executing",
            {"message": f"Executing {action} via {log_label}"},
            workflow_id=workflow_id,
        )

    try:
        result = await AndroidService().execute_service(
            node_id=target_node_id or "tool",
            service_id=service_id,
            action=action,
            parameters=parameters,
            android_host=host,
            android_port=port,
        )

        if target_node_id:
            if result.get("success"):
                await broadcaster.update_node_status(
                    target_node_id,
                    "success",
                    {"message": f"{action} completed", "result": result.get("result", {})},
                    workflow_id=workflow_id,
                )
            else:
                await broadcaster.update_node_status(
                    target_node_id,
                    "error",
                    {"message": result.get("error", "Unknown error")},
                    workflow_id=workflow_id,
                )

        if result.get("success"):
            return {
                "success": True,
                "service": service_id,
                "action": action,
                "data": result.get("result", {}).get("data", result.get("result", {})),
            }
        return {
            "error": result.get("error", "Unknown error"),
            "service": service_id,
            "action": action,
        }
    except Exception as e:
        logger.error(f"[{log_label}] Unexpected error: {e}")
        if target_node_id:
            await broadcaster.update_node_status(
                target_node_id,
                "error",
                {"message": str(e)},
                workflow_id=workflow_id,
            )
        return {"error": str(e)}


async def execute_android_service_tool(
    args: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Route an LLM tool call to a directly-connected Android service node.

    Uses ``SERVICE_ID_MAP[node_type]`` instead of the synthetic toolkit
    indirection — the LLM addresses each service node by its own type.
    """
    node_type = config.get("node_type", "")
    node_id = config.get("node_id", "")
    node_params = config.get("parameters", {})
    workflow_id = config.get("workflow_id")

    service_id = SERVICE_ID_MAP.get(node_type, node_type)
    action = args.get("action") or node_params.get("action", "status")
    parameters = args.get("parameters") or {}
    host = node_params.get("android_host", "localhost")
    port = int(node_params.get("android_port", 8888))

    logger.info(
        "[Android Service] Executing %s.%s (node: %s, workflow: %s)",
        service_id,
        action,
        node_id,
        workflow_id,
    )

    return await _execute_with_broadcast(
        target_node_id=node_id,
        workflow_id=workflow_id,
        service_id=service_id,
        action=action,
        parameters=parameters,
        host=host,
        port=port,
        log_label="Android Service",
    )
