"""RFC-0003 source gates: rules a code review would otherwise have to catch.

AST scans in the same style as ``test_plugin_shape.py``. Each test names the
decision it locks, so a failure says which rule a change broke.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

SERVER_DIR = Path(__file__).resolve().parents[2]
LLM_DIR = SERVER_DIR / "services" / "llm"
MODEL_NODES_DIR = SERVER_DIR / "nodes" / "model"
ENDPOINTS_PY = LLM_DIR / "endpoints.py"
LOCAL_VALIDATOR_PY = MODEL_NODES_DIR / "_local_validator.py"
ONEPASSWORD_ENROLLMENT_PY = SERVER_DIR / "services" / "credentials" / "enrollment.py"

# The two places allowed to touch a "/v1" path segment (AG2): the save-time
# resolver, and the native-host derivation used only by native probes.
V1_ALLOWED_FILES = {ENDPOINTS_PY}
V1_ALLOWED_FUNCTIONS = {(LOCAL_VALIDATOR_PY, "_strip_v1_path")}

# Keys in llm_defaults.json that nothing reads. Listed so a NEW dead key
# fails AG9; tracked for removal in RFC-0003 §14.
KNOWN_DEAD_JSON_KEYS = {"api_key_param", "max_tokens_param"}


def _python_files(*roots: Path) -> Iterator[Path]:
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def _nodes_with_function(tree: ast.AST) -> Iterator[Tuple[ast.AST, Optional[str]]]:
    """Every node, paired with the name of the innermost enclosing function."""

    def walk(node: ast.AST, function: Optional[str]) -> Iterator[Tuple[ast.AST, Optional[str]]]:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            yield child, name
            yield from walk(child, name)

    yield from walk(tree, None)


def _allowed(path: Path, function: Optional[str]) -> bool:
    return path in V1_ALLOWED_FILES or (path, function) in V1_ALLOWED_FUNCTIONS


def test_no_code_appends_or_strips_a_v1_segment_outside_the_allowlist():
    """AG1 / AG2 / D1: a configured base URL is used verbatim.

    Full vendor URLs (``https://openrouter.ai/api/v1``) are declarations and
    allowed; what is forbidden is a ``/v1`` *fragment*, which only code that
    appends or strips a segment needs, and ``rstrip("/")`` normalization.
    """
    violations: List[str] = []
    for path in _python_files(LLM_DIR, MODEL_NODES_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node, function in _nodes_with_function(tree):
            if _allowed(path, function):
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("/v1"):
                violations.append(f"{path.relative_to(SERVER_DIR)}:{node.lineno} '/v1' fragment")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "rstrip"
                and any(isinstance(arg, ast.Constant) and arg.value == "/" for arg in node.args)
            ):
                violations.append(f"{path.relative_to(SERVER_DIR)}:{node.lineno} rstrip('/')")
    assert not violations, "RFC-0003 D1: never rewrite a base URL outside the resolver:\n" + "\n".join(violations)


def _provider_init_methods() -> Iterator[Tuple[Path, str, ast.FunctionDef]]:
    for path in _python_files(LLM_DIR / "providers"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
            for item in cls.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    yield path, cls.name, item


def test_no_provider_constructor_hardcodes_a_placeholder_key():
    """AG7 / D6: placeholders are declared in llm_defaults.json and resolved once."""
    placeholders = {"ollama", "lm-studio", "sk-no-key-required"}
    for path, class_name, init in _provider_init_methods():
        literals = {n.value for n in ast.walk(init) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not literals & placeholders, f"{path.name}:{class_name}.__init__ hardcodes {literals & placeholders}"


def test_client_construction_does_no_io():
    """AG4: rooting happens at save time; building a client only reads the result."""
    for path, class_name, init in _provider_init_methods():
        assert not any(isinstance(n, ast.Await) for n in ast.walk(init)), f"{path.name}:{class_name}.__init__ awaits"

    for path in _python_files(SERVER_DIR / "services", SERVER_DIR / "nodes"):
        if path in (ENDPOINTS_PY, LOCAL_VALIDATOR_PY, ONEPASSWORD_ENROLLMENT_PY):
            continue
        assert "resolve_base_url" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(SERVER_DIR)} probes a base URL; only the save path may"
        )


def test_provider_spec_gained_no_field():
    """AG12 / D11: per-provider facts are declared in JSON, not on the spec."""
    from services.provider_registry import ProviderSpec

    assert {f.name for f in dataclasses.fields(ProviderSpec)} == {
        "name",
        "factory",
        "sdk_exception_refs",
        "client_kwargs",
    }


def test_every_llm_defaults_key_has_a_reader():
    """AG9 / D8: a flag ships only with a consumer.

    Every provider-block key must appear as a string literal somewhere in the
    server's code. A key nothing reads is how configuration goes quietly dead.
    """
    blocks = json.loads((SERVER_DIR / "config" / "llm_defaults.json").read_text(encoding="utf-8"))["providers"]
    keys = {key for block in blocks.values() for key in block if not key.startswith("_")}

    literals = set()
    for path in _python_files(SERVER_DIR / "services", SERVER_DIR / "nodes", SERVER_DIR / "routers"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literals |= {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    unread = keys - literals - KNOWN_DEAD_JSON_KEYS
    assert not unread, f"llm_defaults.json keys nothing reads: {sorted(unread)}"
    assert KNOWN_DEAD_JSON_KEYS <= keys, "a known-dead key was removed; drop it from KNOWN_DEAD_JSON_KEYS"
