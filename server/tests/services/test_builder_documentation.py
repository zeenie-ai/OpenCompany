import pytest

from services.builder_documentation import BuilderDocumentation, build_manifest, package_documentation


def test_manifest_links_pagination_archive_and_no_path_escape(tmp_path):
    docs = tmp_path / "docs-internal"
    (docs / "archive").mkdir(parents=True)
    (docs / "current.md").write_text("# Calendar\n" + "Calendar schedules " * 30 + "[related](related.md#hours) [escape](../../secret.md)", encoding="utf-8")
    (docs / "related.md").write_text("# Working hours\n", encoding="utf-8")
    (docs / "archive" / "old.md").write_text("# Calendar old\nCalendar " * 40, encoding="utf-8")
    generated = tmp_path / "desktop" / "release"
    generated.mkdir(parents=True)
    (generated / "old-copy.md").write_text("# Calendar old copy", encoding="utf-8")
    service = BuilderDocumentation(build_manifest(tmp_path))
    assert not any("release/" in document_id for document_id in service.documents)
    hits = service.search("calendar", 2)
    assert not hits[0]["archived"]
    assert hits[1]["archived"]
    read = service.read(hits[0]["id"], max_chars=40)
    assert len(read["content"]) == 40
    assert read["next_offset"] == 40
    assert read["related_documents"] == [{"link": "related.md#hours", "document_id": "doc:docs-internal/related.md", "anchor": "hours"}]
    with pytest.raises(ValueError, match="manifest ID"):
        service.read("../../secret.md")


def test_packaged_docs_independent_of_source_tree(tmp_path, monkeypatch):
    import services.builder_documentation as documentation

    (tmp_path / "README.md").write_text("# Guide\nHire your team", encoding="utf-8")
    destination = tmp_path / "bundle.json"
    assert package_documentation(tmp_path, destination) == 1
    (tmp_path / "README.md").unlink()
    monkeypatch.setattr(documentation, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(documentation, "BUNDLE_PATH", destination)
    service = BuilderDocumentation()
    assert "Hire your team" in service.read("doc:README.md")["content"]


def test_shipped_manifest_covers_every_node_flow():
    from services.builder_documentation import PROJECT_ROOT

    manifest = build_manifest()
    paths = [path.relative_to(PROJECT_ROOT).as_posix() for path in (PROJECT_ROOT / "docs-internal" / "node-logic-flows").rglob("*.md")]
    assert paths
    assert all("doc:" + path in manifest["documents"] for path in paths)
    assert not any(".opencompany" in document_id or ".venv" in document_id for document_id in manifest["documents"])
