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
from typing import Any, Dict

from services.media import write_media
from services.plugin import NodeContext

# An answer up to this many characters of JSON is returned whole.
INLINE_LIMIT_CHARS = 20_000
# Workspace folder the larger ones are saved under.
RESULT_SUBDIR = "treg"
_PREVIEW_STRING_CHARS = 80
_EMPTY = (None, "", [], {})


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
            "Read the fields you need from that file; do not call the tool again for them."
        ),
    }


__all__ = ["INLINE_LIMIT_CHARS", "RESULT_SUBDIR", "deliver", "preview_of"]
