"""Conversation-memory utilities — package entrypoint.

Reorganised from the single-file ``services/memory.py`` so each
storage format / concern owns its own module:

  - :mod:`services.memory.markdown` — markdown parse/append/trim
    (used by aiAgent / chatAgent / rlm_agent)
  - :mod:`services.memory.jsonl` — Anthropic Messages JSONL
    parse/append/trim (used by claude_code_agent's session bridge)
  - :mod:`services.memory.vector_store` — per-session
    ``InMemoryVectorStore`` for long-term archival
  - :mod:`services.memory.state` — orchestration: clear every store
    keyed by an agent's conversational scope

Public surface is re-exported here so existing
``from services.memory import …`` imports keep working — the file
became a package in-place.
"""

from services.memory.markdown import (
    append_to_memory_markdown,
    parse_memory_markdown,
    trim_markdown_window,
)
from services.memory.vector_store import (
    _memory_vector_stores,
    get_memory_vector_store,
)
from services.memory.state import clear_agent_session_state
from services.memory.jsonl import (
    append_message,
    parse_jsonl,
    trim_window,
)

__all__ = [
    # Markdown helpers — used by every agent bridge (aiAgent /
    # chatAgent / rlm_agent / claude_code_agent).
    "parse_memory_markdown",
    "append_to_memory_markdown",
    "trim_markdown_window",
    # Vector-store helpers.
    "_memory_vector_stores",
    "get_memory_vector_store",
    # Orchestration.
    "clear_agent_session_state",
    # JSONL helpers — standalone primitive, not currently used by any
    # agent bridge but tested + kept for future SDK migration.
    "parse_jsonl",
    "append_message",
    "trim_window",
]
