"""Read-only, bounded access to shipped documentation, never arbitrary files.

The packaged manifest embeds the source Markdown so desktop/npm/Docker do
not depend on a git checkout. Live node contracts always outrank prose.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

SERVER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVER_ROOT.parent
BUNDLE_PATH = SERVER_ROOT / "resources" / "builder_documentation.json"
SOURCE_DIRS = ("docs-internal", "docs", "server", "client", "cli", "desktop", "scripts", "docker", "media")
EXCLUDED = {"node_modules", ".venv", "__pycache__", ".git", ".tmp", ".opencompany", ".codex", ".agents", "dist", "build", "tests", "release", "stage", "out", "vendor", "playwright-report", "test-results", "workspaces", "uploads", "logs", "tmp", "temp"}


def build_manifest(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    paths = list(root.glob("*.md"))
    for directory in SOURCE_DIRS:
        base = root / directory
        if base.is_dir():
            for folder, directories, files in os.walk(base, followlinks=False):
                directories[:] = [name for name in directories if name not in EXCLUDED and not name.startswith(".")]
                paths.extend(Path(folder) / name for name in files if name.lower().endswith(".md"))
    documents = {}
    for path in sorted(set(paths)):
        resolved = path.resolve()
        # Symlinked external documentation must not become a read escape.
        if not resolved.is_relative_to(root.resolve()) or path.stat().st_size > 2_000_000:
            continue
        source = path.relative_to(root).as_posix()
        content = path.read_text(encoding="utf-8")
        title = next((line.lstrip("# ").strip() for line in content.splitlines() if line.startswith("# ")), path.stem)
        documents["doc:" + source] = {
            "id": "doc:" + source, "path": source, "title": title, "content": content,
            "archived": any(part.lower() in {"archive", "archived"} for part in path.parts),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
    return {"version": 1, "documents": documents}


def package_documentation(root: Path = PROJECT_ROOT, destination: Path = BUNDLE_PATH) -> int:
    manifest = build_manifest(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(manifest["documents"])


class BuilderDocumentation:
    def __init__(self, manifest: dict[str, Any] | None = None):
        if manifest is None:
            # Source trees read current docs; packaged trees use the embedded
            # manifest (which includes docs absent from the distribution).
            if (PROJECT_ROOT / "docs-internal").is_dir():
                manifest = build_manifest()
            elif BUNDLE_PATH.is_file():
                manifest = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
            else:
                manifest = build_manifest()
        if manifest.get("version") != 1:
            raise ValueError("Unsupported Builder documentation manifest")
        self.documents = manifest["documents"]

    def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        terms = set(re.findall(r"[\w-]+", query.lower()))
        if not terms:
            return []
        hits = []
        for doc in self.documents.values():
            title, body = (doc["title"] + " " + doc["path"]).lower(), doc["content"].lower()
            score = sum(8 * title.count(term) + min(body.count(term), 10) for term in terms)
            if not score:
                continue
            start = min((body.find(term) for term in terms if term in body), default=0)
            hits.append((score, doc, doc["content"][max(0, start - 60):start + 240]))
        hits.sort(key=lambda hit: (hit[1]["archived"], -hit[0], hit[1]["id"]))
        return [{k: doc[k] for k in ("id", "title", "archived", "sha256")} | {"excerpt": excerpt} for _, doc, excerpt in hits[:min(max(limit, 1), 50)]]

    def related(self, node_type: str) -> list[str]:
        return [doc["id"] for doc in self.documents.values() if doc["path"].endswith("/" + node_type + ".md") or "/" + node_type + "/" in doc["path"]]

    def examples(self, node_type: str) -> list[dict[str, str]]:
        examples = []
        related = sorted((self.documents[key] for key in self.related(node_type)), key=lambda doc: (doc["archived"], doc["id"]))
        for doc in related:
            for language, content in re.findall(r"```(json|python|ts|typescript|javascript|bash)\s*\n(.*?)```", doc["content"], re.DOTALL):
                examples.append({"document_id": doc["id"], "language": language, "content": content[:2400]})
                if len(examples) == 3:
                    return examples
        return examples

    def read(self, document_id: str, offset: int = 0, max_chars: int = 12000) -> dict[str, Any]:
        if document_id not in self.documents:
            raise ValueError("Unknown documentation manifest ID. Search documentation first.")
        doc = self.documents[document_id]
        offset, max_chars = max(offset, 0), min(max(max_chars, 1), 24000)
        content = doc["content"]
        links = []
        for target in re.findall(r"\]\(([^\s)]+)(?:\s[^)]*)?\)", content):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            target_path = unquote(parsed.path)
            if not target_path:
                resolved = document_id
            else:
                source = posixpath.normpath(posixpath.join(posixpath.dirname(doc["path"]), target_path))
                resolved = "doc:" + source
            if resolved in self.documents:
                links.append({"link": target, "document_id": resolved, "anchor": parsed.fragment})
                if len(links) >= 100:
                    break
        return {k: doc[k] for k in ("id", "title", "archived", "sha256")} | {
            "content": content[offset:offset + max_chars], "offset": offset,
            "next_offset": offset + max_chars if offset + max_chars < len(content) else None,
            "total_chars": len(content), "related_documents": links,
            "contract_precedence": "Current registered plugin schemas override conflicting documentation. Archived documents are historical context.",
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package shipped Builder documentation")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--output", type=Path, default=BUNDLE_PATH)
    args = parser.parse_args()
    sys.stdout.write(f"Packaged {package_documentation(args.root, args.output)} Builder documents\n")
