"""Working dir + metadata for each ``company deploy`` deployment.

A deployment is named (``--name``, default ``opencompany``). The name is its
folder under ``<DATA_DIR>/deploy/`` and its cloud resource id, so one machine
can drive several VMs, one per name.

Pre-rebrand deployments used ``machinaos`` and may live under either
``~/.machina`` or a checkout-local ``.machina`` root. For the default name
those paths are discovered before creating new state so a rebrand can never
orphan a live VM, firewall, bucket, or Terraform state.

The selected directory is BOTH the Terraform working dir (rendered module +
``terraform.tfvars.json`` + local state) and the home of a small
``deploy-meta.json`` (provider + port + owner email) used by ``status`` /
``destroy``.

No module-level side effects -- ``user_data_dir`` (platformdirs) is only
resolved when a function is called.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from cli.platform_ import project_root, user_data_dir

#: Default deployment/resource name and the pre-rebrand compatibility name.
NAME = "opencompany"
LEGACY_NAME = "machinaos"

#: A deployment name is also a cloud resource id. GCP service-account ids set
#: the tightest rule: 6-30 lowercase letters, digits and hyphens, starting
#: with a letter and not ending with a hyphen.
NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")

_META_FILENAME = "deploy-meta.json"


def deploy_root() -> Path:
    return user_data_dir() / "deploy"


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


def workdir(name: str = NAME) -> Path:
    """Terraform state location. The default name prefers current state, then legacy state."""
    current = deploy_root() / name
    if name != NAME or _has_state(current):
        return current
    for legacy in _legacy_workdirs():
        if _has_state(legacy):
            return legacy
    return current


def resource_name(name: str = NAME) -> str:
    """Cloud resource id to render without replacing legacy resources."""
    if name != NAME:
        return name
    meta = read_meta() or {}
    configured = meta.get("resource_name")
    if configured in {NAME, LEGACY_NAME}:
        return configured
    return LEGACY_NAME if workdir().name == LEGACY_NAME else NAME


def meta_file(name: str = NAME) -> Path:
    return workdir(name) / _META_FILENAME


def write_meta(meta: dict, name: str = NAME) -> None:
    wd = workdir(name)
    wd.mkdir(parents=True, exist_ok=True)
    meta_file(name).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def read_meta(name: str = NAME) -> dict | None:
    mf = meta_file(name)
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def exists(name: str = NAME) -> bool:
    return meta_file(name).exists()
