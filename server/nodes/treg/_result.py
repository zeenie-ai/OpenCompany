"""How a treg answer reaches whoever asked for it.

Many treg tools answer with a provider's raw objects: a page of social posts
or search results can run to hundreds of thousands of characters. Returned
whole, an agent keeps only the first 100,000 characters of a tool result,
and a few such answers push everything earlier, the loaded skill included,
out of its conversation (docs-internal/errors.md, entry 28).

So an answer longer than :data:`INLINE_LIMIT_CHARS` is written to the
workflow's workspace as indented JSON, and the caller gets a reference to
that file (the ``FileRef`` every node passes around,
docs-internal/media_transport.md) and a preview of its shape.

treg's own copy of ``nodes/scraper/tikhub_action/_result.py``: a plugin does
not import another plugin's folder.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

from services.media import read_media_bytes, workspace_root, write_media
from services.plugin import NodeContext, NodeUserError

# An answer up to this many characters of JSON is returned whole.
INLINE_LIMIT_CHARS = 20_000
# Workspace folder the larger ones are saved under.
RESULT_SUBDIR = "treg"
_PREVIEW_STRING_CHARS = 80
_EMPTY = (None, "", [], {})
_MISSING = object()


def value_at(value: Any, path: Any, default: Any = None) -> Any:
    """Resolve treg's dotted paths, including list indices such as tasks.0.id."""
    if not isinstance(path, str) or not path:
        return default
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isascii() and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            return default
    return value


def read_saved_result(
    ctx: NodeContext, file_path: str, result_path: str, fields: list[str], offset: int, limit: int
) -> Dict[str, Any]:
    """Page a saved JSON answer in this workflow without invoking the provider."""
    folder, _, filename = file_path.partition("/")
    if folder != RESULT_SUBDIR or not re.fullmatch(r"[A-Za-z0-9._-]+\.json", filename):
        raise NodeUserError("Use the file.path returned by treg, such as treg/example.json.")
    result_dir = workspace_root(ctx).resolve() / RESULT_SUBDIR
    if result_dir.resolve() != result_dir:
        raise NodeUserError("The treg result folder must be inside this workflow's workspace.")
    _, payload = read_media_bytes(filename, workspace_dir=str(result_dir))
    try:
        value = json.loads(payload)
    except ValueError as exc:
        raise NodeUserError("This treg file does not contain a JSON answer.") from exc
    if result_path:
        value = value_at(value, result_path, _MISSING)
        if value is _MISSING:
            raise NodeUserError("result_path was not found. Use a dotted path from the saved answer's preview.")

    def project(item: Any) -> Any:
        if not fields:
            return item
        if not isinstance(item, dict):
            raise NodeUserError("fields selects properties of objects. Omit fields to read a scalar value.")
        return {key: selected for key in fields if (selected := value_at(item, key, _MISSING)) is not _MISSING}

    too_large = "The selected value is too large to return. Select a narrower result_path or fewer fields."
    if not isinstance(value, list):
        selected = project(value)
        if len(json.dumps(selected, ensure_ascii=False)) > INLINE_LIMIT_CHARS:
            raise NodeUserError(too_large)
        return {"data": selected}

    page = []
    used = 2  # enclosing brackets
    for item in value[offset : offset + limit]:
        selected = project(item)
        size = len(json.dumps(selected, ensure_ascii=False)) + (2 if page else 0)
        if used + size > INLINE_LIMIT_CHARS:
            if not page:
                raise NodeUserError(too_large)
            break
        page.append(selected)
        used += size
    result = {"data": page, "total": len(value), "count": len(page)}
    if offset + len(page) < len(value):
        result["next_offset"] = offset + len(page)
    return result


def preview_of(value: Any) -> Any:
    """The shape of an answer, with real values.

    Every list is cut to its first element followed by a count of the rest,
    empty values are left out and long strings are clipped. Paths in the
    preview are the paths in the saved file.
    """
    if isinstance(value, dict):
        return {key: preview_of(item) for key, item in value.items() if item not in _EMPTY}
    if isinstance(value, list):
        head = [preview_of(item) for item in value[:1]]
        return head + [f"... {len(value) - 1} more"] if len(value) > 1 else head
    if isinstance(value, str) and len(value) > _PREVIEW_STRING_CHARS:
        return value[:_PREVIEW_STRING_CHARS] + "..."
    return value


def deliver(ctx: NodeContext, stem: str, data: Any) -> Dict[str, Any]:
    """Output fields for one answer: ``data`` when it is small, otherwise
    ``file`` (where the whole answer was saved), ``preview`` and ``note``."""
    size = len(json.dumps(data, ensure_ascii=False))
    if size <= INLINE_LIMIT_CHARS:
        return {"data": data}
    ref = write_media(
        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"),
        ctx=ctx,
        stem=stem,
        ext="json",
        mime_type="application/json",
        subdir=RESULT_SUBDIR,
    )
    return {
        "file": ref.model_dump(mode="json"),
        "preview": preview_of(data),
        "note": (
            f"The whole answer ({size:,} characters) is saved in the workspace at {ref.path}. "
            "preview shows its shape with real values: the first element of each list only, long strings clipped. "
            f"Use treg_action operation=read_result with file_path={ref.path}, a dotted result_path from the preview, "
            "and fields for the properties you need. Continue at next_offset to inspect more rows for free."
        ),
    }


__all__ = ["INLINE_LIMIT_CHARS", "RESULT_SUBDIR", "deliver", "preview_of", "read_saved_result", "value_at"]
