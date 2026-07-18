"""Contract tests for the UserSettings persistence surface.

``save_user_settings`` is generic over ``UserSettings.model_fields``, but
``get_user_settings`` hand-builds its return dict — a new model field that
is not added to the getter silently never reaches the frontend. These
tests lock that symmetry plus the migration branches, using the same
``inspect.getsource`` introspection idiom as test_credential_broadcasts.py
(the conftest stubs ``core.database``, so the real module is loaded from
file like tests/services/test_subagent_concurrency.py does).
"""

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

from models.database import UserSettings

GETTING_STARTED_FIELDS = [
    "getting_started_dismissed",
    "getting_started_added_key",
    "getting_started_ran_example",
    "getting_started_built_workflow",
    "getting_started_tried_theme",
]

# Bookkeeping columns the save path never writes and the getter formats
# specially (timestamps) or returns as-is (user_id).
_MANAGED = {"id", "user_id", "created_at", "updated_at"}


@pytest.fixture(scope="module")
def real_database_module():
    """Load the real core/database.py (conftest stubs core.database)."""
    module_name = "tests._real_database_for_contract"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().parents[1] / "core" / "database.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(module_name, None)


def test_model_declares_getting_started_fields():
    for field in GETTING_STARTED_FIELDS:
        assert field in UserSettings.model_fields, f"UserSettings missing {field}"
        assert UserSettings.model_fields[field].default is False


def test_get_user_settings_returns_every_model_field(real_database_module):
    """Every non-managed model field must appear in the hand-built getter dict."""
    source = inspect.getsource(real_database_module.Database.get_user_settings)
    for field in set(UserSettings.model_fields) - _MANAGED:
        assert f'"{field}"' in source, (
            f"UserSettings.{field} is persisted by save_user_settings but "
            f"missing from the get_user_settings return dict — the frontend "
            f"will never see it."
        )


def test_migration_adds_getting_started_columns(real_database_module):
    source = inspect.getsource(real_database_module.Database._migrate_user_settings)
    for field in GETTING_STARTED_FIELDS:
        assert field in source, f"_migrate_user_settings has no branch for {field}"


def test_migration_backfills_dismissed_for_existing_users(real_database_module):
    """Users who already finished onboarding must not see the checklist."""
    source = inspect.getsource(real_database_module.Database._migrate_user_settings)
    assert (
        "SET getting_started_dismissed = 1 WHERE onboarding_completed = 1" in source
    )
