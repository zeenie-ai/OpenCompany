"""WhatsApp runtime — supervises the edgymeow Go binary.

Spawns the binary with cwd inside the project (``<data_dir>/whatsapp``)
so the SQLite session DB lives next to ``credentials.db`` and survives
``company clean``, version bumps, and worktree switches. Lazy-init
from ``nodes.whatsapp._service.get_client()``; teardown from FastAPI
lifespan.

The binary itself is OpenCompany-managed under the shared OpenCompany
npm tree at ``<DATA_DIR>/packages/`` (resolved by
:func:`._install.edgymeow_binary_path` on first use). The install
runs through ``asyncio.to_thread`` in ``_pre_spawn`` so the long
``npm install`` (~30 s) doesn't block the asyncio event loop —
otherwise the ``StatusBroadcaster._refresh_all_services`` startup
fan-out monopolises the loop during boot and uvicorn can't bind
port 3010.

Subclasses :class:`BaseProcessSupervisor` for spawning, tree-kill, status
snapshots, restart policy, and the singleton ``get_instance()`` accessor.
Plugin-specific surface here is just binary discovery + config patching.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import yaml

from core.paths import packages_dir
from services._supervisor import BaseProcessSupervisor

from ._install import edgymeow_binary_path


class WhatsAppRuntime(BaseProcessSupervisor):
    name = "WhatsApp"

    # Inherit stdio so logrus output (with colors) lands directly in the
    # backend console — no custom forwarder needed.
    pipe_streams = False
    terminate_grace_seconds = 5.0
    graceful_shutdown = False

    def __init__(self, settings=None) -> None:
        super().__init__()
        if settings is None:
            from core.config import Settings

            settings = Settings()
        self.settings = settings

    # ---- public read-only properties -------------------------------------

    @property
    def data_root(self) -> Path:
        # Routes through ``Settings._resolve_under_data`` so dev mode's
        # ``DATA_DIR=.opencompany`` lands the WhatsApp tree at
        # ``<repo>/.opencompany/whatsapp/`` (not
        # ``<repo>/server/.opencompany/...``,
        # which the old ad-hoc ``_PROJECT_ROOT / "server" / data_dir``
        # logic produced when ``DATA_DIR`` was relative).
        return Path(self.settings._resolve_under_data(self.settings.whatsapp_data_subdir))

    @property
    def port(self) -> int:
        return int(getattr(self.settings, "whatsapp_port", 9400))

    @property
    def bind_host(self) -> str:
        # `localhost` is the only bind that Windows Firewall's loopback
        # exception silently allows; 127.0.0.1 / 0.0.0.0 trigger Defender
        # prompts and TLS interception that surfaces as a misleading
        # `Client outdated (405)`. See aarol.dev/posts/go-windows-firewall.
        return getattr(self.settings, "whatsapp_bind_host", "localhost")

    @property
    def _package_dir(self) -> Path:
        # ``edgymeow`` is npm-installed into the shared OpenCompany npm
        # tree at ``<DATA_DIR>/packages/node_modules/edgymeow/`` by
        # :func:`._install.edgymeow_binary_path` (called from
        # ``_pre_spawn``). Same tree holds ``claude-code``,
        # ``agent-browser``, etc. — one package.json + lockfile.
        return packages_dir() / "node_modules" / "edgymeow"

    # ---- BaseProcessSupervisor overrides ---------------------------------

    def binary_path(self) -> Path:
        override = getattr(self.settings, "whatsapp_binary_path", None)
        if override:
            return Path(override).resolve()
        name = "edgymeow-server.exe" if sys.platform == "win32" else "edgymeow-server"
        return self._package_dir / "bin" / name

    def argv(self) -> list[str]:
        return [str(self.binary_path())]

    def cwd(self) -> Path:
        return self.data_root

    def env(self) -> dict[str, str]:
        return {**os.environ, "WA_SERVER_PORT": str(self.port)}

    async def _pre_spawn(self) -> None:
        if not getattr(self.settings, "whatsapp_runtime_enabled", True):
            raise RuntimeError("WhatsApp runtime disabled via WHATSAPP_RUNTIME_ENABLED")
        # Run the (potentially long) ``npm install edgymeow`` off the
        # asyncio event loop. ``edgymeow_binary_path`` is sync and
        # blocks on ``subprocess.run`` for the duration of the install;
        # without this offload the startup ``_refresh_all_services``
        # fan-out monopolises the loop for ~30 s and uvicorn can't
        # bind port 3010 (port-probe times out in the supervisor).
        # Idempotent — instant return when the binary already exists.
        resolved = await asyncio.to_thread(edgymeow_binary_path)
        if resolved is None:
            raise RuntimeError(
                "WhatsApp runtime install failed: npm not on PATH or `npm install edgymeow` "
                "did not produce the expected binary. See log for details."
            )
        self._write_config()

    def _extra_status(self) -> dict:
        base = super()._extra_status()
        return {**base, "port": self.port, "data_root": str(self.data_root)}

    # ---- private helpers -------------------------------------------------

    def _write_config(self) -> None:
        for sub in ("configs", "data"):
            (self.data_root / sub).mkdir(parents=True, exist_ok=True)
        # Read the package's bundled config and patch only the fields we
        # need to override. Keeps Python free of any YAML structure that
        # could drift from upstream.
        bundled = self._package_dir / "configs" / "config.yaml"
        config = yaml.safe_load(bundled.read_text(encoding="utf-8")) or {}
        config.setdefault("server", {})
        config["server"]["port"] = self.port
        config["server"]["host"] = self.bind_host
        (self.data_root / "configs" / "config.yaml").write_text(
            yaml.safe_dump(config, sort_keys=False),
            encoding="utf-8",
        )


def get_whatsapp_runtime() -> WhatsAppRuntime:
    """Return the WhatsApp runtime singleton."""
    return WhatsAppRuntime.get_instance()
