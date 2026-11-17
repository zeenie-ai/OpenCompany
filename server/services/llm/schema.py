"""JSON-schema preparation for native provider tool APIs.

Pydantic emits local ``$ref``/``$defs`` constructs that are valid JSON
Schema but inconsistently supported by model providers.  Tool definitions are
small, so eagerly inlining local references gives all adapters the same stable
input and lets the final provider pass remove only keywords its API rejects.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Mapping


_META_KEYWORDS = {"$schema", "$id", "$defs", "definitions"}
# UI-only annotations (the editor reads them from the NodeSpec, never from a
# compiled schema) and Pydantic's generated titles. Other providers accept
# unknown keywords, so for them this list only saves prompt tokens on every
# call: a UI keyword missing from it still reaches the model harmlessly.
_UI_KEYWORDS = frozenset({
    "title", "displayOptions", "placeholder", "rows", "hidden", "editor", "widget",
    "accept", "component", "uiHints", "loadOptionsMethod", "loadOptionsDependsOn",
    "dynamicOptions", "dependsOn", "numberStepSize", "password", "group", "groups",
})
# Keywords whose value is data, not a schema: copied as they are.
_VALUE_KEYWORDS = frozenset({"default", "const", "enum", "examples"})
# Gemini's function-schema surface does not accept OpenCompany UI annotations
# such as ``displayOptions``, ``rows`` and ``placeholder``. A denylist is
# therefore unsafe: every new UI annotation could invalidate a request. Keep
# this provider pass aligned with the public ``google.genai.types.Schema``
# surface instead. References are inlined above, and ``default`` remains
# excluded for Gemini API compatibility.
_GEMINI_SCHEMA_KEYWORDS = {
    "additionalProperties",
    "anyOf",
    "description",
    "enum",
    "example",
    "format",
    "items",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "nullable",
    "pattern",
    "properties",
    "propertyOrdering",
    "required",
    "title",
    "type",
}


def compile_tool_schema(
    schema: Mapping[str, Any], *, provider: str
) -> Dict[str, Any]:
    """Return an isolated schema accepted by the named provider.

    Only local JSON pointers are expanded.  External references are rejected:
    provider requests cannot dereference project-local or network documents.
    """

    root = deepcopy(dict(schema))
    resolved = _resolve_node(root, root=root, stack=())
    if not isinstance(resolved, dict):
        raise ValueError("Tool parameter schema must resolve to an object")
    for keyword in _META_KEYWORDS:
        resolved.pop(keyword, None)
    return _clean_provider_schema(resolved, provider=provider)


def _resolve_node(value: Any, *, root: Dict[str, Any], stack: tuple[str, ...]) -> Any:
    if isinstance(value, list):
        return [_resolve_node(item, root=root, stack=stack) for item in value]
    if not isinstance(value, dict):
        return value

    ref = value.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise ValueError(f"Only local JSON-schema references are supported: {ref!r}")
        if ref in stack:
            chain = " -> ".join((*stack, ref))
            raise ValueError(f"Recursive tool schema is not supported: {chain}")
        target: Any = root
        for raw_part in ref[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise ValueError(f"Unresolved JSON-schema reference: {ref}")
            target = target[part]
        merged = _resolve_node(
            deepcopy(target),
            root=root,
            stack=(*stack, ref),
        )
        siblings = {
            key: item for key, item in value.items() if key != "$ref"
        }
        if siblings:
            if not isinstance(merged, dict):
                raise ValueError(f"Cannot merge siblings into non-object reference: {ref}")
            merged.update(
                _resolve_node(siblings, root=root, stack=stack)
            )
        return merged

    return {
        key: _resolve_node(item, root=root, stack=stack)
        for key, item in value.items()
        if key not in {"$defs", "definitions"}
    }


def _clean_provider_schema(value: Any, *, provider: str) -> Any:
    if isinstance(value, list):
        return [
            _clean_provider_schema(item, provider=provider)
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    if provider != "gemini":
        kept: Dict[str, Any] = {}
        for key, item in value.items():
            if key in _META_KEYWORDS or key in _UI_KEYWORDS:
                continue
            if key == "properties" and isinstance(item, dict):
                # Mapping keys are property names, not keywords: a parameter
                # named ``title`` or ``group`` is kept.
                kept[key] = {
                    name: _clean_provider_schema(child, provider=provider)
                    for name, child in item.items()
                }
            elif key in _VALUE_KEYWORDS:
                kept[key] = item
            else:
                kept[key] = _clean_provider_schema(item, provider=provider)
        return kept

    missing = object()
    const_value = value.get("const", missing)
    cleaned: Dict[str, Any] = {}
    for key, item in value.items():
        if key in _META_KEYWORDS:
            continue
        if key not in _GEMINI_SCHEMA_KEYWORDS:
            # ``const`` is restored below as a single-value enum. Omit other
            # provider-unsupported keywords and UI-only metadata.
            continue

        if key == "properties" and isinstance(item, dict):
            # Mapping keys are user-defined property names, not JSON Schema
            # keywords. Preserve them even when a field happens to be named
            # ``default``, ``examples`` or another unsupported keyword.
            cleaned[key] = {
                name: _clean_provider_schema(child, provider=provider)
                for name, child in item.items()
            }
        elif key == "anyOf" and isinstance(item, list):
            cleaned[key] = [
                _clean_provider_schema(child, provider=provider)
                for child in item
            ]
        elif key in {"additionalProperties", "items"} and isinstance(
            item, (dict, list)
        ):
            cleaned[key] = _clean_provider_schema(item, provider=provider)
        else:
            # Scalar annotations and values (including enum members) are not
            # schemas and must not be filtered recursively.
            cleaned[key] = item

    # Gemini rejects JSON Schema ``const`` while accepting ``enum``.
    # Dropping const silently widened Pydantic Literal fields, so retain
    # the exact constraint through the equivalent single-value enum.
    if const_value is not missing and "enum" not in cleaned:
        cleaned["enum"] = [const_value]

    # The SDK's OpenAPI-style Schema model represents enum members as
    # strings even for INTEGER, NUMBER and BOOLEAN schemas. Encode
    # non-string JSON values using their JSON lexical form so numeric
    # Pydantic Literals remain constrained without failing local SDK
    # validation.
    enum_values = cleaned.get("enum")
    if isinstance(enum_values, list):
        cleaned["enum"] = [
            item
            if isinstance(item, str)
            else json.dumps(
                item,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            for item in enum_values
        ]

    schema_type = cleaned.get("type")
    if isinstance(schema_type, list) and "null" in schema_type:
        non_null = [item for item in schema_type if item != "null"]
        if len(non_null) == 1:
            cleaned["type"] = non_null[0]
            cleaned["nullable"] = True
    variants = cleaned.get("anyOf")
    if isinstance(variants, list):
        non_null_variants = [
            item
            for item in variants
            if not (isinstance(item, dict) and item.get("type") == "null")
        ]
        if len(non_null_variants) == len(variants) - 1:
            cleaned["anyOf"] = non_null_variants
            cleaned["nullable"] = True

    return cleaned
