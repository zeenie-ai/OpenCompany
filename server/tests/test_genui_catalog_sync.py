"""The setup-screen vocabulary (config/genui_catalog.json), which the
prompt is generated from and the client's renderer imports
(client/src/features/home/genui/catalog.ts, hirePayload.ts; a client test
holds the renderer's prop schemas to it). These are the checks only the
server side can make: the schedule lists are the builder's own, and the
manifest says what the renderer reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.employees.genui_catalog import load_genui_catalog


@pytest.fixture(scope="module")
def manifest():
    return load_genui_catalog()


def test_schedule_choices_are_the_ones_the_builder_runs(manifest):
    """The setup screen offers only times and weekdays the employee's
    schedule can run at, so what the owner picks is never moved to another
    on the way in (services/employees/builder.py snaps anything else)."""
    from services.employees.builder import SCHEDULE_TIMES, WEEKDAYS

    assert manifest["trigger"]["times"] == list(SCHEDULE_TIMES)
    assert sorted(manifest["trigger"]["days"]) == sorted(WEEKDAYS)
    assert manifest["trigger"]["days"][0] == "monday"


def test_inserted_components_are_plain_leaves(manifest):
    """The client inserts these itself; neither the model nor the rules
    card's control filing may own them."""
    inserted = [name for name, spec in manifest["components"].items() if spec.get("inserted")]
    assert inserted == ["Schedule"]
    for name in inserted:
        assert name not in manifest["container_types"] and name not in manifest["control_types"]


def test_the_spec_is_in_json_renders_shape(manifest):
    """spec_version 2: a Toggle binds `checked`, and a Button's action is
    the element's on.press, never a prop."""
    assert "checked:" in manifest["components"]["Toggle"]["props"]
    assert "action" not in manifest["components"]["Button"]["props"]
    assert '"on":{"press":' in manifest["components"]["Button"]["description"]


def test_manifest_is_plain_json():
    raw = (Path(__file__).resolve().parents[1] / "config" / "genui_catalog.json").read_text(encoding="utf-8")
    assert json.loads(raw)["spec_version"] == 2
