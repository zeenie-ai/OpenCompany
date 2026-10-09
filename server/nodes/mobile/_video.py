"""Pinned scrcpy video-only transport. Device control never enters this socket.

The client uses the matching maintained Tango parser. We remove only scrcpy's
forward-tunnel sentinel; metadata and encoded packet bytes pass through intact.
"""

from __future__ import annotations
import asyncio
import contextlib
import hashlib
import secrets
from fastapi import WebSocket, WebSocketDisconnect
from services.process_environment import without_onepassword_environment
from ._control import MobileError
from ._install import SCRCPY_SHA256, SCRCPY_VERSION, VIDEO_MAX_FPS, sdk_tool
from ._paths import mobile_root
from ._process import command, hidden_options

_streams: set[asyncio.Task] = set()


async def shutdown_video() -> None:
    tasks = list(_streams)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def server_arguments(serial: str, scid: str) -> list[str]:
    return [
        str(sdk_tool("adb")),
        "-s",
        serial,
        "shell",
        "CLASSPATH=/data/local/tmp/opencompany-scrcpy.jar",
        "app_process",
        "/",
        "com.genymobile.scrcpy.Server",
        SCRCPY_VERSION,
        f"scid={scid}",
        "tunnel_forward=true",
        "audio=false",
        "control=false",
        "cleanup=false",
        "power_on=false",
        "video_codec=h264",
        "max_size=1280",
        f"max_fps={VIDEO_MAX_FPS}",
        "video_bit_rate=4000000",
        "send_dummy_byte=true",
        "send_device_meta=true",
        "send_stream_meta=true",
        "send_frame_meta=true",
    ]


async def stream_video(websocket: WebSocket, viewer: str) -> None:
    from ._diagnostics import event
    from ._runtime import get_runtime

    runtime = get_runtime()
    if runtime.video_viewer is not None:
        await websocket.close(code=4009, reason="The shared preview is open in another tab")
        return
    if not runtime.serial or not sdk_tool("adb"):
        await websocket.close(code=4004, reason="Start the device first")
        return
    runtime.video_viewer = viewer
    current = asyncio.current_task()
    _streams.add(current)
    proc = writer = None
    jobs: list[asyncio.Task] = []
    port = None
    serial = runtime.serial
    adb = str(sdk_tool("adb"))
    try:
        server = mobile_root() / "scrcpy-server"
        if not server.is_file() or await asyncio.to_thread(lambda: hashlib.sha256(server.read_bytes()).hexdigest()) != SCRCPY_SHA256:
            raise MobileError("video_setup_required", "Repair Mobile setup to restore the pinned video server")
        await command([adb, "-s", serial, "push", str(server), "/data/local/tmp/opencompany-scrcpy.jar"], timeout=30)
        scid = f"{secrets.randbelow(2**31):08x}"
        raw_port = await command([adb, "-s", serial, "forward", "tcp:0", f"localabstract:scrcpy_{scid}"])
        port = int(raw_port.strip())
        if not 1 <= port <= 65535:
            raise MobileError("video_tunnel_failed", "Invalid video tunnel port")
        proc = await asyncio.create_subprocess_exec(
            *server_arguments(serial, scid),
            env=without_onepassword_environment(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **hidden_options(),
        )
        reader = None
        for _ in range(60):
            if proc.returncode is not None:
                break
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port, limit=256 * 1024), 1)
                sentinel = await asyncio.wait_for(reader.readexactly(1), 1)
                if sentinel != b"\x00":
                    raise MobileError("video_protocol_error", "Unexpected scrcpy handshake")
                break
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                if writer is not None:
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                    writer = None
                reader = None
                await asyncio.sleep(0.1)
        if reader is None:
            raise MobileError("video_failed", "The device video stream did not start")
        await websocket.accept()
        event("video_connected", serial=serial)
        await websocket.send_json({"type": "video", "version": SCRCPY_VERSION, "codec": "h264"})

        async def relay():
            while chunk := await reader.read(64 * 1024):
                # TCP backpressure and bounded send time: never build an unbounded
                # browser queue or discard H.264 delta packets. Reconnect starts
                # a fresh encoder/config/keyframe when a viewer falls behind.
                await asyncio.wait_for(websocket.send_bytes(chunk), 2)

        async def disconnected():
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                # This is receive-only video. Even a valid owner must use the
                # HTTP broker routes and lease for commands.
                await websocket.close(code=1008, reason="Video socket accepts no commands")
                return

        jobs = [asyncio.create_task(relay()), asyncio.create_task(disconnected())]
        done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        for job in done:
            job.result()
    except (MobileError, OSError, ValueError, asyncio.TimeoutError, WebSocketDisconnect) as exc:
        event("video_disconnected" if isinstance(exc, WebSocketDisconnect) else "video_failed",
              failed=not isinstance(exc, WebSocketDisconnect), error_type=type(exc).__name__)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason=str(exc)[:110])
    finally:
        with contextlib.suppress(Exception):
            await websocket.close(code=1000)
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if port is not None:
            with contextlib.suppress(Exception):
                await command([adb, "-s", serial, "forward", "--remove", f"tcp:{port}"], timeout=5)
        if runtime.video_viewer == viewer:
            runtime.video_viewer = None
        # Video reconnects independently of manual control. The UI releases
        # its exact epoch through the broker; an old stream must never revoke
        # a newer lease held by the same tab.
        _streams.discard(current)
        event("video_closed")
