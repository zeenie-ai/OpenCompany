"""Offline architecture asset checks and live-registry capability export.

`export-nodes` needs the backend environment; `check` uses only the stdlib.
Image content and architectural meaning still require source and visual review.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path("docs/diagrams/architecture")
STATUSES = {**{i: "current" for i in range(1, 13)}, 13: "accepted-roadmap",
            14: "accepted-roadmap", 15: "draft", 16: "sketch"}
UNAVAILABLE = {"twitterReceive": "Registered, but receive/poll implementation is unavailable; see its node card."}


def safe_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"Missing or out-of-tree architecture asset/source: {name}")
    return path


def sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() != ".png":
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def collect_nodes(root: Path = ROOT) -> list[dict]:
    """Read declarations without executing nodes, contacting providers or starting services."""
    sys.path.insert(0, str(root / "server"))
    importlib.import_module("nodes")
    from models.node_metadata import NODE_METADATA
    from services.node_registry import iter_tool_node_classes, registered_node_classes

    classes = registered_node_classes()
    if set(classes) != set(NODE_METADATA):
        raise ValueError("Metadata/class registry mismatch; inspect discovery before exporting")
    tools = dict(iter_tool_node_classes())
    cards = {p.stem: p for p in (root / "docs-internal/node-logic-flows").rglob("*.md")}
    rows = []
    for node_type, meta in sorted(NODE_METADATA.items()):
        cls = classes[node_type]
        if node_type not in cards:
            raise ValueError(f"Missing node card: {node_type}")
        source = Path(inspect.getfile(cls)).resolve().relative_to(root.resolve()).as_posix()
        hints = meta.get("uiHints", {})
        bases = [base.__name__ for base in cls.__mro__[1:] if base.__name__ not in {"object", "ABC"}]
        rows.append({
            "type": node_type, "name": meta.get("displayName", node_type),
            "family": (meta.get("group") or ["other"])[0],
            "groups": meta.get("group", []), "plugin_bases": bases,
            "component_kind": meta.get("componentKind", "generic"),
            "availability": "unavailable" if node_type in UNAVAILABLE else "registered",
            "availability_note": UNAVAILABLE.get(node_type, "Prerequisites and operation limits remain in the node card."),
            "agent_tool_eligible": node_type in tools,
            "workspace_task_eligible": bool(getattr(cls, "workspace_task", False)),
            "canvas_run_hint": "hidden" if hints.get("hideRunButton") else "renderer-dependent",
            "config_hint": bool(hints.get("isConfigNode")),
            "task_queue": str(getattr(cls, "task_queue", "")),
            "credentials": [getattr(c, "id", c.__name__) for c in getattr(cls, "credentials", ())],
            "operations": sorted(getattr(cls, "_operations", {}).keys()),
            "source": source, "card": cards[node_type].relative_to(root).as_posix(),
        })
    return rows


def export_nodes(root: Path = ROOT) -> None:
    rows = collect_nodes(root)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    directory = root / ASSETS
    directory.mkdir(parents=True, exist_ok=True)
    retired_card = "docs-internal/node-logic-flows/browser/browserHarness.md"
    # The migration card's actual category is discovered, rather than guessed.
    retired = list((root / "docs-internal/node-logic-flows").rglob("browserHarness.md"))
    if retired:
        retired_card = retired[0].relative_to(root).as_posix()
    data = {"source_commit": commit, "count": len(rows), "nodes": rows,
            "retired": [{"type": "browserHarness", "availability": "retired",
                         "card": retired_card, "note": "Migration reference; absent from the registry."},
                        *[{"type": node_type, "availability": "retired",
                           "source": "server/tests/nodes/test_unavailable_nodes.py"}
                          for node_type in ("vertex_managed_agent", "vertex_agent_admin", "vertexCloudTool")]]}
    (directory / "nodes.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    lines = ["# Registered-node capability index", "",
        f"Source-backed declaration snapshot: `{commit}`. **{len(rows)} registered types**.", "",
        "Companion to views 06 and 07. Rows describe declarations, not successful provider/device execution.",
        "Agent-tool eligibility comes from `iter_tool_node_classes`; direct Workspace tasks require the explicit",
        "`workspace_task` flag, authorization, saved-node enablement and controller admission. Graph use can be",
        "a trigger, action, agent, tool or passive binding; it does not make every node a main-flow action.",
        "The Canvas column reports only the NodeSpec hide hint. Actual renderers and runtime prerequisites",
        "can restrict Run independently. A hidden button does not establish backend refusal.", "",
        "Credentials, connected nodes, live runtimes and per-operation limitations remain authoritative in",
        "the linked node cards. Registered status does not certify availability, authorization or isolation.", "",
        "Regenerate with the backend Python environment: `python scripts/architecture_diagrams.py export-nodes`.", ""]
    for family in sorted({r["family"] for r in rows}):
        group = [r for r in rows if r["family"] == family]
        lines += [f"## {family} ({len(group)})", "",
                  "| Node / evidence | Status | Plugin base | Agent tool | Workspace task | Canvas hint | Queue |",
                  "|---|---|---|---|---|---|---|"]
        for row in group:
            link = "../../../" + row["card"]
            source = "../../../" + row["source"]
            lines.append(f'| [`{row["type"]}`]({link}) · [source]({source}) | {row["availability"]} | '
                f'{row["plugin_bases"][0] if row["plugin_bases"] else "BaseNode"} | '
                f'{"eligible" if row["agent_tool_eligible"] else "—"} | '
                f'{"eligible" if row["workspace_task_eligible"] else "—"} | '
                f'{row["canvas_run_hint"]} | `{row["task_queue"]}` |')
        lines.append("")
    lines += ["## Unavailable and retired", "",
        "- `twitterReceive` remains registered but unavailable; its row is not a working ingress promise.",
        f"- [`browserHarness`](../../../{retired_card}) is retired and absent from the registry.", "",
        "- `vertex_managed_agent`, `vertex_agent_admin` and `vertexCloudTool` are retired; saved workflows",
        "  report unknown-node errors. See the [regression contract](../../../server/tests/nodes/test_unavailable_nodes.py).", "",
        "See the [core](../../../docs-internal/audits/CORE_NODES.md) and",
        "[integration](../../../docs-internal/audits/INTEGRATION_NODES.md) audits for node-specific constraints.", ""]
    (directory / "NODES.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {len(rows)} nodes in {len(Counter(r['family'] for r in rows))} families")


def validate_entry(root: Path, entry: dict, baseline: str) -> None:
    number = entry["id"]
    if entry.get("status") != STATUSES.get(number):
        raise ValueError(f"Incorrect current/proposed status for view {number}")
    if entry.get("source_commit") != baseline:
        raise ValueError(f"Mixed source baseline for view {number}")
    if entry.get("review", {}).get("source") != "passed" or entry.get("review", {}).get("visual") != "passed":
        raise ValueError(f"Incomplete source/visual review for view {number}")
    if entry.get("generation", {}).get("tool") != "image_gen__imagegen" or not entry["generation"].get("attempts"):
        raise ValueError(f"Missing ImageGen provenance for view {number}")
    for key in ("image", "mermaid", "prompt", "brief"):
        path = safe_path(root, entry[key])
        if not path.stat().st_size:
            raise ValueError(f"Empty {key} for view {number}")
        expected = entry.get("sha256", {}).get(key)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"Missing provenance hash for {key} in view {number}")
        if sha256(path) != expected:
            raise ValueError(f"Changed {key}; refresh provenance/review for view {number}")
    image = safe_path(root, entry["image"]).read_bytes()
    if image[:8] != b"\x89PNG\r\n\x1a\n" or len(image) < 24:
        raise ValueError(f"Invalid PNG for view {number}")
    width, height = struct.unpack(">II", image[16:24])
    if width < 1024 or width <= height:
        raise ValueError(f"Non-landscape or undersized diagram {number}")
    mermaid = safe_path(root, entry["mermaid"]).read_text(encoding="utf-8")
    if not any(token in mermaid for token in ("flowchart ", "sequenceDiagram", "stateDiagram-v2")):
        raise ValueError(f"Missing Mermaid declaration for view {number}")
    prompt = safe_path(root, entry["prompt"]).read_text(encoding="utf-8")
    if baseline[:8] not in prompt or (number > 12 and not any(word in prompt.lower() for word in ("proposed", "draft", "sketch"))):
        raise ValueError(f"Missing baseline/status in prompt {number}")
    if not entry.get("sources"):
        raise ValueError(f"No implementation evidence for view {number}")
    for source in entry["sources"]:
        safe_path(root, source)
    reviewed = entry.get("reviewed_source_sha256", {})
    if set(reviewed) != set(entry["sources"]) or any(
        not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
        for value in reviewed.values()
    ):
        raise ValueError(f"Missing reviewed-source hashes for view {number}")
    for target in entry.get("doc_targets", []):
        safe_path(root, target)
    for prompt, expected in entry.get("extra_prompt_sha256", {}).items():
        if sha256(safe_path(root, prompt)) != expected:
            raise ValueError(f"Changed edit prompt; refresh provenance for view {number}")


def validate_node_index(root: Path, data: dict, expected_nodes: list[dict] | None = None) -> None:
    rows = data["nodes"]
    types = [r["type"] for r in rows]
    if len(types) != len(set(types)) or data["count"] != len(types):
        raise ValueError("Duplicate nodes or inconsistent capability count")
    if expected_nodes is not None:
        expected = {r["type"]: r for r in expected_nodes}
        actual = {r["type"]: r for r in rows}
        if set(actual) != set(expected):
            raise ValueError(f"Node coverage differs from registry: missing={sorted(set(expected)-set(actual))}, extra={sorted(set(actual)-set(expected))}")
        for key in expected:
            if actual[key] != expected[key]:
                raise ValueError(f"Stale capability declarations for {key}")
    for row in rows:
        safe_path(root, row["source"])
        safe_path(root, row["card"])
    for row in data.get("retired", []):
        if row["type"] in types or row["availability"] != "retired":
            raise ValueError("Retired node incorrectly listed as registered")
        safe_path(root, row.get("card") or row["source"])


def check(root: Path = ROOT) -> None:
    catalog = json.loads((root / ASSETS / "catalog.json").read_text(encoding="utf-8"))
    entries = catalog["diagrams"]
    if sorted(e["id"] for e in entries) != list(STATUSES):
        raise ValueError("Architecture suite must cover views 01–16 exactly once")
    for entry in entries:
        validate_entry(root, entry, catalog["source_commit"])
    changed = {source for entry in entries
               for source, expected in entry.get("reviewed_source_sha256", {}).items()
               if sha256(safe_path(root, source)) != expected}
    nodes = json.loads(safe_path(root, catalog["node_index"]).read_text(encoding="utf-8"))
    if nodes["source_commit"] != catalog["source_commit"]:
        raise ValueError("Node index and diagrams have different baselines")
    validate_node_index(root, nodes)
    print(f"OK: {len(entries)} reviewed ImageGen/Mermaid pairs; {nodes['count']} capability rows")
    if changed:
        print(f"REVIEW: {len(changed)} source files changed after diagram review; assess architectural meaning")


def build_catalog(root: Path = ROOT) -> None:
    entries = []
    for path in sorted((root / ASSETS).glob("[0-9][0-9]-*.json")):
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["sha256"] = {key: sha256(safe_path(root, entry[key]))
                           for key in ("image", "mermaid", "prompt", "brief")}
        raw = safe_path(root, entry["image"]).read_bytes()
        entry["image_dimensions"] = list(struct.unpack(">II", raw[16:24]))
        entry["reviewed_source_sha256"] = {source: sha256(safe_path(root, source)) for source in entry["sources"]}
        entry["generation"].setdefault("mode", "built-in")
        attempts = entry["generation"]["attempts"]
        if "selected_artifact" not in entry["generation"]:
            selected = [attempt for attempt in attempts if attempt.get("selected")]
            entry["generation"]["selected_artifact"] = (selected or attempts)[-1]["artifact"]
        entry["extra_prompt_sha256"] = {attempt["prompt"]: sha256(safe_path(root, attempt["prompt"]))
                                        for attempt in attempts if attempt.get("prompt")}
        entries.append(entry)
    baselines = {entry["source_commit"] for entry in entries}
    if len(baselines) != 1:
        raise ValueError("Expected one pinned code baseline")
    catalog = {"schema_version": 1, "source_commit": baselines.pop(),
               "mermaid_version": "11.16.1", "generator": "built-in ImageGen",
               "hash_normalization": "Text uses LF line endings; PNG uses original bytes",
               "node_index": (ASSETS / "nodes.json").as_posix(), "diagrams": entries}
    for entry in entries:
        validate_entry(root, entry, catalog["source_commit"])
    (root / ASSETS / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    check(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export-nodes", "build-catalog", "check"))
    args = parser.parse_args()
    try:
        if args.command == "export-nodes":
            export_nodes()
        elif args.command == "build-catalog":
            build_catalog()
        else:
            check()
    except (ValueError, KeyError) as exc:
        parser.exit(1, f"Architecture check failed: {exc}\n")
