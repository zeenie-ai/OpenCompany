"""Android System Services routes."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Dict, Any

from ._dispatcher import AndroidService
from core.logging import get_logger
from services.plugin.deps import get_android_service

logger = get_logger(__name__)
router = APIRouter(prefix="/api/android", tags=["android"])


class AndroidServiceRequest(BaseModel):
    """Request model for Android service execution."""
    service_id: str = Field(..., description="Android service ID (e.g., 'battery', 'network', 'app_launcher')")
    action: str = Field(..., description="Service action to perform (e.g., 'status', 'launch', 'list')")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")
    android_host: str = Field(default="localhost", description="Android device API host")
    android_port: int = Field(default=8888, description="Android device API port")


@router.post("/execute")
async def execute_android_service(
    request: AndroidServiceRequest,
    android_service: AndroidService = Depends(get_android_service)
):
    """Execute an Android system service action.

    Examples:
    - Battery status: {"service_id": "battery", "action": "status"}
    - Launch app: {"service_id": "app_launcher", "action": "launch", "parameters": {"package_name": "com.android.settings"}}
    - List apps: {"service_id": "app_list", "action": "list", "parameters": {"include_system": false}}
    """
    logger.info(
        "[Android API] Executing service",
        service_id=request.service_id,
        action=request.action,
        android_host=request.android_host,
        android_port=request.android_port
    )

    result = await android_service.execute_service(
        node_id="api_call",
        service_id=request.service_id,
        action=request.action,
        parameters=request.parameters,
        android_host=request.android_host,
        android_port=request.android_port
    )

    return result


@router.get("/status")
async def check_device_status(
    android_host: str = "localhost",
    android_port: int = 8888,
    android_service: AndroidService = Depends(get_android_service)
):
    """Check if Android device API is reachable."""
    logger.info(
        "[Android API] Checking device status",
        android_host=android_host,
        android_port=android_port
    )

    status = await android_service.check_device_status(
        android_host=android_host,
        android_port=android_port
    )

    return status


@router.get("/services/{service_id}/actions")
async def get_service_actions(
    service_id: str,
    android_service: AndroidService = Depends(get_android_service)
):
    """Get available actions for a specific Android service.

    Returns list of action options that can be performed on the service.
    """
    logger.info(f"[Android API] Getting actions for service: {service_id}")

    actions = android_service.get_service_actions(service_id)

    if not actions:
        return {
            "success": False,
            "error": f"Unknown service: {service_id}",
            "actions": []
        }

    return {
        "success": True,
        "service_id": service_id,
        "actions": actions
    }


@router.get("/services/{service_id}/actions/{action}/parameters")
async def get_action_parameters(
    service_id: str,
    action: str,
    android_service: AndroidService = Depends(get_android_service)
):
    """Get default parameters for a specific service action.

    Returns default parameter template that can be used as a starting point.
    """
    logger.info(f"[Android API] Getting parameters for: {service_id}/{action}")

    default_params = android_service.get_default_parameters(service_id, action)

    return {
        "success": True,
        "service_id": service_id,
        "action": action,
        "default_parameters": default_params
    }


@router.get("/devices")
async def list_android_devices():
    """List all connected Android devices via ADB."""
    import subprocess
    try:
        # Run adb devices command
        result = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5
        )

        devices = []
        lines = result.stdout.strip().split('\n')[1:]  # Skip header "List of devices attached"

        for line in lines:
            if not line.strip():
                continue

            parts = line.split()
            if len(parts) >= 2:
                device_id = parts[0]
                state = parts[1]

                # Extract model and other info
                model = "Unknown"
                android_version = None

                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.split(":")[1]
                    elif part.startswith("device:"):
                        pass  # Can use this for device codename

                devices.append({
                    "id": device_id,
                    "state": state,
                    "model": model,
                    "android_version": android_version
                })

        return {
            "success": True,
            "devices": devices,
            "count": len(devices)
        }
    except FileNotFoundError:
        logger.error("[Android] ADB not found in PATH")
        return {
            "success": False,
            "error": "ADB not found. Please install Android SDK Platform Tools",
            "devices": []
        }
    except Exception as e:
        logger.error(f"[Android] Failed to list devices: {e}")
        return {
            "success": False,
            "error": str(e),
            "devices": []
        }


@router.post("/port-forward")
async def setup_port_forwarding(
    device_id: str,
    local_port: int = 8888,
    device_port: int = 8888
):
    """Setup ADB port forwarding for Android device communication."""
    import subprocess
    try:
        # Setup port forwarding: adb -s device_id forward tcp:local_port tcp:device_port
        cmd = ["adb", "-s", device_id, "forward", f"tcp:{local_port}", f"tcp:{device_port}"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5
        )

        if result.returncode == 0:
            logger.info(
                f"[Android] Port forwarding setup: {device_id} tcp:{local_port} -> tcp:{device_port}"
            )
            return {
                "success": True,
                "device_id": device_id,
                "local_port": local_port,
                "device_port": device_port,
                "message": f"Port forwarding active: localhost:{local_port} -> device:{device_port}"
            }
        else:
            error_msg = result.stderr.strip() or result.stdout.strip()
            logger.error(f"[Android] Port forwarding failed: {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }
    except FileNotFoundError:
        logger.error("[Android] ADB not found in PATH")
        return {
            "success": False,
            "error": "ADB not found. Please install Android SDK Platform Tools"
        }
    except Exception as e:
        logger.error(f"[Android] Port forwarding error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/health")
async def android_health_check():
    """Android service health check."""
    return {
        "status": "OK",
        "service": "android"
    }


@router.get("/relay-status")
async def get_relay_connection_status():
    """Get relay connection status for remote Android devices.

    Returns connection status including whether a relay connection is active
    and the paired Android device.
    """
    try:
        from ._relay import get_current_relay_client

        relay_client = get_current_relay_client()

        if relay_client and relay_client.is_connected():
            return {
                "success": True,
                "connected": True,
                "paired": relay_client.is_paired(),
                "connection_type": "relay",
                "device_id": relay_client.paired_device_id,
                "device_name": relay_client.paired_device_name,
                "session_token": relay_client.session_token,
                "qr_data": relay_client.qr_data if not relay_client.is_paired() else None,
                "status": "paired" if relay_client.is_paired() else "waiting_for_pairing"
            }
        else:
            return {
                "success": True,
                "connected": False,
                "paired": False,
                "connection_type": None,
                "device_id": None,
                "device_name": None,
                "status": "disconnected"
            }
    except Exception as e:
        logger.error(f"[Android] Failed to get relay status: {e}")
        return {
            "success": False,
            "connected": False,
            "error": str(e),
            "status": "error"
        }
