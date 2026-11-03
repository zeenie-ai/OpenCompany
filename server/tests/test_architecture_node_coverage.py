"""Verify the published capability matrix against the authoritative registry."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("architecture_diagrams", ROOT / "scripts/architecture_diagrams.py")
diagrams = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagrams)


def test_architecture_capability_index_matches_live_registry():
    data = json.loads((ROOT / "docs/diagrams/architecture/nodes.json").read_text(encoding="utf-8"))
    diagrams.validate_node_index(ROOT, data, diagrams.collect_nodes(ROOT))
