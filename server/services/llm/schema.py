"""JSON-schema preparation for native provider tool APIs.

Pydantic emits local ``$ref``/``$defs`` constructs that are valid JSON
Schema but inconsistently supported by model providers.  Tool definitions are
small, so eagerly inlining local references gives all adapters the same stable
input and lets the final provider pass remove only keywords its API rejects.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping


_META_KEYWORDS = {"$schema", "$id", "$defs", "definitions"}
_GEMINI_UNSUPPORTED = {
    "$comment",
    "const",
    "default",
    "dependentSchemas",
    "else",
    "examples",
    "if",
    "patternProperties",
    "then",
    "unevaluatedProperties",
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

    missing = object()
    const_value = value.get("const", missing)
    cleaned = {
        key: _clean_provider_schema(item, provider=provider)
        for key, item in value.items()
        if key not in _META_KEYWORDS
        and not (provider == "gemini" and key in _GEMINI_UNSUPPORTED)
    }

    if provider == "gemini":
        # Gemini rejects JSON Schema ``const`` while accepting ``enum``.
        # Dropping const silently widened Pydantic Literal fields, so retain
        # the exact constraint through the equivalent single-value enum.
        if const_value is not missing and "enum" not in cleaned:
            cleaned["enum"] = [
                _clean_provider_schema(const_value, provider=provider)
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
