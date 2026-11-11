"""Parameter validation and coercion helpers shared by plugins.

The parameter panel stores ``""`` for any field the user has cleared or never
filled, **whatever the declared type**. Against a ``str`` field that is
harmless; against ``Optional[float]``, ``bool`` or ``Dict[str, Any]`` it is a
hard validation error, and a freshly-dropped node then fails on its own
defaults with something like *"Input should be a valid dictionary"*.

Lives here rather than in a plugin folder because it is a property of the
panel-to-Pydantic boundary, not of any vendor. Two plugins needed it before it
was worth extracting; the per-node precedents that predate it are
``AndroidServiceParams._coerce_parameters`` and
``WriteTodosParams._coerce_todos``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Sequence, Union, get_args, get_origin

from pydantic import ValidationError


def format_validation_error(error: ValidationError) -> str:
    """Describe the first invalid field without echoing inputs or context."""
    errors = error.errors(include_url=False, include_context=False, include_input=False)
    if not errors:
        return "Invalid input"
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "Invalid input")
    return f"{location}: {message}" if location else message


def _accepts_str(annotation: Any) -> bool:
    """Whether a blank string is a legitimate value for this annotation.

    ``Dict[str, Any]`` is the case worth spelling out: its ``get_args`` are
    ``(str, Any)``, so a naive "is str among the args" check would wrongly
    conclude a dict field accepts a string. Container origins are rejected
    before the args are consulted.
    """
    if annotation is Any or annotation is str:
        return True
    origin = get_origin(annotation)
    if origin is Union:
        return any(_accepts_str(arg) for arg in get_args(annotation))
    return False


def coerce_blank_params(
    cls: Any, values: Any, *, object_fields: Sequence[str] = ()
) -> Any:
    """Normalize panel-supplied blanks before Pydantic validates them.

    Blank strings are dropped for fields that *cannot* hold a string, so the
    field's own default applies. Fields named in ``object_fields``
    additionally accept a JSON **object** string, because the panel has no
    object widget and renders them as a text input — a user who types
    ``{"a": 1}`` should get what they plainly meant.

    Blanks are deliberately NOT dropped for ``str`` fields: doing so would
    turn a ``min_length`` error into a confusing "field required".

    Use from a ``@model_validator(mode="before")``::

        @model_validator(mode="before")
        @classmethod
        def _coerce(cls, values):
            return coerce_blank_params(cls, values, object_fields=("options",))
    """
    if not isinstance(values, dict):
        return values

    cleaned: Dict[str, Any] = {}
    for key, value in values.items():
        if key in object_fields:
            cleaned[key] = coerce_json_object(key, value)
            continue

        field = cls.model_fields.get(key)
        blank = isinstance(value, str) and not value.strip()
        if blank and field is not None and not _accepts_str(field.annotation):
            continue
        cleaned[key] = value
    return cleaned


def coerce_json_object(key: str, value: Any) -> Dict[str, Any]:
    """Accept a dict, a blank, or a JSON object string. Reject anything else.

    Rejecting is deliberate: coercing a blank recovers from a UI artefact,
    but silently turning ``"[1,2]"`` into ``{}`` would hide a real user error.
    """
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise ValueError(
                f'{key} must be a JSON object, e.g. {{"option": "value"}}. '
                f"Could not parse it: {exc}."
            ) from exc
        if isinstance(parsed, dict):
            return parsed
        raise ValueError(
            f"{key} must be a JSON object, not a {type(parsed).__name__}."
        )
    raise ValueError(f"{key} must be a JSON object, not a {type(value).__name__}.")


__all__ = ["coerce_blank_params", "coerce_json_object", "format_validation_error"]
