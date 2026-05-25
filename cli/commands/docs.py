"""``machina docs nodes`` -- replaces ``scripts/build-node-docs-index.js``.

Walks ``docs-internal/node-logic-flows/<category>/<node>.md``, scrapes
each doc's first H1, and rewrites the AUTO-GENERATED-INDEX block in
``docs-internal/node-logic-flows/README.md``. With ``--check`` instead
of rewriting, cross-references against the plugin tree under
``server/nodes/`` and exits non-zero if any registered node lacks a
doc.
"""

from __future__ import annotations

import re
from pathlib import Path

import typer

from cli.colors import console
from cli.platform_ import project_root, server_dir


app = typer.Typer(
    name="docs",
    help="Documentation-tooling subcommands.",
    no_args_is_help=True,
    add_completion=False,
)


_START = "<!-- AUTO-GENERATED-INDEX-START -->"
_END = "<!-- AUTO-GENERATED-INDEX-END -->"
_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
# Mirror the existing JS regex: ``    type[: str] = "<nodeType>"`` at
# class-attribute indentation.
_TYPE_ATTR = re.compile(
    r"^\s{4}type\s*(?::\s*str\s*)?=\s*['\"]([A-Za-z_][\w]*)['\"]",
    re.MULTILINE,
)


def _docs_dir(root: Path) -> Path:
    return root / "docs-internal" / "node-logic-flows"


def _read_h1(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _H1.search(text)
    return match.group(1).strip() if match else path.stem


def _list_categories(docs_dir: Path) -> list[str]:
    if not docs_dir.exists():
        return []
    return sorted(
        d.name for d in docs_dir.iterdir() if d.is_dir() and not d.name.startswith("_")
    )


def _list_docs(docs_dir: Path, category: str) -> list[dict]:
    cat_dir = docs_dir / category
    out: list[dict] = []
    for path in sorted(cat_dir.iterdir()):
        if path.suffix != ".md":
            continue
        if path.name.startswith("_") or path.name == "README.md":
            continue
        out.append(
            {
                "rel_path": f"{category}/{path.name}",
                "title": _read_h1(path),
                "node_key": path.stem,
            }
        )
    return out


def _build_index_block(docs_dir: Path) -> str:
    lines = [_START]
    for cat in _list_categories(docs_dir):
        docs = _list_docs(docs_dir, cat)
        if not docs:
            continue
        lines.extend(["", f"### {cat}", ""])
        for doc in docs:
            lines.append(f"- [{doc['title']}](./{doc['rel_path']})")
    lines.extend(["", _END])
    return "\n".join(lines)


def _collect_documented_keys(docs_dir: Path) -> set[str]:
    return {
        doc["node_key"]
        for cat in _list_categories(docs_dir)
        for doc in _list_docs(docs_dir, cat)
    }


def _collect_registry_keys(plugins_dir: Path) -> set[str]:
    """Scrape ``type = "<nodeType>"`` from every plugin file."""
    keys: set[str] = set()
    if not plugins_dir.exists():
        return keys
    for path in plugins_dir.rglob("*.py"):
        if path.name == "__init__.py" or path.name.startswith("_"):
            continue
        match = _TYPE_ATTR.search(path.read_text(encoding="utf-8"))
        if match:
            keys.add(match.group(1))
    return keys


def _rewrite_index(root: Path) -> None:
    docs_dir = _docs_dir(root)
    index_file = docs_dir / "README.md"
    original = index_file.read_text(encoding="utf-8")
    block = _build_index_block(docs_dir)
    pattern = re.compile(re.escape(_START) + r".*?" + re.escape(_END), re.DOTALL)
    if not pattern.search(original):
        console.print(
            f"[red]error: {index_file} is missing the AUTO-GENERATED-INDEX markers[/]"
        )
        raise typer.Exit(code=2)
    index_file.write_text(pattern.sub(block, original), encoding="utf-8")
    console.print(f"wrote index to {index_file.relative_to(root)}")


def _check_completeness(root: Path) -> int:
    documented = _collect_documented_keys(_docs_dir(root))
    registered = _collect_registry_keys(server_dir(root) / "nodes")
    missing = sorted(k for k in registered if k not in documented)
    if not missing:
        console.print(
            f"OK: every registered node has a logic-flow doc ({len(registered)} total)"
        )
        return 0
    console.print(f"[red]MISSING DOCS for {len(missing)}/{len(registered)} nodes:[/]")
    for key in missing:
        console.print(f"  - {key}")
    return 1


@app.command("nodes", help="Rebuild (or --check) the per-node documentation index.")
def nodes(
    check: bool = typer.Option(
        False,
        "--check",
        help="Instead of rewriting, exit 1 if any registered node lacks a doc.",
    ),
) -> None:
    root = project_root()
    if check:
        rc = _check_completeness(root)
        if rc != 0:
            raise typer.Exit(code=rc)
    else:
        _rewrite_index(root)
