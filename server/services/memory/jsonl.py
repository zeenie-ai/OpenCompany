"""Standard-JSONL conversation-memory helpers.

Lines are Anthropic Messages API objects: ``{"role": "user"|"assistant",
"content": str | List[ContentBlock], ...}``. Extra metadata
(``timestamp``, ``session_id``, ``model``, ...) rides alongside
``role`` / ``content`` and is preserved on round-trip; standard parsers
(Anthropic SDK, LangChain converters) ignore unknown keys.

Used by :mod:`nodes.agent.claude_code_agent` (session memory) via
:func:`services.cli_agent.service.AICliService.run_batch`.
"""

import json
from typing import Any, List, Tuple

from services.llm.protocol import Message


def parse_jsonl(text: str) -> List[Message]:
    """Standard JSONL -> native :class:`Message` list.

    Tool-call content blocks collapse to text; rows with unknown roles
    or unparseable JSON are skipped (forward compatibility).
    """
    if not text:
        return []
    out: List[Message] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = obj.get("role")
        content = obj.get("content")
        if isinstance(content, list):
            content = " ".join(
                blk.get("text", "") if isinstance(blk, dict) else str(blk)
                for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text"
            )
        if not isinstance(content, str):
            continue
        if role == "user":
            out.append(Message(role="user", content=content))
        elif role == "assistant":
            out.append(Message(role="assistant", content=content))
    return out


def append_message(
    text: str,
    role: str,
    content: str,
    **metadata: Any,
) -> str:
    """Append one Anthropic Messages-format line to a JSONL string.

    Metadata fields ride alongside ``role`` / ``content``. Always emits
    a trailing newline so successive appends concatenate cleanly.
    """
    line = json.dumps(
        {"role": role, "content": content, **metadata},
        ensure_ascii=False,
    )
    if text and not text.endswith("\n"):
        text = text + "\n"
    return (text or "") + line + "\n"


def trim_window(text: str, window_size: int) -> Tuple[str, List[str]]:
    """Keep the last ``window_size * 2`` lines (~ N user/assistant
    pairs). Returns ``(trimmed, removed)``. Removed lines are returned
    verbatim so callers can hand them to the long-term vector store.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    keep = window_size * 2
    if len(lines) <= keep:
        return text, []
    removed = lines[:-keep]
    trimmed = "\n".join(lines[-keep:]) + "\n"
    return trimmed, removed
