"""Offline Markdown checks; test-only, independent of CLI/backend runtimes."""

from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import unicodedata
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

_MARKDOWN = MarkdownIt("commonmark", {"html": True})
_STATUS = re.compile(
    r"^\s*(?:>\s*)?(?:\*\*)?(?:status:\s*(?:\*\*)?\s*)?"
    r"(?:\*\*)?(archived|historical|superseded|prototype|planned)\b", re.I,
)
_PLACEHOLDER = re.compile(r"<[^>]+>|\*|\.\.\.")
_OPTIONAL_LINKS = {
    # Optional sibling project, deliberately not vendored in OpenCompany.
    ("CLAUDE.md", "../polyglot-server/ARCHITECTURE.md"),
}


def markdown_paths(root: Path) -> list[Path]:
    """Tracked Markdown plus new, non-ignored source documents."""
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root, check=True, capture_output=True)
    return sorted({root / name for name in result.stdout.decode("utf-8").split("\0") if name.lower().endswith(".md") and (root / name).is_file()})


def document_status(path: Path, text: str) -> str:
    if any(part.lower() in {"archive", "archived"} for part in path.parts):
        return "historical"
    for line in text.splitlines()[:20]:
        match = _STATUS.match(line)
        if match:
            status = match.group(1).lower()
            return "historical" if status in {"archived", "historical", "superseded"} else status
    return "current"


def navigation_text(path: Path, text: str) -> str:
    """Preserve archived bodies; check their first current-successor notice."""
    text = without_historical_sections(text)
    if document_status(path, text) != "historical":
        return text
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines[:20]) if line.lstrip().startswith(">")), None)
    if start is None:
        return ""
    end = start
    while end < len(lines) and lines[end].lstrip().startswith(">"):
        end += 1
    return "\n" * start + "\n".join(lines[start:end])


def without_historical_sections(text: str) -> str:
    """Explicit paired regions preserve a historical body in a current guide."""
    start = "<!-- docs-lint: historical-start -->"
    end = "<!-- docs-lint: historical-end -->"
    historical = False
    output = []
    for line in text.splitlines():
        if line.strip() == start:
            if historical:
                raise ValueError("Historical documentation regions cannot nest")
            historical = True
            output.append("")
        elif line.strip() == end:
            if not historical:
                raise ValueError("Historical documentation region has no opening marker")
            historical = False
            output.append("")
        else:
            output.append("" if historical else line)
    if historical:
        raise ValueError("Historical documentation region has no closing marker")
    return "\n".join(output)


def _walk(tokens):
    for token in tokens:
        yield token
        if token.children:
            yield from _walk(token.children)


def heading_slug(text: str) -> str:
    """GitHub-style Unicode slug after Markdown formatting is removed."""
    chars = []
    for char in text.lower():
        if char.isspace():
            chars.append("-")
        elif char in "-_" or unicodedata.category(char)[0] in {"L", "N", "M"}:
            if char not in {"\ufe0e", "\ufe0f"}:
                chars.append(char)
    return "".join(chars)


class _HTMLIds(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if value and (key == "id" or (tag == "a" and key == "name")):
                self.ids.add(value)


def anchors(text: str) -> set[str]:
    tokens = _MARKDOWN.parse(text)
    found: set[str] = set()
    seen: Counter[str] = Counter()
    for i, token in enumerate(tokens):
        if token.type == "heading_open":
            plain = "".join(t.content for t in _walk(tokens[i + 1].children or []) if t.type in {"text", "code_inline"})
            base = heading_slug(plain)
            slug = base
            while slug in found:
                seen[base] += 1
                slug = f"{base}-{seen[base]}"
            found.add(slug)
    parser = _HTMLIds()
    for token in _walk(tokens):
        if token.type in {"html_inline", "html_block"}:
            parser.feed(token.content)
    found.update(parser.ids)
    found.update("user-content-" + value for value in parser.ids)
    return found


def link_errors(root: Path, doc: Path, text: str | None = None) -> list[str]:
    text = doc.read_text(encoding="utf-8") if text is None else text
    errors = []
    for block in _MARKDOWN.parse(navigation_text(doc, text)):
        if not block.children:
            continue
        line = (block.map or [0])[0] + 1
        for token in _walk(block.children):
            attribute = "href" if token.type == "link_open" else "src" if token.type == "image" else None
            if attribute is None:
                continue
            target = token.attrGet(attribute) or ""
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            destination, fragment = unquote(parsed.path), unquote(parsed.fragment)
            if _PLACEHOLDER.search(destination) and doc.name == "_TEMPLATE.md":
                continue
            if (doc.relative_to(root).as_posix(), destination) in _OPTIONAL_LINKS:
                continue
            resolved = (root / destination.lstrip("/") if destination.startswith("/") else doc.parent / destination).resolve() if destination else doc.resolve()
            if not resolved.is_relative_to(root.resolve()):
                errors.append(f"line {line}: {target!r} leaves the repository")
            elif not resolved.exists():
                errors.append(f"line {line}: missing target {target!r}")
            elif fragment and resolved.is_file() and resolved.suffix.lower() == ".md":
                if fragment not in anchors(resolved.read_text(encoding="utf-8")):
                    errors.append(f"line {line}: missing anchor {target!r}")
    return errors


def prose_code_spans(text: str):
    """Inline code in prose only, excluding fenced illustrative examples."""
    text = without_historical_sections(text)
    lines = text.splitlines()
    for block in _MARKDOWN.parse(text):
        if block.children:
            first, last = block.map or (0, len(lines))
            source = "\n".join(lines[first:last])
            cursor = 0
            for token in _walk(block.children):
                if token.type == "code_inline":
                    needle = token.markup + token.content + token.markup
                    position = source.find(needle, cursor)
                    if position < 0:
                        position = source.find(token.content, cursor)
                    if position < 0:
                        position = cursor
                    cursor = position + len(needle)
                    yield first + source[:position].count("\n") + 1, token.content
