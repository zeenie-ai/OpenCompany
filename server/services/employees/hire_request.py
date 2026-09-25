"""What ``hire_employee`` accepts: the employee the setup screen describes.

Mirrors the client's ``HireEmployeePayload``
(client/src/features/home/genui/hirePayload.ts); ``HIRE_PAYLOAD_KEYS``
there must equal this model's fields, which
tests/test_hire_payload_contract.py checks off disk.

Forgiving where the client already clamped: strings are trimmed and cut to
their caps, lists are cut to theirs, and unknown keys are ignored. A hire
without its identity (name, role, job, at least one step, an idempotency
key) is refused. Node types never come from here: the graph builder picks
them from the app registry.
"""

from __future__ import annotations

import re
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_REQUEST_BYTES = 32 * 1024

NAME_MAX = 40
ROLE_MAX = 60
DESCRIPTION_MAX = 280
JOB_MAX = 2000
APPS_MAX = 6
STEPS_MAX = 6
ITEMS_MAX = 8
KEY_MAX = 40
LABEL_MAX = 120
VALUE_MAX = 200
IDEMPOTENCY_KEY_MAX = 128

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _clip(value: Any, limit: int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _objects(value: Any, limit: int) -> List[Any]:
    """The dict items of a list, at most `limit` of them; anything else is dropped."""
    return [item for item in value if isinstance(item, dict)][:limit] if isinstance(value, list) else []


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HireStep(_Lenient):
    title: str = ""
    detail: str = ""
    role: Literal["trigger", "agent", "tool", "workflow"] = "agent"
    app: Optional[str] = None

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, value: Any) -> str:
        return _clip(value, 60)

    @field_validator("detail", mode="before")
    @classmethod
    def _detail(cls, value: Any) -> str:
        return _clip(value, 80)

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, value: Any) -> str:
        return value if value in ("trigger", "agent", "tool", "workflow") else "agent"

    @field_validator("app", mode="before")
    @classmethod
    def _app(cls, value: Any) -> Optional[str]:
        return _clip(value, 40) or None


class RuleItem(_Lenient):
    key: str = ""
    label: str = ""
    value: bool = False

    @field_validator("key", mode="before")
    @classmethod
    def _key(cls, value: Any) -> str:
        return _clip(value, KEY_MAX)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: Any) -> str:
        return _clip(value, LABEL_MAX)


class HireRules(_Lenient):
    #: Missing means on: nothing goes out without the owner's say-so.
    ask_first: bool = True
    items: List[RuleItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, value: Any) -> List[Any]:
        return _objects(value, ITEMS_MAX)


class ValueItem(_Lenient):
    key: str = ""
    label: str = ""
    value: str = ""

    @field_validator("key", mode="before")
    @classmethod
    def _key(cls, value: Any) -> str:
        return _clip(value, KEY_MAX)

    @field_validator("label", mode="before")
    @classmethod
    def _label(cls, value: Any) -> str:
        return _clip(value, LABEL_MAX)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, value: Any) -> str:
        return _clip(value, VALUE_MAX)


class HireTrigger(_Lenient):
    kind: Literal["app_event", "schedule", "manual"]
    app: Optional[str] = None
    every: Optional[Literal["hour", "day", "weekday", "week", "month"]] = None
    at: Optional[str] = None
    day: Optional[str] = None

    @field_validator("app", "day", mode="before")
    @classmethod
    def _short(cls, value: Any) -> Optional[str]:
        return _clip(value, 40) or None

    @field_validator("every", mode="before")
    @classmethod
    def _every(cls, value: Any) -> Optional[str]:
        return value if value in ("hour", "day", "weekday", "week", "month") else None

    @field_validator("at", mode="before")
    @classmethod
    def _at(cls, value: Any) -> Optional[str]:
        text = _clip(value, 5)
        return text if _HHMM.match(text) else None


class HireSource(_Lenient):
    spec_version: Literal[1] = 1
    provider: Optional[str] = None
    model: Optional[str] = None

    @field_validator("provider", "model", mode="before")
    @classmethod
    def _short(cls, value: Any) -> Optional[str]:
        return _clip(value, 200) or None


class HireEmployeeRequest(_Lenient):
    idempotency_key: str
    job: str
    name: str
    role: str
    description: str = ""
    apps: List[str] = Field(default_factory=list)
    steps: List[HireStep]
    rules: HireRules = Field(default_factory=HireRules)
    choices: List[ValueItem] = Field(default_factory=list)
    inputs: List[ValueItem] = Field(default_factory=list)
    trigger: Optional[HireTrigger] = None
    sends_via: Optional[str] = None
    source: HireSource = Field(default_factory=HireSource)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def _key(cls, value: Any) -> str:
        return _clip(value, IDEMPOTENCY_KEY_MAX)

    @field_validator("job", mode="before")
    @classmethod
    def _job(cls, value: Any) -> str:
        return str(value).strip()[:JOB_MAX] if isinstance(value, str) else ""

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, value: Any) -> str:
        return _clip(value, NAME_MAX)

    @field_validator("role", mode="before")
    @classmethod
    def _role(cls, value: Any) -> str:
        return _clip(value, ROLE_MAX)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: Any) -> str:
        return _clip(value, DESCRIPTION_MAX)

    @field_validator("apps", mode="before")
    @classmethod
    def _apps(cls, value: Any) -> List[str]:
        out: List[str] = []
        for item in value if isinstance(value, list) else []:
            text = _clip(item, 40)
            if text and text.lower() not in (seen.lower() for seen in out):
                out.append(text)
        return out[:APPS_MAX]

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, value: Any) -> List[Any]:
        return _objects(value, STEPS_MAX)

    @field_validator("choices", "inputs", mode="before")
    @classmethod
    def _values(cls, value: Any) -> List[Any]:
        return _objects(value, ITEMS_MAX)

    @field_validator("trigger", mode="before")
    @classmethod
    def _trigger(cls, value: Any) -> Any:
        # An unusable trigger is dropped, not fatal: the builder picks one.
        if not isinstance(value, dict) or value.get("kind") not in ("app_event", "schedule", "manual"):
            return None
        return value

    @field_validator("sends_via", mode="before")
    @classmethod
    def _sends_via(cls, value: Any) -> Optional[str]:
        return _clip(value, 40) or None

    def missing_identity(self) -> Optional[str]:
        """Why this hire cannot proceed, or None."""
        if not self.idempotency_key:
            return "idempotency_key"
        for field in ("job", "name", "role"):
            if not getattr(self, field):
                return field
        steps = [step for step in self.steps if step.title]
        if not steps:
            return "steps"
        return None


__all__ = [
    "HireEmployeeRequest",
    "HireRules",
    "HireSource",
    "HireStep",
    "HireTrigger",
    "MAX_REQUEST_BYTES",
    "RuleItem",
    "ValueItem",
]
