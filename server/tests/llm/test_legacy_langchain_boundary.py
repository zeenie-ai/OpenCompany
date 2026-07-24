"""Keep LangChain isolated to the pre-cutover Temporal compatibility path."""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable, NamedTuple


SERVER_ROOT = Path(__file__).resolve().parents[2]
LEGACY_IMPORT_SCOPES = {
    Path("services/ai.py"): {
        "TYPE_CHECKING",
        "_get_chat_anthropic",
        "_get_chat_groq",
        "_get_chat_cerebras",
        "_get_google_genai_class",
        "__getattr__",
        "_build_provider_class_map",
        "_build_provider_configs",
        "_run_agent_loop",
        "AIService.create_model",
    },
    Path("services/agent_runtime.py"): {
        "AgentToolSpec.to_langchain",
    },
    Path("services/temporal/agent_activities.py"): {
        "execute_llm_step",
    },
}


class _ImportUse(NamedTuple):
    module: str
    scope: str
    line: int


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.uses: list[_ImportUse] = []
        self._scope: list[str] = []

    @property
    def scope(self) -> str:
        return ".".join(self._scope) if self._scope else "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = (
            isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        )
        if is_type_checking:
            self._scope.append("TYPE_CHECKING")
        for statement in node.body:
            self.visit(statement)
        if is_type_checking:
            self._scope.pop()
        for statement in node.orelse:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        self.uses.extend(
            _ImportUse(alias.name, self.scope, node.lineno)
            for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.uses.append(
                _ImportUse(node.module, self.scope, node.lineno)
            )

    def visit_Call(self, node: ast.Call) -> None:
        module: str | None = None
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ):
            module = _literal_first_argument(node)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"import_module", "resolve_name"}
        ):
            module = _literal_first_argument(node)
        if module:
            # pkgutil.resolve_name accepts ``package:object`` references.
            self.uses.append(
                _ImportUse(module.split(":", 1)[0], self.scope, node.lineno)
            )
        self.generic_visit(node)


def _literal_first_argument(node: ast.Call) -> str | None:
    if not node.args:
        return None
    value = node.args[0]
    return value.value if isinstance(value, ast.Constant) and isinstance(
        value.value, str
    ) else None


def _third_party_imports(path: Path) -> Iterable[_ImportUse]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _ImportVisitor()
    visitor.visit(tree)
    return visitor.uses


def _is_forbidden(module: str) -> bool:
    return (
        module == "deepagents"
        or module.startswith("deepagents.")
        or module.startswith(("langgraph", "langsmith"))
    )


def test_langchain_is_confined_to_legacy_temporal_compatibility():
    violations: list[str] = []
    for path in SERVER_ROOT.rglob("*.py"):
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        relative = path.relative_to(SERVER_ROOT)
        allowed_scopes = LEGACY_IMPORT_SCOPES.get(relative, set())
        for use in _third_party_imports(path):
            if _is_forbidden(use.module):
                violations.append(
                    f"{relative}:{use.line} [{use.scope}]: {use.module}"
                )
            elif (
                use.module.startswith("langchain")
                and use.scope not in allowed_scopes
            ):
                violations.append(
                    f"{relative}:{use.line} [{use.scope}]: {use.module}"
                )

    assert not violations, (
        "LangChain may remain only in the explicitly temporary pre-cutover "
        "Temporal compatibility surface:\n" + "\n".join(sorted(violations))
    )


def test_native_agent_entrypoints_do_not_use_langchain_shaped_calls():
    tree = ast.parse(
        (SERVER_ROOT / "services" / "ai.py").read_text(encoding="utf-8")
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in {"execute_agent", "execute_chat_agent"}:
            continue
        for descendant in ast.walk(node):
            if (
                isinstance(descendant, ast.Attribute)
                and descendant.attr
                in {"ainvoke", "bind_tools", "get_input_schema"}
            ):
                violations.append(
                    f"{node.name}:{descendant.lineno}: {descendant.attr}"
                )
    assert not violations, (
        "Native agent entrypoints used a LangChain-shaped API:\n"
        + "\n".join(violations)
    )


def test_container_import_does_not_load_legacy_dependency_tree():
    code = """
import builtins
import sys

blocked = ("langchain", "langgraph", "langsmith", "deepagents")
real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.startswith(blocked):
        raise AssertionError(f"legacy dependency imported at boot: {name}")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import core.container  # noqa: F401
loaded = sorted(
    name for name in sys.modules if name.startswith(blocked)
)
assert not loaded, loaded
"""
    env = dict(os.environ)
    env["DEBUG"] = "false"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=SERVER_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
