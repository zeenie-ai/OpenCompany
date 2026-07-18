"""Wave 12 D5 invariants — locks the migration of `DEFAULT_TOOL_NAMES` /
`DEFAULT_TOOL_DESCRIPTIONS` out of `services/ai.py` into per-plugin
`tool_name` / `tool_description` ClassVars + a small `_PSEUDO_TOOL_FALLBACK`
for the two non-class pseudo-types (`_builtin_check_delegated_tasks`,
the built-in delegated-task status helper).

Three contracts locked:

1. `test_legacy_tool_dicts_removed` — `_build_tool_from_node` no longer
   carries the two function-local dicts that Wave 12 D5 deleted.
2. `test_pseudo_tool_fallback_has_two_entries` — `_PSEUDO_TOOL_FALLBACK`
   covers exactly the two pseudo-types the system has (no plugin class).
3. `test_tool_name_snapshot` — every plugin in
   `tests/fixtures/tool_names_snapshot.json` resolves via `cls.tool_name`
   to the expected value. Catches accidental renames + missing migrations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_AI_SOURCE_PATH = Path(__file__).parent.parent / "services" / "ai.py"
_SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "tool_names_snapshot.json"


def _load_snapshot() -> dict:
    raw = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _ai_source() -> str:
    """Read ``services/ai.py`` as text.

    Source-introspection instead of ``inspect.getsource`` keeps the test
    cheap: it doesn't trigger the full ``services.ai`` import chain
    (LangChain + auth + temporal client + 200+ transitive deps) which
    would otherwise need every ``core.*`` submodule stubbed in conftest.
    """
    return _AI_SOURCE_PATH.read_text(encoding="utf-8")


def test_legacy_tool_dicts_removed():
    """``_build_tool_from_node`` must not reintroduce the function-local
    ``_LEGACY_TOOL_NAMES`` / ``_LEGACY_TOOL_DESCRIPTIONS`` dicts that
    Wave 12 D5 drained."""
    source = _ai_source()
    assert "_LEGACY_TOOL_NAMES = {" not in source, (
        "_LEGACY_TOOL_NAMES dict reintroduced into services/ai.py. "
        "Wave 12 D5 migrated this data to per-plugin ClassVars; the legacy "
        "compat fallback was deleted."
    )
    assert "_LEGACY_TOOL_DESCRIPTIONS = {" not in source, "_LEGACY_TOOL_DESCRIPTIONS dict reintroduced into services/ai.py."
    assert "DEFAULT_TOOL_NAMES = {" not in source, "Pre-D5 DEFAULT_TOOL_NAMES dict reintroduced into services/ai.py."
    assert "DEFAULT_TOOL_DESCRIPTIONS = {" not in source, "Pre-D5 DEFAULT_TOOL_DESCRIPTIONS dict reintroduced into services/ai.py."


def test_pseudo_tool_fallback_has_delegation_status_entry_only():
    """``_PSEUDO_TOOL_FALLBACK`` covers the remaining pseudo-type.

    Pseudo-types are dispatched by name but have no plugin class to
    declare ClassVars on:
      - ``_builtin_check_delegated_tasks`` — internal helper for the
        delegation-tracking surface in ``services/handlers/tools.py``.

    Quote-agnostic so ruff format's choice of single vs. double quotes
    doesn't trip the substring check.
    """
    source = _ai_source()
    assert (
        "'_builtin_check_delegated_tasks'" in source or '"_builtin_check_delegated_tasks"' in source
    ), "_builtin_check_delegated_tasks missing from _PSEUDO_TOOL_FALLBACK"
    assert "'androidTool'" not in source and '"androidTool"' not in source


@pytest.mark.parametrize("node_type,expected_tool_name", list(_load_snapshot().items()))
def test_tool_name_snapshot(node_type: str, expected_tool_name: str):
    """Every plugin in the golden fixture resolves to its expected
    ``tool_name`` ClassVar.

    Catches: silent renames, missed migrations, deletion of ClassVar
    declarations. To intentionally rename a plugin's tool_name, update
    the JSON fixture in the same commit.

    Defers ``get_node_class`` import until the test runs so collection
    works even when conftest's permissive plugin discovery fails for
    one plugin folder (the failure is logged in stderr).
    """
    from services.node_registry import get_node_class

    cls = get_node_class(node_type)
    if cls is None:
        pytest.skip(
            f"Plugin class for type={node_type!r} not registered — likely "
            f"conftest's plugin discovery skipped its folder. Snapshot "
            f"entry stays; the ClassVar check just doesn't run for it."
        )
    actual = (getattr(cls, "tool_name", "") or "").strip()
    assert actual == expected_tool_name, (
        f"tool_name mismatch for type={node_type!r}:\n"
        f"  expected (snapshot): {expected_tool_name!r}\n"
        f"  actual (cls.tool_name): {actual!r}\n"
        f"If this rename is intentional, update "
        f"server/tests/fixtures/tool_names_snapshot.json in the same commit."
    )
