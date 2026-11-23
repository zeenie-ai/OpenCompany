"""One trusted mobile-use task. No device clients: every action crosses the broker."""

from __future__ import annotations
import asyncio
import base64
import json
import os
import re
import sys
import urllib.request
import uuid
import time
import threading
from io import BytesIO

try:
    from .errors import describe_error
    from .progress import ProgressCallbacks
except ImportError:  # Executed by path in the isolated engine environment.
    from errors import describe_error
    from progress import ProgressCallbacks

STAGE = "bootstrap"
WIRE = sys.stdout
WIRE_LOCK = threading.Lock()


def emit(event_type: str, **data) -> None:
    with WIRE_LOCK:
        WIRE.write(json.dumps({"v": 1, "type": event_type, **data}, default=str) + "\n")
        WIRE.flush()


class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.geometry = None

    def read_sync(self, operation: str, **params):
        payload = {
            "operation": operation,
            "parameters": params,
            "epoch": self.config["epoch"],
            "operation_id": str(uuid.uuid4()),
            "geometry": self.geometry,
        }
        req = urllib.request.Request(
            self.config["broker_url"],
            json.dumps(payload).encode(),
            {"Content-Type": "application/json", "Authorization": f"Bearer {self.config['capability']}"},
        )
        started = time.monotonic()
        emit("activity", operation=operation, state="started")
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                result = json.load(response)
            if not result.get("success"):
                raise RuntimeError(result.get("error", "Device command failed"))
        except Exception:
            emit("activity", operation=operation, state="failed", duration_ms=round((time.monotonic() - started) * 1000))
            raise
        emit("activity", operation=operation, state="completed", duration_ms=round((time.monotonic() - started) * 1000))
        return result["result"]

    async def read(self, operation: str, **params):
        return await asyncio.to_thread(self.read_sync, operation, **params)

    async def get_screen_data(self):
        from minitap.mobile_use.controllers.device_controller import ScreenDataResponse

        result = await self.read("observe")
        self.geometry = result.pop("geometry")
        return ScreenDataResponse(**result)

    async def screenshot(self):
        return (await self.get_screen_data()).base64

    async def get_ui_hierarchy(self):
        return (await self.get_screen_data()).elements

    async def tap(self, coords, long_press=False, long_press_duration=1000):
        from minitap.mobile_use.controllers.types import TapOutput

        await self.read("tap", x=coords.x, y=coords.y, duration=long_press_duration if long_press else 0)
        return TapOutput(error=None)

    async def swipe(self, start, end, duration=400):
        await self.read("swipe", x=start.x, y=start.y, end_x=end.x, end_y=end.y, duration=duration)
        return None

    async def input_text(self, text):
        return await self.read("text", text=text)

    async def launch_app(self, package_or_bundle_id):
        return await self.read("launch", package=package_or_bundle_id)

    async def terminate_app(self, package_or_bundle_id):
        return await self.read("terminate", package=package_or_bundle_id)

    async def open_url(self, url):
        return await self.read("url", url=url)

    async def press_back(self):
        return await self.read("key", key="back")

    async def press_home(self):
        return await self.read("key", key="home")

    async def press_enter(self):
        return await self.read("key", key="enter")

    async def erase_text(self, nb_chars=None):
        return await self.read("erase", count=nb_chars or 50)

    async def cleanup(self):
        pass  # broker owns the persistent device

    def find_element(self, ui_hierarchy, resource_id=None, text=None, index=0):
        from minitap.mobile_use.controllers.types import Bounds

        matches = [
            e
            for e in ui_hierarchy
            if (resource_id and e.get("resource-id") == resource_id)
            or (text and (e.get("text") == text or e.get("accessibilityText") == text))
        ]
        if not matches and text and index == 0:
            return self._locate(text, Bounds)
        if index < 0 or index >= len(matches):
            return None, None, "Element not found"
        element = matches[index]
        points = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", element.get("bounds", ""))
        bounds = Bounds(**dict(zip(("x1", "y1", "x2", "y2"), map(int, points.groups())))) if points else None
        return element, bounds, None

    def _locate(self, text, Bounds):
        """An element the UI tree does not name (a canvas, a web view, an icon
        drawn without a label), found on the screenshot by the broker's
        ``locate`` (``nodes/mobile/_locate.py``); the engine taps the centre
        of its bounds. Anything short of a found, ordered box is "Element not
        found", as before. It blocks, since ``find_element`` answers at once."""
        try:
            found = self.read_sync("locate", text=text)
            x1, y1, x2, y2 = (found["bounds"][edge] for edge in ("x1", "y1", "x2", "y2"))
        except Exception:
            return None, None, "Element not found"
        usable = found.get("found") is True and all(type(edge) is int for edge in (x1, y1, x2, y2))
        if not usable or not (0 <= x1 < x2 and 0 <= y1 < y2):
            return None, None, "Element not found"
        element = {"text": text, "bounds": f"[{x1},{y1}][{x2},{y2}]", "located": True}
        return element, Bounds(x1=x1, y1=y1, x2=x2, y2=y2), None

    def get_compressed_b64_screenshot(self, image_base64, quality=50):
        from PIL import Image

        output = BytesIO()
        Image.open(BytesIO(base64.b64decode(image_base64))).convert("RGB").save(output, "JPEG", quality=quality)
        return base64.b64encode(output.getvalue()).decode()

    async def start_video_recording(self, max_duration_seconds=900):
        raise RuntimeError("Recording is not enabled; use Workspace preview")

    async def stop_video_recording(self):
        raise RuntimeError("Recording is not enabled")


async def main(config: dict) -> None:
    global STAGE
    emit("diagnostic", stage=STAGE)
    sys.stdout = sys.stderr  # upstream diagnostics can never corrupt the protocol
    os.environ.update(config.pop("model_env"))
    os.environ["MOBILE_USE_TELEMETRY_ENABLED"] = "false"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.path.insert(0, config["source"])
    from minitap.mobile_use.config import LLM, LLMWithFallback, LLMConfig, LLMConfigUtils
    from minitap.mobile_use.context import DeviceContext, DevicePlatform
    from minitap.mobile_use.sdk.agent import Agent
    from minitap.mobile_use.sdk.builders.agent_config_builder import AgentConfigBuilder
    from minitap.mobile_use.sdk.types.task import AgentProfile, TaskRequest
    # Only this isolated engine process uses LangChain; the server does not.
    from langchain_core.callbacks import BaseCallbackHandler

    class Progress(ProgressCallbacks, BaseCallbackHandler):
        def on_chat_model_start(self, serialized, messages, **kwargs):
            role = (kwargs.get("metadata") or {}).get("langgraph_node")
            allowed = {"planner", "orchestrator", "contextor", "cortex", "executor", "outputter", "hopper"}
            emit("diagnostic", stage="model_request", request_id=str(kwargs.get("run_id", "model")),
                 role=role if role in allowed else None)

        def on_llm_error(self, error, **kwargs):
            emit("diagnostic", stage="model_error", request_id=str(kwargs.get("run_id", "model")))

        def on_llm_end(self, response, **kwargs):
            emit("diagnostic", stage="model_response", request_id=str(kwargs.get("run_id", "model")))
            output = response.llm_output or {}
            usage = output.get("token_usage") or output.get("usage") or {}
            if usage:
                emit("usage", usage=usage)

    model = LLM(provider=config["provider"], model=config["model"])
    stage = LLMWithFallback(provider=model.provider, model=model.model, fallback=model)
    profile = AgentProfile(
        name="opencompany",
        llm_config=LLMConfig(
            planner=stage,
            orchestrator=stage,
            contextor=stage,
            cortex=stage,
            executor=stage,
            utils=LLMConfigUtils(outputter=stage, hopper=stage),
        ),
    )
    agent_config = (
        AgentConfigBuilder()
        .with_default_profile(profile)
        .for_device(DevicePlatform(config["platform"]), config["serial"])
        .with_graph_config_callbacks([Progress(emit)])
        .build()
    )
    agent_config.local_controller = Controller(config)
    agent_config.local_device_context = DeviceContext(
        host_platform="WINDOWS" if sys.platform == "win32" else "MACOS" if sys.platform == "darwin" else "LINUX",
        mobile_platform=DevicePlatform(config["platform"]),
        device_id=config["serial"],
        device_width=config["width"],
        device_height=config["height"],
    )
    agent = Agent(config=agent_config)
    task_failed = False
    try:
        STAGE = "engine_initialization"
        emit("diagnostic", stage=STAGE)
        await agent.init()
        emit("ready")
        STAGE = "task_execution"
        emit("diagnostic", stage=STAGE)
        result = await asyncio.wait_for(
            agent.run_task(request=TaskRequest(goal=config["prompt"], max_steps=config["max_steps"], record_trace=False)),
            config["timeout_s"],
        )
        emit("completed", result=result)
    except BaseException:
        task_failed = True
        raise
    finally:
        try:
            if not task_failed:
                emit("diagnostic", stage="engine_cleanup")
            await agent.clean()
        except Exception as cleanup_error:
            if not task_failed:
                STAGE = "engine_cleanup"
                raise
            # Preserve the original provider/device failure if cleanup fails.
            emit("diagnostic", stage="cleanup_failed", error_type=type(cleanup_error).__name__)


if __name__ == "__main__":
    try:
        asyncio.run(main(json.loads(sys.stdin.readline())))
    except BaseException as exc:
        emit("failed", **describe_error(exc, STAGE))
        sys.exit(1)
