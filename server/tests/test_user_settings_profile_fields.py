"""Owner-profile settings (Normal mode, Settings > Profile).

The getter/model symmetry for these fields is locked generically by
test_user_settings_contract.py; this file locks the migration branches and
the save-path normalization, whose output lands in employees' instructions.
"""

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

from models.database import UserSettings
from services.settings.profile import normalize_profile_patch

PROFILE_TEXT = {
    "profile_full_name": 100,
    "profile_call_name": 60,
    "profile_role": 100,
    "profile_preferences": 2000,
    "profile_timezone": 64,
}
PROFILE_SWITCHES = ("memory_across_chats", "prefer_local_ai")


@pytest.fixture(scope="module")
def real_database_module():
    """Load the real core/database.py (conftest stubs core.database)."""
    module_name = "tests._real_database_for_profile"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[1] / "core" / "database.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(module_name, None)


class TestModel:
    def test_text_fields_are_bounded_and_empty_by_default(self):
        for field, length in PROFILE_TEXT.items():
            info = UserSettings.model_fields[field]
            assert info.default is None
            assert any(getattr(m, "max_length", None) == length for m in info.metadata), field

    def test_switches_default_on(self):
        for field in PROFILE_SWITCHES:
            assert UserSettings.model_fields[field].default is True


class TestMigration:
    def test_every_profile_column_has_a_branch(self, real_database_module):
        source = inspect.getsource(real_database_module.Database._migrate_user_settings)
        for field in (*PROFILE_TEXT, *PROFILE_SWITCHES):
            assert f'"{field}"' in source, f"_migrate_user_settings has no branch for {field}"

    def test_text_column_widths_match_the_model(self, real_database_module):
        source = inspect.getsource(real_database_module.Database._migrate_user_settings)
        for field, length in PROFILE_TEXT.items():
            assert f'("{field}", {length})' in source


class TestNormalizeProfilePatch:
    def test_trims_collapses_and_nulls_empty_text(self):
        patch = normalize_profile_patch(
            {"profile_full_name": "  Ada \t  Lovelace \n", "profile_call_name": "   ", "profile_role": "Owner"}
        )
        assert patch["profile_full_name"] == "Ada Lovelace"
        assert patch["profile_call_name"] is None
        assert patch["profile_role"] == "Owner"

    def test_preferences_keep_line_breaks_but_not_runs_of_them(self):
        patch = normalize_profile_patch({"profile_preferences": "Be brief.\r\n\r\n\r\n\r\nSign off as  Ada.\t"})
        assert patch["profile_preferences"] == "Be brief.\n\nSign off as Ada."

    def test_cuts_to_the_column_width(self):
        patch = normalize_profile_patch({"profile_call_name": "x" * 500, "profile_preferences": "y" * 5000})
        assert len(patch["profile_call_name"]) == 60
        assert len(patch["profile_preferences"]) == 2000

    def test_strips_controls_and_bidi_overrides_but_keeps_joiners(self):
        name = "Da\u0000na‮ س‌لام \U0001f469‍\U0001f4bb"
        patch = normalize_profile_patch({"profile_full_name": name})
        assert patch["profile_full_name"] == "Dana س‌لام \U0001f469‍\U0001f4bb"

    @pytest.mark.parametrize("zone", ["Europe/London", "America/Argentina/Buenos_Aires", "UTC", "Asia/Kolkata"])
    def test_accepts_real_timezones(self, zone):
        assert normalize_profile_patch({"profile_timezone": f" {zone} "})["profile_timezone"] == zone

    @pytest.mark.parametrize("zone", ["Mars/Olympus", "../../etc/passwd", "", 42, "A" * 80])
    def test_drops_unknown_timezones_so_the_stored_one_stands(self, zone):
        assert "profile_timezone" not in normalize_profile_patch({"profile_timezone": zone})

    @pytest.mark.parametrize("value", ["false", 0, 1, None, "yes"])
    def test_drops_non_boolean_switches(self, value):
        assert "memory_across_chats" not in normalize_profile_patch({"memory_across_chats": value})

    def test_keeps_real_booleans_and_explicit_nulls(self):
        patch = normalize_profile_patch({"prefer_local_ai": False, "profile_role": None, "profile_timezone": None})
        assert patch == {"prefer_local_ai": False, "profile_role": None, "profile_timezone": None}

    def test_drops_non_text_values(self):
        assert normalize_profile_patch({"profile_full_name": ["Ada"]}) == {}

    def test_passes_other_settings_through_untouched(self):
        other = {"auto_save": False, "compaction_ratio": 0.5, "default_llm_model": "  gpt-x  "}
        assert normalize_profile_patch(other) == other

    def test_does_not_mutate_its_input(self):
        original = {"profile_full_name": "  Ada  "}
        normalize_profile_patch(original)
        assert original == {"profile_full_name": "  Ada  "}
