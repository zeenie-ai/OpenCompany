"""Working dir + metadata for the (single) ``machina deploy`` deployment.

The VM instance is always named ``machinaos`` (one deployment per project), so
the working dir is fixed at ``<user-data>/deploy/machinaos/``. That directory
is BOTH the Terraform working dir (rendered module + ``terraform.tfvars.json``
+ local state) and the home of a small ``deploy-meta.json`` (provider + port +
owner email) used by ``status`` / ``destroy``.

No module-level side effects -- ``user_data_dir`` (platformdirs) is only
resolved when a function is called.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli.platform_ import user_data_dir

#: The fixed instance / deployment name.
NAME = "machinaos"

_META_FILENAME = "deploy-meta.json"


def deploy_root() -> Path:
    return user_data_dir() / "deploy"


def workdir() -> Path:
    """Terraform working dir + state location for the deployment."""
    return deploy_root() / NAME


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
