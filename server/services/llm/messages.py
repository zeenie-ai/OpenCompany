"""Message validation and filtering utilities.

Provider-agnostic -- works with any message format.
"""

from typing import Any, List, Sequence


def is_valid_message_content(content: Any) -> bool:
    """Check if message content is non-empty for API calls."""
    if content is None:
        return False
    if isinstance(content, list):
        return any((isinstance(b, dict) and b.get("text", "").strip()) or (isinstance(b, str) and b.strip()) for b in content)
    if isinstance(content, str):
        return bool(content.strip())
    return bool(content)


def filter_empty_messages(messages: Sequence) -> List:
    """Filter out messages with empty content.

    Works with both LangChain BaseMessage objects and native Message dataclasses.
    """
    filtered = []
    for m in messages:
        # Detect role (works for both LangChain and native messages)
        role = getattr(m, "role", None) or getattr(m, "type", "")

        # Tool messages -- always keep
        if role == "tool":
            filtered.append(m)
            continue

        # AI/assistant with tool_calls -- keep even if content is empty
        if role in ("ai", "assistant"):
            tool_calls = getattr(m, "tool_calls", None)
            if tool_calls:
                filtered.append(m)
                continue

        # Everything else -- keep only if content is non-empty
        content = getattr(m, "content", None)
        if is_valid_message_content(content):
            filtered.append(m)

    return filtered
