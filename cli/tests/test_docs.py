"""Smoke tests for ``cli.commands.docs``."""

from __future__ import annotations

from pathlib import Path

import pytest

from cli.commands import docs
from cli.platform_ import project_root


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
        "class FooNode:\n" '    type: str = "fooNode"\n' '    other = "ignored"\n',
        encoding="utf-8",
    )
    keys = docs._collect_registry_keys(tmp_path)
    assert keys == {"fooNode"}


def test_collect_registry_keys_skips_underscore_helpers(tmp_path: Path):
    cat = tmp_path / "category"
    cat.mkdir(parents=True)
    (cat / "_base.py").write_text('    type = "skipMe"\n')
    (cat / "_inline.py").write_text('    type = "alsoSkip"\n')
    (cat / "good.py").write_text('    type = "goodNode"\n')
    assert docs._collect_registry_keys(tmp_path) == {"goodNode"}


def test_collect_registry_keys_scans_init_files(tmp_path: Path):
    plugin = tmp_path / "group" / "plugin"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text(
        "class InitNode:\n" '    type = "initNode"\n',
        encoding="utf-8",
    )
    assert docs._collect_registry_keys(tmp_path) == {"initNode"}


def test_collect_registry_keys_collects_every_class_in_a_file(tmp_path: Path):
    plugin = tmp_path / "group" / "two.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        "class Receive:\n"
        '    type = "fooReceive"\n'
        "\n"
        "class Status:\n"
        '    type: str = "fooStatus"\n',
        encoding="utf-8",
    )
    assert docs._collect_registry_keys(tmp_path) == {"fooReceive", "fooStatus"}


def test_collect_registry_keys_resolves_module_level_constant(tmp_path: Path):
    plugin = tmp_path / "group" / "plugin" / "__init__.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(
        '_MY_TYPE = "constNode"\n'
        "_OTHER = frozenset({_MY_TYPE})\n"
        "\n"
        "class ConstNode:\n"
        "    type = _MY_TYPE\n"
        "\n"
        "class Unresolvable:\n"
        "    type = _NOT_DEFINED_HERE\n",
        encoding="utf-8",
    )
    assert docs._collect_registry_keys(tmp_path) == {"constNode"}


def _assert_current_index(root: Path) -> None:
    directory = docs._docs_dir(root)
    content = (directory / "README.md").read_text(encoding="utf-8")
    assert content.count(docs._START) == content.count(docs._END) == 1, "Node index needs exactly one marker pair"
    start, end = content.index(docs._START), content.index(docs._END)
    assert start < end, "Node index markers are reversed"
    actual = content[start:end + len(docs._END)]
    assert actual == docs._build_index_block(directory), "Node index is stale; regenerate with company docs nodes"


def test_repository_node_index_is_current():
    _assert_current_index(project_root())


def test_index_check_detects_stale_titles_and_duplicate_markers(tmp_path: Path):
    directory = docs._docs_dir(tmp_path)
    (directory / "group").mkdir(parents=True)
    card = directory / "group" / "node.md"
    card.write_text("# Original\n", encoding="utf-8")
    index = directory / "README.md"
    index.write_text(docs._build_index_block(directory), encoding="utf-8")
    _assert_current_index(tmp_path)
    card.write_text("# Updated\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="stale"):
        _assert_current_index(tmp_path)
    index.write_text(docs._build_index_block(directory) + docs._START, encoding="utf-8")
    with pytest.raises(AssertionError, match="marker pair"):
        _assert_current_index(tmp_path)
