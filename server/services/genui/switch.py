"""The owner's Generated UI switch: ``UserSettings.generated_ui``, off by
default (docs-internal/chat_protocol.md, "Generated UI").

Generated UI costs tokens on every step: the ``show_ui`` tool's description
rides each request of an agent that holds it, and the chat guide asks for
follow-up questions. One owner-wide switch, read when a run starts, turns
these off together:

- ``show_ui``: :func:`drop_chat_ui_when_off`, called once at the end of
  ``collect_agent_connections``, so every runtime that collects an agent's
  tools binds it only while the switch is on;
- the guide's follow-ups line and the follow-up buttons
  (``services/chat/guide.py``, ``services/chat/ledger.py``);
- the setup screen made for each hire (``services/employees/setup.py``).

Interfaces already in a chat keep working: a press still reaches the
employee as a ``[ui-event]`` line.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.logging import get_logger

logger = get_logger(__name__)

#: The node type of the Show UI tool (``nodes/chat/chat_ui``).
CHAT_UI_TYPE = "chatUi"


def generated_ui_on(settings: Optional[Mapping[str, Any]]) -> bool:
    """Whether a settings row turns Generated UI on: only a real ``True``."""
    return bool(settings) and settings.get("generated_ui") is True


async def generated_ui_enabled(database: Any) -> bool:
    """The owner's switch. Off when their settings row is missing or cannot
    be read."""
    try:
        settings = await database.get_user_settings()
    except Exception:  # noqa: BLE001 - off is the safe default
        logger.warning("Generated UI switch could not be read; treating it as off", exc_info=True)
        return False
    return generated_ui_on(settings)


def _shows_ui(tool: Mapping[str, Any]) -> bool:
    """Show UI itself, or an agent (a delegate tool, a teammate) that lists
    it among its own tools."""
    children = tool.get("child_tools") or ()
    return tool.get("node_type") == CHAT_UI_TYPE or any(child.get("node_type") == CHAT_UI_TYPE for child in children)


async def drop_chat_ui_when_off(tools: List[Dict[str, Any]], database: Any) -> List[Dict[str, Any]]:
    """``tools`` without Show UI while the switch is off: the tool itself, and
    the Show UI an agent's entry lists among its own tools, whose description
    would otherwise ride the delegating agent's requests. The switch is read
    only when Show UI is there."""
    if not any(_shows_ui(tool) for tool in tools):
        return tools
    if await generated_ui_enabled(database):
        return tools
    kept: List[Dict[str, Any]] = []
    for tool in tools:
        if tool.get("node_type") == CHAT_UI_TYPE:
            continue
        if _shows_ui(tool):
            tool = {**tool, "child_tools": [child for child in tool["child_tools"] if child.get("node_type") != CHAT_UI_TYPE]}
        kept.append(tool)
    logger.info("Generated UI is off, so Show UI is not bound")
    return kept


__all__ = ["CHAT_UI_TYPE", "drop_chat_ui_when_off", "generated_ui_enabled", "generated_ui_on"]
