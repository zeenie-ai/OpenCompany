"""Tests for the dev-secret startup guard (core.config).

Locks three contracts:
  1. Drift lock: the dev placeholder values shipped in ``.env.template``
     are exactly the ones ``DEV_SECRET_LITERALS`` knows about.
  2. ``dev_secret_offenders`` posture logic: silent in dev posture
     (auth disabled AND local), loud otherwise.
  3. Dev literals never hard-fail ``Settings`` construction — the guard
     is a warning, not a gate.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.config import DEV_SECRET_LITERALS, Settings, dev_secret_offenders

REPO_ROOT = Path(__file__).resolve().parents[3]

SECRET_ENV_VARS = ("SECRET_KEY", "JWT_SECRET_KEY", "API_KEY_ENCRYPTION_KEY")


def _read_env_template() -> dict:
    """Simple KEY=VALUE reader for .env.template (skips comments/blanks)."""
    values: dict = {}
    for raw in (REPO_ROOT / ".env.template").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


_TEMPLATE = _read_env_template()
# Dev literals sourced from the template (SSOT), never re-typed here.
DEV_SECRET = _TEMPLATE["SECRET_KEY"]
DEV_JWT = _TEMPLATE["JWT_SECRET_KEY"]
DEV_ENC = _TEMPLATE["API_KEY_ENCRYPTION_KEY"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ambient env must not leak into posture / secret detection."""
    for var in (*SECRET_ENV_VARS, "VITE_AUTH_ENABLED", "DEPLOYMENT_MODE"):
        monkeypatch.delenv(var, raising=False)


def _ns(**overrides) -> SimpleNamespace:
    """Duck-typed settings object with a dev-posture, dev-literal baseline."""
    base = dict(
        secret_key=DEV_SECRET,
        jwt_secret_key=DEV_JWT,
        api_key_encryption_key=DEV_ENC,
        vite_auth_enabled="false",
        deployment_mode="local",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestTemplateDriftLock:
    def test_template_secret_values_are_all_known_dev_literals(self):
        for var in SECRET_ENV_VARS:
            assert _TEMPLATE[var] in DEV_SECRET_LITERALS, (
                f".env.template {var} value is not in DEV_SECRET_LITERALS -- "
                "update core/config.py (and cli/commands/build.py scaffold) together"
            )

    def test_template_secret_values_carry_dev_prefix(self):
        # cli/commands/build.py scaffolds fresh secrets by recognising the
        # "dev-" placeholder prefix; the template must keep shipping it.
        for var in SECRET_ENV_VARS:
            assert _TEMPLATE[var].startswith("dev-")


class TestDevSecretOffenders:
    def test_dev_posture_returns_empty(self):
        assert dev_secret_offenders(_ns()) == []

    def test_auth_enabled_flags_offenders(self):
        offenders = dev_secret_offenders(_ns(vite_auth_enabled="true"))
        assert offenders == ["SECRET_KEY", "JWT_SECRET_KEY", "API_KEY_ENCRYPTION_KEY"]

    def test_auth_unset_flags_offenders(self):
        # None means the middleware default (auth NOT explicitly disabled).
        offenders = dev_secret_offenders(_ns(vite_auth_enabled=None))
        assert offenders == ["SECRET_KEY", "JWT_SECRET_KEY", "API_KEY_ENCRYPTION_KEY"]

    def test_single_dev_literal_flags_only_that_var(self):
        import secrets

        offenders = dev_secret_offenders(
            _ns(
                vite_auth_enabled="true",
                secret_key=secrets.token_hex(24),
                api_key_encryption_key=secrets.token_hex(24),
            )
        )
        assert offenders == ["JWT_SECRET_KEY"]

    def test_cloud_mode_flags_offenders_even_with_auth_disabled(self):
        offenders = dev_secret_offenders(_ns(deployment_mode="cloud"))
        assert offenders == ["SECRET_KEY", "JWT_SECRET_KEY", "API_KEY_ENCRYPTION_KEY"]

    def test_real_secrets_never_flagged(self):
        import secrets

        offenders = dev_secret_offenders(
            _ns(
                vite_auth_enabled="true",
                deployment_mode="cloud",
                secret_key=secrets.token_hex(24),
                jwt_secret_key=secrets.token_hex(24),
                api_key_encryption_key=secrets.token_hex(24),
            )
        )
        assert offenders == []


class TestSettingsConstruction:
    def _required_kwargs(self) -> dict:
        """Minimal kwargs for every Field without a default (env-required)."""
        return {
            "host": "127.0.0.1",
            "port": 3010,
            "jwt_secret_key": DEV_JWT,
            "secret_key": DEV_SECRET,
            "cors_origins": ["http://localhost:3001"],
            "workflow_db_filename": "workflow.db",
            "temporal_enabled": False,
            "temporal_server_address": "localhost:7233",
            "temporal_namespace": "default",
            "temporal_task_queue": "machina-tasks",
            "temporal_per_type_dispatch": True,
            "temporal_agent_workflow_enabled": True,
            "temporal_graceful_shutdown_seconds": 30,
            "temporal_frontend_grpc_port": 7233,
            "temporal_ui_port": 8233,
            "temporal_sqlite_path": "temporal.db",
            "temporal_terminate_running_on_startup": True,
            "api_key_encryption_key": DEV_ENC,
        }

    def test_settings_constructs_with_dev_literals(self):
        """Dev literals must never hard-fail startup (guard is non-fatal)."""
        settings = Settings(_env_file=None, **self._required_kwargs())
        assert settings.secret_key == DEV_SECRET
        assert settings.jwt_secret_key == DEV_JWT
        assert settings.api_key_encryption_key == DEV_ENC

    def test_real_settings_default_posture_flags_offenders(self):
        # vite_auth_enabled defaults to None (auth not explicitly disabled)
        # and deployment_mode defaults to "local" -> non-dev posture.
        settings = Settings(_env_file=None, **self._required_kwargs())
        assert dev_secret_offenders(settings) == [
            "SECRET_KEY",
            "JWT_SECRET_KEY",
            "API_KEY_ENCRYPTION_KEY",
        ]
