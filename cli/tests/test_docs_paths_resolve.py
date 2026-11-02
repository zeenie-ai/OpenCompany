"""Offline navigation and implementation-path checks for source Markdown.

Archived bodies remain historical; their current-successor notice and incoming
links still work. Prototype/planned material keeps navigation coverage without
asserting that illustrative source paths are shipped implementation files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.platform_ import project_root
from cli.tests.docs_checks import document_status, link_errors, markdown_paths, prose_code_spans

ROOT = project_root()
SEARCH_BASES = (ROOT, ROOT / "server", ROOT / "client", ROOT / "client" / "src")
_PATH = re.compile(
    r"^((?:server|client|cli|scripts|docs-internal|services|nodes|core|routers|src|"
    r"components|hooks|models|middleware|tests|config|skills|lib|store|stores|utils|"
    r"types|contexts|assets)/[A-Za-z0-9_./-]+"
    r"\.(?:py|ts|tsx|js|json|md|css|svg|toml|yml|yaml|html))$"
)
_ILLUSTRATIVE = {
    "server/nodes/search/acme_search.py",
    "server/nodes/search/acme_search/__init__.py",
}


@pytest.mark.parametrize("doc", markdown_paths(ROOT), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_markdown_links_and_anchors_resolve(doc: Path) -> None:
    errors = link_errors(ROOT, doc)
    assert not errors, f"{doc.relative_to(ROOT).as_posix()}:\n" + "\n".join(errors)


@pytest.mark.parametrize("doc", markdown_paths(ROOT), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_backtick_paths_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    if document_status(doc, text) != "current":
        return
    lines = text.splitlines()
    dead = []
    for lineno, span in prose_code_spans(text):
        if "docs-lint: ignore" in lines[lineno - 1]:
            continue
        match = _PATH.fullmatch(span)
        if not match:
            continue
        rel = match.group(1)
        if rel in _ILLUSTRATIVE or "/dist/" in rel or "..." in rel:
            continue
        # Package READMEs legitimately use paths relative to their own package.
        local_bases = [doc.parent, *[parent for parent in doc.parents if parent.is_relative_to(ROOT)]]
        if not any((base / rel).exists() for base in (*SEARCH_BASES, *local_bases)):
            dead.append(f"line {lineno}: {rel}")
    assert not dead, (
        f"{doc.relative_to(ROOT).as_posix()} names paths that do not exist:\n"
        + "\n".join(dead)
        + "\nFix the citation, or use docs-lint: ignore with a reason for an illustrative path."
    )
