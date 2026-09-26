"""Facts about the machine the backend runs on, for launching Chrome.

Each answer decides one launch flag or one user-facing option:

- running as root on Linux: Chrome's sandbox cannot start (``--no-sandbox``);
- a small ``/dev/shm`` (Docker defaults to 64 MB): ``--disable-dev-shm-usage``;
- unprivileged user namespaces restricted (Ubuntu 23.10+ AppArmor): the
  sandbox fails as a normal user unless an AppArmor profile allows Chrome;
- in a container: there is no user browser to import logins from;
- whether "import from your installed Chrome" can be offered at all, which
  needs the backend on the user's own machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

_SMALL_SHM_BYTES = 512 * 1024 * 1024


def is_linux_root() -> bool:
    return sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0


def in_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def userns_restricted() -> bool:
    """Whether Linux blocks the unprivileged user namespaces Chrome's sandbox uses."""
    if not sys.platform.startswith("linux"):
        return False
    for path, blocked in (
        ("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "1"),
        ("/proc/sys/kernel/unprivileged_userns_clone", "0"),
        ("/proc/sys/user/max_user_namespaces", "0"),
    ):
        try:
            if Path(path).read_text().strip() == blocked:
                return True
        except OSError:
            continue
    return False


def shm_small() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        stats = os.statvfs("/dev/shm")
    except OSError:
        return False
    return stats.f_frsize * stats.f_blocks < _SMALL_SHM_BYTES


def sandbox_disabled(policy: str = "auto") -> tuple[bool, str]:
    """Whether to pass ``--no-sandbox`` and why.

    ``auto`` disables the sandbox only where Chrome cannot run with it: as
    root on Linux (Docker, the GCP VM). ``off`` disables it everywhere,
    ``on`` never (Chrome then refuses to start as root).
    """
    policy = (policy or "auto").strip().lower()
    if policy == "off":
        return True, "BROWSER_SANDBOX=off"
    if policy == "on":
        return False, "BROWSER_SANDBOX=on"
    if is_linux_root():
        return True, "running as root on Linux"
    return False, "sandbox on"


def is_loopback_client(host: Optional[str]) -> bool:
    return (host or "") in {"127.0.0.1", "::1", "localhost", "testclient"}


def local_import_available(websocket: Any = None, settings: Any = None) -> tuple[bool, str]:
    """Whether "import logins from your installed browser" can work here.

    It needs the backend on the same machine as the user's browser: the
    desktop app or a local install, not a container or a server, and the
    request coming from this machine.
    """
    from core.desktop import is_desktop_mode

    if in_container():
        return False, "container"
    if not is_desktop_mode():
        mode = str(getattr(settings, "deployment_mode", "") or os.environ.get("DEPLOYMENT_MODE", "local")).lower()
        if mode != "local":
            return False, "remote_backend"
    if websocket is not None:
        client = getattr(websocket, "client", None)
        if not is_loopback_client(getattr(client, "host", None)):
            return False, "remote_client"
    return True, "available"


__all__ = [
    "in_container",
    "is_linux_root",
    "is_loopback_client",
    "local_import_available",
    "sandbox_disabled",
    "shm_small",
    "userns_restricted",
]
