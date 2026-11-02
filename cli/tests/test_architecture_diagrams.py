"""Keep architecture publication complete without making image-quality claims."""
import importlib.util
from pathlib import Path
import struct

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("architecture_diagrams", ROOT / "scripts/architecture_diagrams.py")
diagrams = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagrams)


def test_published_architecture_assets_are_complete():
    diagrams.check(ROOT)


def test_missing_image_is_rejected(tmp_path):
    entry = {"id": 1, "status": "current", "source_commit": "baseline",
             "review": {"source": "passed", "visual": "passed"},
             "generation": {"tool": "image_gen__imagegen", "attempts": [{"kind": "generate"}]},
             "image": "missing.png"}
    with pytest.raises(ValueError, match="Missing or out-of-tree"):
        diagrams.validate_entry(tmp_path, entry, "baseline")


def test_proposal_cannot_be_published_as_current(tmp_path):
    with pytest.raises(ValueError, match="Incorrect current/proposed status"):
        diagrams.validate_entry(tmp_path, {"id": 13, "status": "current"}, "baseline")


def test_omitted_registry_node_is_rejected(tmp_path):
    data = {"count": 1, "nodes": [{"type": "one"}]}
    with pytest.raises(ValueError, match="missing=.*two"):
        diagrams.validate_node_index(tmp_path, data, [{"type": "one"}, {"type": "two"}])


def test_parent_directory_asset_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Missing or out-of-tree"):
        diagrams.safe_path(tmp_path, "../outside.png")


def test_text_provenance_survives_git_line_ending_conversion(tmp_path):
    lf = tmp_path / "diagram.mmd"
    crlf = tmp_path / "checkout.mmd"
    lf.write_bytes(b"flowchart LR\n A --> B\n")
    crlf.write_bytes(b"flowchart LR\r\n A --> B\r\n")
    assert diagrams.sha256(lf) == diagrams.sha256(crlf)


@pytest.mark.parametrize("hashes", [{}, {"image": ""}])
def test_missing_or_empty_asset_hash_is_rejected(tmp_path, hashes):
    (tmp_path / "image.png").write_bytes(b"image contents")
    entry = {"id": 1, "status": "current", "source_commit": "baseline",
             "review": {"source": "passed", "visual": "passed"},
             "generation": {"tool": "image_gen__imagegen", "attempts": [{}]},
             "image": "image.png", "sha256": hashes}
    with pytest.raises(ValueError, match="Missing provenance hash"):
        diagrams.validate_entry(tmp_path, entry, "baseline")


def test_missing_reviewed_source_hashes_are_rejected(tmp_path):
    contents = {"image": b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1280, 720),
                "mermaid": b"flowchart LR\n A --> B\n", "prompt": b"baseline diagram prompt\n", "brief": b"Source-backed brief\n"}
    entry = {"id": 1, "status": "current", "source_commit": "baseline",
             "review": {"source": "passed", "visual": "passed"},
             "generation": {"tool": "image_gen__imagegen", "attempts": [{}]},
             "sources": ["source.py"], "sha256": {}}
    for key, content in contents.items():
        path = tmp_path / (key + (".png" if key == "image" else ".txt"))
        path.write_bytes(content)
        entry[key] = path.name
        entry["sha256"][key] = diagrams.sha256(path)
    (tmp_path / "source.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing reviewed-source hashes"):
        diagrams.validate_entry(tmp_path, entry, "baseline")
