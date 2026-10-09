"""ADB port forwarding rejects command inputs before starting a subprocess."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from nodes.android._router import router, setup_port_forwarding


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client


def _completed(*, returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    "device_id",
    [
        "R58M123ABC",
        "emulator-5554",
        "192.168.1.20:5555",
        "phone.local:5555",
        "adb-serial-token._adb-tls-connect._tcp",
        "A" * 64,
    ],
)
def test_valid_serials_are_passed_as_one_fixed_command_argument(client, device_id):
    with patch("subprocess.run", return_value=_completed()) as run:
        response = client.post("/api/android/port-forward", params={"device_id": device_id})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "device_id": device_id,
        "local_port": 8888,
        "device_port": 8888,
        "message": "Port forwarding active: localhost:8888 -> device:8888",
    }
    assert run.call_count == 1
    assert run.call_args.args == (["adb", "-s", device_id, "forward", "tcp:8888", "tcp:8888"],)
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["timeout"] == 5
    assert run.call_args.kwargs["capture_output"] is True
    assert run.call_args.kwargs["text"] is True
    assert run.call_args.kwargs["encoding"] == "utf-8"
    assert run.call_args.kwargs["errors"] == "replace"


@pytest.mark.parametrize("local_port,device_port", [(1024, 65535), (65535, 1024)])
def test_port_boundaries_preserve_forwarding_contract(client, local_port, device_port):
    with patch("subprocess.run", return_value=_completed()) as run:
        response = client.post(
            "/api/android/port-forward",
            params={"device_id": "emulator-5554", "local_port": local_port, "device_port": device_port},
        )

    assert response.status_code == 200
    assert response.json()["local_port"] == local_port
    assert response.json()["device_port"] == device_port
    assert run.call_args.args[0][-2:] == [f"tcp:{local_port}", f"tcp:{device_port}"]


@pytest.mark.parametrize(
    "device_id",
    [
        "",
        "-Hlocalhost",
        "--help",
        ".device",
        ":5555",
        "serial other",
        "serial;whoami",
        "serial&&whoami",
        "$(whoami)",
        "`whoami`",
        "serial/other",
        "serial\\other",
        "serial\n",
        "serial\r",
        "serial\t",
        "serial\x00",
        "A" * 65,
        "телефон",
    ],
)
def test_invalid_serials_are_rejected_before_starting_adb(client, device_id):
    with patch("subprocess.run") as run:
        response = client.post("/api/android/port-forward", params={"device_id": device_id})

    assert response.status_code == 422
    run.assert_not_called()


@pytest.mark.parametrize("port_name", ["local_port", "device_port"])
@pytest.mark.parametrize("port", [1023, 65536, "12.5", "true", "8888;whoami"])
def test_invalid_query_ports_are_rejected_before_starting_adb(client, port_name, port):
    with patch("subprocess.run") as run:
        response = client.post(
            "/api/android/port-forward", params={"device_id": "emulator-5554", port_name: port}
        )

    assert response.status_code == 422
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("device_id", ["--help", "serial\n", "A" * 65, None, 123])
async def test_direct_calls_cannot_bypass_serial_validation(device_id):
    with patch("subprocess.run") as run:
        with pytest.raises(HTTPException) as error:
            await setup_port_forwarding(device_id=device_id, local_port=8888, device_port=8888)

    assert error.value.status_code == 422
    run.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("port_name", ["local_port", "device_port"])
@pytest.mark.parametrize("port", [1023, 65536, "8888", 8888.0, True, None])
async def test_direct_calls_cannot_bypass_port_validation(port_name, port):
    parameters = {"device_id": "emulator-5554", "local_port": 8888, "device_port": 8888}
    parameters[port_name] = port
    with patch("subprocess.run") as run:
        with pytest.raises(HTTPException) as error:
            await setup_port_forwarding(**parameters)

    assert error.value.status_code == 422
    run.assert_not_called()


@pytest.mark.parametrize(
    "completed,expected_error",
    [
        (_completed(returncode=1, stderr="  device unavailable\n"), "device unavailable"),
        (_completed(returncode=1, stdout="  adb server unavailable\n"), "adb server unavailable"),
    ],
)
def test_adb_failures_keep_existing_error_contract(client, completed, expected_error):
    with patch("subprocess.run", return_value=completed):
        response = client.post("/api/android/port-forward", params={"device_id": "emulator-5554"})

    assert response.status_code == 200
    assert response.json() == {"success": False, "error": expected_error}


@pytest.mark.parametrize(
    "exception,expected_error",
    [
        (FileNotFoundError(), "ADB not found. Please install Android SDK Platform Tools"),
        (subprocess.TimeoutExpired(cmd=["adb"], timeout=5), "Port forwarding failed"),
        (RuntimeError("internal subprocess details"), "Port forwarding failed"),
    ],
)
def test_subprocess_exceptions_return_safe_error_contract(client, exception, expected_error):
    with patch("subprocess.run", side_effect=exception):
        response = client.post("/api/android/port-forward", params={"device_id": "emulator-5554"})

    assert response.status_code == 200
    assert response.json() == {"success": False, "error": expected_error}


def test_adb_environment_excludes_onepassword_bootstrap_credentials(client, monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "private-test-token")
    monkeypatch.setenv("OP_CONNECT_TOKEN", "private-connect-token")
    monkeypatch.setenv("op_test_sensitive", "private-lowercase-token")
    monkeypatch.setenv("ANDROID_TEST_VISIBLE", "ordinary-setting")
    with patch("subprocess.run", return_value=_completed()) as run:
        response = client.post("/api/android/port-forward", params={"device_id": "emulator-5554"})

    assert response.json()["success"] is True
    environment = run.call_args.kwargs["env"]
    assert environment["ANDROID_TEST_VISIBLE"] == "ordinary-setting"
    assert all(not key.upper().startswith("OP_") for key in environment)
