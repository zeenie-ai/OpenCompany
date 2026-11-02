"""Command identity inventory; payload/retry/timeout changes do not alter it."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
COMMANDS = {"execute_activity", "start_activity", "execute_child_workflow", "start_child_workflow", "patched", "deprecate_patch"}
HANDLERS = {"signal", "update", "query"}


def _workflow_modules(source_root: Path) -> list[Path]:
    paths = sorted((source_root / "services" / "temporal").glob("*.py"))
    paths.append(source_root / "nodes" / "scheduler" / "cron_scheduler" / "_workflow.py")
    return paths


def _is_workflow_attr(node: ast.expr, names: set[str]) -> bool:
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "workflow" and node.attr in names


def handler_snapshot(source_root: Path = SERVER) -> dict:
    """Signal, Update and Query handler names per workflow type.

    A recorded Signal or Update names its handler, so renaming or removing
    one breaks open runs; adding one does not. Names default to the method
    name, as in the SDK.
    """
    result = {}
    for path in _workflow_modules(source_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        strings = _module_strings(tree)

        def option(call: ast.expr, key: str) -> object:
            for kw in getattr(call, "keywords", []):
                if kw.arg == key:
                    if isinstance(kw.value, ast.Constant):
                        return kw.value.value
                    return strings.get(kw.value.id, ast.unparse(kw.value)) if isinstance(kw.value, ast.Name) else ast.unparse(kw.value)
            return None

        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            defn = next((dec for dec in cls.decorator_list if _is_workflow_attr(dec.func if isinstance(dec, ast.Call) else dec, {"defn"})), None)
            if defn is None:
                continue
            handlers = []
            for method in cls.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in method.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    if _is_workflow_attr(target, HANDLERS):
                        name = "<dynamic>" if option(dec, "dynamic") else option(dec, "name") or method.name
                        handlers.append(f"{target.attr}:{name}")
            result[option(defn, "name") or cls.name] = sorted(handlers)
    return result


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "text"`` constants, which commands and decorators reference by name."""
    strings = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            strings.update({target.id: node.value.value for target in node.targets if isinstance(target, ast.Name)})
    return strings


def identity_snapshot(source_root: Path = SERVER) -> dict:
    result = {}
    for path in _workflow_modules(source_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        has_workflow = any(isinstance(node, ast.ClassDef) and any(
            isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "defn"
            for dec in node.decorator_list
        ) for node in tree.body)
        has_commands = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                           and isinstance(node.func.value, ast.Name) and node.func.value.id == "workflow"
                           and node.func.attr in COMMANDS for node in ast.walk(tree))
        if not has_workflow and not has_commands:
            continue
        strings = _module_strings(tree)
        commands, identifiers, bindings = [], [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in COMMANDS:
                if not node.args:
                    continue
                first = node.args[0]
                command = {"command": node.func.attr, "type": strings.get(first.id, ast.unparse(first)) if isinstance(first, ast.Name) else ast.unparse(first)}
                command.update({keyword.arg: ast.unparse(keyword.value) for keyword in node.keywords if keyword.arg in {"id", "activity_id"}})
                commands.append(command)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name.endswith("_id") or node.name.endswith("_identity")
            ):
                # Include helper bodies, because commands often reference a
                # computed local id rather than an inline format string.
                identifiers.append(ast.unparse(node))
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and (target.id.endswith("_id") or target.id.endswith("_identity")):
                        bindings.append({target.id: ast.unparse(node.value)})
        result[str(path.relative_to(source_root)).replace("\\", "/")] = {
            "commands": sorted(commands, key=lambda item: json.dumps(item, sort_keys=True)),
            "identifier_helpers": sorted(identifiers),
            "id_bindings": sorted(bindings, key=lambda item: json.dumps(item, sort_keys=True)),
        }
    return result


def _parsed(text: str) -> str:
    """A snippet's AST. ``ast.unparse`` quotes nested f-strings differently
    across 3.12 patch releases (3.12.8 writes ``f'{d['k']}'``, 3.12.15
    ``f"{d['k']}"``), so a snapshot recorded on one would not match on another."""
    try:
        return ast.dump(ast.parse(text))
    except SyntaxError:
        return text


def _sorted(items: list) -> list:
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))


def canonical(snapshot: dict) -> dict:
    """The snapshot as parsed code, the same on every 3.12 interpreter."""
    return {
        module: {
            "commands": _sorted([{key: value if key == "command" else _parsed(value) for key, value in item.items()}
                                 for item in entry["commands"]]),
            "identifier_helpers": sorted(_parsed(text) for text in entry["identifier_helpers"]),
            "id_bindings": _sorted([{name: _parsed(text) for name, text in item.items()} for item in entry["id_bindings"]]),
        }
        for module, entry in snapshot.items()
    }


def digest(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(canonical(snapshot), sort_keys=True).encode()).hexdigest()
