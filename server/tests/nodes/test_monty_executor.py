"""Contract tests for the montyExecutor node (sandboxed Python via Monty).

Tier A (always runs): drives the node through NodeExecutor with a FAKE
`pydantic_monty` module injected into sys.modules. The fake mirrors the real
v0.0.18 surface the plugin depends on (Monty(code, inputs=...), .run(*, inputs,
limits, external_functions, print_callback, mount), CollectString().output,
ResourceLimits, MountDir, and the MontyError/MontyRuntimeError/MontySyntaxError
hierarchy). This keeps CI deterministic regardless of whether the real wheel is
present.

Tier B (skipif no real package): exercises the actual pydantic-monty interpreter
to prove the headline behaviours — enforced timeout, the supported subset, and
the unsupported-feature error path.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import patch

import pytest


pytestmark = pytest.mark.node_contract


# ============================================================================
# Fake pydantic_monty (Tier A)
# ============================================================================


def _make_fake_monty_module() -> types.ModuleType:
    mod = types.ModuleType("pydantic_monty")

    class MontyError(Exception):
        pass

    class MontyRuntimeError(MontyError):
        pass

    class MontySyntaxError(MontyError):
        pass

    class MontyTypingError(MontyError):
        pass

    class MontyComplete:
        def __init__(self, output):
            self.output = output

    class CollectString:
        def __init__(self):
            self.output = ""

    def ResourceLimits(**kwargs):  # real one is a TypedDict -> returns a dict
        return dict(kwargs)

    class MountDir:
        def __init__(self, virtual_path, host_path, *, mode="read-only", write_bytes_limit=None):
            self.virtual_path = virtual_path
            self.host_path = host_path
            self.mode = mode

    class Monty:
        """Behaviour is keyed off sentinel substrings in the code string."""

        last_instance = None

        def __init__(self, code, *, inputs=None, **kwargs):
            self.code = code
            self.inputs = inputs
            self.last_inputs = None
            self.last_limits = None
            self.last_external_functions = None
            self.last_mount = None
            Monty.last_instance = self
            if "SYNTAX" in code:
                raise MontySyntaxError("Expected an expression")

        def run(self, *, inputs=None, limits=None, external_functions=None, print_callback=None, mount=None, os=None):
            self.last_inputs = inputs
            self.last_limits = limits
            self.last_external_functions = external_functions
            self.last_mount = mount
            if print_callback is not None:
                print_callback.output = "console-out\n"
            if "TIMEOUT" in self.code:
                raise MontyRuntimeError("TimeoutError: time limit exceeded: 1001ms > 1000ms")
            if "MEMORY" in self.code:
                raise MontyRuntimeError("MemoryError: memory limit exceeded: 9 bytes > 8 bytes")
            if "UNSUPPORTED" in self.code:
                raise MontyRuntimeError(
                    "NotImplementedError: The monty syntax parser does not yet support class definitions"
                )
            if "RUNTIME" in self.code:
                raise MontyRuntimeError("NameError: name 'x' is not defined")
            return {"echo": (inputs or {}).get("input_data")}

    mod.MontyError = MontyError
    mod.MontyRuntimeError = MontyRuntimeError
    mod.MontySyntaxError = MontySyntaxError
    mod.MontyTypingError = MontyTypingError
    mod.MontyComplete = MontyComplete
    mod.CollectString = CollectString
    mod.ResourceLimits = ResourceLimits
    mod.MountDir = MountDir
    mod.Monty = Monty
    return mod


@pytest.fixture
def fake_monty():
    mod = _make_fake_monty_module()
    with patch.dict(sys.modules, {"pydantic_monty": mod}):
        yield mod


# ============================================================================
# Tier A — fake-backed contract tests
# ============================================================================


class TestMontyExecutor:
    async def test_empty_code_rejected(self, harness, fake_monty):
        result = await harness.execute("montyExecutor", {"code": "   \n\t"})
        harness.assert_envelope(result, success=False)
        assert "no code provided" in result["error"].lower()

    async def test_package_missing_graceful_error(self, harness):
        # sys.modules[name] = None makes `import name` raise ImportError.
        with patch.dict(sys.modules, {"pydantic_monty": None}):
            result = await harness.execute("montyExecutor", {"code": "1 + 1"})
        harness.assert_envelope(result, success=False)
        assert result.get("error_type") == "NodeUserError"
        assert "pydantic-monty" in result["error"] or "install" in result["error"].lower()

    async def test_happy_path_reads_input_and_returns(self, harness, fake_monty):
        # connected_outputs are keyed by SOURCE TYPE (see node_executor).
        upstream = {"source_A::output_main": {"n": 5}}
        nodes = [
            {"id": "m1", "type": "montyExecutor"},
            {"id": "source_A", "type": "start"},
        ]
        edges = [{"source": "source_A", "target": "m1", "sourceHandle": "output-main"}]

        result = await harness.execute(
            "montyExecutor",
            {"code": "input_data"},
            node_id="m1",
            upstream_outputs=upstream,
            nodes=nodes,
            edges=edges,
        )

        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(result, ["output", "console_output"])
        payload = result["result"]
        assert payload["output"] == {"echo": {"start": {"n": 5}}}
        assert "console-out" in payload["console_output"]

    async def test_input_data_wired_into_monty(self, harness, fake_monty):
        # Regression-guards the _NEEDS_CONNECTED_OUTPUTS edit: without it,
        # input_data would arrive empty.
        upstream = {"source_A::output_main": {"k": "v"}}
        nodes = [
            {"id": "m1", "type": "montyExecutor"},
            {"id": "source_A", "type": "start"},
        ]
        edges = [{"source": "source_A", "target": "m1", "sourceHandle": "output-main"}]

        await harness.execute(
            "montyExecutor",
            {"code": "input_data"},
            node_id="m1",
            upstream_outputs=upstream,
            nodes=nodes,
            edges=edges,
        )

        inst = fake_monty.Monty.last_instance
        assert inst.inputs == ["input_data"]
        assert inst.last_inputs == {"input_data": {"start": {"k": "v"}}}
        # ResourceLimits carried the enforced bounds (defaults).
        assert inst.last_limits["max_duration_secs"] == 30
        assert inst.last_limits["max_memory"] == 256 * 1024 * 1024

    async def test_timeout_maps_to_user_error(self, harness, fake_monty):
        result = await harness.execute("montyExecutor", {"code": "TIMEOUT", "timeout": 1})
        harness.assert_envelope(result, success=False)
        assert result.get("error_type") == "NodeUserError"
        assert "limit exceeded" in result["error"].lower()

    async def test_memory_limit_maps_to_user_error(self, harness, fake_monty):
        result = await harness.execute("montyExecutor", {"code": "MEMORY"})
        harness.assert_envelope(result, success=False)
        assert "memory limit" in result["error"].lower()

    async def test_unsupported_feature_points_to_python_code(self, harness, fake_monty):
        result = await harness.execute("montyExecutor", {"code": "UNSUPPORTED"})
        harness.assert_envelope(result, success=False)
        assert "python_code" in result["error"]
        assert "does not yet support" in result["error"]

    async def test_syntax_error_maps_to_user_error(self, harness, fake_monty):
        result = await harness.execute("montyExecutor", {"code": "SYNTAX"})
        harness.assert_envelope(result, success=False)
        assert "syntaxerror" in result["error"].lower()

    async def test_unknown_capability_rejected(self, harness, fake_monty):
        result = await harness.execute("montyExecutor", {"code": "1", "capabilities": ["bogus"]})
        harness.assert_envelope(result, success=False)
        assert "unknown capability" in result["error"].lower()
        assert "http_get" in result["error"]

    async def test_no_capabilities_grants_nothing(self, harness, fake_monty):
        await harness.execute("montyExecutor", {"code": "1"})
        inst = fake_monty.Monty.last_instance
        assert inst.last_external_functions is None
        assert inst.last_mount is None

    async def test_capabilities_wire_grants(self, harness, fake_monty):
        ctx = harness.build_context(workspace_dir="/tmp/ws")
        await harness.execute(
            "montyExecutor",
            {"code": "1", "capabilities": ["http_get", "workspace_write"]},
            node_id="m1",
            context=ctx,
        )
        inst = fake_monty.Monty.last_instance
        assert "http_get" in (inst.last_external_functions or {})
        assert inst.last_mount is not None
        assert inst.last_mount.mode == "read-write"
        assert inst.last_mount.virtual_path == "/workspace"
        assert inst.last_mount.host_path == "/tmp/ws"

    async def test_workspace_read_only_mode(self, harness, fake_monty):
        ctx = harness.build_context(workspace_dir="/tmp/ws")
        await harness.execute(
            "montyExecutor",
            {"code": "1", "capabilities": ["workspace_read"]},
            node_id="m1",
            context=ctx,
        )
        inst = fake_monty.Monty.last_instance
        assert inst.last_mount.mode == "read-only"

    async def test_workspace_capability_without_workspace_dir_errors(self, harness, fake_monty):
        result = await harness.execute(
            "montyExecutor",
            {"code": "1", "capabilities": ["workspace_read"]},
        )
        harness.assert_envelope(result, success=False)
        assert "workspace" in result["error"].lower()


# ============================================================================
# Tier B — real pydantic-monty (skipped when the wheel is absent)
# ============================================================================


@pytest.mark.skipif(
    importlib.util.find_spec("pydantic_monty") is None,
    reason="pydantic-monty not installed",
)
class TestMontyExecutorReal:
    async def test_real_happy_path(self, harness):
        result = await harness.execute("montyExecutor", {"code": "print('hi')\n40 + 2"})
        harness.assert_envelope(result, success=True)
        assert result["result"]["output"] == 42
        assert "hi" in result["result"]["console_output"]

    async def test_real_enforced_timeout(self, harness):
        result = await harness.execute(
            "montyExecutor", {"code": "while True:\n    pass", "timeout": 1}
        )
        harness.assert_envelope(result, success=False)
        assert "limit" in result["error"].lower()

    async def test_real_unsupported_feature(self, harness):
        result = await harness.execute("montyExecutor", {"code": "class Foo:\n    pass"})
        harness.assert_envelope(result, success=False)
        assert "python_code" in result["error"]

    async def test_real_curated_stdlib_import(self, harness):
        result = await harness.execute("montyExecutor", {"code": "import math\nmath.factorial(5)"})
        harness.assert_envelope(result, success=True)
        assert result["result"]["output"] == 120
