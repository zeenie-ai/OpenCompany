"""Unit tests for ``inline_schema_refs`` — the LLM function-calling
schema flattener used by ``ToolNode.as_tool_schema`` and the Vertex
managed-agent tool bridge.

The contract: declared tool parameter schemas must never contain
``$defs`` / ``definitions`` / dangling ``$ref`` — nested models and
Enums inline; pathological schemas degrade to a permissive object
schema instead of raising.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from services.plugin.tool import inline_schema_refs


class Color(str, Enum):
    RED = "red"
    BLUE = "blue"


class Inner(BaseModel):
    name: str
    weight: float = 1.0


class NestedParams(BaseModel):
    items: List[Inner]
    color: Color = Color.RED


class TreeNode(BaseModel):
    label: str
    children: List["TreeNode"] = []


class TestInlineSchemaRefs:
    def test_nested_model_inlined(self):
        schema = inline_schema_refs(NestedParams.model_json_schema())
        dumped = json.dumps(schema)
        assert "$defs" not in schema
        assert "definitions" not in schema
        assert '"$ref"' not in dumped
        item_schema = schema["properties"]["items"]["items"]
        assert item_schema["type"] == "object"
        assert set(item_schema["properties"]) == {"name", "weight"}

    def test_str_enum_inlined(self):
        schema = inline_schema_refs(NestedParams.model_json_schema())
        color = schema["properties"]["color"]
        # Pydantic may wrap defaulted enum refs in allOf; dereference_refs
        # inlines the target either way — assert the enum values survive
        # somewhere inside the property with no $ref indirection.
        dumped = json.dumps(color)
        assert '"$ref"' not in dumped
        assert "red" in dumped and "blue" in dumped

    def test_recursive_model_degrades_safely(self):
        schema = inline_schema_refs(TreeNode.model_json_schema())
        dumped = json.dumps(schema)  # must stay JSON-serializable
        assert "$defs" not in schema
        assert '"$ref"' not in dumped

    def test_flat_schema_passthrough(self):
        class Flat(BaseModel):
            query: str
            limit: int = 5

        original = Flat.model_json_schema()
        schema = inline_schema_refs(original)
        assert schema["properties"] == original["properties"]
        assert schema["required"] == original["required"]

    def test_broken_ref_falls_back_permissive(self):
        broken = {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/DoesNotExist"}},
        }
        schema = inline_schema_refs(broken)
        assert schema == {"type": "object", "properties": {}}
