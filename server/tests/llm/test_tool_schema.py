import pytest

from services.llm.schema import compile_tool_schema


def test_schema_compiler_inlines_pydantic_refs_without_mutating_input():
    schema = {
        "$defs": {
            "Location": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            }
        },
        "type": "object",
        "properties": {
            "location": {
                "$ref": "#/$defs/Location",
                "description": "Where to search",
            }
        },
    }

    compiled = compile_tool_schema(schema, provider="anthropic")
    assert "$defs" in schema
    assert "$defs" not in compiled
    assert compiled["properties"]["location"] == {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
        "description": "Where to search",
    }


def test_gemini_schema_normalizes_nullable_and_drops_unsupported_metadata():
    compiled = compile_tool_schema(
        {
            "type": "object",
            "properties": {
                "unit": {
                    "type": ["string", "null"],
                    "default": None,
                    "examples": ["celsius"],
                }
            },
        },
        provider="gemini",
    )
    unit = compiled["properties"]["unit"]
    assert unit == {"type": "string", "nullable": True}


def test_gemini_schema_preserves_literals_enums_and_nested_nullable_unions():
    compiled = compile_tool_schema(
        {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "const": "lookup",
                },
                "options": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "anyOf": [
                                {"type": "string", "const": "fast"},
                                {"type": "null"},
                            ]
                        },
                        "unit": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                        },
                    },
                },
            },
        },
        provider="gemini",
    )

    assert compiled["properties"]["operation"] == {
        "type": "string",
        "enum": ["lookup"],
    }
    options = compiled["properties"]["options"]["properties"]
    assert options["mode"] == {
        "anyOf": [{"type": "string", "enum": ["fast"]}],
        "nullable": True,
    }
    assert options["unit"]["enum"] == ["celsius", "fahrenheit"]


def test_schema_compiler_rejects_external_and_recursive_refs():
    with pytest.raises(ValueError, match="Only local"):
        compile_tool_schema(
            {"$ref": "https://example.com/schema.json"},
            provider="openai",
        )

    recursive = {
        "$defs": {"Node": {"$ref": "#/$defs/Node"}},
        "$ref": "#/$defs/Node",
    }
    with pytest.raises(ValueError, match="Recursive"):
        compile_tool_schema(recursive, provider="openai")
