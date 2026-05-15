"""Project configuration -- ports + flags from ``.env``.

Stdlib-only env-var loader. ``.env`` is parsed as plain ``KEY=VALUE``
lines (with ``#`` comments and surrounding ``"`` / ``'`` stripped); the
backend has python-dotenv if full shell semantics are needed. Process
env wins over file env, matching the prior pydantic-settings behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cli.platform_ import project_root


def _load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(env.get(key, default))
    except (ValueError, TypeError):
        return default


def _bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    """Typed view over ``.env``; defaults match the JS ``loadEnvConfig``."""

    client_port: int = 3000
    backend_port: int = 3010
    whatsapp_port: int = 9400
    nodejs_port: int = 3020
    temporal_address: str = "localhost:7233"
    temporal_enabled: bool = True

    @property
    def temporal_port(self) -> int:
        try:
            return int(self.temporal_address.rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            return 7233

    @property
    def all_ports(self) -> list[int]:
        return [
            self.client_port,
            self.backend_port,
            self.whatsapp_port,
            self.nodejs_port,
            self.temporal_port,
        ]


@lru_cache(maxsize=1)
def load_config(root: Path | None = None) -> Config:
    root = root or project_root()
    primary = root / ".env"
    fallback = root / ".env.template"
    file_env = _load_env_file(primary if primary.exists() else fallback)
    env = {**file_env, **os.environ}

    return Config(
        client_port=_int(env, "VITE_CLIENT_PORT", 3000),
        backend_port=_int(env, "PYTHON_BACKEND_PORT", 3010),
        whatsapp_port=_int(env, "WHATSAPP_RPC_PORT", 9400),
        nodejs_port=_int(env, "NODEJS_EXECUTOR_PORT", 3020),
        temporal_address=env.get("TEMPORAL_SERVER_ADDRESS", "localhost:7233"),
        temporal_enabled=_bool(env, "TEMPORAL_ENABLED", True),
    )
