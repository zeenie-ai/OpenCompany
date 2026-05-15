"""Smoke tests for ``cli.commands.docs``."""

from __future__ import annotations

from pathlib import Path

from cli.commands import docs


def test_read_h1_extracts_first_h1(tmp_path: Path):
    f = tmp_path / "x.md"
    f.write_text("# My Title\n\nbody\n## sub\n", encoding="utf-8")
    assert docs._read_h1(f) == "My Title"


def test_read_h1_falls_back_to_stem(tmp_path: Path):
    f = tmp_path / "fallback.md"
    f.write_text("no heading here", encoding="utf-8")
    assert docs._read_h1(f) == "fallback"


def test_list_categories_skips_underscore_and_returns_sorted(tmp_path: Path):
    docs_dir = tmp_path / "docs-internal" / "node-logic-flows"
    (docs_dir / "z_cat").mkdir(parents=True)
    (docs_dir / "a_cat").mkdir()
    (docs_dir / "_skipped").mkdir()
    (docs_dir / "not-a-dir.md").write_text("")
    assert docs._list_categories(docs_dir) == ["a_cat", "z_cat"]


def test_list_docs_excludes_readme_and_underscore_files(tmp_path: Path):
    docs_dir = tmp_path / "x"
    cat = docs_dir / "cat"
    cat.mkdir(parents=True)
    (cat / "node1.md").write_text("# Node One\n")
    (cat / "_helper.md").write_text("")
    (cat / "README.md").write_text("")
    (cat / "ignore.txt").write_text("")
    out = docs._list_docs(docs_dir, "cat")
    assert [d["node_key"] for d in out] == ["node1"]
    assert out[0]["title"] == "Node One"


def test_build_index_block_omits_empty_categories(tmp_path: Path):
    docs_dir = tmp_path / "x"
    full = docs_dir / "full"
    full.mkdir(parents=True)
    (full / "node.md").write_text("# Heading\n")
    empty = docs_dir / "empty"
    empty.mkdir()
    block = docs._build_index_block(docs_dir)
    assert "### full" in block
    assert "### empty" not in block
    assert block.startswith(docs._START)
    assert block.endswith(docs._END)


def test_collect_registry_keys_scrapes_type_attribute(tmp_path: Path):
    plugin = tmp_path / "category" / "node.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        'class FooNode:\n'
        '    type: str = "fooNode"\n'
        '    other = "ignored"\n',
        encoding="utf-8",
    )
    keys = docs._collect_registry_keys(tmp_path)
    assert keys == {"fooNode"}


def test_collect_registry_keys_skips_underscore_and_init(tmp_path: Path):
    cat = tmp_path / "category"
    cat.mkdir(parents=True)
    (cat / "_base.py").write_text('    type = "skipMe"\n')
    (cat / "__init__.py").write_text('    type = "alsoSkip"\n')
    (cat / "good.py").write_text('    type = "goodNode"\n')
    assert docs._collect_registry_keys(tmp_path) == {"goodNode"}
