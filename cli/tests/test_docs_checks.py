"""Fixtures for Markdown checks; no live services or HTTP requests."""

from pathlib import Path

import pytest

from cli.tests.docs_checks import anchors, document_status, link_errors, prose_code_spans


def test_reference_links_images_and_encoded_paths(tmp_path: Path):
    (tmp_path / "target space.md").write_text("# Target\n", encoding="utf-8")
    (tmp_path / "photo.png").write_bytes(b"fixture")
    source = tmp_path / "source.md"
    text = "[reference][r]\n\n![photo](photo.png)\n\n[r]: target%20space.md#target\n"
    assert link_errors(tmp_path, source, text) == []
    assert "missing target" in link_errors(tmp_path, source, "![missing](missing.png)")[0]


def test_code_examples_are_not_navigation(tmp_path: Path):
    source = tmp_path / "source.md"
    text = "`[not a link](missing.md)`\n\n```md\n[example](missing.md)\n```\n"
    assert link_errors(tmp_path, source, text) == []
    assert list(prose_code_spans("`server/real.py`\n```py\n`server/fake.py`\n```")) == [(1, "server/real.py")]


def test_unicode_duplicate_headings_and_explicit_ids(tmp_path: Path):
    target = tmp_path / "target.md"
    target.write_text("# Café & **世界**\n# Repeat\n# Repeat\n# Repeat-1\n<a id=\"custom-id\"></a>\n", encoding="utf-8")
    assert {"café--世界", "repeat", "repeat-1", "repeat-1-1", "custom-id"} <= anchors(target.read_text(encoding="utf-8"))
    source = tmp_path / "source.md"
    assert link_errors(tmp_path, source, "[one](target.md#caf%C3%A9--%E4%B8%96%E7%95%8C) [two](target.md#custom-id)") == []
    assert "missing anchor" in link_errors(tmp_path, source, "[bad](target.md#absent)")[0]


def test_incidental_history_word_does_not_exempt_doc(tmp_path: Path):
    source = tmp_path / "current.md"
    text = "# Current guide\nThis replaces historical behavior.\n[broken](missing.md)"
    assert document_status(source, text) == "current"
    assert link_errors(tmp_path, source, text)
    assert document_status(tmp_path / "SKILL.v1.md", "# Versioned active default") == "current"


@pytest.mark.parametrize("notice,expected", [
    ("**Status:** Planned", "planned"),
    ("**Status:** Prototype reference", "prototype"),
    ("**Status:** **Historical**", "historical"),
    ("Status: **Planned**", "planned"),
    ("> **Status:** Superseded", "historical"),
    ("**Status: Archived**", "historical"),
    ("**Prototype reference**", "prototype"),
    ("The status is planned for a later review.", "current"),
    ("This section explains superseded prototype behavior.", "current"),
])
def test_explicit_markdown_status_forms(notice, expected, tmp_path: Path):
    assert document_status(tmp_path / "guide.md", "# Guide\n\n" + notice) == expected


def test_archived_body_preserved_current_notice_checked(tmp_path: Path):
    source = tmp_path / "ARCHIVE" / "old.md"
    text = "# Old\n\n> **ARCHIVED — historical.**\n> Current guide: [current](../current.md)\n\n[old implementation](gone.py)\n"
    errors = link_errors(tmp_path, source, text)
    assert len(errors) == 1 and "current.md" in errors[0]
    (tmp_path / "current.md").write_text("# Current", encoding="utf-8")
    assert link_errors(tmp_path, source, text) == []


def test_optional_sibling_exception_is_narrow(tmp_path: Path):
    link = "[optional](../polyglot-server/ARCHITECTURE.md)"
    assert link_errors(tmp_path, tmp_path / "CLAUDE.md", link) == []
    assert "leaves the repository" in link_errors(tmp_path, tmp_path / "other.md", link)[0]


def test_template_exception_keeps_real_links_checked(tmp_path: Path):
    text = "[template](../server/nodes/%3Cgroup%3E/node.py) [broken](missing.md)"
    errors = link_errors(tmp_path, tmp_path / "_TEMPLATE.md", text)
    assert len(errors) == 1 and "missing.md" in errors[0]


def test_historical_regions_preserve_current_claims_and_incoming_anchors(tmp_path: Path):
    source = tmp_path / "mixed.md"
    text = "# Current\n[current](missing.md)\n<!-- docs-lint: historical-start -->\n## Old section\n[old](gone.py)\n`server/gone.py`\n<!-- docs-lint: historical-end -->\n`server/current.py`\n"
    errors = link_errors(tmp_path, source, text)
    assert len(errors) == 1 and "missing.md" in errors[0]
    assert list(prose_code_spans(text)) == [(8, "server/current.py")]
    assert "old-section" in anchors(text)
    with pytest.raises(ValueError, match="closing marker"):
        list(prose_code_spans("<!-- docs-lint: historical-start -->\nanything"))
