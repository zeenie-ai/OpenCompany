"""Wave 11 — plugin contract invariants.

Enforces every :class:`BaseNode` subclass honours the declared shape
before it ships. Mirrors the 108-invariant suite in ``test_node_spec.py``
but for the class-based plugin path (11.B onwards).

These run even when no plugins exist yet (emptiness ⇒ pass). As 11.B
adds plugins, they automatically participate.
"""

from __future__ import annotations


from pydantic import BaseModel


def _all_plugin_classes():
    # Import populates the plugin-class registry at module import time.
    import nodes  # noqa: F401  (populate registry)
    from services.node_registry import registered_node_classes

    return list(registered_node_classes().values())


class TestBaseNodeDeclaration:
    """Every subclass must declare the minimum viable fields."""

    def test_non_empty_type(self):
        for cls in _all_plugin_classes():
            assert cls.type, f"{cls.__qualname__} missing required class attr `type`"

    def test_non_empty_display_name(self):
        for cls in _all_plugin_classes():
            assert cls.display_name, f"{cls.__qualname__} missing `display_name`"

    def test_non_empty_group(self):
        for cls in _all_plugin_classes():
            assert cls.group, f"{cls.__qualname__} missing `group`"

    def test_version_is_positive_int(self):
        for cls in _all_plugin_classes():
            assert isinstance(cls.version, int) and cls.version >= 1, f"{cls.__qualname__} version must be a positive int"


class TestParamsAndOutput:
    """Params + Output must be Pydantic models."""

    def test_params_is_basemodel(self):
        for cls in _all_plugin_classes():
            assert issubclass(cls.Params, BaseModel), f"{cls.__qualname__}.Params must be a Pydantic BaseModel"

    def test_output_is_basemodel(self):
        for cls in _all_plugin_classes():
            assert issubclass(cls.Output, BaseModel), f"{cls.__qualname__}.Output must be a Pydantic BaseModel"


class TestCredentials:
    """Every declared credential must resolve to a registered class."""

    def test_credentials_are_registered(self):
        from services.plugin.credential import CREDENTIAL_REGISTRY, Credential

        for cls in _all_plugin_classes():
            for cred in cls.credentials:
                assert issubclass(cred, Credential), f"{cls.__qualname__}.credentials must be Credential subclasses"
                assert cred.id in CREDENTIAL_REGISTRY, f"{cls.__qualname__} references unregistered credential '{cred.id}'"


class TestOperations:
    """At least one op; names unique; routing requires credentials."""

    def test_at_least_one_operation(self):
        from services.plugin.base import _EmptyOutput

        for cls in _all_plugin_classes():
            # Pure-display plugins (no handler) are allowed — skip those.
            if cls.Params.__name__ == "_EmptyParams" and cls.Output is _EmptyOutput:
                continue
            assert cls._operations, f"{cls.__qualname__} must declare at least one @Operation"

    def test_operation_names_unique(self):
        for cls in _all_plugin_classes():
            names = [spec.name for spec in cls._operations.values()]
            assert len(names) == len(set(names)), f"{cls.__qualname__} has duplicate operation names: {names}"

    def test_routing_requires_credentials(self):
        for cls in _all_plugin_classes():
            for spec in cls._operations.values():
                if spec.routing is not None:
                    assert cls.credentials, f"{cls.__qualname__}.{spec.name} uses routing but no " f"credentials declared"


class TestScalingKnobs:
    """Temporal knobs must have sane values."""

    def test_task_queue_declared(self):
        from services.plugin.scaling import TaskQueue

        for cls in _all_plugin_classes():
            assert cls.task_queue in TaskQueue.ALL, f"{cls.__qualname__}.task_queue={cls.task_queue!r} " f"not in TaskQueue.ALL"

    def test_retry_policy_type(self):
        from services.plugin.scaling import RetryPolicy

        for cls in _all_plugin_classes():
            assert isinstance(cls.retry_policy, RetryPolicy), f"{cls.__qualname__}.retry_policy must be a RetryPolicy"


class TestStartToCloseTimeoutOverridesAreCommented:
    """Wave 12 — A1: any plugin that overrides ``start_to_close_timeout``
    away from its kind-base default must carry an inline comment in the
    class body explaining why.

    Today every plugin inherits the kind-base default (``ActionNode``=10m,
    ``TriggerNode``=24h, ``ToolNode``=10m). The kind-defaults are the
    declared intent at the kind-base level; per-plugin overrides are the
    exception, not the norm. When an override does ship, the comment
    forces the author to justify the deviation (and gives the next reader
    the reason without git-blame archaeology).

    Why introspect with ``inspect.getsource`` rather than just check
    equality against the kind-default: equality alone can't catch the
    case where a future plugin author copies the literal kind-default
    value into their class body (silently restating intent — still
    needs a comment so reviewers know the override is deliberate).
    """

    _OVERRIDE_PATTERN = "start_to_close_timeout"

    def test_overrides_have_inline_comment(self):
        import inspect

        for cls in _all_plugin_classes():
            # Only flag plugins that LITERALLY set the attribute in their
            # own class body (not inherited from kind-base). The class-dict
            # check is the cheapest signal — if it's not in __dict__, it's
            # inherited and there's nothing to comment.
            if self._OVERRIDE_PATTERN not in cls.__dict__:
                continue
            try:
                src = inspect.getsource(cls)
            except (OSError, TypeError):
                # Skip if source unavailable (e.g. dynamically-created class
                # in a test fixture).
                continue
            # Find the override line and check the same-line comment OR the
            # immediately-preceding line is a comment.
            lines = src.splitlines()
            override_idx = None
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(self._OVERRIDE_PATTERN) and "=" in stripped:
                    override_idx = i
                    break
            if override_idx is None:
                # Defensive: __dict__ said it's there but source parsing
                # missed it (e.g. multi-line attribute assignment). Skip
                # rather than false-positive.
                continue
            override_line = lines[override_idx]
            has_inline_comment = "#" in override_line.split("=", 1)[1]
            has_preceding_comment = override_idx > 0 and lines[override_idx - 1].strip().startswith("#")
            assert has_inline_comment or has_preceding_comment, (
                f"{cls.__qualname__} overrides start_to_close_timeout away "
                f"from the kind-base default but has no explanatory comment. "
                f"Add a comment on the same line or directly above explaining "
                f"why this plugin needs a non-default timeout."
            )


class TestToolSchemaGeneration:
    """ToolNode.Params must produce LLM-compatible JSON schema."""

    def test_tool_schema_has_no_refs(self):
        import json

        from services.plugin.tool import ToolNode

        for cls in _all_plugin_classes():
            if not issubclass(cls, ToolNode):
                continue
            schema = cls.as_tool_schema()
            params = schema["parameters"]
            assert "$defs" not in params, f"{cls.__qualname__} tool schema contains $defs — LLM " "function-calling rejects $ref"
            assert "definitions" not in params
            # A stripped $defs with a surviving $ref is worse than $defs
            # itself — the declaration points at a definition that no
            # longer exists (nested BaseModel / Enum Params fields).
            assert '"$ref"' not in json.dumps(params), f"{cls.__qualname__} tool schema contains a dangling $ref — " "nested refs must be inlined, not stripped"
            assert schema.get("name") == cls.type


class TestToolSchemaFastPath:
    """Wave 11.D.12: every plugin node usable as an AI tool must resolve
    via the plugin fast-path in :meth:`AIService._get_tool_schema`.
    Legacy per-type hardcoded branches become dead code once this test
    passes — scheduled for deletion in 11.D.13.
    """

    def test_fast_path_covers_every_plugin_tool(self):
        from services.node_registry import get_node_class
        from services.plugin.tool import ToolNode

        for cls in _all_plugin_classes():
            is_tool = issubclass(cls, ToolNode) or getattr(cls, "usable_as_tool", False)
            if not is_tool:
                continue
            resolved = get_node_class(cls.type)
            assert resolved is cls, f"{cls.__qualname__}: fast-path lookup by type '{cls.type}' " f"returned {resolved}, expected {cls}"
            assert hasattr(resolved, "Params"), f"{cls.__qualname__} has no Params — fast-path would miss it"


class TestInterpretResultContract:
    """``BaseNode.interpret_result`` + ``ToolNode.interpret_result`` are
    the polymorphic envelope-unwrap contract the F4.A Temporal activity
    wrapper calls in :meth:`BaseNode.as_activity` (see
    ``server/services/plugin/base.py``). Locks the
    behaviour so the writeTodos / agentBuilder bug-fix doesn't regress.
    """

    def test_basenode_envelope_success(self):
        from services.plugin.base import BaseNode

        success, payload, error = BaseNode.interpret_result({"success": True, "result": {"value": 42}})
        assert success is True
        assert payload == {"value": 42}
        assert error is None

    def test_basenode_envelope_success_with_missing_result_key(self):
        from services.plugin.base import BaseNode

        # Defensive: an envelope with success=True but no result key
        # surfaces an empty dict, not None — protects downstream code
        # that does `.get(...)` on payload.
        success, payload, error = BaseNode.interpret_result({"success": True})
        assert success is True
        assert payload == {}

    def test_basenode_envelope_failure(self):
        from services.plugin.base import BaseNode

        success, payload, error = BaseNode.interpret_result({"success": False, "error": "boom"})
        assert success is False
        assert payload is None
        assert error == "boom"

    def test_toolnode_flat_dict_is_success(self):
        """ToolNode plugins return their Output as a flat dict (no
        ``success`` key); the wrapper must treat absence of the key as
        success, not failure. This was the writeTodos bug — the F4.A
        wrapper was checking ``result.get("success")`` directly and
        misclassifying every ToolNode as failed."""
        from services.plugin.tool import ToolNode

        success, payload, error = ToolNode.interpret_result({"message": "ok", "todos": [1, 2, 3], "count": 3})
        assert success is True
        assert payload == {"message": "ok", "todos": [1, 2, 3], "count": 3}
        assert error is None

    def test_toolnode_error_envelope_routes_to_failure(self):
        """When a ToolNode operation throws, BaseNode._wrap_error still
        produces the standard ``{success: False, error: ...}`` envelope.
        ToolNode.interpret_result must route that to the failure path
        rather than treating it as a success payload."""
        from services.plugin.tool import ToolNode

        success, payload, error = ToolNode.interpret_result({"success": False, "error": "tool crashed"})
        assert success is False
        assert payload is None
        assert error == "tool crashed"

    def test_every_toolnode_subclass_inherits_override(self):
        """Every ToolNode subclass must resolve ``interpret_result`` to
        the ToolNode override (or further override it). If a subclass
        accidentally reverts to BaseNode's contract, every flat-dict
        result is misclassified as failure."""
        from services.plugin.base import BaseNode
        from services.plugin.tool import ToolNode

        for cls in _all_plugin_classes():
            if not issubclass(cls, ToolNode):
                continue
            # Resolved method must come from ToolNode or a ToolNode
            # subclass — NEVER from BaseNode directly.
            owner = cls.interpret_result.__func__.__qualname__.split(".")[0]
            assert owner != BaseNode.__name__, (
                f"{cls.__qualname__}.interpret_result resolves to "
                f"BaseNode — flat-dict ToolNode results will be "
                f"misclassified as failure. Override on ToolNode or the "
                f"subclass must shadow the base method."
            )


class TestNeedsCanvasContract:
    """``needs_canvas`` is the declarative flag plugins opt into when
    their operations require the parent workflow's full canvas
    (``nodes`` + ``edges``) inside ``NodeContext``. The F4.B
    AgentWorkflow tool-dispatch path reads this attribute to decide
    whether to forward the canvas; default ``False`` keeps the regular-
    tool fast path canvas-free.
    """

    def test_basenode_default_is_false(self):
        from services.plugin.base import BaseNode

        assert BaseNode.needs_canvas is False, "BaseNode.needs_canvas must default to False — only canvas-mutating " "plugins (agentBuilder) opt in to True"

    def test_agent_builder_opts_in(self):
        """AgentBuilderNode walks edges to resolve its calling agent
        and mutates the canvas. Without ``needs_canvas = True`` the
        F4.B path drops the canvas and ``_resolve_caller`` falls back
        to self-as-caller — newly-spawned tools wire to agentBuilder
        instead of the parent AI Agent."""
        from nodes.tool.agent_builder import AgentBuilderNode

        assert AgentBuilderNode.needs_canvas is True

    def test_regular_tools_do_not_need_canvas(self):
        """Sanity check that the common case (calculatorTool,
        currentTimeTool, writeTodos, …) does NOT leak the canvas into
        their per-tool activity context."""
        from services.plugin.tool import ToolNode

        for cls in _all_plugin_classes():
            if not issubclass(cls, ToolNode):
                continue
            if cls.type == "agentBuilder":
                continue
            assert cls.needs_canvas is False, (
                f"{cls.__qualname__} declares needs_canvas=True but isn't agentBuilder. "
                f"Add the plugin to the canvas-aware allowlist in the "
                f"AgentWorkflow tool-dispatch comment if this is intentional, "
                f"or remove the override if accidental."
            )


class TestTriggerRegistryAutoPopulate:
    """Wave 11.D.11: every event-mode TriggerNode plugin must register
    itself into event_waiter.TRIGGER_REGISTRY + FILTER_BUILDERS
    (hardcoded entries still win so this is a superset check)."""

    def test_every_event_trigger_has_event_type(self):
        from services.plugin.trigger import TriggerNode

        for cls in _all_plugin_classes():
            if not issubclass(cls, TriggerNode):
                continue
            if getattr(cls, "mode", "event") != "event":
                continue
            assert cls.event_type, (
                f"{cls.__qualname__} (TriggerNode, mode=event) is missing " "event_type — event_waiter won't auto-populate for it"
            )

    def test_event_triggers_populate_registry(self):
        from services import event_waiter
        from services.plugin.trigger import TriggerNode

        for cls in _all_plugin_classes():
            if not issubclass(cls, TriggerNode):
                continue
            if getattr(cls, "mode", "event") != "event" or not cls.event_type:
                continue
            cfg = event_waiter.get_trigger_config(cls.type)
            assert cfg is not None, f"{cls.__qualname__}: get_trigger_config('{cls.type}') returned None"
            assert cfg.event_type == cls.event_type
