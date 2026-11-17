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
                    "displayOptions": {"show": {"operation": ["convert"]}},
                    "placeholder": "celsius",
                    "rows": 3,
                },
                # Property names are data, not schema keywords. A
                # structure-aware filter must retain this field even though
                # the Gemini Schema model does not accept the ``default``
                # annotation keyword.
                "default": {
                    "type": "string",
                    "displayOptions": {"show": {"advanced": [True]}},
                },
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "widget": "tags",
                        "displayOptions": {"show": {"advanced": [True]}},
                    },
                },
                "settings": {
                    "type": "object",
                    "additionalProperties": False,
                },
            },
        },
        provider="gemini",
    )
    unit = compiled["properties"]["unit"]
    assert unit == {"type": "string", "nullable": True}
    assert compiled["properties"]["default"] == {"type": "string"}
    assert compiled["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert compiled["properties"]["settings"]["additionalProperties"] is False


def test_gemini_compiled_pydantic_ui_schema_is_accepted_by_sdk():
    """Regression: real Pydantic schemas must form a valid Gemini config."""

    from typing import Literal

    from google.genai import types
    from pydantic import BaseModel, Field

    class FileModifyParams(BaseModel):
        operation: str = "write"
        content: str = Field(
            default="",
            json_schema_extra={
                "displayOptions": {"show": {"operation": ["write"]}},
                "rows": 6,
                "placeholder": "File contents",
            },
        )
        replace_all: bool = Field(
            default=False,
            json_schema_extra={
                "displayOptions": {"show": {"operation": ["edit"]}},
                "hidden": True,
            },
        )
        memory: Literal[128, 256] = 128

    parameters = compile_tool_schema(
        FileModifyParams.model_json_schema(),
        provider="gemini",
    )
    config = types.GenerateContentConfig(
        tools=[
            {
                "function_declarations": [
                    {
                        "name": "file_modify",
                        "description": "Write or edit a file",
                        "parameters": parameters,
                    }
                ]
            }
        ]
    )

    declaration = config.tools[0].function_declarations[0]
    assert declaration.parameters_json_schema is None
    schema = declaration.parameters
    assert schema is not None
    assert set(schema.properties) == {
        "operation",
        "content",
        "replace_all",
        "memory",
    }
    assert schema.properties["memory"].enum == ["128", "256"]


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


@pytest.mark.parametrize("provider", ["anthropic", "openai", "openrouter"])
def test_ui_annotations_never_reach_the_model(provider):
    """The editor reads UI keys from the NodeSpec; a model only pays for them."""
    compiled = compile_tool_schema(
        {
            "title": "BrowserToolInput",
            "type": "object",
            "properties": {
                "url": {
                    "title": "Url",
                    "type": "string",
                    "description": "Page to open",
                    "placeholder": "https://example.com",
                    "displayOptions": {"show": {"operation": ["open"]}},
                },
                "steps": {
                    "title": "Steps",
                    "type": "array",
                    "items": {"title": "Step", "type": "string", "widget": "tags"},
                },
                "target": {
                    "title": "Target",
                    "anyOf": [
                        {"type": "string", "title": "Selector", "hidden": True},
                        {"type": "null"},
                    ],
                    "loadOptionsMethod": "browserTabs",
                    "uiHints": {"compact": True},
                },
            },
            "required": ["url"],
        },
        provider=provider,
    )

    assert compiled == {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Page to open"},
            "steps": {"type": "array", "items": {"type": "string"}},
            "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["url"],
    }


def test_parameters_named_like_ui_keys_are_kept():
    """Property names are data: a ``title`` or ``group`` parameter survives."""
    from pydantic import BaseModel, Field

    class NoteParams(BaseModel):
        title: str = Field(json_schema_extra={"placeholder": "Note title"})
        group: str = Field(
            json_schema_extra={
                "group": "options",
                "displayOptions": {"show": {"mode": ["grouped"]}},
            }
        )
        body: str = Field(default="", json_schema_extra={"rows": 6})

    compiled = compile_tool_schema(
        NoteParams.model_json_schema(), provider="anthropic"
    )

    assert compiled == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "group": {"type": "string"},
            "body": {"default": "", "type": "string"},
        },
        "required": ["title", "group"],
    }


def test_values_and_descriptions_are_kept_as_they_are():
    """Keywords whose value is data are copied whole, UI-like keys inside them too."""
    compiled = compile_tool_schema(
        {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["fast", "deep"],
                    "description": "How hard to look",
                },
                "options": {
                    "type": "object",
                    "default": {"title": "kept", "group": "kept"},
                    "examples": [{"title": "kept"}],
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "enum_values": ["title", "network"],
                },
                "shape": {
                    "oneOf": [
                        {"type": "string", "title": "Name"},
                        {"type": "integer", "const": 3},
                    ]
                },
            },
        },
        provider="openai",
    )

    assert compiled["properties"] == {
        "mode": {
            "type": "string",
            "enum": ["fast", "deep"],
            "description": "How hard to look",
        },
        "options": {
            "type": "object",
            "default": {"title": "kept", "group": "kept"},
            "examples": [{"title": "kept"}],
        },
        "capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "enum_values": ["title", "network"],
        },
        "shape": {"oneOf": [{"type": "string"}, {"type": "integer", "const": 3}]},
    }
