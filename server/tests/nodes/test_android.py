"""Contract tests for the 16 Android service nodes.

All Android nodes share a single handler (`handle_android_service`) bound by
the NodeExecutor registry. The handler maps the incoming camelCase `node_type`
to a snake_case `service_id` via its own internal `SERVICE_ID_MAP`, parses
parameters, and calls `android_service.execute_service(...)`.

These tests freeze that contract:

  1. Registry dispatch - every one of the 16 node types routes to the shared
     handler and calls `execute_service` with the correct `service_id`.
  2. NodeExecutor's Android output flatten - `result.data` contents are
     promoted to top-level of the stored output.
  3. Action branches - `status`, `enable`, `disable`, etc. all pass through
     unchanged.
  4. Parameter promotion - `package_name` (a root-level param on
     `appLauncher`) is hoisted into the nested `parameters` dict.
  5. JSON-string parameters - coerced to dict, malformed JSON silently becomes
     `{}`.
  6. Error envelopes - when the service returns `success=False` or raises, the
     envelope surfaces that to the caller.

The default mock `android_service` from `_harness` only wires
`execute_action`; the real handler calls `execute_service`. Tests install a
fresh `AsyncMock` on `execute_service` per test.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.node_contract


# Map camelCase node_type -> expected service_id. Mirrors the SERVICE_ID_MAP
# inside handlers/android.py. Keeping this list here lets us detect drift via
# the parametrized dispatch test below.
SERVICE_ID_MAP = {
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

DEFAULT_ACTIONS = {
    "batteryMonitor": "status",
    "networkMonitor": "status",
    "systemInfo": "info",
    "location": "current",
    "appLauncher": "launch",
    "appList": "list",
    "wifiAutomation": "status",
    "bluetoothAutomation": "status",
    "audioAutomation": "get_volume",
    "deviceStateAutomation": "status",
    "screenControlAutomation": "status",
    "airplaneModeControl": "status",
    "motionDetection": "current_motion",
    "environmentalSensors": "ambient_conditions",
    "cameraControl": "camera_info",
    "mediaControl": "volume_control",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_service_envelope(
    *,
    service_id: str,
    action: str,
    data: dict,
    success: bool = True,
    error: str | None = None,
    node_id: str = "test_node",
    android_host: str = "localhost",
    android_port: int = 8888,
) -> dict:
    """Shape identical to what AndroidService.execute_service returns."""
    result_block = {
        "service_id": service_id,
        "action": action,
        "data": data,
        "response_time": 0.01,
        "android_host": android_host,
        "android_port": android_port,
        "timestamp": datetime.now().isoformat(),
    }
    if not success and error is not None:
        result_block["error"] = error
    envelope = {
        "success": success,
        "node_id": node_id,
        "node_type": "androidService",
        "result": result_block,
        "execution_time": 0.01,
        "timestamp": datetime.now().isoformat(),
    }
    if not success and error is not None:
        envelope["error"] = error
    return envelope


def _install_execute_service(harness, *, return_value=None, side_effect=None):
    """Install a fresh AsyncMock on harness.android_service.execute_service."""
    kwargs = {}
    if side_effect is not None:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = (
            return_value
            if return_value is not None
            else {
                "success": True,
                "result": {
                    "service_id": "unknown",
                    "action": "unknown",
                    "data": {},
                    "response_time": 0.01,
                    "android_host": "localhost",
                    "android_port": 8888,
                    "timestamp": datetime.now().isoformat(),
                },
                "execution_time": 0.01,
                "timestamp": datetime.now().isoformat(),
            }
        )
    mock = AsyncMock(**kwargs)
    harness.android_service.execute_service = mock
    return mock


# ============================================================================
# Dispatch: all 16 node types route through the shared handler
# ============================================================================


class TestDispatch:
    """All 16 Android node types share one handler; dispatch the right service_id."""

    @pytest.mark.parametrize("node_type,expected_service_id", list(SERVICE_ID_MAP.items()))
    async def test_registry_routes_to_correct_service_id(self, harness, node_type, expected_service_id):
        action = DEFAULT_ACTIONS[node_type]
        envelope = _build_service_envelope(service_id=expected_service_id, action=action, data={"ok": True})
        mock = _install_execute_service(harness, return_value=envelope)

        result = await harness.execute(node_type, {"action": action})

        harness.assert_envelope(result, success=True)
        mock.assert_awaited_once()
        kwargs = mock.await_args.kwargs
        assert kwargs["service_id"] == expected_service_id
        assert kwargs["action"] == action

    async def test_handler_ignores_frontend_service_id_param(self, harness):
        """Frontend stores hidden `service_id` but handler's SERVICE_ID_MAP wins."""
        envelope = _build_service_envelope(service_id="device_state", action="status", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        # Frontend sends the wrong snake_case value (matches factory output,
        # not the handler map). Handler must override.
        await harness.execute(
            "deviceStateAutomation",
            {"action": "status", "service_id": "device_state_automation"},
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["service_id"] == "device_state"

    async def test_default_host_and_port_used_when_absent(self, harness):
        envelope = _build_service_envelope(service_id="battery", action="status", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute("batteryMonitor", {"action": "status"})

        kwargs = mock.await_args.kwargs
        assert kwargs["android_host"] == "localhost"
        assert kwargs["android_port"] == 8888

    async def test_custom_host_and_port_forwarded(self, harness):
        envelope = _build_service_envelope(
            service_id="battery",
            action="status",
            data={},
            android_host="10.0.0.5",
            android_port=9999,
        )
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute(
            "batteryMonitor",
            {"action": "status", "android_host": "10.0.0.5", "android_port": 9999},
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["android_host"] == "10.0.0.5"
        assert kwargs["android_port"] == 9999


# ============================================================================
# Parameter handling
# ============================================================================


class TestParameterHandling:
    async def test_package_name_promoted_into_parameters_dict(self, harness):
        """appLauncher: root-level package_name must end up inside parameters."""
        envelope = _build_service_envelope(service_id="app_launcher", action="launch", data={"launched": True})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute(
            "appLauncher",
            {"action": "launch", "package_name": "com.whatsapp"},
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["parameters"] == {"package_name": "com.whatsapp"}

    async def test_empty_package_name_is_not_promoted(self, harness):
        """Handler checks `if parameters[key]` - empty strings are skipped."""
        envelope = _build_service_envelope(service_id="app_launcher", action="launch", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute(
            "appLauncher",
            {"action": "launch", "package_name": ""},
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["parameters"] == {}

    async def test_parameters_json_string_is_parsed(self, harness):
        envelope = _build_service_envelope(service_id="audio_automation", action="set_volume", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute(
            "audioAutomation",
            {
                "action": "set_volume",
                "parameters": '{"stream": "music", "level": 5}',
            },
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["parameters"] == {"stream": "music", "level": 5}

    async def test_parameters_invalid_json_becomes_empty_dict(self, harness):
        envelope = _build_service_envelope(service_id="audio_automation", action="set_volume", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute(
            "audioAutomation",
            {"action": "set_volume", "parameters": "{not valid json"},
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["parameters"] == {}

    async def test_parameters_dict_passed_through(self, harness):
        envelope = _build_service_envelope(service_id="screen_control", action="set_brightness", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute(
            "screenControlAutomation",
            {"action": "set_brightness", "parameters": {"level": 200}},
        )

        kwargs = mock.await_args.kwargs
        assert kwargs["parameters"] == {"level": 200}

    async def test_default_action_fallback(self, harness):
        """Handler falls back to action='status' when param missing."""
        envelope = _build_service_envelope(service_id="battery", action="status", data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute("batteryMonitor", {})

        kwargs = mock.await_args.kwargs
        assert kwargs["action"] == "status"


# ============================================================================
# Action branches - representative set
# ============================================================================


class TestActionBranches:
    @pytest.mark.parametrize("action", ["status", "enable", "disable", "scan"])
    async def test_wifi_actions_pass_through(self, harness, action):
        envelope = _build_service_envelope(service_id="wifi_automation", action=action, data={"enabled": True})
        mock = _install_execute_service(harness, return_value=envelope)

        result = await harness.execute("wifiAutomation", {"action": action})

        harness.assert_envelope(result, success=True)
        assert mock.await_args.kwargs["action"] == action

    @pytest.mark.parametrize("action", ["status", "enable", "disable"])
    async def test_airplane_mode_actions_pass_through(self, harness, action):
        envelope = _build_service_envelope(service_id="airplane_mode", action=action, data={})
        mock = _install_execute_service(harness, return_value=envelope)

        await harness.execute("airplaneModeControl", {"action": action})

        assert mock.await_args.kwargs["action"] == action


# ============================================================================
# NodeExecutor output flatten (Android-specific)
# ============================================================================


class TestOutputFlatten:
    async def test_battery_status_data_flattened_to_top_level(self, harness):
        envelope = _build_service_envelope(
            service_id="battery",
            action="status",
            data={
                "battery_level": 87,
                "charging": True,
                "temperature": 29.4,
                "health": "good",
            },
        )
        _install_execute_service(harness, return_value=envelope)

        result = await harness.execute("batteryMonitor", {"action": "status"}, node_id="battery_1")

        harness.assert_envelope(result, success=True)
        # The output store receives the flattened payload
        stored = harness.output_for("battery_1", key="output_main")
        assert stored is not None
        assert stored["battery_level"] == 87
        assert stored["charging"] is True
        assert stored["temperature"] == 29.4
        assert stored["health"] == "good"
        # Original nested block preserved for consumers that still use it
        assert stored["data"]["battery_level"] == 87
        # Metadata preserved
        assert stored["service_id"] == "battery"
        assert stored["action"] == "status"

    async def test_location_data_flattened(self, harness):
        envelope = _build_service_envelope(
            service_id="location",
            action="current",
            data={
                "latitude": 37.7749,
                "longitude": -122.4194,
                "accuracy": 15.0,
                "provider": "gps",
            },
        )
        _install_execute_service(harness, return_value=envelope)

        result = await harness.execute("location", {"action": "current"}, node_id="loc_1")

        harness.assert_envelope(result, success=True)
        stored = harness.output_for("loc_1", key="output_main")
        assert stored["latitude"] == 37.7749
        assert stored["longitude"] == -122.4194
        assert stored["accuracy"] == 15.0
        assert stored["provider"] == "gps"

    async def test_non_dict_data_leaves_output_unflattened(self, harness):
        """If data is not a dict, flatten is a no-op - no crash."""
        envelope = _build_service_envelope(
            service_id="app_list",
            action="list",
            data=["com.app.one", "com.app.two"],  # list not dict
        )
        _install_execute_service(harness, return_value=envelope)

        result = await harness.execute("appList", {"action": "list"}, node_id="app_list_1")

        harness.assert_envelope(result, success=True)
        stored = harness.output_for("app_list_1", key="output_main")
        assert stored is not None
        assert stored["service_id"] == "app_list"
        assert stored["data"] == ["com.app.one", "com.app.two"]


# ============================================================================
# Error paths
# ============================================================================


class TestErrorPaths:
    async def test_service_returns_success_false_surfaces_as_error_envelope(self, harness):
        envelope = _build_service_envelope(
            service_id="battery",
            action="status",
            data={},
            success=False,
            error="Cannot connect to Android device at localhost:8888",
        )
        _install_execute_service(harness, return_value=envelope)

        result = await harness.execute("batteryMonitor", {"action": "status"})

        harness.assert_envelope(result, success=False)
        assert "cannot connect" in result["error"].lower()

    async def test_service_raises_propagates_as_exception(self, harness):
        """execute_service claims to never raise, but we still guard the path.

        If it does raise (e.g. during future refactors), NodeExecutor catches
        it and returns a failure envelope via ExecutionResult.
        """
        _install_execute_service(harness, side_effect=RuntimeError("relay client crashed"))

        result = await harness.execute("batteryMonitor", {"action": "status"})

        harness.assert_envelope(result, success=False)
        assert "relay client crashed" in result["error"].lower()
