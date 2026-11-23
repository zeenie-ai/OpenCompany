"""Reading a model's reply that should be one JSON object.

Shared by the code that prompts a chat model for JSON: the translate
plugin's LLM provider and the judgment plugin's chat-model fallback.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


def parse_json_object(content: Optional[str]) -> Dict[str, Any]:
    """The JSON object in ``content``, tolerating a code fence around it.

    Models still occasionally wrap JSON in ```json despite being told not to,
    and failing over a fence would be needlessly brittle. Raises
    ``ValueError`` when the reply is not JSON, or is JSON but not an object.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"model did not return JSON: {text[:200]}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"model returned {type(parsed).__name__}, expected an object")
    return parsed


__all__ = ["parse_json_object"]
