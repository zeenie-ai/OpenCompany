"""Working dir + metadata for the (single) ``company deploy`` deployment.

New deployments use ``opencompany``. Pre-rebrand deployments used
``machinaos`` and may live under either ``~/.machina`` or a checkout-local
``.machina`` root. Those paths are discovered before creating new state so a
rebrand can never orphan a live VM, firewall, bucket, or Terraform state.

The selected directory is BOTH the Terraform working dir (rendered module +
``terraform.tfvars.json`` + local state) and the home of a small
``deploy-meta.json`` (provider + port + owner email) used by ``status`` /
``destroy``.

No module-level side effects -- ``user_data_dir`` (platformdirs) is only
resolved when a function is called.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli.platform_ import project_root, user_data_dir

#: Current deployment/resource name and the pre-rebrand compatibility name.
NAME = "opencompany"
LEGACY_NAME = "machinaos"

_META_FILENAME = "deploy-meta.json"


def deploy_root() -> Path:
    return user_data_dir() / "deploy"


def _new_workdir() -> Path:
    return deploy_root() / NAME


def _legacy_workdirs() -> tuple[Path, ...]:
    """All locations used by released versions before the rebrand."""
    candidates = (
        deploy_root() / LEGACY_NAME,
        Path.home() / ".machina" / "deploy" / LEGACY_NAME,
        project_root() / ".machina" / "deploy" / LEGACY_NAME,
    )
    # Preserve ordering while removing duplicates (DATA_DIR can point at one
    # of the explicit legacy roots above).
    return tuple(dict.fromkeys(candidates))


def _has_state(path: Path) -> bool:
    return (path / _META_FILENAME).exists() or (path / "terraform.tfstate").exists()


def workdir() -> Path:
    """Terraform state location, preferring current state then legacy state."""
    current = _new_workdir()
    if _has_state(current):
        return current
    for legacy in _legacy_workdirs():
        if _has_state(legacy):
            return legacy
    return current


def resource_name() -> str:
    """Cloud resource id to render without replacing legacy resources."""
    meta = read_meta() or {}
    configured = meta.get("resource_name")
    if configured in {NAME, LEGACY_NAME}:
        return configured
    return LEGACY_NAME if workdir().name == LEGACY_NAME else NAME


def meta_file() -> Path:
    return workdir() / _META_FILENAME


def write_meta(meta: dict) -> None:
    wd = workdir()
    wd.mkdir(parents=True, exist_ok=True)
    meta_file().write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_meta() -> dict | None:
    mf = meta_file()
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def exists() -> bool:
    return meta_file().exists()
