"""Android System Services integration."""

import time
import httpx
from datetime import datetime
from typing import Dict, Any, List

from core.logging import get_logger, log_execution_time

logger = get_logger(__name__)

# Default parameters for service actions (based on web_tester DEFAULTS)
SERVICE_DEFAULT_PARAMETERS = {
    ("app_launcher", "launch"): '{"package_name": "com.android.settings"}',
    ("wifi_automation", "enable"): "{}",
    ("wifi_automation", "disable"): "{}",
    ("wifi_automation", "status"): "{}",
    ("wifi_automation", "scan"): "{}",
    ("bluetooth_automation", "enable"): "{}",
    ("bluetooth_automation", "disable"): "{}",
    ("bluetooth_automation", "status"): "{}",
    ("audio_automation", "get_volume"): "{}",
    ("audio_automation", "set_volume"): '{"volume": 50}',
    ("audio_automation", "mute"): "{}",
    ("audio_automation", "unmute"): "{}",
    ("media_control", "volume_control"): '{"action": "get_volume"}',
    ("media_control", "media_control"): '{"action": "play"}',
    ("media_control", "play_media"): '{"url": "https://www.soundjay.com/misc/bell-ringing-05.wav"}',
    ("camera_control", "camera_info"): "{}",
    ("camera_control", "take_photo"): '{"filename": "test_photo.jpg"}',
    ("motion_detection", "current_motion"): "{}",
    ("motion_detection", "shake_detection"): "{}",
    ("environmental_sensors", "ambient_conditions"): "{}",
    ("environmental_sensors", "proximity"): "{}",
    ("location", "current"): "{}",
    ("location", "start_tracking"): "{}",
    ("location", "stop_tracking"): "{}",
    ("device_state_automation", "airplane_mode"): '{"enabled": true}',
    ("device_state_automation", "screen_on"): "{}",
    ("device_state_automation", "screen_off"): "{}",
    ("device_state_automation", "status"): "{}",
    ("screen_control_automation", "brightness"): '{"level": 150}',
    ("screen_control_automation", "wake"): "{}",
    ("screen_control_automation", "status"): "{}",
    ("app_list", "list"): '{"include_system": false, "include_disabled": false}',
    ("app_killer", "kill"): '{"package_name": "com.example.app"}',
    ("app_usage", "stats"): "{}",
    ("battery", "status"): "{}",
    ("network", "status"): "{}",
    ("system_info", "info"): "{}",
    ("airplane_mode_control", "status"): "{}",
}

# Service action mappings based on Android service schemas
SERVICE_ACTIONS = {
    "battery": [{"name": "Status", "value": "status", "description": "Get battery status information"}],
    "network": [{"name": "Status", "value": "status", "description": "Get network connectivity status"}],
    "system_info": [{"name": "Info", "value": "info", "description": "Get system and device information"}],
    "location": [
        {"name": "Current", "value": "current", "description": "Get current location"},
        {"name": "Start Tracking", "value": "start_tracking", "description": "Start location tracking"},
        {"name": "Stop Tracking", "value": "stop_tracking", "description": "Stop location tracking"},
    ],
    "app_launcher": [{"name": "Launch", "value": "launch", "description": "Launch an application"}],
    "app_list": [{"name": "List", "value": "list", "description": "Get list of installed apps"}],
    "app_killer": [{"name": "Kill", "value": "kill", "description": "Force stop an application"}],
    "app_usage": [{"name": "Stats", "value": "stats", "description": "Get app usage statistics"}],
    "wifi_automation": [
        {"name": "Enable", "value": "enable", "description": "Enable WiFi"},
        {"name": "Disable", "value": "disable", "description": "Disable WiFi"},
        {"name": "Status", "value": "status", "description": "Get WiFi status"},
        {"name": "Scan", "value": "scan", "description": "Scan for networks"},
    ],
    "bluetooth_automation": [
        {"name": "Enable", "value": "enable", "description": "Enable Bluetooth"},
        {"name": "Disable", "value": "disable", "description": "Disable Bluetooth"},
        {"name": "Status", "value": "status", "description": "Get Bluetooth status"},
    ],
    "audio_automation": [
        {"name": "Get Volume", "value": "get_volume", "description": "Get current volume"},
        {"name": "Set Volume", "value": "set_volume", "description": "Set volume level"},
        {"name": "Mute", "value": "mute", "description": "Mute audio"},
        {"name": "Unmute", "value": "unmute", "description": "Unmute audio"},
    ],
    "device_state_automation": [
        {"name": "Airplane Mode", "value": "airplane_mode", "description": "Toggle airplane mode"},
        {"name": "Screen On", "value": "screen_on", "description": "Turn screen on"},
        {"name": "Screen Off", "value": "screen_off", "description": "Turn screen off"},
        {"name": "Status", "value": "status", "description": "Get device state"},
    ],
    "screen_control_automation": [
        {"name": "Brightness", "value": "brightness", "description": "Set brightness level"},
        {"name": "Wake", "value": "wake", "description": "Wake up screen"},
        {"name": "Status", "value": "status", "description": "Get screen status"},
    ],
    "motion_detection": [
        {"name": "Current Motion", "value": "current_motion", "description": "Get current motion data"},
        {"name": "Shake Detection", "value": "shake_detection", "description": "Detect shake gesture"},
    ],
    "environmental_sensors": [
        {"name": "Ambient Conditions", "value": "ambient_conditions", "description": "Get temperature, humidity, pressure"},
        {"name": "Proximity", "value": "proximity", "description": "Get proximity sensor data"},
    ],
    "camera_control": [
        {"name": "Camera Info", "value": "camera_info", "description": "Get camera information"},
        {"name": "Take Photo", "value": "take_photo", "description": "Capture a photo"},
    ],
    "media_control": [
        {"name": "Volume Control", "value": "volume_control", "description": "Control media volume"},
        {"name": "Media Control", "value": "media_control", "description": "Control playback"},
        {"name": "Play Media", "value": "play_media", "description": "Play media file"},
    ],
    "airplane_mode_control": [{"name": "Status", "value": "status", "description": "Get airplane mode status"}],
    # Unavailable services on current device (emulator-5554)
    # These services returned "Service not available" errors during testing
    # 'notification_sender': [
    #     {'name': 'Send', 'value': 'send', 'description': 'Send notification'}
    # ],
    # 'contact_list': [
    #     {'name': 'List', 'value': 'list', 'description': 'Get contacts list'}
    # ],
    # 'clipboard_automation': [
    #     {'name': 'Get', 'value': 'get', 'description': 'Get clipboard content'},
    #     {'name': 'Set', 'value': 'set', 'description': 'Set clipboard content'}
    # ],
    # 'torch_control': [
    #     {'name': 'Status', 'value': 'status', 'description': 'Get torch status'}
    # ],
    # 'vibrator_control': [
    #     {'name': 'Vibrate', 'value': 'vibrate', 'description': 'Vibrate device'}
    # ],
    # 'sms_sender': [
    #     {'name': 'Send', 'value': 'send', 'description': 'Send SMS'}
    # ],
    # 'call_automation': [
    #     {'name': 'Dial', 'value': 'dial', 'description': 'Dial phone number'}
    # ],
    # 'camera_automation': [
    #     {'name': 'Status', 'value': 'status', 'description': 'Get camera status'}
    # ],
    # 'flashlight': [
    #     {'name': 'Turn On', 'value': 'turn_on', 'description': 'Turn flashlight on'},
    #     {'name': 'Turn Off', 'value': 'turn_off', 'description': 'Turn flashlight off'},
    #     {'name': 'Toggle', 'value': 'toggle', 'description': 'Toggle flashlight'}
    # ],
    # 'sound_recording': [
    #     {'name': 'Start Recording', 'value': 'start_recording', 'description': 'Start audio recording'},
    #     {'name': 'Stop Recording', 'value': 'stop_recording', 'description': 'Stop audio recording'}
    # ]
}


class AndroidService:
    """Android System Services client for device automation."""

    def __init__(self):
        self.default_timeout = 30.0

    def get_service_actions(self, service_id: str) -> List[Dict[str, str]]:
        """Get available actions for a specific Android service.

        Args:
            service_id: Android service ID

        Returns:
            List of action options with name, value, and description
        """
        return SERVICE_ACTIONS.get(service_id, [])

    def get_default_parameters(self, service_id: str, action: str) -> str:
        """Get default parameters for a specific service action.

        Args:
            service_id: Android service ID
            action: Action name

        Returns:
            Default parameters as JSON string
        """
        return SERVICE_DEFAULT_PARAMETERS.get((service_id, action), "{}")

    async def _execute_via_relay(
        self, node_id: str, service_id: str, action: str, parameters: Dict[str, Any], start_time: float
    ) -> Dict[str, Any]:
        """Execute Android service via Relay WebSocket (remote device).

        Uses JSON-RPC 2.0 protocol via relay.send to paired Android device.

        Args:
            node_id: Node identifier
            service_id: Android service ID
            action: Service action
            parameters: Action parameters
            start_time: Execution start time

        Returns:
            Execution result with service response data
        """
        try:
            from ._relay import get_current_relay_client

            relay_client = get_current_relay_client()
            if not relay_client:
                raise Exception("Relay client not available - run Android Device Setup first")

            if not relay_client.is_paired():
                raise Exception("No Android device paired. Scan QR code with Android app to pair.")

            device_id = relay_client.paired_device_id
            device_name = relay_client.paired_device_name

            logger.debug(
                "[Android Service] Sending relay service request",
                service_id=service_id,
                action=action,
                device_id=device_id,
                device_name=device_name,
            )

            # Send service request via relay
            response = await relay_client.send_service_request(
                service_id=service_id,
                action=action,
                parameters=parameters,
                timeout=30.0,  # Increased timeout for relay communication
            )

            if not response:
                raise Exception("No response from Android device (timeout). " "Ensure the Android app is running and paired.")

            # Parse response - Android device may return:
            # 1. {"success": true, "data": {...}}
            # 2. {"data": {...}} (success implied)
            # 3. {"success": false, "error": "..."}
            # 4. Raw data object without wrapper
            logger.debug(
                "[Android Service] Raw relay response", response_keys=list(response.keys()) if isinstance(response, dict) else "not_dict"
            )

            # Check for explicit success field, otherwise infer from data presence
            if "success" in response:
                success = response.get("success", False)
            else:
                # No explicit success field - if we got data, consider it success
                success = "data" in response or ("error" not in response and response)

            data = response.get("data", response if "success" not in response and "error" not in response else {})
            error_msg = response.get("error")

            logger.debug("[Android Service] Relay response parsed", success=success, has_data=bool(data), error=error_msg)

            if success:
                result = {
                    "success": True,
                    "node_id": node_id,
                    "node_type": "androidService",
                    "result": {
                        "service_id": service_id,
                        "action": action,
                        "data": data,
                        "response_time": time.time() - start_time,
                        "connection_type": "relay",
                        "device_id": device_id,
                        "device_name": device_name,
                        "timestamp": datetime.now().isoformat(),
                    },
                    "execution_time": time.time() - start_time,
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                result = {
                    "success": False,
                    "node_id": node_id,
                    "node_type": "androidService",
                    "error": error_msg or "Service execution failed on Android device",
                    "result": {
                        "service_id": service_id,
                        "action": action,
                        "data": data,
                        "connection_type": "relay",
                        "device_id": device_id,
                        "timestamp": datetime.now().isoformat(),
                    },
                    "execution_time": time.time() - start_time,
                    "timestamp": datetime.now().isoformat(),
                }

            log_execution_time(logger, f"android_service_{service_id}_{action}_relay", start_time, time.time())

            logger.debug(
                "[Android Service] Relay execution completed", node_id=node_id, service_id=service_id, action=action, success=success
            )

            return result

        except Exception as e:
            logger.error(
                "[Android Service] Relay execution failed",
                node_id=node_id, service_id=service_id, action=action,
                error=str(e), exc_info=True,
            )

            return {
                "success": False,
                "node_id": node_id,
                "node_type": "androidService",
                "error": "Relay execution failed",
                "result": {"service_id": service_id, "action": action, "connection_type": "relay", "timestamp": datetime.now().isoformat()},
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    async def execute_service(
        self,
        node_id: str,
        service_id: str,
        action: str,
        parameters: Dict[str, Any],
        android_host: str = "localhost",
        android_port: int = 8888,
    ) -> Dict[str, Any]:
        """Execute an Android system service action.

        Args:
            node_id: Node identifier
            service_id: Android service ID (e.g., 'battery', 'network', 'app_launcher')
            action: Service action to perform (e.g., 'status', 'launch', 'list')
            parameters: Action-specific parameters
            android_host: Android device API host
            android_port: Android device API port

        Returns:
            Execution result with service response data
        """
        start_time = time.time()

        try:
            # Check if relay connection is available (remote device)
            from ._relay import get_current_relay_client

            relay_client = get_current_relay_client()

            if relay_client and relay_client.is_paired():
                # Use relay for remote Android device
                logger.debug("[Android Service] Using relay connection", node_id=node_id, service_id=service_id, action=action)
                return await self._execute_via_relay(node_id, service_id, action, parameters, start_time)
            else:
                # Use local HTTP connection
                base_url = f"http://{android_host}:{android_port}/api"
                logger.debug(
                    "[Android Service] Using local HTTP connection",
                    node_id=node_id,
                    service_id=service_id,
                    action=action,
                    base_url=base_url,
                )

            # Build request payload
            request_payload = {"action": action, "parameters": parameters}

            # Make request to Android device API
            async with httpx.AsyncClient(timeout=self.default_timeout) as client:
                response = await client.post(f"{base_url}/{service_id}", json=request_payload)

                # Parse response
                if response.status_code == 200:
                    response_data = response.json()

                    # Check if Android service returned success
                    service_success = response_data.get("success", True)

                    result = {
                        "success": service_success,
                        "node_id": node_id,
                        "node_type": "androidService",
                        "result": {
                            "service_id": service_id,
                            "action": action,
                            "data": response_data.get("data", response_data),
                            "response_time": response.elapsed.total_seconds(),
                            "android_host": android_host,
                            "android_port": android_port,
                            "timestamp": datetime.now().isoformat(),
                        },
                        "execution_time": time.time() - start_time,
                        "timestamp": datetime.now().isoformat(),
                    }

                    if not service_success:
                        result["result"]["error"] = response_data.get("error", "Service execution failed")

                    log_execution_time(logger, f"android_service_{service_id}_{action}", start_time, time.time())

                    logger.debug(
                        "[Android Service] Execution completed",
                        node_id=node_id,
                        service_id=service_id,
                        action=action,
                        success=service_success,
                    )

                    return result

                else:
                    # HTTP error
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    logger.error(
                        "[Android Service] HTTP error",
                        node_id=node_id,
                        service_id=service_id,
                        status_code=response.status_code,
                        error=error_msg,
                    )

                    return {
                        "success": False,
                        "node_id": node_id,
                        "node_type": "androidService",
                        "error": error_msg,
                        "result": {
                            "service_id": service_id,
                            "action": action,
                            "status_code": response.status_code,
                            "timestamp": datetime.now().isoformat(),
                        },
                        "execution_time": time.time() - start_time,
                        "timestamp": datetime.now().isoformat(),
                    }

        except httpx.ConnectError as e:
            error_msg = f"Cannot connect to Android device at {android_host}:{android_port}. Ensure ADB port forwarding is active: adb forward tcp:{android_port} tcp:{android_port}"
            logger.error(
                "[Android Service] Connection error",
                node_id=node_id,
                service_id=service_id,
                error=str(e),
                android_host=android_host,
                android_port=android_port,
            )

            return {
                "success": False,
                "node_id": node_id,
                "node_type": "androidService",
                "error": error_msg,
                "result": {"service_id": service_id, "action": action, "connection_error": "connection failed", "timestamp": datetime.now().isoformat()},
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except httpx.TimeoutException:
            error_msg = f"Request timeout after {self.default_timeout}s"
            logger.error("[Android Service] Timeout", node_id=node_id, service_id=service_id, timeout=self.default_timeout)

            return {
                "success": False,
                "node_id": node_id,
                "node_type": "androidService",
                "error": error_msg,
                "result": {
                    "service_id": service_id,
                    "action": action,
                    "timeout": self.default_timeout,
                    "timestamp": datetime.now().isoformat(),
                },
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error("[Android Service] Unexpected error", node_id=node_id, service_id=service_id, error=str(e), exc_info=True)

            return {
                "success": False,
                "node_id": node_id,
                "node_type": "androidService",
                "error": "Android service execution failed",
                "result": {"service_id": service_id, "action": action, "timestamp": datetime.now().isoformat()},
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    async def check_device_status(self, android_host: str = "localhost", android_port: int = 8888) -> Dict[str, Any]:
        """Check if Android device API is reachable.

        Returns:
            Status check result with online status
        """
        try:
            base_url = f"http://{android_host}:{android_port}/api"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{base_url}/status")

                return {
                    "online": response.status_code == 200,
                    "data": response.json() if response.status_code == 200 else None,
                    "android_host": android_host,
                    "android_port": android_port,
                }
        except Exception as e:
            logger.warning("[Android Service] Device offline", android_host=android_host, android_port=android_port, error=str(e))
            return {"online": False, "error": "Device offline", "android_host": android_host, "android_port": android_port}
