"""Invariant: only sanctioned ``print()`` calls are allowed in server code.

The supervisor (``cli/colors.py``) prepends ``[HH:MM:SS.fff]`` to every
aggregated line and ``configure_logging`` is the single source of truth
for structured logs. Stray ``print()`` calls bypass structlog's
processor chain — they don't get the level tag, don't get
``contextvars`` (``workflow_id`` / ``node_id``), don't go to the
RotatingFileHandler, and don't surface in the WebSocket Terminal panel.

Three sanctioned exemptions, each documented in its own module:

1. ``main._startup_log`` — pre-logger boot-progress markers.
2. ``core.container._clog`` — same role for DI-container bootstrap.
3. ``nodes.code.python_executor.captured_print`` — the sandboxed
   ``print`` builtin we hand to user code (the user's print, not ours).

Anything else must go through ``logger = get_logger(__name__)``. This
test parses every ``.py`` file under ``server/`` (excluding ``.venv``,
``tests/``, ``scripts/``, and the docs-only ``skills/`` markdown trees)
and walks each ``ast.Call`` node looking for bare ``print(...)``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List, Tuple

SERVER_ROOT = Path(__file__).resolve().parents[1]

# Directories whose ``print()`` calls are out of scope.
_EXCLUDED_DIRS = {
    ".venv",
    "tests",  # tests themselves can print freely
    "scripts",  # CLI smoke tests are intentional stdout tools
    "skills",  # SKILL.md markdown — not Python source
    "__pycache__",
}

# Each entry: (relative module path, name of the enclosing function/method
# whose body is allowed to contain ``print(``). The function name is
# matched against the *deepest enclosing* ``FunctionDef`` for each call
# site, so a stray top-level ``print()`` in the same file still fails.
_SANCTIONED: List[Tuple[str, str]] = [
    ("main.py", "_startup_log"),
    ("core/container.py", "_clog"),
    ("nodes/code/python_executor/__init__.py", "captured_print"),
    # Temporal CLI shims — supervised subprocess + build-time installer.
    # These run BEFORE the structlog pipeline is configured in their own
    # subprocess (same role as ``_startup_log`` / ``_clog`` for the main
    # process). Stdout markers are consumed by the supervisor's
    # line-prefixer (``cli/colors.py``), which adds the timestamp tag.
    ("services/temporal/_supervised_runtime.py", "_run"),
    ("services/temporal/_supervised_runtime.py", "main"),
    ("services/temporal/_install.py", "_main"),
]


def _iter_python_files() -> List[Path]:
    """Yield every .py file under ``server/`` outside the exclusion set."""
    files: List[Path] = []
    for path in SERVER_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(SERVER_ROOT).parts
        if any(part in _EXCLUDED_DIRS for part in rel_parts):
            continue
        files.append(path)
    return files


def _enclosing_function_name(tree: ast.AST, target: ast.AST) -> str:
    """Return the name of the deepest ``FunctionDef`` ancestor of ``target``.

    Returns ``""`` when the call is at module level (no enclosing
    function) — those calls are always violations.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    node: ast.AST | None = target
    while node is not None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
        node = parents.get(node)
    return ""


def test_no_raw_prints_in_server_code() -> None:
    """Every ``print(`` call in server runtime code is in an allow-listed body."""
    # Multimap: a single module may sanction multiple entry-point bodies
    # (e.g. ``_supervised_runtime`` has both ``_run`` and ``main``).
    sanctioned_map: dict[str, set[str]] = {}
    for rel, fn in _SANCTIONED:
        sanctioned_map.setdefault(rel.replace("\\", "/"), set()).add(fn)
    violations: List[str] = []

    for py in _iter_python_files():
        rel = py.relative_to(SERVER_ROOT).as_posix()
        try:
            source = py.read_text(encoding="utf-8")
        except OSError:
            continue

        try:
            tree = ast.parse(source, filename=str(py))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "print"):
                continue

            enclosing = _enclosing_function_name(tree, node)
            allowed_fns = sanctioned_map.get(rel)
            if allowed_fns is not None and enclosing in allowed_fns:
                continue

            violations.append(
                f"  {rel}:{node.lineno} — print() inside {enclosing or '<module>'}() "
                f"(use ``logger = get_logger(__name__); logger.info/warning/error(...)`` "
                f"or one of the sanctioned helpers _startup_log / _clog)"
            )

    assert not violations, (
        "Raw ``print()`` calls bypass the structlog pipeline (no level tag, "
        "no contextvars, no file handler, no WS Terminal panel). Convert "
        "to ``logger.<level>(...)`` or move into a sanctioned helper:\n\n" + "\n".join(violations)
    )
