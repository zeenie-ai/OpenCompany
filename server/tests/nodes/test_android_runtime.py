"""Focused runtime-readiness tests for Android device and relay plumbing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

from nodes.android._dispatcher import AndroidService
from nodes.android._events import android_connection_status
from nodes.android._handlers import handle_android_relay_connect
from nodes.android._relay.client import RelayWebSocketClient
from nodes.android._router import get_relay_connection_status, list_android_devices


@pytest.mark.asyncio
async def test_list_devices_parses_adb_output_and_is_shared_by_http():
    completed = SimpleNamespace(
        returncode=0,
        stdout="List of devices attached\nemulator-5554 device product:sdk model:Pixel_8 device:emu\n",
        stderr="",
    )
    service = AndroidService()
    with patch("nodes.android._dispatcher.subprocess.run", return_value=completed):
        result = await list_android_devices(android_service=service)

    assert result == {
        "success": True,
        "devices": [{"id": "emulator-5554", "state": "device", "model": "Pixel_8", "android_version": None}],
        "count": 1,
    }


@pytest.mark.asyncio
async def test_list_devices_reports_missing_adb_structurally():
    with patch("nodes.android._dispatcher.subprocess.run", side_effect=FileNotFoundError):
        result = await AndroidService().list_devices()
    assert result["success"] is False
    assert result["error_code"] == "ADB_NOT_FOUND"
    assert result["devices"] == []
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_list_devices_reports_nonzero_adb_exit_structurally():
    completed = SimpleNamespace(returncode=1, stdout="", stderr="adb server unavailable")
    with patch("nodes.android._dispatcher.subprocess.run", return_value=completed):
        result = await AndroidService().list_devices()
    assert result["error_code"] == "ADB_COMMAND_FAILED"
    assert result["error"] == "adb server unavailable"


@pytest.mark.asyncio
async def test_failed_relay_handshake_closes_session():
    ws = SimpleNamespace(
        closed=False,
        receive=AsyncMock(return_value=SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='{"method":"unexpected"}')),
        close=AsyncMock(),
    )
    session = SimpleNamespace(closed=False, ws_connect=AsyncMock(return_value=ws), close=AsyncMock())
    client = RelayWebSocketClient("wss://relay.example/ws", "secret-key")
    with patch("nodes.android._relay.client.aiohttp.ClientSession", return_value=session):
        connected, error = await client.connect()
    assert connected is False
    assert "Unexpected response" in error
    ws.close.assert_awaited_once()
    session.close.assert_awaited_once()
    assert client.session is None


@pytest.mark.asyncio
async def test_public_relay_surfaces_omit_session_token():
    relay = SimpleNamespace(
        is_connected=lambda: True,
        is_paired=lambda: True,
        paired_device_id="device-1",
        paired_device_name="Pixel",
        session_token="private-token",
        qr_data="qr",
    )
    with patch("nodes.android._relay.get_current_relay_client", return_value=relay):
        status = await get_relay_connection_status()
    assert "session_token" not in status

    with patch("nodes.android._relay.get_relay_client", new=AsyncMock(return_value=(relay, ""))):
        response = await handle_android_relay_connect(
            {"url": "wss://relay.example/ws", "api_key": "secret-key"},
            websocket=SimpleNamespace(),
        )
    assert "session_token" not in response

    event = android_connection_status(connected=True)
    assert "session_token" not in event.data


def test_relay_errors_redact_api_key_and_internal_session_token_is_retained():
    client = RelayWebSocketClient("wss://relay.example/ws", "secret-key")
    client.session_token = "private-token"
    message = client._safe_error("url?api_key=secret-key token=private-token")
    assert "secret-key" not in message
    assert "private-token" not in message
    assert client.session_token == "private-token"
