"""The hire payload is written by the client (genui/hirePayload.ts) and read
by the server (services/employees/hire_request.py). A key one side adds and
the other ignores is silently lost (the model ignores unknown keys), so the
two key lists must be the same; this reads the TypeScript off disk."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.employees.hire_request import HireEmployeeRequest, HireTrigger

REPO_ROOT = Path(__file__).resolve().parents[2]
HIRE_PAYLOAD_TS = REPO_ROOT / "client" / "src" / "features" / "home" / "genui" / "hirePayload.ts"


def _ts_list(source: str, name: str) -> list:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, f"could not find {name} in hirePayload.ts"
    return re.findall(r"'([^']*)'", match.group(1))


@pytest.fixture(scope="module")
def source() -> str:
    if not HIRE_PAYLOAD_TS.exists():
        pytest.skip(f"client source not present: {HIRE_PAYLOAD_TS}")
    return HIRE_PAYLOAD_TS.read_text(encoding="utf-8")


def test_payload_keys_match_the_request_model(source):
    assert _ts_list(source, "HIRE_PAYLOAD_KEYS") == list(HireEmployeeRequest.model_fields)


def test_trigger_choices_match(source):
    kinds = HireTrigger.model_fields["kind"].annotation.__args__
    assert tuple(_ts_list(source, "TRIGGER_KINDS")) == kinds


def test_caps_match(source):
    block = re.search(r"export const HIRE_LIMITS = \{(.*?)\}", source, re.DOTALL)
    assert block
    limits = {key: int(value) for key, value in re.findall(r"(\w+):\s*(\d+)", block.group(1))}
    from services.employees import hire_request as server

    assert limits["name"] == server.NAME_MAX
    assert limits["role"] == server.ROLE_MAX
    assert limits["description"] == server.DESCRIPTION_MAX
    assert limits["job"] == server.JOB_MAX
    assert limits["apps"] == server.APPS_MAX
    assert limits["steps"] == server.STEPS_MAX
    assert limits["items"] == server.ITEMS_MAX


def test_a_minimal_request_parses_and_ask_first_defaults_on():
    request = HireEmployeeRequest.model_validate(
        {"idempotency_key": "k", "job": "j", "name": "Maya", "role": "Receptionist", "steps": [{"title": "Answer"}]}
    )
    assert request.rules.ask_first is True
    assert request.missing_identity() is None


def test_odd_values_degrade_instead_of_failing():
    request = HireEmployeeRequest.model_validate(
        {
            "idempotency_key": "k",
            "job": "j",
            "name": "N" * 90,
            "role": "Helper",
            "steps": [{"title": "Go", "role": "wizard"}, "not a step", {"detail": "no title"}],
            "rules": {"items": [{"key": "hours", "label": "Only 9-6"}, 3]},
            "trigger": {"kind": "sometimes"},
            "apps": ["WhatsApp", "whatsapp", 7, ""],
            "extra": {"ignored": True},
        }
    )
    assert request.name == "N" * 40
    assert request.steps[0].role == "agent"
    assert len(request.steps) == 2
    assert request.rules.items[0].value is False
    assert request.trigger is None
    assert request.apps == ["WhatsApp", "7"]


def test_identity_is_required():
    request = HireEmployeeRequest.model_validate({"idempotency_key": "k", "job": "j", "name": "", "role": "r", "steps": []})
    assert request.missing_identity() == "name"
