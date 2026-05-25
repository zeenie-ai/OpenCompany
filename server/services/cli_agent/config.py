"""Provider configuration loader for the AI CLI framework.

Loads `server/config/ai_cli_providers.json` and exposes
`get_provider_config(name)` returning a typed `CliProviderConfig`. The
JSON file is the single source of truth for binary names, npm packages,
default flags, IDE lockfile paths, and per-provider feature flags.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider config dataclass
# ---------------------------------------------------------------------------


@dataclass
class CliProviderConfig:
    """Per-provider config from `ai_cli_providers.json`."""

    name: str
    package_name: str
    binary_name: str
    login_argv: Tuple[str, ...]
    auth_status_argv: Optional[Tuple[str, ...]]
    ide_lock_env_var: Optional[str]
    ide_lockfile_dir: Optional[Path]
    defaults: Dict[str, Any] = field(default_factory=dict)
    supports: frozenset = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Load config once at import time
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    return Path(__file__).parent.parent.parent / "config" / "ai_cli_providers.json"


def _load_raw() -> Dict[str, Any]:
    path = _config_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"ai_cli_providers.json not found at {path}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load ai_cli_providers.json: {e}")
        return {}


def _expand_lockfile_dir(raw: Optional[str]) -> Optional[Path]:
    """Expand ~ and the literal `<tmpdir>` placeholder in lockfile paths."""
    if not raw:
        return None
    if raw.startswith("<tmpdir>"):
        return Path(tempfile.gettempdir()) / raw[len("<tmpdir>") :].lstrip("/").lstrip("\\")
    return Path(os.path.expanduser(raw))


def _build_configs() -> Dict[str, CliProviderConfig]:
    raw = _load_raw()
    configs: Dict[str, CliProviderConfig] = {}
    for name, prov in raw.items():
        # Pull known fields out; everything else lives in `defaults` so
        # provider impls can read e.g. `default_model`, `default_max_turns`.
        defaults = {k: v for k, v in prov.items() if k.startswith("default_")}
        configs[name] = CliProviderConfig(
            name=name,
            package_name=prov.get("package_name", ""),
            binary_name=prov.get("binary_name", name),
            login_argv=tuple(prov.get("login_argv", [])),
            auth_status_argv=(tuple(prov["auth_status_argv"]) if prov.get("auth_status_argv") else None),
            ide_lock_env_var=prov.get("ide_lock_env_var"),
            ide_lockfile_dir=_expand_lockfile_dir(prov.get("ide_lockfile_dir")),
            defaults=defaults,
            supports=frozenset(prov.get("supports", [])),
        )
    return configs


PROVIDER_CONFIGS: Dict[str, CliProviderConfig] = _build_configs()


def get_provider_config(name: str) -> Optional[CliProviderConfig]:
    return PROVIDER_CONFIGS.get(name)


def list_provider_names() -> List[str]:
    return list(PROVIDER_CONFIGS.keys())


def reload_configs() -> None:
    """Reload `ai_cli_providers.json` (useful for tests)."""
    global PROVIDER_CONFIGS
    PROVIDER_CONFIGS = _build_configs()
