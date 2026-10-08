"""Every repo path a live internal doc names must exist.

``docs-internal`` cites files in backticks (``server/services/foo.py``). When
the file moves, the citation silently rots; a September 2026 audit found
dozens of dead ones, some pointing at modules that never existed. This test
keeps that from recurring. It checks existence only -- never line numbers or
symbols, which drift with every commit and would make the test a nuisance.

Skipped on purpose:

- anything under ``docs-internal/ARCHIVE/`` and any doc whose first lines
  carry an ARCHIVED / HISTORICAL / Superseded banner -- those record the
  past and are expected to name things that are gone;
- paths containing ``<``, ``*`` or ``...`` (templates and globs);
- the ``_ILLUSTRATIVE`` set (recipe placeholders such as ``acme_search``);
- any line carrying ``docs-lint: ignore``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.platform_ import project_root

ROOT = project_root()
DOCS = ROOT / "docs-internal"
SEARCH_BASES = (ROOT, ROOT / "server", ROOT / "client", ROOT / "client" / "src")
SKIP_DIRS = {"ARCHIVE", "design-system", "diagram-generation"}

_BANNER = re.compile(r"ARCHIVED|HISTORICAL|Superseded", re.I)
_PATH = re.compile(
    r"`((?:server|client|cli|scripts|docs-internal|services|nodes|core|routers|src|"
    r"components|hooks|models|middleware|tests|config|skills|lib|store|stores|utils|"
    r"types|contexts|assets)/[A-Za-z0-9_./-]+"
    r"\.(?:py|ts|tsx|js|json|md|css|svg|toml|yml|yaml|html))`"
)

_ILLUSTRATIVE = {
    "server/nodes/search/acme_search.py",
    "server/nodes/search/acme_search/__init__.py",
}


def _live_docs() -> list[Path]:
    out: list[Path] = []
    for path in sorted(DOCS.rglob("*.md")):
        if SKIP_DIRS & set(path.relative_to(DOCS).parts):
            continue
        head = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[:12])
        if _BANNER.search(head):
            continue
        out.append(path)
    return out


def _resolves(rel: str) -> bool:
    return any((base / rel).exists() for base in SEARCH_BASES)


@pytest.mark.parametrize("doc", _live_docs(), ids=lambda p: p.relative_to(DOCS).as_posix())
def test_backtick_paths_resolve(doc: Path) -> None:
    dead: list[str] = []
    for lineno, line in enumerate(doc.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if "docs-lint: ignore" in line:
            continue
        for rel in _PATH.findall(line):
            if rel in _ILLUSTRATIVE or "<" in rel or "*" in rel or "..." in rel:
                continue
            if "/dist/" in rel:  # build output, exists only after a build
                continue
            if not _resolves(rel):
                dead.append(f"  line {lineno}: {rel}")
    assert not dead, (
        f"{doc.relative_to(ROOT).as_posix()} names paths that do not exist:\n"
        + "\n".join(dead)
        + "\nFix the citation, or mark the line with `docs-lint: ignore` if it is illustrative."
    )
