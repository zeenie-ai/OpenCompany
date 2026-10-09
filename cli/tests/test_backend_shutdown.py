"""Backend shutdown deadlines must cover every sequential teardown phase."""

import re
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli._common import (
    BACKEND_CLEANUP_GRACE_SECONDS,
    UVICORN_GRACEFUL_SHUTDOWN_SECONDS,
    backend_shutdown_grace_seconds,
    free_all_ports,
)
from cli.commands import dev, serve, start, stop
from cli.config import _load_env_file, load_config
from cli.platform_ import project_root
from cli.ports import KillResult

# The systemd units that run the backend on a VM: the two Terraform startup
# templates and the manual GCP runbook.
VM_UNITS = (
    "cli/terraform/gcp/startup.sh.tftpl",
    "cli/terraform/aws/startup.sh.tftpl",
    "docs-internal/gcp_vm_deploy_runbook.md",
)
# Room after the CLI's allowance for the supervisor's wait after its own
# tree-kill and for the processes to exit.
UNIT_STOP_MARGIN_SECONDS = 10
# The desktop shell sets the backend's Temporal grace (env.ts) and waits
# STOP_TIMEOUT_MS (shutdown.ts) before its own tree-kill; the backend's own
# deadline is SHUTDOWN_DEADLINE_SECONDS (core/desktop.py).
DESKTOP_ENV = "desktop/src/main/env.ts"
DESKTOP_SHUTDOWN = "desktop/src/main/shutdown.ts"
DESKTOP_HOST = "server/core/desktop.py"
# Room after the backend's deadline for its own tree-kill and exit.
DESKTOP_STOP_MARGIN_SECONDS = 10


@pytest.mark.parametrize("temporal_grace", [1, 30, 80])
def test_start_dev_serve_share_configured_backend_shutdown_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, temporal_grace: int
):
    cfg = replace(load_config(), temporal_enabled=True)
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", str(temporal_grace))
    expected = UVICORN_GRACEFUL_SHUTDOWN_SECONDS + 3 * temporal_grace + BACKEND_CLEANUP_GRACE_SECONDS
    manager = MagicMock()
    manager.run = AsyncMock(return_value=0)
    with (
        patch.object(serve, "preflight", return_value=(cfg, tmp_path)),
        patch.object(serve, "validate_build"),
        patch("cli.ports.kill_port") as kill_port,
        patch("cli.supervisor.Manager", return_value=manager),
    ):
        serve.serve_command(port=8090)
    production_spec = manager.add_all.call_args.args[0][0]
    specs = [
        start._build_specs(tmp_path, cfg)[0],
        next(s for s in dev._build_specs(tmp_path, cfg, daemon=False, use_vite=True) if s.name == "server"),
        production_spec,
    ]
    for spec in specs:
        assert spec.terminate_grace_seconds == expected
        drain_index = spec.argv.index("--timeout-graceful-shutdown")
        assert spec.argv[drain_index + 1] == str(UVICORN_GRACEFUL_SHUTDOWN_SECONDS)
    assert production_spec.ready_port == 8090
    assert production_spec.argv[production_spec.argv.index("--port") + 1] == "8090"
    assert production_spec.env == {"SERVE_STATIC_CLIENT": "1", "PORT": "8090"}
    kill_port.assert_called_once_with(8090, backend_graceful_timeout=expected)
    client = next(s for s in dev._build_specs(tmp_path, cfg, daemon=False, use_vite=True) if s.name == "client")
    assert client.terminate_grace_seconds == 5.0


def test_temporal_disabled_does_not_reserve_worker_grace(monkeypatch: pytest.MonkeyPatch):
    cfg = replace(load_config(), temporal_enabled=False)
    monkeypatch.delenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", raising=False)
    assert backend_shutdown_grace_seconds(cfg) == UVICORN_GRACEFUL_SHUTDOWN_SECONDS + BACKEND_CLEANUP_GRACE_SECONDS


@pytest.mark.parametrize("invalid", ["0", "-5", "bad"])
def test_invalid_temporal_grace_fails_before_launch(monkeypatch: pytest.MonkeyPatch, invalid: str):
    cfg = replace(load_config(), temporal_enabled=True)
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", invalid)
    with pytest.raises(ValueError):
        backend_shutdown_grace_seconds(cfg)


def test_reserved_port_cleanup_offers_backend_budget(monkeypatch: pytest.MonkeyPatch):
    cfg = replace(load_config(), temporal_enabled=True)
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", "40")
    grace = backend_shutdown_grace_seconds(cfg)
    with patch("cli.ports.kill_port", side_effect=lambda port, **kw: KillResult(port, [], True)) as kill_port:
        results = free_all_ports(cfg)
    assert [r.port for r in results] == cfg.all_ports
    assert [c.args[0] for c in kill_port.call_args_list] == cfg.all_ports
    assert all(c.kwargs == {"backend_graceful_timeout": grace} for c in kill_port.call_args_list)


def test_stop_orphan_cleanup_preserves_backend_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = replace(load_config(), temporal_enabled=True)
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", "40")
    with (
        patch.object(stop, "preflight", return_value=(cfg, tmp_path)),
        patch.object(stop, "load_dev_overrides"),
        patch.object(stop, "free_all_ports", return_value=[KillResult(p, [], True) for p in cfg.all_ports]),
        patch.object(stop, "kill_by_pattern", return_value=[]),
        patch.object(stop, "kill_orphaned_opencompany_processes", return_value=[]) as kill_orphans,
    ):
        stop.stop_command()
    kill_orphans.assert_called_once_with(str(tmp_path), backend_graceful_timeout=backend_shutdown_grace_seconds(cfg))


def test_stop_kills_only_this_installations_temporal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = replace(load_config(), temporal_enabled=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "state"))
    with (
        patch.object(stop, "preflight", return_value=(cfg, tmp_path)),
        patch.object(stop, "load_dev_overrides"),
        patch.object(stop, "free_all_ports", return_value=[KillResult(p, [], True) for p in cfg.all_ports]),
        patch.object(stop, "kill_by_pattern", return_value=[]) as kill_by_pattern,
        patch.object(stop, "kill_orphaned_opencompany_processes", return_value=[]),
    ):
        stop.stop_command()
    kill_by_pattern.assert_called_once_with("temporal", within=tmp_path / "state")


@pytest.mark.parametrize("rel", VM_UNITS)
def test_vm_unit_outwaits_the_backend_shutdown_allowance(rel: str, monkeypatch: pytest.MonkeyPatch):
    """systemd must not SIGKILL the backend before the CLI's own deadline.

    ``company deploy`` VMs run with Temporal on (the ``.env.template`` default)
    and the manual runbook documents turning it on, so each unit covers the
    allowance with Temporal on and the template's
    ``TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS``. A shorter ``TimeoutStopSec`` kills
    the backend before its shutdown hooks run, and with Temporal on the next
    boot then treats the stop as a crash and pauses running deployments.
    """
    text = (project_root() / rel).read_text(encoding="utf-8")
    timeouts = [int(value) for value in re.findall(r"^TimeoutStopSec=(\d+)\s*$", text, re.MULTILINE)]
    assert len(timeouts) == 1, f"{rel}: expected one TimeoutStopSec line, found {timeouts}"
    template = _load_env_file(project_root() / ".env.template")
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", template["TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS"])
    allowance = backend_shutdown_grace_seconds(replace(load_config(), temporal_enabled=True))
    assert timeouts[0] >= allowance + UNIT_STOP_MARGIN_SECONDS, (
        f"{rel}: TimeoutStopSec={timeouts[0]} does not outlast the CLI's backend shutdown "
        f"allowance of {allowance:.0f}s plus {UNIT_STOP_MARGIN_SECONDS}s"
    )


def _one_number(rel: str, pattern: str) -> float:
    found = re.findall(pattern, (project_root() / rel).read_text(encoding="utf-8"), re.MULTILINE)
    assert len(found) == 1, f"{rel}: expected one match for {pattern!r}, found {found}"
    return float(found[0].replace("_", ""))


def test_desktop_shell_outwaits_the_backend_deadline(monkeypatch: pytest.MonkeyPatch):
    """The desktop backend must finish, or kill its own tree, before the shell kills it.

    Its deadline covers the CLI's allowance at the Temporal grace the shell
    sets, and the shell waits longer than that deadline. A tree-kill before
    the shutdown hooks run makes the next launch treat the quit as a crash
    and pause running deployments.
    """
    grace = _one_number(DESKTOP_ENV, r'^\s*TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS: "(\d+)",\s*$')
    monkeypatch.setenv("TEMPORAL_GRACEFUL_SHUTDOWN_SECONDS", str(int(grace)))
    allowance = backend_shutdown_grace_seconds(replace(load_config(), temporal_enabled=True))
    deadline = _one_number(DESKTOP_HOST, r"^SHUTDOWN_DEADLINE_SECONDS = ([\d.]+)\s*$")
    shell_wait = _one_number(DESKTOP_SHUTDOWN, r"^export const STOP_TIMEOUT_MS = ([\d_]+);\s*$") / 1000
    assert deadline >= allowance, f"the backend's {deadline:.0f}s deadline is shorter than the {allowance:.0f}s allowance"
    assert shell_wait >= deadline + DESKTOP_STOP_MARGIN_SECONDS, (
        f"the shell waits {shell_wait:.0f}s, less than {DESKTOP_STOP_MARGIN_SECONDS}s past the backend's {deadline:.0f}s deadline"
    )
