"""Invariant: only ``core/approot.py`` may locate the application root.

The backend ships as a sibling tree (``<root>/{server, client/dist,
.env.template, package.json, .opencompany/workflows}``) that a source
checkout, the npm tarball and the desktop bundle all share — but the
desktop bundle needs to *override* where that root is
(``OPENCOMPANY_APP_ROOT``) and where the writable ``.env`` lives
(``OPENCOMPANY_ENV_FILE``). That only works if exactly one module computes
those locations. Historically ``core/paths.py``, ``core/env_defaults.py``
and ``main.py`` each climbed ``Path(__file__).resolve().parents[N]`` on
their own; this test keeps that from coming back.

Rule: a module at ``server/a/b/c.py`` may reference ``parents[N]`` only for
``N < depth`` (depth = number of path components below ``server/``, here
3), and ``.parent`` chains of at most ``depth`` links — anything longer
reaches ``server/``'s parent, i.e. the app root. Climbing *to* ``server/``
(config files, skills, the Node sidecar) stays legal: that directory ships
inside the bundle unchanged.

Sanctioned: ``core/approot.py`` and explicitly identified dependency-package
manifests. A dependency manifest allowance never permits climbing above
``server/`` or spelling another sibling-layout path. Same AST-walk style as
``tests/test_no_raw_prints.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List

from tests._source_tree import SERVER_ROOT, server_python_files

# Top-level trees that are not backend modules. The walker also skips the
# virtualenv and runtime data such as the Mobile phone runtime.
_EXCLUDED_TOP_DIRS = ("tests", "scripts", "skills", "nodejs", "static")

_SANCTIONED = {
    "core/approot.py",
    # Writes the shared packages tree's own manifest under DATA_DIR
    # (a bun project file, unrelated to the app root's package.json).
    "core/js_runtime.py",
}

# Literal path joins that encode the sibling layout; only approot may spell them.
_LAYOUT_LITERALS = {("client", "dist"), (".env.template",), ("package.json",)}

# Pi checks the pinned SDK's own manifest in server/pi's dependency tree
# before running the host from source. That is not the application-version
# manifest owned by core.approot. Allow only this exact literal suffix in
# this reader; all other joins and root climbs stay checked.
_PRIVATE_PACKAGE_MANIFEST_JOINS = {
    "services/pi_runtime.py": ("node_modules", "@earendil-works", "pi-coding-agent", "package.json"),
}


def _is_file_anchor(node: ast.AST) -> bool:
    """True for ``Path(__file__)`` and ``Path(__file__).resolve()`` chains."""
    while isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in {"resolve", "absolute"}:
            node = func.value
            continue
        if isinstance(func, ast.Name) and func.id in {"Path", "_SpaPath"}:
            return bool(node.args) and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__"
        if isinstance(func, ast.Attribute) and func.attr == "Path":
            return bool(node.args) and isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__"
        return False
    return False


def _parent_chain_length(node: ast.AST) -> tuple[int, ast.AST]:
    """Count consecutive ``.parent`` attribute hops and return the base."""
    count = 0
    while isinstance(node, ast.Attribute) and node.attr == "parent":
        count += 1
        node = node.value
    return count, node


def _violations_in(path: Path, tree: ast.AST) -> List[str]:
    rel = path.relative_to(SERVER_ROOT).as_posix()
    depth = len(path.relative_to(SERVER_ROOT).parts)
    out: List[str] = []
    for node in ast.walk(tree):
        # Path(__file__)...parents[N]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "parents":
            if _is_file_anchor(node.value.value):
                idx = node.slice
                if isinstance(idx, ast.Constant) and isinstance(idx.value, int) and idx.value >= depth:
                    out.append(
                        f"  {rel}:{node.lineno} — Path(__file__).parents[{idx.value}] climbs above server/ "
                        f"(use core.approot.app_root() / client_dist() / env_file_path())"
                    )
        # Path(__file__).parent.parent...
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            hops, base = _parent_chain_length(node)
            if hops > depth and _is_file_anchor(base):
                out.append(
                    f"  {rel}:{node.lineno} — {hops} x .parent climbs above server/ (use core.approot)"
                )
        # "client" / "dist", ".env.template", "package.json" spelled as path joins
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            parts: list[str] = []
            cur: ast.AST = node
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                if isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                    parts.insert(0, cur.right.value)
                cur = cur.left
            for literal in _LAYOUT_LITERALS:
                n = len(literal)
                private_manifest = _PRIVATE_PACKAGE_MANIFEST_JOINS.get(rel)
                if (literal == ("package.json",) and private_manifest is not None
                        and tuple(parts[-len(private_manifest):]) == private_manifest):
                    continue
                if any(tuple(parts[i : i + n]) == literal for i in range(len(parts) - n + 1)):
                    out.append(
                        f"  {rel}:{node.lineno} — path join spells {'/'.join(literal)!r}; "
                        f"the sibling layout is owned by core.approot"
                    )
    return out


def test_only_approot_locates_the_app_root() -> None:
    violations: List[str] = []
    for py in server_python_files(*_EXCLUDED_TOP_DIRS):
        rel = py.relative_to(SERVER_ROOT).as_posix()
        if rel in _SANCTIONED:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except (OSError, SyntaxError):
            continue
        violations.extend(_violations_in(py, tree))
    assert not violations, (
        "Only core/approot.py may compute the application root or spell the "
        "sibling layout — the desktop bundle overrides both via "
        "OPENCOMPANY_APP_ROOT / OPENCOMPANY_CLIENT_DIST / OPENCOMPANY_ENV_FILE:\n\n" + "\n".join(violations)
    )


def test_approot_is_the_only_sanctioned_module_and_exists() -> None:
    for rel in _SANCTIONED:
        assert (SERVER_ROOT / rel).is_file(), rel


def test_climbing_to_server_root_is_still_allowed() -> None:
    """Regression guard for the rule itself: ``nodes/x/y.py`` -> ``parents[2]``
    is ``server/`` and must NOT be flagged, while ``parents[3]`` must."""
    fake = SERVER_ROOT / "nodes" / "fake" / "mod.py"  # depth 3
    ok = "from pathlib import Path\nCFG = Path(__file__).resolve().parents[2] / 'config' / 'x.json'\n"
    assert _violations_in(fake, ast.parse(ok)) == []
    bad = "from pathlib import Path\nROOT = Path(__file__).resolve().parents[3]\n"
    assert len(_violations_in(fake, ast.parse(bad))) == 1
    chain_ok = "from pathlib import Path\nSRV = Path(__file__).parent.parent.parent\n"
    assert _violations_in(fake, ast.parse(chain_ok)) == []
    chain_bad = "from pathlib import Path\nROOT = Path(__file__).parent.parent.parent.parent\n"
    assert len(_violations_in(fake, ast.parse(chain_bad))) == 1
    literal_bad = "from pathlib import Path\nD = Path('x') / 'client' / 'dist'\n"
    assert len(_violations_in(fake, ast.parse(literal_bad))) == 1


def test_pi_private_manifest_allowance_keeps_app_root_checks() -> None:
    pi_reader = SERVER_ROOT / "services" / "pi_runtime.py"
    suffix = " / 'node_modules' / '@earendil-works' / 'pi-coding-agent' / 'package.json'"
    for base in ("root", "target", "temporary"):
        assert _violations_in(pi_reader, ast.parse(f"manifest = {base}{suffix}\n")) == []

    # The exception is for one dependency manifest, not the whole module,
    # arbitrary package.json files, or application-root computation.
    for source in (
        "manifest = root / 'package.json'\n",
        "manifest = root / 'node_modules' / 'another-package' / 'package.json'\n",
        "template = root / '.env.template'\n",
        "client = root / 'client' / 'dist'\n",
        f"manifest = Path(__file__).resolve().parents[2]{suffix}\n",
        f"manifest = Path(__file__).parent.parent.parent{suffix}\n",
    ):
        assert _violations_in(pi_reader, ast.parse(source)), source

    other_reader = SERVER_ROOT / "services" / "another_reader.py"
    assert _violations_in(other_reader, ast.parse(f"manifest = root{suffix}\n"))
