"""Secret + environment generation for ``machina deploy``.

Generates the cryptographic keys + owner password the login gate needs, and
assembles the full env-var map (``app_env``) that Terraform renders into the
VM's ``/etc/machinaos/machina.env`` (a systemd ``EnvironmentFile``). The map
carries only the deployment overrides + secrets; the VM's ``.env`` (copied
from ``.env.template`` by the package build) supplies the rest of the required
settings, and OS env (the EnvironmentFile) wins over ``.env`` in
pydantic-settings.
"""

from __future__ import annotations

import secrets
import string

_PW_ALPHABET = string.ascii_letters + string.digits


def new_key() -> str:
    """48-char hex secret (>= the 32-char minimum the app enforces)."""
    return secrets.token_hex(24)


def new_password(length: int = 20) -> str:
    """Strong alphanumeric password (avoids shell/systemd-quoting pitfalls)."""
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))


def build_app_env(
    *,
    owner_email: str,
    owner_password: str,
    port: int,
    data_dir: str = "/var/lib/machinaos",
) -> dict[str, str]:
    """The systemd EnvironmentFile map: gate overrides + freshly minted secrets.

    JWT/SECRET/ENCRYPTION keys are generated per deploy. ``JWT_COOKIE_SECURE``
    is ``false`` because the VM is reached over plain HTTP on its IP; flip to
    true once a domain + TLS terminator is in front. Temporal/Redis/event
    framework are off (local execution).
    """
    return {
        "HOST": "0.0.0.0",
        "PORT": str(port),
        "DATA_DIR": data_dir,
        "WORKSPACE_BASE_DIR": "workspaces",
        "SERVE_STATIC_CLIENT": "true",
        "VITE_AUTH_ENABLED": "true",
        "AUTH_MODE": "single",
        "JWT_COOKIE_SECURE": "false",
        "JWT_COOKIE_SAMESITE": "lax",
        "TEMPORAL_ENABLED": "false",
        "EVENT_FRAMEWORK_ENABLED": "false",
        "REDIS_ENABLED": "false",
        "NODEJS_EXECUTOR_PORT": "3020",
        "LOG_FORMAT": "text",
        "JWT_SECRET_KEY": new_key(),
        "SECRET_KEY": new_key(),
        "API_KEY_ENCRYPTION_KEY": new_key(),
        "MACHINA_OWNER_EMAIL": owner_email,
        "MACHINA_OWNER_NAME": "Owner",
        "MACHINA_OWNER_PASSWORD": owner_password,
    }
