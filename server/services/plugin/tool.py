"""ToolNode — passive capability exposed to AI Agents via input-tools.

Return shape differs from :class:`ActionNode`: the LLM harness expects a
flat dict (no ``success`` wrapper). ``.as_tool_schema()`` produces the
JSON Schema the LLM sees — derived from :class:`Params` automatically.
"""

from __future__ import annotations

from copy import copy, deepcopy
from functools import lru_cache
from typing import Any, ClassVar, Dict, FrozenSet, Optional, Type

from pydantic import BaseModel, ConfigDict, create_model

from core.logging import get_logger
from services.plugin.base import BaseNode
from services.plugin.scaling import TaskQueue

logger = get_logger(__name__)


def inline_schema_refs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Inline $defs/$ref indirection for LLM function-calling surfaces.

    Function-calling APIs reject schema indirection, but a Params model
    with nested BaseModel or Enum fields emits ``$defs`` + ``$ref`` under
    Pydantic v2 — stripping ``$defs`` alone would leave dangling refs.
    Circular refs degrade to a permissive ``{}`` at the cycle point.
    A dereference failure (out-of-document ref, malformed schema) falls
    back to a permissive object schema.

    Only local JSON Pointers are accepted. Tool schemas are generated from
    trusted Pydantic models, but keeping the resolver local and document-only
    also prevents an accidental network/file fetch if an externally supplied
    schema reaches this helper.
    """
    root = deepcopy(schema)

    def _unescape_pointer_token(token: str) -> str:
        return token.replace("~1", "/").replace("~0", "~")

    def _lookup(ref: str) -> Any:
        if ref == "#":
            return root
        if not ref.startswith("#/"):
            raise ValueError(f"Only local JSON Pointer refs are supported: {ref!r}")
        current: Any = root
        for raw_token in ref[2:].split("/"):
            token = _unescape_pointer_token(raw_token)
            if isinstance(current, dict):
                current = current[token]
            elif isinstance(current, list):
                current = current[int(token)]
            else:
                raise ValueError(f"Cannot traverse JSON Pointer {ref!r}")
        return current

    def _resolve(value: Any, active_refs: frozenset[str]) -> Any:
        if isinstance(value, list):
            return [_resolve(item, active_refs) for item in value]
        if not isinstance(value, dict):
            return value

        if "$ref" in value:
            ref = value["$ref"]
            if not isinstance(ref, str):
                raise ValueError("$ref must be a string")
            if ref in active_refs:
                return {}
            target = _lookup(ref)
            if not isinstance(target, (dict, list)):
                raise ValueError(f"JSON Pointer {ref!r} does not target a schema")
            resolved = _resolve(deepcopy(target), active_refs | {ref})
            # JSON Schema permits siblings alongside $ref. Preserve them,
            # with the referencing site taking precedence over the target.
            siblings = {
                key: _resolve(item, active_refs)
                for key, item in value.items()
                if key != "$ref"
            }
            if siblings:
                if not isinstance(resolved, dict):
                    raise ValueError(f"Cannot merge siblings into ref {ref!r}")
                resolved.update(siblings)
            return resolved

        return {
            key: _resolve(item, active_refs)
            for key, item in value.items()
            if key not in {"$defs", "definitions"}
        }

    try:
        inlined = _resolve(root, frozenset())
        if not isinstance(inlined, dict):
            raise ValueError("Root JSON Schema must be an object")
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning(
            "inline_schema_refs: dereference failed (%s) — falling back to permissive schema",
            e,
        )
        return {"type": "object", "properties": {}}
    inlined.pop("$defs", None)
    inlined.pop("definitions", None)
    return inlined


class ToolNode(BaseNode, abstract=True):
    """Base class for AI-Agent tool nodes (calculatorTool, currentTimeTool)."""

    component_kind: ClassVar[str] = "tool"
    task_queue: ClassVar[str] = TaskQueue.REST_API

    # Tool-safety annotations (Pipedream pattern).
    annotations: ClassVar[Dict[str, Any]] = {
        "destructive": False,
        "readonly": True,
        "open_world": False,
    }

    # ``Params`` describes persisted, operator-controlled node
    # configuration. ``ToolInput`` describes arguments an LLM is allowed to
    # supply for a single invocation. Existing tools keep their historical
    # contract automatically: when a subclass does not declare ToolInput,
    # ``__init_subclass__`` aliases it to that subclass's Params model.
    #
    # Keeping these as distinct models is a security boundary. In particular,
    # a model must never be able to smuggle workflow/node scope or overwrite a
    # server-controlled setting merely because both dictionaries happened to
    # be merged before validation.
    ToolInput: ClassVar[Optional[Type[BaseModel]]] = None
    server_controlled_fields: ClassVar[FrozenSet[str]] = frozenset()

    # Some first-party tools have a security-sensitive public schema. Their
    # ToolInput must win over a stale/custom ToolSchema row saved by an older
    # client. Simple Memory opts into this contract.
    tool_schema_locked: ClassVar[bool] = False

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        if not abstract and "ToolInput" not in cls.__dict__:
            cls.ToolInput = cls.Params
        super().__init_subclass__(abstract=abstract, **kwargs)

    @classmethod
    def tool_input_model(cls) -> Type[BaseModel]:
        """Return the invocation model, defaulting to persisted ``Params``."""
        return cls.ToolInput or cls.Params

    @classmethod
    @lru_cache(maxsize=None)
    def partial_config_model(cls) -> Type[BaseModel]:
        """Model used to validate the persisted subset for legacy tools.

        Historically a ToolNode's Params mixed operator configuration with
        required invocation arguments, so a saved node may legitimately omit
        fields the LLM must provide. This derived model keeps every field's
        type/constraints and extra-field policy while making absence valid.
        """
        partial_fields: Dict[str, tuple[Any, Any]] = {}
        for field_name, field_info in cls.Params.model_fields.items():
            optional_info = copy(field_info)
            optional_info.default = None
            optional_info.default_factory = None
            partial_fields[field_name] = (field_info.annotation, optional_info)
        return create_model(
            f"{cls.Params.__name__}PersistedConfig",
            __config__=ConfigDict(
                extra=cls.Params.model_config.get("extra", "ignore")
            ),
            **partial_fields,
        )

    @classmethod
    def as_tool_schema(cls) -> Dict[str, Any]:
        """LLM-visible schema: ``{name, description, parameters}`` where
        ``parameters`` is the Pydantic JSON schema of :class:`ToolInput`."""
        # Inline $defs / $ref — LLM function-calling doesn't cope with
        # indirection, and a bare strip would leave dangling $ref.
        schema = inline_schema_refs(cls.tool_input_model().model_json_schema())
        return {
            "name": (
                cls.tool_name
                if cls.tool_schema_locked and cls.tool_name
                else cls.type
            ),
            "description": cls.description or cls.display_name or cls.type,
            "parameters": schema,
        }

    def _wrap_success(self, *, start_time: float, result):
        """Tools return flat result (no success wrapper)."""
        from pydantic import BaseModel, ValidationError
        from pydantic_core import PydanticSerializationError

        if isinstance(result, (BaseModel, dict)):
            try:
                # Same Output-contract enforcement as the base class —
                # validate + dump(mode="json") so the flat tool payload
                # is always JSON-compatible (see BaseNode._serialize_result).
                return self._serialize_result(result)
            except (ValidationError, PydanticSerializationError) as e:
                return self._wrap_error(
                    start_time=start_time,
                    error=f"Output contract violation: {e}",
                    error_type="OutputValidationError",
                    exc=e,
                )
        return {"result": result}

    @classmethod
    def interpret_result(cls, result: Dict[str, Any]) -> tuple[bool, Any, Optional[str]]:
        """ToolNode contract: a flat dict (no ``success`` key) IS the
        success payload. Operation exceptions still flow through
        :meth:`_wrap_error` and produce the standard envelope — those
        get the base-class semantics."""
        if isinstance(result, dict) and "success" not in result:
            return True, result, None
        return super().interpret_result(result)
