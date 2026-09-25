"""The setup-screen vocabulary is written twice: the server's manifest
(config/genui_catalog.json), which the prompt is generated from, and the
client's renderer (client/src/features/home/genui/catalog.ts, plus the hire
payload's trigger lists in hirePayload.ts). A component the prompt offers
but the renderer lacks is silently dropped from every screen; one the
renderer knows but the prompt never mentions is dead code. This reads the
TypeScript off disk and holds the two to each other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services.employees.genui_catalog import load_genui_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
GENUI = REPO_ROOT / "client" / "src" / "features" / "home" / "genui"
CATALOG_TS = GENUI / "catalog.ts"
HIRE_PAYLOAD_TS = GENUI / "hirePayload.ts"


def _source(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"client source not present: {path}")
    return path.read_text(encoding="utf-8")


def _string_list(source: str, name: str) -> list:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, f"could not find the literal list {name}"
    return re.findall(r"'([^']*)'", match.group(1))


def _object_literal(source: str, name: str) -> dict:
    match = re.search(rf"export const {name}[^=]*= \{{(.*?)\}}", source, re.DOTALL)
    assert match, f"could not find the literal object {name}"
    body = match.group(1)
    pairs = re.findall(r"(\w+):\s*('([^']*)'|(\d+))", body)
    return {key: (int(number) if number else text) for key, _whole, text, number in pairs}


@pytest.fixture(scope="module")
def manifest():
    return load_genui_catalog()


@pytest.fixture(scope="module")
def catalog_ts():
    return _source(CATALOG_TS)


def test_components_match_in_order(manifest, catalog_ts):
    assert _string_list(catalog_ts, "COMPONENT_TYPES") == list(manifest["components"])


def test_container_and_control_types_match(manifest, catalog_ts):
    assert _string_list(catalog_ts, "CONTAINER_TYPES") == manifest["container_types"]
    assert _string_list(catalog_ts, "CONTROL_TYPES") == manifest["control_types"]


def test_actions_and_aliases_match(manifest, catalog_ts):
    assert _string_list(catalog_ts, "ACTION_TYPES") == list(manifest["actions"])
    assert _object_literal(catalog_ts, "ACTION_ALIASES") == manifest["action_aliases"]


def test_tones_and_roles_match(manifest, catalog_ts):
    assert _string_list(catalog_ts, "TONES") == manifest["tones"]
    assert _string_list(catalog_ts, "STEP_ROLES") == manifest["step_roles"]


def test_state_paths_and_the_ask_first_label_match(manifest, catalog_ts):
    assert _object_literal(catalog_ts, "STATE_PATHS") == manifest["state_paths"]
    label = re.search(r"export const ASK_FIRST_LABEL = '([^']*)';", catalog_ts)
    assert label and label.group(1) == manifest["ask_first_label"]


def test_limits_match(manifest, catalog_ts):
    assert _object_literal(catalog_ts, "LIMITS") == manifest["limits"]


def test_trigger_lists_match():
    source = _source(HIRE_PAYLOAD_TS)
    manifest = load_genui_catalog()
    assert _string_list(source, "TRIGGER_KINDS") == manifest["trigger"]["kinds"]
    assert _string_list(source, "SCHEDULE_EVERY") == manifest["trigger"]["every"]


def test_every_component_has_a_prop_schema(catalog_ts):
    schemas = re.search(r"export const PROP_SCHEMAS = \{(.*)\} satisfies", catalog_ts, re.DOTALL)
    assert schemas, "could not find PROP_SCHEMAS"
    declared = set(re.findall(r"^  (\w+): z\.", schemas.group(1), re.MULTILINE))
    assert declared == set(_string_list(catalog_ts, "COMPONENT_TYPES"))


def test_manifest_is_plain_json():
    raw = (Path(__file__).resolve().parents[1] / "config" / "genui_catalog.json").read_text(encoding="utf-8")
    assert json.loads(raw)["spec_version"] == 1
