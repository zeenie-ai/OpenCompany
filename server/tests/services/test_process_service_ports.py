from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.process_service import ProcessService


def test_requested_ports_combines_explicit_cli_and_environment() -> None:
    ports = ProcessService._requested_ports(
        ["server", "--port=4100", "--other", "x", "-p", "4200"],
        [4300, 4100],
        {"API_PORT": "4400", "NOT_A_PORT": "text"},
    )

    assert ports == [4100, 4200, 4300, 4400]


def test_requested_ports_does_not_guess_positional_numbers() -> None:
    assert ProcessService._requested_ports(["python", "-m", "http.server", "8123"], [], {}) == []


def test_occupied_ports_reports_listener_without_killing_it() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert port in ProcessService._occupied_ports([port])
        # Detection is non-destructive: the original socket still owns it.
        challenger = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError):
                challenger.bind(("127.0.0.1", port))
        finally:
            challenger.close()
    finally:
        listener.close()


@pytest.mark.asyncio
async def test_start_rejects_occupied_declared_port_before_spawn(monkeypatch) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    service = ProcessService()
    spawned = False

    async def fake_spawn(*args, **kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("spawn must not run for an occupied port")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_spawn)
    workspace = Path.cwd().resolve()
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(workspace_base_resolved=str(workspace)))
    monkeypatch.setattr("core.paths.daemons_dir", lambda: workspace / ".opencompany-test-daemons")
    try:
        # The collision check happens before subprocess creation; use an
        # OpenCompany-controlled default cwd supplied by the configured root.
        result = await service.start("web", "python --port 1", working_directory=str(workspace), ports=[port])
        assert result["success"] is False
        assert result["code"] == "PORT_IN_USE"
        assert result["ports"] == [port]
        assert spawned is False
    finally:
        listener.close()
